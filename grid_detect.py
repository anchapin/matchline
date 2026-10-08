"""Column grids and grid bubbles on vector plan and elevation sheets (#742, first slice).

Input: a sheet from ``pdf_ingest`` (``Sheet`` or its JSON dict). Output: a
:class:`GridSet` in sheet points, one :class:`GridLine` per grid label, each
with the bubbles that name it and provenance.

A grid line is

* a long axis-aligned stroked run: a dashed path, a chain of short collinear
  pieces (dash-dot drawn as separate strokes), or a solid line;
* with a bubble at or past one of its ends (a line running on through a
  circle is a room tag sitting on the grid, not its bubble): a near-round closed path (curves
  or a many-sided polygon) ``BUBBLE_D_MIN_PT``..``BUBBLE_D_MAX_PT`` across,
  centred on the line's axis;
* whose bubble holds a grid label (``A``, ``AA``, ``A.1``, ``1``, ``12``,
  ``2.5``) in the text layer.

Solid lines register at lower confidence than dashed ones. Nothing is dropped
silently: a labelled bubble with no line, and one label drawn at two different
positions, go to ``review``. Raster sheets (OCR on bubble crops) are a
follow-up; this slice reads the vector text layer only.

:func:`plan_grid_m` and :func:`elevation_bubbles` turn grid sets into the
inputs :func:`registration.register_elevation_grid` takes.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

SCHEMA = "matchline.grid_detect/1"

ANGLE_TOL = 0.02  # rad; grid lines are drawn axis-aligned on the sheet
COLLINEAR_TOL_PT = 0.75  # pieces of one run share a coordinate to within this
RUN_GAP_PT = 14.0  # dash-dot gaps; wider breaks start a new run
BUBBLE_D_MIN_PT = 10.0
BUBBLE_D_MAX_PT = 60.0
BUBBLE_ROUND = 0.15  # |w/h - 1| for a bubble's bbox
AXIS_TOL_R = 0.3  # bubble centre off the line axis, in radii
END_REACH_R = 2.5  # line end to bubble perimeter, in radii
INSIDE_END_R = 1.2  # a line may run into its bubble, but must stop by about its far side
MIN_LEN_D = 1.5  # shortest grid line, in bubble diameters (elevation stubs are short)
SAME_LINE_PT = 2.0  # two bubbles with one label name one line if this close
CONF_DASHED = 0.9
CONF_SOLID = 0.75

GRID_LABEL_RE = re.compile(r"^(?:[A-Z]{1,2}(?:\.\d{1,2})?|\d{1,3}(?:\.\d{1,2})?)$")

Pt = Tuple[float, float]


def _get(o, k):
    return o[k] if isinstance(o, dict) else getattr(o, k)


@dataclass
class Bubble:
    center: Pt
    r: float
    label: Optional[str] = None
    src: int = -1  # primitive index


@dataclass
class GridLine:
    label: str
    orient: str  # "v" (constant x) | "h" (constant y)
    coord_pt: float  # x for "v", y for "h"
    span_pt: Tuple[float, float]  # extent along the line
    dashed: bool
    bubbles: List[Tuple[float, float, float]]  # (cx, cy, r)
    confidence: float
    provenance: Dict[str, object] = field(default_factory=dict)


@dataclass
class GridSet:
    sheet_id: str
    lines: List[GridLine] = field(default_factory=list)
    review: List[dict] = field(default_factory=list)

    def by_label(self, orient: Optional[str] = None) -> Dict[str, GridLine]:
        return {g.label: g for g in self.lines if orient is None or g.orient == orient}

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, **asdict(self)}


@dataclass
class _Run:
    orient: str
    coord: float
    a: float
    b: float
    pieces: int
    dashed: bool
    src: List[int]


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", "", s).upper()


def _bubbles(sheet) -> List[Bubble]:
    out: List[Bubble] = []
    for i, p in enumerate(_get(sheet, "primitives")):
        if not _get(p, "stroked"):
            continue
        segs = _get(p, "segments")
        n_curves = sum(1 for k, _ in segs if k == "C")
        n_lines = sum(1 for k, _ in segs if k == "L")
        if n_curves < 4 and n_lines < 12:  # a circle: 4+ beziers or a fine polygon
            continue
        x0, y0, x1, y1 = _get(p, "bbox")
        w, h = x1 - x0, y1 - y0
        if min(w, h) <= 0 or abs(w / h - 1) > BUBBLE_ROUND:
            continue
        d = (w + h) / 2
        if not BUBBLE_D_MIN_PT <= d <= BUBBLE_D_MAX_PT:
            continue
        pts = [q for _, ps in segs for q in ps]
        first, last = pts[0], pts[-1]
        closed = any(k == "Z" for k, _ in segs) or math.dist(first, last) < 0.1 * d
        if not closed:  # a door swing arc is round-ish but open
            continue
        out.append(Bubble(((x0 + x1) / 2, (y0 + y1) / 2), d / 2, src=i))
    # concentric duplicates (double-stroked bubbles): keep the outer one
    out.sort(key=lambda b: -b.r)
    kept: List[Bubble] = []
    for b in out:
        if not any(math.dist(b.center, k.center) < 0.3 * k.r for k in kept):
            kept.append(b)
    return kept


def _label_bubbles(sheet, bubbles: List[Bubble]) -> None:
    for t in _get(sheet, "text"):
        x0, y0, x1, y1 = _get(t, "bbox")
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        s = _norm_label(_get(t, "text"))
        if not GRID_LABEL_RE.match(s):
            continue
        best = None
        for b in bubbles:
            dd = math.dist(c, b.center)
            if dd <= 0.8 * b.r and (best is None or dd < best[0]):
                best = (dd, b)
        if best is not None:
            b = best[1]
            b.label = s if b.label is None else b.label + s  # "A" + ".1" split spans
    for b in bubbles:
        if b.label is not None and not GRID_LABEL_RE.match(b.label):
            b.label = None


def _runs(sheet, skip: set) -> List[_Run]:
    pieces = []  # (orient, coord, a, b, dashed, src)
    for i, p in enumerate(_get(sheet, "primitives")):
        if i in skip or not _get(p, "stroked"):
            continue
        dashed = bool(_get(p, "dashed"))
        cur = None
        for kind, pts in _get(p, "segments"):
            if kind == "M":
                cur = tuple(pts[0])
            elif kind == "L" and cur is not None:
                nxt = tuple(pts[0])
                dx, dy = nxt[0] - cur[0], nxt[1] - cur[1]
                ln = math.hypot(dx, dy)
                if ln > 0:
                    if abs(dx) <= ANGLE_TOL * ln:
                        x = (cur[0] + nxt[0]) / 2
                        pieces.append(("v", x, min(cur[1], nxt[1]), max(cur[1], nxt[1]), dashed, i))
                    elif abs(dy) <= ANGLE_TOL * ln:
                        y = (cur[1] + nxt[1]) / 2
                        pieces.append(("h", y, min(cur[0], nxt[0]), max(cur[0], nxt[0]), dashed, i))
                cur = nxt
            elif kind == "C" and cur is not None:
                cur = tuple(pts[-1])
    pieces.sort(key=lambda q: (q[0], round(q[1] / COLLINEAR_TOL_PT), q[2]))
    runs: List[_Run] = []
    for o, c, a, b, dsh, src in pieces:
        r = runs[-1] if runs else None
        if (
            r is not None
            and r.orient == o
            and abs(r.coord - c) <= COLLINEAR_TOL_PT
            and a - r.b <= RUN_GAP_PT
        ):
            r.b = max(r.b, b)
            r.pieces += 1
            r.dashed = r.dashed or dsh
            r.src.append(src)
        else:
            runs.append(_Run(o, c, a, b, 1, dsh, [src]))
    for r in runs:  # dash-dot drawn as separate strokes reads as dashed
        if r.pieces >= 3:
            r.dashed = True
    return runs


def _attach(b: Bubble, runs: List[_Run]) -> Optional[_Run]:
    best = None
    for r in runs:
        cross, along = (b.center[0], b.center[1]) if r.orient == "v" else (b.center[1], b.center[0])
        if abs(cross - r.coord) > AXIS_TOL_R * b.r:
            continue
        if r.b - r.a < MIN_LEN_D * 2 * b.r:
            continue
        gap = max(r.a - along, along - r.b, 0.0)  # 0 when the line runs into the bubble
        if gap - b.r > END_REACH_R * b.r:
            continue
        if gap == 0.0 and min(along - r.a, r.b - along) > INSIDE_END_R * b.r:
            continue  # the line runs on through: a tag sitting on a grid, not its bubble
        key = (r.dashed, r.b - r.a)
        if best is None or key > best[0]:
            best = (key, r)
    return None if best is None else best[1]


def detect_grids(sheet, sheet_id: str = "") -> GridSet:
    """Grid lines with their bubble labels on one vector sheet."""
    gs = GridSet(sheet_id=sheet_id)
    bubbles = _bubbles(sheet)
    _label_bubbles(sheet, bubbles)
    labelled = [b for b in bubbles if b.label]
    runs = _runs(sheet, {b.src for b in bubbles})
    found: Dict[Tuple[str, str], List[Tuple[Bubble, _Run]]] = {}
    for b in labelled:
        r = _attach(b, runs)
        if r is None:
            gs.review.append(
                {
                    "kind": "grid_bubble_without_line",
                    "label": b.label,
                    "center_pt": [round(v, 2) for v in b.center],
                    "reason": "labelled bubble with no axis-aligned line running to it",
                }
            )
            continue
        found.setdefault((b.label, r.orient), []).append((b, r))
    for (lab, orient), hits in sorted(found.items()):
        coords = [r.coord for _, r in hits]
        if max(coords) - min(coords) > SAME_LINE_PT:
            gs.review.append(
                {
                    "kind": "grid_label_conflict",
                    "label": lab,
                    "coords_pt": sorted(round(c, 2) for c in coords),
                    "reason": "one grid label names lines at different positions",
                }
            )
            continue
        rs = [r for _, r in hits]
        dashed = any(r.dashed for r in rs)
        conf = CONF_DASHED if dashed else CONF_SOLID
        if len(hits) > 1:
            conf = min(0.95, conf + 0.05)  # bubbles at both ends agree
        gs.lines.append(
            GridLine(
                label=lab,
                orient=orient,
                coord_pt=round(sum(coords) / len(coords), 3),
                span_pt=(round(min(r.a for r in rs), 3), round(max(r.b for r in rs), 3)),
                dashed=dashed,
                bubbles=[
                    (round(b.center[0], 3), round(b.center[1], 3), round(b.r, 3)) for b, _ in hits
                ],
                confidence=conf,
                provenance={
                    "sheet_id": sheet_id,
                    "method": "vector_grid_bubble",
                    "primitives": sorted({i for r in rs for i in r.src} | {b.src for b, _ in hits}),
                },
            )
        )
    # a label on both axes is a drawing error, not two grids
    by_lab: Dict[str, List[GridLine]] = {}
    for g in gs.lines:
        by_lab.setdefault(g.label, []).append(g)
    for lab, gl in by_lab.items():
        if len(gl) > 1:
            gs.lines = [g for g in gs.lines if g.label != lab]
            gs.review.append(
                {
                    "kind": "grid_label_conflict",
                    "label": lab,
                    "reason": "one grid label names both a vertical and a horizontal line",
                }
            )
    gs.lines.sort(key=lambda g: (g.orient, g.coord_pt))
    return gs


def plan_grid_m(
    gs: GridSet, orient: str, m_per_pt: float, origin_pt: float = 0.0, flip: bool = False
) -> Dict[str, float]:
    """``{label: metres}`` along the facade axis for one family of plan grids.

    ``orient="v"`` gives x positions of vertical lines (a south or north
    facade); ``"h"`` gives y positions (east or west). ``flip`` for sheet y
    pointing down when the model's y points up."""
    s = -1.0 if flip else 1.0
    return {
        g.label: round(s * (g.coord_pt - origin_pt) * m_per_pt, 4)
        for g in gs.lines
        if g.orient == orient
    }


def elevation_bubbles(gs: GridSet, px_per_pt: float = 1.0) -> List[dict]:
    """``[{label, u_px}]`` for the vertical grid lines on an elevation."""
    return [
        {"label": g.label, "u_px": g.coord_pt * px_per_pt, "confidence": g.confidence}
        for g in gs.lines
        if g.orient == "v"
    ]
