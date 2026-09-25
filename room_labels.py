"""Room name/number labeling for floor-plan space polygons.

Pipeline: OCR text detection on the sheet image -> parse each text line into
(room name, room number) -> associate each label with its enclosing room
polygon via text-centroid point-in-polygon, with a nearest-polygon fallback
for labels sitting in doorways/hallways.

Nothing is ever dropped: unmatched labels and unlabeled polygons are
reported on the output contract.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------


@dataclass
class TextBox:
    text: str  # raw OCR string
    bbox: tuple  # (xtl, ytl, xbr, ybr) in source px
    confidence: float  # OCR confidence 0..1


@dataclass
class RoomLabel:
    name: str  # e.g. "OPEN OFFICE" ("" if number-only)
    number: str  # e.g. "201"       ("" if name-only)
    raw_text: str  # OCR string before parsing
    bbox: tuple
    centroid: tuple  # (cx, cy)
    ocr_confidence: float
    parse_confidence: float = 1.0  # lowered when regexes fall back


@dataclass
class LabeledSpace:
    polygon_px: list  # [(x, y), ...] room boundary
    source: str = ""
    name: str = ""  # assigned room name
    number: str = ""  # assigned room number
    label_confidence: float = 0.0  # 0.0 -> unlabeled
    label_source: str = ""  # "enclosed" | "nearest" | ""
    label_bbox: tuple | None = None


@dataclass
class LabeledTakeoff:
    spaces: list  # LabeledSpace, one per input polygon
    labels: list  # RoomLabel, every OCR label found
    unmatched_labels: list  # RoomLabel with no polygon assigned
    n_labeled: int = 0
    n_total: int = 0

    @property
    def unlabeled_spaces(self):
        return [s for s in self.spaces if not s.name and not s.number]


# ---------------------------------------------------------------------------
# OCR backend (local only, no cloud APIs)
# ---------------------------------------------------------------------------

_ENGINE = None
_ENGINE_NAME = None


def get_ocr_engine():
    """Lazily construct the local OCR engine.

    Uses rapidocr_onnxruntime (PP-OCRv4 detection + recognition, ONNX,
    runs fully on CPU, no network at inference time). Raises a clear
    error if it is not installed.
    """
    global _ENGINE, _ENGINE_NAME
    if _ENGINE is not None:
        return _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        raise RuntimeError(
            "rapidocr_onnxruntime is not installed; install it with "
            "`pip install rapidocr_onnxruntime` (local ONNX OCR, no cloud API)"
        ) from e
    _ENGINE = RapidOCR()
    _ENGINE_NAME = "rapidocr_onnxruntime (PP-OCRv4 det+rec, ONNX CPU)"
    return _ENGINE


def ocr_engine_name() -> str:
    get_ocr_engine()
    return _ENGINE_NAME


def detect_text(image: np.ndarray, min_conf: float = 0.30) -> list[TextBox]:
    """Run text detection+recognition on a sheet image.

    image: (H, W) grayscale or (H, W, 3) RGB, dark ink on light background.
    Returns TextBox list sorted top-to-bottom, left-to-right.
    """
    engine = get_ocr_engine()
    if image.ndim == 2:
        img = np.stack([image] * 3, axis=-1)
    else:
        img = image
    img = np.ascontiguousarray(img.astype(np.uint8))
    result, _ = engine(img)
    boxes: list[TextBox] = []
    if not result:
        return boxes
    for quad, text, score in result:
        q = np.asarray(quad, dtype=np.float64)
        xtl, ytl = float(q[:, 0].min()), float(q[:, 1].min())
        xbr, ybr = float(q[:, 0].max()), float(q[:, 1].max())
        text = (text or "").strip()
        if not text or float(score) < min_conf:
            continue
        boxes.append(TextBox(text=text, bbox=(xtl, ytl, xbr, ybr), confidence=float(score)))
    boxes.sort(key=lambda b: (b.bbox[1] // 20, b.bbox[0]))
    return boxes


# ---------------------------------------------------------------------------
# Dimension-string rejection: real sheets are full of dimension text
# ("17'-6\"", "5 X 5", "1/2\"", "@ 16 O.C.") that must never become
# room labels. Steel tags ("W12X72") are left to the polygon-association
# step (they rarely sit inside room polygons).
# ---------------------------------------------------------------------------

_DIMENSION_RES = [
    re.compile(r"\d\s*['\u2032]"),  # 17'  (feet)
    re.compile(r"\d\s*[\"\u2033]"),  # 10"  (inches)
    re.compile(r"\d\s*/\s*\d"),  # 1/2  (fractions)
    re.compile(r"\d\s*[xX\u00d7]\s*\d"),  # 5X5 / W12X72 (sizes, steel tags)
    re.compile(r"\b[O0]\.?C\.?\b"),  # 19 O.C. (also OCR-mangled 0.C.)
    re.compile(r"@"),  # @ 16
    re.compile(r"[°\u00b0]"),  # angles
    re.compile(r"\b\d+\s*(?:SF|SQ\.?\s*FT)\b", re.IGNORECASE),  # area tags
]


def looks_like_dimension(text: str) -> bool:
    t = _normalize(text)
    return any(r.search(t) for r in _DIMENSION_RES)


# ---------------------------------------------------------------------------
# Label parsing: "OPEN OFFICE 201" -> ("OPEN OFFICE", "201")
# ---------------------------------------------------------------------------

_NUM = r"[A-Z]?\d{1,4}[A-Z]?(?:[-/][A-Z]?\d{1,3}[A-Z]?)?"  # 201, 201A, 201-A, C1, B-102
_NAME = r"[A-Z][A-Z0-9 .&'/\-]*"

_PATTERNS = [
    # "RM 201" / "ROOM 201A"
    (re.compile(rf"^(?:RM|ROOM)\.?\s*({_NUM})$"), lambda m: ("", m.group(1), 1.0)),
    # number only: "201", "201A"
    (re.compile(rf"^({_NUM})$"), lambda m: ("", m.group(1), 1.0)),
    # name + number: "OPEN OFFICE 201", "CONF-202"
    (
        re.compile(rf"^({_NAME}?)\s*[.\-]?\s*({_NUM})$"),
        lambda m: (m.group(1).strip(" .-"), m.group(2), 0.95),
    ),
    # number + name: "201 OPEN OFFICE"
    (re.compile(rf"^({_NUM})\s+({_NAME})$"), lambda m: (m.group(2), m.group(1), 0.95)),
    # name only: "LOBBY", "OPEN OFFICE"
    (re.compile(rf"^({_NAME})$"), lambda m: (m.group(1), "", 0.9)),
]


def _normalize(text: str) -> str:
    t = text.upper()
    # drawing-OCR heuristic: a letter O touching digits is almost always a
    # zero ("2O5A" -> "205A"); plain words like ROOM are untouched.
    t = re.sub(r"(?<=\d)O|O(?=\d)", "0", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def parse_room_label(text: str) -> tuple[str, str, float]:
    """Parse OCR text into (name, number, parse_confidence).

    Never fails: unrecognized text comes back as (text, "", 0.5).
    """
    t = _normalize(text)
    if not t:
        return "", "", 0.0
    for pat, build in _PATTERNS:
        m = pat.match(t)
        if m:
            name, number, conf = build(m)
            return name.strip(), number.strip(), conf
    return t, "", 0.5  # fallback: treat whole string as a name


def to_room_label(tb: TextBox) -> RoomLabel:
    name, number, pconf = parse_room_label(tb.text)
    xtl, ytl, xbr, ybr = tb.bbox
    return RoomLabel(
        name=name,
        number=number,
        raw_text=tb.text,
        bbox=tb.bbox,
        centroid=((xtl + xbr) / 2, (ytl + ybr) / 2),
        ocr_confidence=tb.confidence,
        parse_confidence=pconf,
    )


# ---------------------------------------------------------------------------
# Geometry: point-in-polygon + nearest-polygon association
# ---------------------------------------------------------------------------


def point_in_polygon(pt, poly) -> bool:
    """Ray-casting point-in-polygon. poly: [(x, y), ...]."""
    x, y = pt
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xinters = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < xinters:
                inside = not inside
        j = i
    return inside


def point_to_polygon_dist(pt, poly) -> float:
    """Minimum distance from point to polygon edges."""
    x, y = pt
    best = float("inf")
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy
        if L2 == 0:
            d = math.hypot(x - x1, y - y1)
        else:
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
            d = math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
        best = min(best, d)
    return best


def polygon_area_px2(poly) -> float:
    p = np.asarray(poly, dtype=np.float64)
    if len(p) < 3:
        return 0.0
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


def assign_labels(
    spaces: list[LabeledSpace], labels: list[RoomLabel], max_nearest_px: float = 150.0
) -> LabeledTakeoff:
    """Associate each label with a room polygon.

    1. Text-centroid strictly inside a polygon -> "enclosed" (pick smallest
       area on nesting ambiguity), confidence = ocr * parse.
    2. Otherwise nearest polygon edge within max_nearest_px -> "nearest",
       confidence scaled by 0.6 and distance falloff (doorway/hallway labels).
    3. Anything else -> unmatched (reported, never dropped).
    """
    out = LabeledTakeoff(
        spaces=list(spaces), labels=list(labels), unmatched_labels=[], n_total=len(spaces)
    )
    claimed = set()  # space idx already given an enclosed label
    for lab in labels:
        # 1. enclosed candidates
        cands = [
            i for i, s in enumerate(out.spaces) if point_in_polygon(lab.centroid, s.polygon_px)
        ]
        if cands:
            # smallest area wins on nesting; prefer unclaimed
            free = [i for i in cands if i not in claimed] or cands
            i = min(free, key=lambda k: polygon_area_px2(out.spaces[k].polygon_px))
            s = out.spaces[i]
            conf = lab.ocr_confidence * lab.parse_confidence
            if conf > s.label_confidence:  # keep the best label per space
                s.name, s.number = lab.name, lab.number
                s.label_confidence = conf
                s.label_source = "enclosed"
                s.label_bbox = lab.bbox
            claimed.add(i)
            continue
        # 2. nearest fallback
        dists = [
            (point_to_polygon_dist(lab.centroid, s.polygon_px), i) for i, s in enumerate(out.spaces)
        ]
        d, i = min(dists, key=lambda t: t[0]) if dists else (float("inf"), -1)
        if i >= 0 and d <= max_nearest_px:
            s = out.spaces[i]
            conf = (
                lab.ocr_confidence * lab.parse_confidence * 0.6 * max(0.0, 1.0 - d / max_nearest_px)
            )
            if conf > s.label_confidence:
                s.name, s.number = lab.name, lab.number
                s.label_confidence = conf
                s.label_source = "nearest"
                s.label_bbox = lab.bbox
        else:
            out.unmatched_labels.append(lab)
    out.n_labeled = sum(1 for s in out.spaces if s.name or s.number)
    return out


def label_spaces_from_sheet(
    sheet_image: np.ndarray,
    spaces: list[LabeledSpace] | None,
    min_conf: float = 0.30,
    max_nearest_px: float = 150.0,
    pre_detected: list[TextBox] | None = None,
) -> LabeledTakeoff:
    """Full pipeline: OCR a sheet image and label the given room polygons."""
    boxes = pre_detected if pre_detected is not None else detect_text(sheet_image, min_conf)
    boxes = [b for b in boxes if not looks_like_dimension(b.text)]
    labels = [to_room_label(b) for b in boxes]
    # keep only labels that look like room labels (have a number or a name)
    labels = [l for l in labels if l.name or l.number]
    return assign_labels(spaces or [], labels, max_nearest_px)


def attach_room_labels(
    takeoff_result,
    sheet_image: np.ndarray | None = None,
    min_conf: float = 0.30,
    max_nearest_px: float = 150.0,
    sheet_id: str | None = None,
    revision: str | None = None,
) -> LabeledTakeoff:
    """Label the 'room'-category regions of an existing TakeoffResult in place.

    Extends takeoff_result with `.spaces` (LabeledSpace list),
    `.unmatched_labels`, and returns the LabeledTakeoff.
    """
    if sheet_image is None:
        spaces = [
            LabeledSpace(polygon_px=r.polygon_px, source=r.source)
            for r in takeoff_result.regions
            if getattr(r, "category", "") == "room"
        ]
        labeled = LabeledTakeoff(
            spaces=spaces, labels=[], unmatched_labels=[], n_labeled=0, n_total=0
        )
    else:
        spaces = [
            LabeledSpace(polygon_px=r.polygon_px, source=r.source)
            for r in takeoff_result.regions
            if getattr(r, "category", "") == "room"
        ]
        labeled = label_spaces_from_sheet(sheet_image, spaces, min_conf, max_nearest_px)
    takeoff_result.spaces = labeled.spaces
    takeoff_result.unmatched_labels = labeled.unmatched_labels
    return labeled


# ---------------------------------------------------------------------------
# Synthetic labeled floor plan for validation (no GT room labels exist in
# the open corpora: AEC-geometric-bench redacts annotations).
# ---------------------------------------------------------------------------


def synthesize_test_plan(seed: int = 7):
    """Draw a labeled floor plan with PIL.

    Returns (image, spaces, expected) where expected maps space index ->
    (name, number). Includes edge cases: doorway-straddling label, tiny
    text, label outside all rooms, one unlabeled room.
    """
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1600, 1200
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
        small = font

    # (x, y, w, h, name, number, label_pos)
    rooms = [
        (60, 60, 420, 300, "OPEN OFFICE", "201", "center"),
        (500, 60, 300, 300, "CONF", "202", "center"),
        (820, 60, 380, 300, "LOBBY", "100", "center"),
        (60, 380, 340, 320, "RESTROOM", "205A", "center"),
        (420, 380, 380, 320, "", "206", "center"),  # number-only
        (820, 380, 380, 320, "ELEC", "", "center"),  # name-only
        (60, 720, 500, 380, "KITCHEN", "207", "center"),
        (580, 720, 300, 380, "STORAGE", "208", "tiny"),  # small text
        (900, 720, 300, 380, "CORRIDOR", "C1", "edge"),  # near shared wall
        (1220, 720, 320, 380, "MECH", "209", "none"),  # unlabeled room
    ]
    wall = 10
    spaces, expected = [], {}
    for i, (x, y, w, h, name, number, pos) in enumerate(rooms):
        d.rectangle([x, y, x + w, y + h], outline=0, width=wall, fill=255)
        poly = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        spaces.append(LabeledSpace(polygon_px=poly, source=f"synth:room{i}"))
        if pos == "none":
            continue
        if name or number:
            expected[i] = (name, number)
        label = f"{name} {number}".strip()
        f = small if pos == "tiny" else font
        bb = d.textbbox((0, 0), label, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        if pos == "edge":
            # just inside room 8, hugging the shared wall with room 9
            bb = d.textbbox((0, 0), label, font=f)
            tw = bb[2] - bb[0]
            cx = 1200 - tw / 2 - 15
            cy = y + h / 2
            d.text((cx - tw / 2, cy - th / 2), label, fill=0, font=f)
        elif pos == "doorway":
            # door gap in the shared wall between room 8 (900..1200) and
            # room 9 (1200..1520): erase wall, center label in the opening
            d.rectangle([1150, 860, 1250, 960], fill=255)
            cx, cy = 1200, 910
            d.text((cx - tw / 2, cy - th / 2), label, fill=0, font=f)
        else:
            cx, cy = x + w / 2, y + h / 2
            d.text((cx - tw / 2, cy - th / 2), label, fill=0, font=f)

    # one stray label far outside any room -> must be unmatched
    d.text((1300, 60), "NOTE 1", fill=0, font=font)

    arr = np.asarray(img).astype(np.float64)
    return arr, spaces, expected
