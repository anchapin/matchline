"""Dataset adapters + quantity-takeoff measurement layer.

End goal is quantity takeoff from drawings. Per domain input, facades use
symbols/tags for window (and door) types, with a separate schedule table on
the drawings giving dimensions per type. The pipeline is therefore
three-stage -- not dense per-pixel segmentation:

  (1) SYMBOL SPOTTING .... spot + classify window/door type symbols on
      elevations and floor plans -> count per type tag. This is the WiSARD
      classifier's job (region in, label out).
  (2) SCHEDULE PARSING ... document-layout / table-parsing task: find the
      window/door schedule table on the sheet, parse it into
      {tag: dimensions}. (Jesse's document-layout analysis is the natural
      fit here; v1 takes the schedule as CSV.)
  (3) AREA ROLLUP ........ count x scheduled dimensions, summed per category.

  detections -> schedule entries -> takeoff lines (count x dims = m^2)

Floor areas are the exception: they come from polygon measurement of Room /
area regions (count x dims cannot produce them), via measure_takeoff.

The WiSARD core (jesse.py) stays a pure classifier. v1 region proposals come
from ground-truth annotations; v2 will use a WiSARD sliding-window scan plus
OCR of adjacent tag text; tag text extraction is the v2 gap (current
annotation sets carry labels but not the type tags).

Output contract
---------------
* ``SymbolSample``  - one cropped symbol instance for classifier training.
* ``Detection``     - one spotted symbol: label, schedule tag, bbox, score.
* ``ScheduleEntry`` - one schedule row: tag -> category + dimensions (m).
* ``TakeoffLine``   - joined row: count x dims = area for one tag.
* ``TakeoffResult`` - per-tag lines + per-category m^2 totals + unmatched
  detections (tags with no schedule entry -- reported, never silently
  dropped; auditability is the point).
* ``Region`` / ``measure_takeoff`` - polygon path, retained for floor_area.

Drawing scale: polygon areas need ``DrawingScale`` (m/px). The count x dims
path does NOT need drawing scale at all -- dimensions come from the
schedule, which is why it is more robust than pixel measurement.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import cv2  # opencv-python
except ImportError:
    cv2 = None  # lazy import below; parse_schedule_table requires it
import numpy as np
from lxml import etree

from detector.classes import CLASS_NAMES
from safe_xml import safe_xml_parser

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------

TAKEOFF_CATEGORIES = (
    "floor_area",  # enclosed areas: rooms, shafts, balconies, elevators, stairs
    "wall",
    "window",
    "door",
    "fixture",  # sinks, toilets, tubs, showers, cooktops (counted, area ~0)
    "lighting",  # light fixtures: counted per schedule tag, watts summed
    "opening",  # generic openings when the set does not distinguish
)


@dataclass
class SymbolSample:
    """One cropped symbol instance for classifier training."""

    image: np.ndarray  # (H, W) float64 grayscale, 0..255, dark ink on light bg
    label: str
    source: str  # e.g. "aec-bench:sheet_01"
    bbox: tuple  # (xtl, ytl, xbr, ybr) in source pixel coords


@dataclass
class Region:
    """One measured polygon with a takeoff category."""

    category: str  # one of TAKEOFF_CATEGORIES
    polygon_px: list  # [(x, y), ...] in source pixel coords
    source: str
    drawing_type: str = "floor_plan"  # or "elevation"
    label: str = ""  # original dataset label, e.g. "Single Swing Door"


@dataclass
class DrawingScale:
    m_per_px: float | None
    note: str = ""


@dataclass
class TakeoffResult:
    drawing_type: str
    scale: DrawingScale
    regions: list = field(default_factory=list)
    area_px2: dict = field(default_factory=dict)  # category -> px^2
    area_m2: dict = field(default_factory=dict)  # category -> m^2 (if scale)
    counts: dict = field(default_factory=dict)  # category -> region count
    # --- three-stage pipeline (detections -> schedule -> rollup) ---
    lines: list = field(default_factory=list)  # TakeoffLine per tag
    unmatched: list = field(default_factory=list)  # Detections w/o schedule entry


@dataclass
class Detection:
    """Stage-1 output: one spotted + classified symbol instance."""

    label: str  # classifier label, e.g. "Window"
    tag: str  # schedule tag, e.g. "A" / "W-1" ("" if not extracted)
    score: float  # classifier margin/confidence
    bbox: tuple  # (xtl, ytl, xbr, ybr) in source pixel coords
    source: str  # e.g. "aec-bench:sheet_01"
    drawing_type: str = "floor_plan"


@dataclass
class ScheduleEntry:
    """Stage-2 output: one schedule-table row.

    Window/door rows carry dimensions in meters; lighting rows carry watts
    per fixture (width/height None). ``description``/``lamp_type`` are the
    human-readable schedule text (e.g. "2x4 recessed LED troffer" / "LED").
    """

    tag: str
    category: str  # "door" / "window" / "lighting"
    width_m: float | None
    height_m: float | None
    note: str = ""
    watts: float | None = None  # lighting: W per fixture
    description: str = ""  # lighting: fixture description
    lamp_type: str = ""  # lighting: lamp/technology


@dataclass
class TakeoffLine:
    """Stage-3 output: count x scheduled dims for one tag."""

    tag: str
    category: str
    count: int
    width_m: float | None
    height_m: float | None
    area_m2: float | None  # count * width * height (None if dims unknown)


def polygon_area_px2(poly) -> float:
    """Shoelace area of a polygon given as [(x, y), ...]."""
    p = np.asarray(poly, dtype=np.float64)
    if len(p) < 3:
        return 0.0
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


def box_to_polygon(xtl, ytl, xbr, ybr):
    return [(xtl, ytl), (xbr, ytl), (xbr, ybr), (xtl, ybr)]


def measure_takeoff(regions: list[Region], drawing_type: str, scale: DrawingScale) -> TakeoffResult:
    """Sum region polygon areas per takeoff category."""
    res = TakeoffResult(drawing_type=drawing_type, scale=scale, regions=list(regions))
    for r in regions:
        a = polygon_area_px2(r.polygon_px)
        res.area_px2[r.category] = res.area_px2.get(r.category, 0.0) + a
        res.counts[r.category] = res.counts.get(r.category, 0) + 1
    if scale.m_per_px:
        k = scale.m_per_px**2
        for cat, a in res.area_px2.items():
            res.area_m2[cat] = a * k
    return res


def scale_from_reference(
    regions: list[Region], category: str, known_m: float, agg=np.median
) -> DrawingScale:
    """Estimate m/px from a known real-world dimension.

    E.g. a single swing door leaf is typically 0.9 m wide: measure the
    median box width of door regions and solve for the scale. Marked as an
    estimate -- replace with title-block scale whenever available.
    """
    widths = []
    for r in regions:
        if r.category == category and len(r.polygon_px) == 4:
            xs = [p[0] for p in r.polygon_px]
            widths.append(max(xs) - min(xs))
    if not widths:
        return DrawingScale(None, f"no {category} regions for reference scale")
    m_per_px = known_m / float(agg(widths))
    return DrawingScale(
        m_per_px,
        f"estimated from {category} width={known_m} m "
        f"(median {float(agg(widths)):.1f} px, n={len(widths)})",
    )


# ---------------------------------------------------------------------------
# Three-stage takeoff pipeline: detections -> schedule -> rollup
# ---------------------------------------------------------------------------


class DetectorOutputValidationError(ValueError):
    """Raised when detector JSON output violates the schema contract."""


def _validate_detector_output(data: dict, path: str | Path) -> None:
    """Validate detector JSON output against the versioned schema contract.

    Raises DetectorOutputValidationError with a descriptive message on any
    violation. The pipeline MUST fail loudly — not silently — on schema
    violations to catch detector output format drift.
    """
    schema_path = Path(__file__).resolve().parent / "schemas" / "detector_output_v1.schema.json"
    if not schema_path.exists():
        raise DetectorOutputValidationError(
            f"Schema not found at {schema_path}; cannot validate {path}"
        )

    try:
        import jsonschema
    except ImportError:
        raise DetectorOutputValidationError(
            "jsonschema is required for detector output validation. "
            "Install with: pip install jsonschema"
        )

    schema = json.loads(schema_path.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(data))
    if errors:
        first = errors[0]
        raise DetectorOutputValidationError(
            f"Detector output {path} violates schema "
            f"at {' > '.join(str(p) for p in first.path)}: {first.message}"
        )


def detections_from_yolo_json(
    yolo_json_path: str | Path,
    source: str,
) -> list[Detection]:
    """Convert sahi_infer.py JSON output to Detection list.

    yolo_json_path: path to the JSON written by sahi_infer.py main().
    source: sheet identifier, e.g. "aec-bench:sheet_01" — stored as Detection.source.

    Raises DetectorOutputValidationError if the JSON violates the versioned
    detector output schema (schemas/detector_output_v1.schema.json).
    """
    try:
        data = json.loads(Path(yolo_json_path).read_text())
    except json.JSONDecodeError as exc:
        raise DetectorOutputValidationError(
            f"Detector output {yolo_json_path} is not valid JSON: {exc}"
        ) from exc
    _validate_detector_output(data, yolo_json_path)
    dets = []
    for i, p in enumerate(data["preds"]):
        try:
            label = CLASS_NAMES[p["cls"]]
        except IndexError:
            raise DetectorOutputValidationError(
                f"preds[{i}]: cls={p['cls']} is out of range for CLASS_NAMES "
                f"(len={len(CLASS_NAMES)}). Detector output format may have changed."
            ) from None
        dets.append(
            Detection(
                label=label,
                tag="",  # tag extracted by separate OCR/WiSARD step (DET-02/DET-03)
                score=float(p["conf"]),
                bbox=(float(p["x0"]), float(p["y0"]), float(p["x1"]), float(p["y1"])),
                source=source,
                drawing_type="floor_plan",
            )
        )
    return dets


def detections_from_regions(regions: list[Region], score: float = 1.0) -> list[Detection]:
    """Stage 1 (v1): ground-truth regions -> Detections.

    Boxes become detections with the region label; polygons (floor_area,
    walls) are NOT detections -- they are measured directly by
    measure_takeoff. Tags are empty: current annotation sets carry class
    labels but not the type tags printed next to symbols on real sheets.

    TODO(v2): tag extraction -- OCR the text adjacent to each symbol bbox
    (type tags like "A", "W-1" are callouts, not part of the symbol glyph).
    Until then, detections can only be counted per class, not per type.
    """
    out = []
    for r in regions:
        if r.category not in ("door", "window", "fixture", "lighting"):
            continue
        xs = [p[0] for p in r.polygon_px]
        ys = [p[1] for p in r.polygon_px]
        out.append(
            Detection(
                label=r.label,
                tag="",
                score=score,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                source=r.source,
                drawing_type=r.drawing_type,
            )
        )
    return out


def parse_schedule_csv(path_or_rows) -> dict[str, ScheduleEntry]:
    """Stage 2 (v1): parse a window/door schedule from CSV.

    Columns: tag, category, width_m, height_m[, note]. This is the interchange
    format: whatever the table parser (stage 2 full version) extracts gets
    normalized into this before rollup.
    """
    if isinstance(path_or_rows, (str, Path)):
        f = open(path_or_rows, newline="")
        close = True
    else:
        f, close = path_or_rows, False
    sched: dict[str, ScheduleEntry] = {}
    try:
        for row in csv.DictReader(f):
            tag = row["tag"].strip()
            sched[tag] = ScheduleEntry(
                tag=tag,
                category=row.get("category", "").strip().lower() or "window",
                width_m=float(row["width_m"]) if row.get("width_m") else None,
                height_m=float(row["height_m"]) if row.get("height_m") else None,
                note=row.get("note", "").strip(),
            )
    finally:
        if close:
            f.close()
    return sched


def parse_lighting_schedule_csv(path_or_rows) -> dict[str, ScheduleEntry]:
    """Stage 2 (v1): parse a lighting fixture schedule from CSV.

    Columns: tag, description, lamp_type, watts[, category][, note].
    Category defaults to "lighting". This is the interchange format for the
    full table-parser (stage 2): whatever it extracts gets normalized here
    before the lighting rollup.
    """
    if isinstance(path_or_rows, (str, Path)):
        f = open(path_or_rows, newline="")
        close = True
    else:
        f, close = path_or_rows, False
    sched: dict[str, ScheduleEntry] = {}
    try:
        for row in csv.DictReader(f):
            tag = row["tag"].strip()
            watts = row.get("watts", "").strip()
            sched[tag] = ScheduleEntry(
                tag=tag,
                category=row.get("category", "").strip().lower() or "lighting",
                width_m=None,
                height_m=None,
                note=row.get("note", "").strip(),
                watts=float(watts) if watts else None,
                description=row.get("description", "").strip(),
                lamp_type=row.get("lamp_type", "").strip(),
            )
    finally:
        if close:
            f.close()
    return sched


def parse_schedule_table(sheet_image: np.ndarray) -> dict[str, ScheduleEntry]:
    """Stage 2 (full): find + parse the schedule table on a sheet image.

    Heuristic approach:
    1. Detect horizontal/vertical separator lines (table grid)
    2. Segment cells
    3. OCR cell text
    4. Map columns by header row

    Raises ImportError if opencv-python is not installed.
    """
    if cv2 is None:
        raise ImportError(
            "opencv-python is required for parse_schedule_table: pip install opencv-python"
        )
    # Convert to grayscale if needed
    if len(sheet_image.shape) == 3:
        gray = cv2.cvtColor(sheet_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = sheet_image

    # Detect horizontal lines: close horizontal morphology, threshold
    horiz = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((1, 40)))
    h_lines = cv2.threshold(horiz, 0, 255, cv2.THRESH_BINARY)[1]
    h_coords = [r for r in range(h_lines.shape[0]) if h_lines[r, :].mean() > 200]

    # Detect vertical lines: close vertical morph, threshold
    vert = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, np.ones((40, 1)))
    v_lines = cv2.threshold(vert, 0, 255, cv2.THRESH_BINARY)[1]
    v_coords = [c for c in range(v_lines.shape[1]) if v_lines[:, c].mean() > 200]

    if len(h_coords) < 2 or len(v_coords) < 2:
        raise NotImplementedError(
            "No table grid detected on this sheet. "
            "Use parse_schedule_csv() with a CSV export instead."
        )

    # Row detection: split at horizontal lines
    row_bounds = []
    prev_h = 0
    for h in h_coords:
        if h - prev_h > 10:  # minimum row height
            row_bounds.append((prev_h, h))
        prev_h = h
    if prev_h < gray.shape[0] - 5:
        row_bounds.append((prev_h, gray.shape[0]))

    # Column detection: split at vertical lines
    col_bounds = []
    prev_v = 0
    for v in v_coords:
        if v - prev_v > 10:  # minimum col width
            col_bounds.append((prev_v, v))
        prev_v = v
    if prev_v < gray.shape[1] - 5:
        col_bounds.append((prev_v, gray.shape[1]))

    # OCR each cell with pytesseract
    try:
        import pytesseract
    except ImportError:
        raise ImportError(
            "pytesseract required for schedule table parsing: pip install pytesseract"
        )

    rows_data = []
    for y0, y1 in row_bounds:
        row_cells = []
        for x0, x1 in col_bounds:
            crop = gray[y0:y1, x0:x1]
            text = pytesseract.image_to_string(crop, config="--psm 6").strip()
            row_cells.append(text)
        rows_data.append(row_cells)

    # Find header row: look for "tag", "type", "width", "height" in first non-empty row
    header_idx = None
    for i, row in enumerate(rows_data):
        joined = " ".join(row).lower()
        if any(k in joined for k in ["tag", "type", "width", "height", "dims"]):
            header_idx = i
            break

    if header_idx is None:
        raise NotImplementedError(
            "Could not identify table header row. Use parse_schedule_csv() instead."
        )

    # Build column index from header
    header = [c.lower().strip() for c in rows_data[header_idx]]
    # Map: "tag"->0, "type"/"cat"/"category"->1, "width"/"w"->2, "height"/"h"->3, "note"/"desc"->4
    col_map = {}
    for i, h in enumerate(header):
        if "tag" in h and "num" not in h:
            col_map["tag"] = i
        elif "type" in h or "cat" in h or "category" in h:
            col_map["category"] = i
        elif "width" in h or ("w" == h.strip()):
            col_map["width"] = i
        elif "height" in h or ("h" == h.strip()):
            col_map["height"] = i
        elif "note" in h or "desc" in h:
            col_map["note"] = i

    # Parse data rows
    schedule = {}
    for row in rows_data[header_idx + 1 :]:
        if not any(c.strip() for c in row):
            continue
        tag_raw = row[col_map.get("tag", 0)].strip()
        if not tag_raw or tag_raw in [c.lower() for c in header]:
            continue
        tag = tag_raw.upper().replace(" ", "")

        cat_raw = row[col_map.get("category", 1)].strip().lower() if col_map.get("category") else ""
        category = (
            "window"
            if "window" in cat_raw or "win" in cat_raw
            else "door"
            if "door" in cat_raw
            else "lighting"
            if "light" in cat_raw
            else "opening"
        )

        w_raw = row[col_map.get("width", 2)].strip() if col_map.get("width") else ""
        h_raw = row[col_map.get("height", 3)].strip() if col_map.get("height") else ""

        def parse_dim(s):
            m = re.search(r"[\d.]+", s)
            return float(m.group()) if m else None

        width_m = parse_dim(w_raw)
        height_m = parse_dim(h_raw)

        note = row[col_map.get("note", -1)].strip() if col_map.get("note") else ""

        schedule[tag] = ScheduleEntry(
            tag=tag,
            category=category,
            width_m=width_m,
            height_m=height_m,
            note=note,
        )

    return schedule


def rollup_takeoff(
    detections: list[Detection],
    schedule: dict[str, ScheduleEntry],
    drawing_type: str = "floor_plan",
) -> TakeoffResult:
    """Stage 3: join detections to the schedule; count x dims per tag.

    Areas need NO drawing scale: dimensions come from the schedule table.
    Detections whose tag has no schedule entry land in
    ``result.unmatched`` -- reported, never silently dropped.
    """
    by_tag: dict[str, list[Detection]] = {}
    for d in detections:
        by_tag.setdefault(d.tag, []).append(d)

    res = TakeoffResult(
        drawing_type=drawing_type, scale=DrawingScale(None, "not needed: count x schedule dims")
    )
    for tag in sorted(by_tag):
        ds = by_tag[tag]
        entry = schedule.get(tag)
        if entry is None:
            res.unmatched.extend(ds)
            continue
        n = len(ds)
        area = n * entry.width_m * entry.height_m if entry.width_m and entry.height_m else None
        res.lines.append(
            TakeoffLine(
                tag=tag,
                category=entry.category,
                count=n,
                width_m=entry.width_m,
                height_m=entry.height_m,
                area_m2=area,
            )
        )
        if area is not None:
            res.area_m2[entry.category] = res.area_m2.get(entry.category, 0.0) + area
        res.counts[entry.category] = res.counts.get(entry.category, 0) + n
    return res


# ---------------------------------------------------------------------------
# Shared crop / normalize helpers (classifier path)
# ---------------------------------------------------------------------------


def normalize_crop(crop: np.ndarray, size: int = 28, pad_frac: float = 0.15) -> np.ndarray:
    """Crop -> padded square -> (size, size) float64 0..255, dark ink on light.

    Matches the input contract jesse.py expects (thermometer thresholds at
    35/120 assume dark strokes on a light background).
    """
    from PIL import Image

    arr = np.asarray(crop)
    if arr.ndim == 3:
        # luminance; drawings are near-grayscale anyway
        arr = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    arr = arr.astype(np.float64)
    # invert if the crop is dark-on-light already? No: keep as-is; ensure
    # ink is dark: if mean < 128 the background is dark -> invert.
    if arr.mean() < 128:
        arr = 255.0 - arr
    h, w = arr.shape
    side = int(max(h, w) * (1.0 + 2 * pad_frac))
    canvas = np.full((side, side), 255.0)
    y0 = (side - h) // 2
    x0 = (side - w) // 2
    canvas[y0 : y0 + h, x0 : x0 + w] = arr
    img = Image.fromarray(canvas.astype(np.uint8)).resize((size, size), Image.LANCZOS)
    return np.asarray(img, dtype=np.float64)


# ---------------------------------------------------------------------------
# AEC-geometric-bench: CVAT 1.1 XML + PDF, CC BY-NC 4.0  [VALIDATED]
# ---------------------------------------------------------------------------
# 15 real construction sheets. Annotations: 8 object classes as boxes
# (Single/Double Swing Door, Window, Sink, Toilet, Bathtub, Shower, Cooktops)
# plus Wall boxes and Room/Shaft/Balcony/Elevator/Stairs polygons.
# Coordinates are pixels in a 200-dpi rasterization of each PDF page
# (verified: get_pixmap(dpi=200) gives exactly the XML width/height).

AEC_OBJECT_TO_TAKEOFF = {
    "Single Swing Door": "door",
    "Double Swing Door": "door",
    "Window": "window",
    "Sink": "fixture",
    "Toilet": "fixture",
    "Bathtub": "fixture",
    "Shower": "fixture",
    "Cooktops": "fixture",
}
AEC_AREA_LABELS = {"Room", "Shaft", "Balcony", "Elevator", "Stairs"}  # -> floor_area
AEC_WALL_LABELS = {"Wall", "Railing"}  # -> wall


def _rasterize_pdf(pdf_path: Path, dpi: int):
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    pix = doc[0].get_pixmap(dpi=dpi)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)


def load_aec_bench(
    root: str | Path,
    dpi: int = 200,
    size: int = 28,
    scale_m_per_px: float | None = None,
    drawing_type: str = "floor_plan",
) -> tuple[list[SymbolSample], TakeoffResult]:
    """Load AEC-geometric-bench sheets.

    Returns (symbol_samples, takeoff). Takeoff areas are in px^2 unless
    scale_m_per_px is given (title blocks are redacted, so scale is unknown;
    use scale_from_reference on the door regions for an estimate).

    Validated against the real 15-sheet release in ~/workspace/datasets/
    aec-geometric-bench/dataset.
    """
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(
            f"AEC Bench dataset root not found: {root}\n"
            "Hint: Ensure --aec-bench points to the dataset directory containing "
            "annotations_15.xml and a pdf/ subdirectory."
        )
    xml_path = root / "annotations_15.xml"
    if not xml_path.exists():
        raise FileNotFoundError(
            f"AEC Bench annotations file not found: {xml_path}\n"
            "Hint: The dataset directory should contain 'annotations_15.xml' and a 'pdf/' subdirectory."
        )
    tree = etree.parse(str(xml_path), safe_xml_parser())
    samples: list[SymbolSample] = []
    regions: list[Region] = []

    for img_el in tree.getroot().findall(".//image"):
        name = img_el.attrib["name"]  # sheet_01.png
        stem = Path(name).stem
        stem = re.sub(r"[^\w\-.]", "_", stem)  # prevent path traversal via name attr
        W, H = int(img_el.attrib["width"]), int(img_el.attrib["height"])
        pdf = root / "pdf" / f"{stem}.pdf"
        try:
            pdf.resolve().relative_to(root / "pdf")
        except ValueError:
            continue  # drops items that escape the pdf subdirectory
        if not pdf.exists():
            continue
        page = _rasterize_pdf(pdf, dpi)
        # annotation frame is a 200-dpi rasterization; rescale if dpi differs
        sx, sy = page.shape[1] / W, page.shape[0] / H
        src = f"aec-bench:{stem}"

        for box in img_el.findall("box"):
            label = box.attrib["label"]
            xtl, ytl = float(box.attrib["xtl"]) * sx, float(box.attrib["ytl"]) * sy
            xbr, ybr = float(box.attrib["xbr"]) * sx, float(box.attrib["ybr"]) * sy
            poly = box_to_polygon(xtl, ytl, xbr, ybr)
            if label in AEC_OBJECT_TO_TAKEOFF:
                cat = AEC_OBJECT_TO_TAKEOFF[label]
                regions.append(Region(cat, poly, src, drawing_type, label))
                x0, y0, x1, y1 = (
                    max(0, int(xtl)),
                    max(0, int(ytl)),
                    min(page.shape[1], int(math.ceil(xbr))),
                    min(page.shape[0], int(math.ceil(ybr))),
                )
                if x1 > x0 and y1 > y0:
                    samples.append(
                        SymbolSample(
                            normalize_crop(page[y0:y1, x0:x1], size),
                            label,
                            src,
                            (xtl, ytl, xbr, ybr),
                        )
                    )
            elif label in AEC_WALL_LABELS:
                regions.append(Region("wall", poly, src, drawing_type, label))

        for poly_el in img_el.findall("polygon"):
            label = poly_el.attrib["label"]
            pts = [
                (float(x) * sx, float(y) * sy)
                for x, y in (p.split(",") for p in poly_el.attrib["points"].split(";") if p)
            ]
            if label in AEC_AREA_LABELS:
                regions.append(Region("floor_area", pts, src, drawing_type, label))
            elif label in AEC_WALL_LABELS:
                regions.append(Region("wall", pts, src, drawing_type, label))
            # other polygon labels (fixtures drawn as polygons) -> fixture count
            elif label in AEC_OBJECT_TO_TAKEOFF:
                regions.append(Region(AEC_OBJECT_TO_TAKEOFF[label], pts, src, drawing_type, label))

    scale = DrawingScale(
        scale_m_per_px, "explicit" if scale_m_per_px else "unknown (title block redacted)"
    )
    return samples, measure_takeoff(regions, drawing_type, scale)


# ---------------------------------------------------------------------------
# FloorPlanCAD (Fan et al., ICCV 2021)  [PENDING VALIDATION]
# ---------------------------------------------------------------------------
# Documented format: train/val/test splits; original release ships drawings as
# SVG (per the ArchCAD repo README: dataset/FloorplanCAD/{train,val,test}/
# svg_gt/*.svg, converted to JSON via parse_FpCAD_svg.py). Panoptic symbol
# annotations: ~30 "thing" classes (doors, windows, furniture) + "stuff"
# (wall). Our HF parquet export (train-00000-of-00001.parquet, still
# downloading at time of writing) is expected to mirror this with columns
# carrying the SVG/drawing bytes and annotation records.
#
# TODO(validate): inspect the parquet schema once the download completes and
# confirm column names / annotation encoding; then wire the branches below.


def load_floorplancad(
    root: str | Path, size: int = 28, scale_m_per_px: float | None = None
) -> tuple[list[SymbolSample], TakeoffResult]:
    """Load FloorPlanCAD symbols + takeoff regions.

    Tries, in order: (1) HuggingFace parquet export train-*.parquet in root;
    (2) svg_gt/*.svg + *.json annotation layout. Raises a descriptive error
    if neither is found.
    """
    root = Path(root)
    parquets = sorted(root.glob("train-*.parquet")) + sorted(root.glob("*.parquet"))
    if parquets:
        return _floorplancad_from_parquet(parquets[0], size, scale_m_per_px)
    svg_dirs = list(root.glob("**/svg_gt"))
    if svg_dirs:
        return _floorplancad_from_svg(svg_dirs[0].parent, size, scale_m_per_px)
    raise FileNotFoundError(
        f"No FloorPlanCAD data found under {root}: expected train-*.parquet "
        f"or a */svg_gt directory (download still in progress?)"
    )


def _floorplancad_from_parquet(path: Path, size: int, scale_m_per_px: float | None):
    import pyarrow.parquet as pq

    table = pq.read_table(str(path))
    names = table.schema.names
    raise NotImplementedError(
        f"FloorPlanCAD parquet at {path}: columns are {names}. "
        f"Implement _floorplancad_from_parquet to decode this schema. "
        f"See https://github.com/ for dataset details."
    )


def _floorplancad_from_svg(split_dir: Path, size: int, scale_m_per_px: float | None):
    raise NotImplementedError(
        f"FloorPlanCAD SVG+JSON loader not yet implemented. "
        f"SVG root: {split_dir}. "
        f"Implement _floorplancad_from_svg to decode the SVG+JSON annotation format."
    )


# ---------------------------------------------------------------------------
# ArchCAD-400K (Luo et al., arXiv 2503.22346)  [PENDING VALIDATION]
# ---------------------------------------------------------------------------
# First public release: curated 40K-sample subset on HuggingFace
# (jackluoluo/ArchCAD). Paper: 413,062 chunks from 5,538 highly standardized
# drawings with "line-grained" annotations (vector primitives grouped into
# symbols). The repo's DPSS model consumes SVG-line inputs (see svgnets/).
#
# TODO(validate): download/inspect the HF export, confirm the per-sample
# record layout (expected: drawing chunk + symbol list with class + geometry),
# then implement decoding below.


def load_archcad(
    root: str | Path, size: int = 28, scale_m_per_px: float | None = None
) -> tuple[list[SymbolSample], TakeoffResult]:
    """Load ArchCAD-400K samples.

    Auto-detects the local export layout (parquet / image dir + metadata).
    """
    root = Path(root)
    contents = list(root.iterdir())
    if not contents:
        raise FileNotFoundError(
            f"ArchCAD-400K dataset at {root} is empty: "
            f"download from https://huggingface.co/jackluoluo/ArchCAD "
            f"and extract to that directory."
        )
    # TODO(validate): inspect the HF export layout and implement _archcad_from_parquet
    # or _archcad_from_dir once the format is confirmed.
    raise NotImplementedError(
        "ArchCAD-400K loader not yet implemented. "
        "Dataset root ("
        + str(root)
        + ") contains: "
        + ", ".join(sorted(p.name for p in contents)[:10])
        + ". "
        "Implement the loader after inspecting the export format."
    )
