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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from detector.classes import CLASS_NAMES

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


def detections_from_yolo_json(
    yolo_json_path: str | Path,
    source: str,
) -> list[Detection]:
    """Convert sahi_infer.py JSON output to Detection list.

    yolo_json_path: path to the JSON written by sahi_infer.py main().
    source: sheet identifier, e.g. "aec-bench:sheet_01" — stored as Detection.source.
    """
    data = json.loads(Path(yolo_json_path).read_text())
    dets = []
    for p in data["preds"]:
        label = CLASS_NAMES[p["cls"]]
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

    TODO: document-layout stage -- (a) locate the schedule table region
    (Jesse's document-layout analysis claims 100% on clean vector sheets and
    is the natural fit), (b) cell segmentation, (c) OCR cell text, (d)
    normalize into ScheduleEntry records via parse_schedule_csv's format.
    Raises until implemented; v1 callers pass CSV directly.
    """
    raise NotImplementedError(
        "Schedule table detection/parsing not yet implemented. "
        "Provide the schedule as CSV to parse_schedule_csv()."
    )


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
    xml_path = root / "annotations_15.xml"
    tree = ET.parse(str(xml_path))
    samples: list[SymbolSample] = []
    regions: list[Region] = []

    for img_el in tree.getroot().findall(".//image"):
        name = img_el.attrib["name"]  # sheet_01.png
        stem = Path(name).stem
        W, H = int(img_el.attrib["width"]), int(img_el.attrib["height"])
        pdf = root / "pdf" / f"{stem}.pdf"
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
    # TODO(validate): confirm these column names against the real export.
    # Expected: an image/svg payload column and an annotations column with
    # per-symbol {label, bbox|polygon} records.
    raise NotImplementedError(
        f"FloorPlanCAD parquet columns are {names}; annotation decoding not "
        f"yet validated against the completed download. See TODO above."
    )


def _floorplancad_from_svg(split_dir: Path, size: int, scale_m_per_px: float | None):
    # TODO(validate): confirm against the original FloorPlanCAD release layout
    # (svg_gt/*.svg + JSON annotations as produced by parse_FpCAD_svg.py).
    raise NotImplementedError(
        "FloorPlanCAD SVG+JSON branch not yet validated (no SVG data on disk)."
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
    if not any(root.iterdir()):
        raise FileNotFoundError(f"{root} is empty: ArchCAD-400K HF export not yet downloaded.")
    # TODO(validate): implement once the HF export layout is known.
    raise NotImplementedError(
        "ArchCAD-400K loader pending: HF export layout not yet inspected. "
        "Contents: " + ", ".join(sorted(p.name for p in root.iterdir())[:10])
    )
