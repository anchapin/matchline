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

from building_model import Provenance
from datasets_adapter import polygon_area_px2

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
    core_provenance: Provenance | None = None


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
# Line grouping: multi-line room labels ("OPEN" / "OFFICE" / "201")
#
# Drawings print a room label as several stacked text lines. OCR detects each
# line as its own box, so the label has to be regrouped geometrically before
# parsing/assignment. The pass is purely geometric (no OCR changes, no room
# polygons involved): a line joins the group of the line above it when
#
#   * the vertical gap between the two boxes is at most one line height
#     (GROUP_MAX_GAP_FACTOR * max(line heights)) — lines of one label sit
#     right under each other, lines of different labels do not; and
#   * the boxes are horizontally aligned/overlapping in x (their x-ranges
#     overlap, or their centers are within half the narrower line width) —
#     side-by-side text on the same line is never grouped.
#
# The merged box carries the union bbox of its members and the *minimum*
# member OCR confidence (a merged label is only as trustworthy as its least
# certain line).
# ---------------------------------------------------------------------------

# Vertical gap allowed between two stacked lines, as a multiple of the
# line height (1.0 == one line height).
GROUP_MAX_GAP_FACTOR = 1.25
# Vertical overlap tolerated between two stacked lines, as a multiple of the
# line height (0.5 == half a line may overlap).
GROUP_MAX_OVERLAP_FACTOR = 0.5
# Required x-overlap between two stacked lines, in px.
GROUP_MIN_X_OVERLAP = 0.0


def _line_height(box: TextBox) -> float:
    return max(1.0, box.bbox[3] - box.bbox[1])


def _line_width(box: TextBox) -> float:
    return max(1.0, box.bbox[2] - box.bbox[0])


def _is_stacked_line(above: TextBox, below: TextBox) -> bool:
    """True when `below` reads as the next line of the same label as `above`."""
    h = max(_line_height(above), _line_height(below))
    gap = below.bbox[1] - above.bbox[3]  # >0: whitespace between the lines
    if gap > GROUP_MAX_GAP_FACTOR * h:
        return False
    if gap < -GROUP_MAX_OVERLAP_FACTOR * h:
        return False
    overlap = min(above.bbox[2], below.bbox[2]) - max(above.bbox[0], below.bbox[0])
    if overlap >= GROUP_MIN_X_OVERLAP:
        return True
    above_cx = (above.bbox[0] + above.bbox[2]) / 2.0
    below_cx = (below.bbox[0] + below.bbox[2]) / 2.0
    return abs(above_cx - below_cx) <= 0.5 * min(_line_width(above), _line_width(below))


def _group_stacked_labels(
    boxes: list[TextBox], polygons: list | None = None
) -> list[list[TextBox]]:
    """Group vertically stacked text boxes into multi-line label groups.

    Purely geometric grouping (see the section comment above). Returns a list
    of groups; every group holds its member boxes ordered top-to-bottom.
    Groups are returned in the order of their first (topmost) member so the
    caller keeps a stable, top-to-bottom label order.

    When `polygons` (room polygons) are supplied, a candidate line is only
    merged into a group when it does not sit strictly inside a *different*
    polygon than the group: lines of one label share a room, lines of two
    different rooms stay apart. A line that is inside no polygon (doorway,
    hallway) never blocks a merge.
    """

    def enclosed_index(box: TextBox) -> int | None:
        if not polygons:
            return None
        cx = (box.bbox[0] + box.bbox[2]) / 2.0
        cy = (box.bbox[1] + box.bbox[3]) / 2.0
        cands = [i for i, poly in enumerate(polygons) if point_in_polygon((cx, cy), poly)]
        if not cands:
            return None
        return min(cands, key=lambda k: polygon_area_px2(polygons[k]))

    groups: list[list[TextBox]] = []
    indexed = list(enumerate(boxes))
    # top-to-bottom sweep; ties broken by x so equal lines stay left-to-right
    for _idx, box in sorted(indexed, key=lambda t: (t[1].bbox[1], t[1].bbox[0])):
        best_group = None
        best_gap = None
        for g in groups:
            prev = g[-1]
            if not _is_stacked_line(prev, box):
                continue
            if polygons:
                idx = enclosed_index(box)
                if idx is not None:
                    others = {enclosed_index(m) for m in g} - {None}
                    if others and idx not in others:
                        # the line belongs to a different room -> not the
                        # same multi-line label
                        continue
            gap = box.bbox[1] - prev.bbox[3]
            if best_gap is None or gap < best_gap:
                best_group, best_gap = g, gap
        if best_group is None:
            groups.append([box])
        else:
            best_group.append(box)
    # keep the original (top-to-bottom) group order
    order = {id(b): i for i, b in enumerate(boxes)}
    groups.sort(key=lambda g: min(order[id(b)] for b in g))
    return groups


def _parse_group_lines(lines: list[str]) -> tuple[str, str, float]:
    """Parse the lines of a multi-line label into (name, number, confidence).

    Primary rule: the lines joined top-to-bottom with spaces are parsed as one
    string, so the canonical stack "OPEN" / "OFFICE" / "201" yields
    ("OPEN OFFICE", "201").

    Fallback rule: when the joined text does not yield a number but one of the
    lines does (e.g. "OPEN" / "201" / "OFFICE"), the name parts are taken from
    the lines that carry no number and joined top-to-bottom, and the number
    from the line that carries it.

    Parse confidence: the confidence of the parse that was used; for the
    fallback rule the minimum confidence over the contributing lines.
    """
    joined = " ".join(lines)
    name, number, conf = parse_room_label(joined)
    if number:
        return name, number, conf
    # per-line fallback: one line carries the number, the others the name
    names: list[str] = []
    line_number = ""
    confs: list[float] = []
    for raw in lines:
        n, num, c = parse_room_label(raw)
        confs.append(c)
        if num and not line_number:
            line_number = num
        elif n:
            names.append(n)
    if line_number and names:
        return " ".join(names).strip(), line_number, min(confs) if confs else 1.0
    return name, number, conf


def _room_label_from_group(group: list[TextBox]) -> RoomLabel:
    """Build one RoomLabel from a group of stacked text lines.

    Text: the lines joined top-to-bottom with single spaces.
    Bbox: the union of the member boxes.
    OCR confidence: the *minimum* member confidence (stated rule: a merged
    label is only as trustworthy as its least certain line).
    Parse confidence: the minimum member parse confidence.
    """
    ordered = sorted(group, key=lambda b: (b.bbox[1], b.bbox[0]))
    if len(ordered) == 1:
        # single-line labels behave exactly as before
        return to_room_label(ordered[0])
    lines = [b.text.strip() for b in ordered if b.text.strip()]
    text = " ".join(lines)
    xtl = min(b.bbox[0] for b in ordered)
    ytl = min(b.bbox[1] for b in ordered)
    xbr = max(b.bbox[2] for b in ordered)
    ybr = max(b.bbox[3] for b in ordered)
    name, number, pconf = _parse_group_lines(lines)
    return RoomLabel(
        name=name,
        number=number,
        raw_text=text,
        bbox=(xtl, ytl, xbr, ybr),
        centroid=((xtl + xbr) / 2, (ytl + ybr) / 2),
        ocr_confidence=min(b.confidence for b in ordered),
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
    # line-grouping pass (before assignment): vertically stacked lines of one
    # label ("OPEN" / "OFFICE" / "201") become a single RoomLabel.
    polygons = [s.polygon_px for s in (spaces or []) if s.polygon_px]
    groups = _group_stacked_labels(boxes, polygons)
    labels = [_room_label_from_group(g) for g in groups]
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
