"""Walls and rooms from a vector floor-plan sheet (#740, first slice).

Input: a sheet from ``pdf_ingest`` (``Sheet`` or its JSON dict) and its scale
in metres per sheet point (``drawing_scale``). Output: walls (centreline +
thickness), openings bridged in wall runs, and rooms as the closed faces of
the wall-centreline graph, each with a gross (centreline) and net (inside the
wall faces) polygon in metres, y up.

Vector path only. Walls come from

* line pairs: two parallel stroked lines a wall thickness apart
  (``WALL_T_MIN_M``..``WALL_T_MAX_M``) with no third parallel line between
  them; adjacent bands sharing a face (a window's glazing line) are merged
  when the total still fits a wall;
* solid poché: filled near-rectangles whose short side is a wall thickness.

Collinear pieces of the same thickness join across gaps up to
``MAX_OPENING_M``: a gap with another wall ending in it is a junction,
otherwise an opening. Wall ends snap to the centreline of a crossing wall
within reach, closing L and T junctions; once openings are bridged, a free end
stopping just short of an opening line it crosses joins that line too.

Nothing is dropped silently: wall ends that connect to nothing, faces too
small to be rooms, and faces holding two different room numbers go to
``review``. A gap with a door swing drawn in it (a curve centred on one
jamb with the leaf width as radius, or two half-width curves for a pair)
is marked ``kind: "door"``; a glazing line inside a wall run is a
``kind: "window"`` opening (#743). The raster fallback for scanned sheets, label-less face
classification and the Clinic measurement are follow-ups.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize_full, unary_union

SCHEMA = "matchline.plan_walls/1"

WALL_T_MIN_M = 0.05
WALL_T_MAX_M = 0.60
MIN_WALL_LEN_M = 0.25  # shortest wall piece kept (overlap of the two faces)
MAX_OPENING_M = 2.50  # widest gap bridged inside a wall run
WIDE_OPENING_M = 4.5  # wider collinear gaps close with an air wall for review (#740)
ANGLE_TOL_DEG = 1.0
MIN_ROOM_M2 = 1.0
SLIVER_M2 = 2.0  # unlabeled faces below this merge into a neighbour (#740)
RECT_FILL = 0.90  # filled shape area / its rotated bounding rectangle
SWING_TOL = 0.20  # door swing radius match, fraction of the leaf width (#743)
DOOR_CONFIDENCE = {"single": 0.85, "double": 0.8}
WINDOW_CONFIDENCE = 0.75  # glazing line inside a wall run (#743)
WINDOW_RETURN_M = 0.30  # wall must run on past the glazing at least this far on one side
STOREFRONT_CONFIDENCE = 0.6  # full-length glazing broken by mullion ticks (#793)
MULLION_MIN = 2  # ticks across the wall inside the glazed run
MULLION_T_TOL = 0.5  # tick length within +-50% of the wall thickness
TAG_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Z]?$")  # W1, D-12, SF1, SF-1A (#793)
TAG_RADIUS_M = 1.5  # a tag this close to an opening labels it
THIN_BAND_RATIO = 0.6  # a wall this thin next to the opaque walls either side may be glazing
THIN_BAND_MIN_M = 1.0  # shorter thin pieces are jambs or frames, not a glazed bay
THIN_FILL_SHARE = 0.8  # a thin wall covering this much of a gap in a thick run stops the run
MAX_DOOR_M = 2.5  # widest door pair whose jambs sit inside storefront glazing (#793)
LAYER_GLAZING_CONFIDENCE = 0.7  # full-length glazing line on a glazing CAD layer (#793)
# CAD layer (PDF optional content group) names, NCS/AIA style and common variants (#793)
GLAZING_LAYER_RE = re.compile(r"GLAZ|STORE-?FRONT|STORFRNT|CURTAIN|CURT-?WALL|CWALL|WINDOW|WIND\b")
PATTERN_LAYER_RE = re.compile(r"PATT|INSUL|HATCH|BATT")

Pt = Tuple[float, float]


# --------------------------------------------------------------- primitives


@dataclass
class Seg:
    a: Pt
    b: Pt
    src: int  # primitive index
    layer: Optional[str] = None  # the primitive's CAD layer, when the PDF keeps them (#793)

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def theta(self) -> float:
        t = math.atan2(self.b[1] - self.a[1], self.b[0] - self.a[0]) % math.pi
        return 0.0 if t > math.pi - 1e-9 else t


def _get(o, k):
    return o[k] if isinstance(o, dict) else getattr(o, k)


def _segments(sheet, skip: set) -> List[Seg]:
    out: List[Seg] = []
    for i, p in enumerate(_get(sheet, "primitives")):
        if i in skip or not _get(p, "stroked") or _get(p, "dashed"):
            continue
        lay = p.get("layer") if isinstance(p, dict) else getattr(p, "layer", None)
        cur = start = None
        for kind, pts in _get(p, "segments"):
            if kind == "M":
                cur = start = tuple(pts[0])
            elif kind == "L" and cur is not None:
                nxt = tuple(pts[0])
                out.append(Seg(cur, nxt, i, lay))
                cur = nxt
            elif kind == "C" and cur is not None:
                cur = tuple(pts[-1])  # curves (door swings) are not wall faces
            elif kind == "Z" and cur is not None and start is not None:
                if cur != start:
                    out.append(Seg(cur, start, i, lay))
                cur = start
    return out


def _arcs(sheet) -> List[Tuple[Pt, Pt]]:
    """Stroked curve runs as (start, end): door swing candidates (#743).

    Consecutive curve segments in one path are one run (CAD exporters often
    split a quarter circle into several Beziers). Dashed swings count.
    """
    out: List[Tuple[Pt, Pt]] = []
    for p in _get(sheet, "primitives"):
        if not _get(p, "stroked"):
            continue
        cur = run = None
        for kind, pts in _get(p, "segments"):
            if kind == "C" and cur is not None:
                run = run or cur
                cur = tuple(pts[-1])
                continue
            if run is not None:
                out.append((run, cur))
                run = None
            if kind in ("M", "L"):
                cur = tuple(pts[0])
        if run is not None:
            out.append((run, cur))
    return out


def _swing(arcs, hinge: Pt, closed: Pt, r: float, t: float) -> bool:
    """A curve run centred on ``hinge`` with radius ``r``: one end at the closed
    leaf position ``closed`` (the far jamb or the meeting point), the other
    swung off the wall line."""
    tol = max(SWING_TOL * r, t)
    ux, uy = closed[0] - hinge[0], closed[1] - hinge[1]
    n = math.hypot(ux, uy) or 1.0
    for p, q in arcs:
        for shut, open_ in ((p, q), (q, p)):
            if math.dist(shut, closed) > tol:
                continue
            if abs(math.dist(open_, hinge) - r) > SWING_TOL * r:
                continue
            off = abs((open_[0] - hinge[0]) * uy - (open_[1] - hinge[1]) * ux) / n
            if off >= 0.5 * r:
                return True
    return False


def _classify_door(arcs, a: Pt, b: Pt, t: float) -> Optional[Tuple[str, List[Pt]]]:
    """``("single", [hinge])`` / ``("double", [hinge_a, hinge_b])`` when the plan
    draws a swing in the gap ``a``-``b``, else None. No swing, no claim."""
    w = math.dist(a, b)
    if w <= 0:
        return None
    lo = (min(a[0], b[0]) - 1.2 * w, min(a[1], b[1]) - 1.2 * w)
    hi = (max(a[0], b[0]) + 1.2 * w, max(a[1], b[1]) + 1.2 * w)
    near = [
        (p, q)
        for p, q in arcs
        if all(lo[0] <= z[0] <= hi[0] and lo[1] <= z[1] <= hi[1] for z in (p, q))
    ]
    if not near:
        return None
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if _swing(near, a, mid, w / 2, t) and _swing(near, b, mid, w / 2, t):
        return "double", [a, b]
    for h, o in ((a, b), (b, a)):
        if _swing(near, h, o, w, t):
            return "single", [h]
    return None


def _mullion_pos(
    segs, ls, th: float, t: float, s0: float, s1: float, gap: float, atol: float
) -> List[float]:
    """Positions along the wall of ticks across it between ``s0`` and ``s1`` (#793).

    A tick is a stroked segment perpendicular to the wall, about one wall
    thickness long, centred on the wall line. Ticks closer than ``gap`` to
    each other or to the ends of the run count once: a frame or a corner is
    not a mullion.
    """
    pos: List[float] = []
    for g in segs:
        if not _angle_close((g.theta + math.pi / 2) % math.pi, th, atol):
            continue
        if abs(g.length - t) > MULLION_T_TOL * t:
            continue
        mid = Point((g.a[0] + g.b[0]) / 2, (g.a[1] + g.b[1]) / 2)
        if ls.distance(mid) > 0.25 * t:
            continue
        s = ls.project(mid)
        if s0 + gap <= s <= s1 - gap and all(abs(s - q) >= gap for q in pos):
            pos.append(s)
    return sorted(pos)


def _mullions(segs, ls, th: float, t: float, s0: float, s1: float, gap: float, atol: float) -> int:
    """Number of ticks across a wall between ``s0`` and ``s1`` (see ``_mullion_pos``)."""
    return len(_mullion_pos(segs, ls, th, t, s0, s1, gap, atol))


def _door_jambs(arcs, ls, pos: List[float], t: float, max_w: float) -> List[Tuple[float, float]]:
    """Pairs of ticks that are a door's jambs (#793): two neighbouring ticks
    at most ``max_w`` apart with a door swing drawn between them. A door in a
    storefront drawn without a gap in the wall has jambs that look exactly
    like mullions; they bound the door, they do not break up glass."""
    out: List[Tuple[float, float]] = []
    i = 0
    while i < len(pos) - 1:
        p, q = pos[i], pos[i + 1]
        if q - p <= max_w and _classify_door(
            arcs, ls.interpolate(p).coords[0], ls.interpolate(q).coords[0], t
        ):
            out.append((p, q))
            i += 2
            continue
        i += 1
    return out


def _layer_kind(name: Optional[str]) -> Optional[str]:
    """``"glazing"``, ``"pattern"`` or None for a CAD layer name (#793)."""
    if not name:
        return None
    u = name.upper()
    if PATTERN_LAYER_RE.search(u):
        return "pattern"  # A-GLAZ-PATT is a hatch, not the glass
    if GLAZING_LAYER_RE.search(u):
        return "glazing"
    return None


def _band_layer(band, segs) -> Optional[str]:
    """What the layer of a glazed band's middle line says (#793): glazing,
    pattern or None (no layer evidence). Only the line inside the wall is
    judged; the wall's own face lines (often on A-WALL, sometimes on a hatch
    layer) say nothing about whether the middle line is glass."""
    _d, n = _frame(band.theta)
    kinds = set()
    for i in band.segs:
        if i >= len(segs):
            continue
        sg = segs[i]
        off = (_dot(sg.a, n) + _dot(sg.b, n)) / 2 - band.rho
        if abs(off) < 0.4 * band.t:  # inside the wall, not one of its faces
            kinds.add(_layer_kind(sg.layer))
    if "glazing" in kinds:
        return "glazing"
    if "pattern" in kinds:
        return "pattern"
    return None


def _glazing_windows(
    bands, walls, k: float, tol: float, segs=(), arcs=()
) -> List[Tuple["Wall", Pt, Pt, str, List[Tuple[Pt, Pt]]]]:
    """Windows drawn as a glazing line inside a wall (#743).

    ``_merge_glazing`` folds the two half-thickness bands either side of a
    glazing (or sill) line back into the wall; each such band is a window
    candidate. It counts when it lies along one wall, is no wider than
    ``WIDE_OPENING_M``, and the wall runs on past it by ``WINDOW_RETURN_M``
    (or two wall thicknesses) on at least one side: a middle line along the
    whole wall is a cavity or insulation line, not glazing. Overlapping
    candidates on one wall are one window.

    Full-length glazing (storefront, curtain wall) is the exception (#793):
    a middle line along the whole wall counts when at least ``MULLION_MIN``
    mullion ticks cross the wall inside it, at any width. Cavity and
    insulation lines are not broken by ticks. These come out with source
    ``glazing_mullions``; the others ``glazing_line``.

    When the PDF keeps CAD layers, they decide first (#793): a band with a
    face line on a glazing layer (A-GLAZ, storefront, curtain wall) is a window
    at any length, source ``glazing_layer``; a middle line on a pattern or
    insulation layer (A-WALL-PATT) is never one, ticks or not.

    A door drawn inside full-length glazing with no gap in the wall has two
    jamb ticks with its swing between them (#793). Those ticks are not
    mullions. The window still spans the whole glazed run (a storefront's
    schedule width may or may not include its door, so the tag match decides
    downstream) and lists the doors inside it, each as its two jamb points.
    """
    atol = math.radians(ANGLE_TOL_DEG)
    out: List[Tuple[Wall, Pt, Pt, str, List[Tuple[Pt, Pt]]]] = []
    spans: Dict[str, List[Tuple[float, float]]] = {}
    for g in bands:
        if not g.glazed:
            continue
        lay = _band_layer(g, segs)
        if lay == "pattern":
            continue
        ga, gb = _endpoints(g.theta, g.rho, g.u0, g.u1)
        wide = math.dist(ga, gb) > WIDE_OPENING_M * k
        mid = Point((ga[0] + gb[0]) / 2, (ga[1] + gb[1]) / 2)
        for w in walls:
            L = math.dist(w.a, w.b)
            if L <= tol:
                continue
            th = math.atan2(w.b[1] - w.a[1], w.b[0] - w.a[0]) % math.pi
            if not _angle_close(th, g.theta, atol):
                continue
            ls = LineString([w.a, w.b])
            if ls.distance(mid) > max(tol, 0.5 * w.t):
                continue
            s0, s1 = sorted((ls.project(Point(ga)), ls.project(Point(gb))))
            if _wall_inside_band(g, w, walls, atol, tol):
                break
            margin = max(2 * w.t, WINDOW_RETURN_M * k)
            source = "glazing_line"
            doors: List[Tuple[float, float]] = []
            # reveal returns at the jambs end the run there (#743); the
            # collinear walls just past them still count as the wall running on
            run_lo, run_hi = 0.0, L
            if s0 < margin and L - s1 < margin and not _middle_line_continues(g, bands, tol):
                run_lo, run_hi = _collinear_run(w, ls, th, walls, atol, tol)
            if wide or (s0 - run_lo < margin and run_hi - s1 < margin):
                # runs the whole wall, or wider than a window: storefront only
                # when its layer says glazing or mullions break it up, else a
                # cavity or insulation line
                pos = _mullion_pos(segs, ls, th, w.t, s0, s1, margin, atol)
                doors = _door_jambs(arcs, ls, pos, w.t, MAX_DOOR_M * k)
                jambs = {s for d in doors for s in d}
                if lay == "glazing":
                    source = "glazing_layer"
                elif len([s for s in pos if s not in jambs]) < MULLION_MIN:
                    break
                else:
                    source = "glazing_mullions"
            if any(min(s1, b1) - max(s0, b0) > 0.5 * (s1 - s0) for b0, b1 in spans.get(w.id, [])):
                break
            spans.setdefault(w.id, []).append((s0, s1))
            out.append(
                (
                    w,
                    ls.interpolate(s0).coords[0],
                    ls.interpolate(s1).coords[0],
                    source,
                    [
                        (ls.interpolate(d0).coords[0], ls.interpolate(d1).coords[0])
                        for d0, d1 in doors
                    ],
                )
            )
            break
    return out


def _wall_inside_band(g, w, walls, atol: float, tol: float) -> bool:
    """Another wall runs alongside ``w`` inside glazed band ``g`` (#868).

    A small enclosure (a box or chase) built against a wall's inside face
    leaves a face line between the two that ``_merge_glazing`` reads as
    glazing, folding wall, gap and box wall into one thick band. A real
    window's band is its own wall: no other parallel wall lies inside it.
    So a candidate whose band holds another, thinner wall, off its centre
    line and along at least half its length, is not a window. A wall at least
    as thick as the band (a thickened pier or lined wall beside the window)
    cannot lie inside it and never counts. Walls are not changed.
    """
    d, n = _frame(g.theta)
    span = g.u1 - g.u0
    if span <= 0:
        return False
    for o in walls:
        if o is w or o.t >= g.t:
            continue  # a wall as thick as the band cannot lie inside it
        tho = math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0]) % math.pi
        if not _angle_close(tho, g.theta, atol):
            continue
        off = abs((_dot(o.a, n) + _dot(o.b, n)) / 2 - g.rho)
        if off <= max(tol, 0.25 * w.t):
            continue  # on the band's own line: a collinear wall piece
        if off - 0.5 * o.t >= 0.5 * g.t:
            continue  # outside the band
        u0, u1 = sorted((_dot(o.a, d), _dot(o.b, d)))
        if min(u1, g.u1) - max(u0, g.u0) >= 0.5 * span:
            return True
    return False


def _middle_line_continues(g, bands, tol: float) -> bool:
    """Another glazed band carries ``g``'s middle line on along the same line,
    end to end with it (#743). A cavity or insulation line drawn in a wall
    split into pieces by partitions does this; a window's glazing stops at
    its jambs."""
    atol = math.radians(ANGLE_TOL_DEG)
    gap = max(g.t, tol)
    for o in bands:
        if o is g or not o.glazed or not _angle_close(o.theta, g.theta, atol):
            continue
        ro, o0, o1 = _in_frame(o, g.theta)
        if abs(ro - g.rho) > max(tol, 0.25 * g.t):
            continue
        if abs(o0 - g.u1) <= gap or abs(g.u0 - o1) <= gap:
            return True
    return False


def _collinear_run(w, ls, th, walls, atol: float, tol: float) -> Tuple[float, float]:
    """Extent of ``w`` along its own line, in ``ls.project`` units, grown
    through collinear walls of about the same thickness whose ends lie within
    one wall thickness of it (#743). A short return meeting a wall's face at a
    window jamb ends the wall run there, leaving the window alone on a wall
    no longer than itself, which would read as full-length glazing."""
    L = ls.length
    lo, hi = 0.0, L
    gap = max(w.t, tol)
    pieces = []
    for o in walls:
        if o is w or abs(o.t - w.t) > 0.25 * w.t:
            continue
        tho = math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0]) % math.pi
        if not _angle_close(th, tho, atol):
            continue
        # offset from the line, measured at both ends
        if max(_line_offset(ls, o.a), _line_offset(ls, o.b)) > 0.5 * w.t:
            continue
        pieces.append(sorted((_signed_proj(ls, o.a), _signed_proj(ls, o.b))))
    changed = True
    while changed:
        changed = False
        for p0, p1 in pieces:
            if p1 > hi and p0 <= hi + gap:
                hi, changed = p1, True
            if p0 < lo and p1 >= lo - gap:
                lo, changed = p0, True
    return lo, hi


def _signed_proj(ls: LineString, p: Pt) -> float:
    (ax, ay), (bx, by) = ls.coords[0], ls.coords[-1]
    L = ls.length
    return ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L


def _line_offset(ls: LineString, p: Pt) -> float:
    (ax, ay), (bx, by) = ls.coords[0], ls.coords[-1]
    L = ls.length
    return abs((p[0] - ax) * (by - ay) - (p[1] - ay) * (bx - ax)) / L


def _thin_fill(walls: List[Wall], a: Pt, b: Pt, t: float, tol: float) -> bool:
    """A wall at most ``THIN_BAND_RATIO`` x ``t`` thick lies along the gap
    a-b (centreline within half ``t``) and covers at least
    ``THIN_FILL_SHARE`` of it (#793). Without this check a gap up to
    ``MAX_OPENING_M`` with the thin wall's ends in it reads as a junction and
    the thick run continues straight through the thin bay."""
    L = math.dist(a, b)
    if L <= tol:
        return False
    atol = math.radians(ANGLE_TOL_DEG)
    ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    th = math.atan2(uy, ux) % math.pi
    for o in walls:
        if o.t > THIN_BAND_RATIO * t:
            continue
        tho = math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0]) % math.pi
        if not _angle_close(th, tho, atol):
            continue
        off = [abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux) for p in (o.a, o.b)]
        if max(off) > 0.5 * t + tol:
            continue
        s0, s1 = sorted(((p[0] - a[0]) * ux + (p[1] - a[1]) * uy) for p in (o.a, o.b))
        if min(s1, L) - max(s0, 0.0) >= THIN_FILL_SHARE * L:
            return True
    return False


def _thin_bands(walls: List[Wall], openings: List[dict], tol: float, min_len: float) -> List[str]:
    """Walls that may be storefront drawn as a thin band of its own (#793).

    A wall at most ``THIN_BAND_RATIO`` as thick as the wall it meets end to end
    on BOTH sides, on the same line (centrelines within half the thicker wall,
    since glazing often sits on one face), at least ``min_len`` long and with no
    opening on it, is a glazed bay between opaque walls more often than a
    partition: a partition turns off the exterior line, it does not continue it.
    It is only flagged; nothing is modelled from the band alone.
    """
    has_op = {wid for o in openings for wid in o.get("walls", [])}
    atol = math.radians(ANGLE_TOL_DEG)
    out = []
    for w in walls:
        L = math.dist(w.a, w.b)
        if w.id in has_op or L < min_len or L == 0:
            continue
        th = math.atan2(w.b[1] - w.a[1], w.b[0] - w.a[0])
        ux, uy = (w.b[0] - w.a[0]) / L, (w.b[1] - w.a[1]) / L

        def along(p) -> float:
            return (p[0] - w.a[0]) * ux + (p[1] - w.a[1]) * uy

        sides = set()
        for o in walls:
            if o is w or w.t > THIN_BAND_RATIO * o.t:
                continue
            tho = math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0])
            if not _angle_close(th % math.pi, tho % math.pi, atol):
                continue
            reach = 0.5 * o.t + tol
            s0, s1 = sorted((along(o.a), along(o.b)))
            # perpendicular offset at o's end nearest the joint: walls up to
            # ANGLE_TOL_DEG apart drift along a long neighbour's far end
            near = min((o.a, o.b), key=lambda p: min(abs(along(p)), abs(along(p) - L)))
            vx, vy = near[0] - w.a[0], near[1] - w.a[1]
            if abs(vx * uy - vy * ux) > reach:
                continue
            if abs(s1) <= reach and s0 < -reach:
                sides.add("a")
            if abs(s0 - L) <= reach and s1 > L + reach:
                sides.add("b")
        if sides == {"a", "b"}:
            out.append(w.id)
    return out


def _glazing_layer_band(w: "Wall", segs: List[Seg], tol: float) -> bool:
    """A thin wall band drawn on a glazing CAD layer (#793): its face lines
    (parallel, within half the band of its centreline) that sit on a glazing
    layer cover at least ``THIN_FILL_SHARE`` of its length, and none of its
    face lines is on a pattern layer."""
    L = math.dist(w.a, w.b)
    if L <= tol:
        return False
    atol = math.radians(ANGLE_TOL_DEG)
    ux, uy = (w.b[0] - w.a[0]) / L, (w.b[1] - w.a[1]) / L
    th = math.atan2(uy, ux) % math.pi
    ivs: List[Tuple[float, float]] = []
    for sg in segs:
        kind = _layer_kind(sg.layer)
        if kind is None or not _angle_close(sg.theta, th, atol):
            continue
        off = [abs((p[0] - w.a[0]) * uy - (p[1] - w.a[1]) * ux) for p in (sg.a, sg.b)]
        if max(off) > 0.5 * w.t + tol:
            continue
        s0, s1 = sorted(((p[0] - w.a[0]) * ux + (p[1] - w.a[1]) * uy) for p in (sg.a, sg.b))
        s0, s1 = max(s0, 0.0), min(s1, L)
        if s1 - s0 <= tol:
            continue
        if kind == "pattern":
            return False
        ivs.append((s0, s1))
    covered, end = 0.0, 0.0
    for s0, s1 in sorted(ivs):
        if s1 > end:
            covered += s1 - max(s0, end)
            end = s1
    return covered >= THIN_FILL_SHARE * L


def _attach_tags(openings: List[dict], spans, to_m) -> int:
    """Schedule-tag text placed next to an opening labels it (#793).

    A span reads as a tag when it looks like one (``TAG_RE``: W1, D-12,
    SF-1). Each tag goes to the single nearest opening within
    ``TAG_RADIUS_M``. An opening's ``tag_text`` is its nearest tag, and
    ``tags_near`` lists every tag it got, nearest first, because a wall-type
    mark can sit closer than the door/window mark (#829). Which tag is really
    scheduled is for the caller to check.
    """
    lines = [LineString([o["a_m"], o["b_m"]]) for o in openings]
    got: Dict[int, List[Tuple[float, str]]] = {}
    for text, (x0, y0, x1, y1) in spans:
        t = str(text).strip().upper()
        if not TAG_RE.match(t) or not lines:
            continue
        c = Point(to_m(((x0 + x1) / 2, (y0 + y1) / 2)))
        d, i = min((ln.distance(c), i) for i, ln in enumerate(lines))
        if d <= TAG_RADIUS_M:
            got.setdefault(i, []).append((d, t))
    for i, tags in got.items():
        tags.sort()
        d, t = tags[0]
        openings[i]["tag_text"] = t
        openings[i]["tag_dist_m"] = round(d, 3)
        openings[i]["tags_near"] = [{"tag": t, "dist_m": round(d, 3)} for d, t in tags]
    return len(got)


def _wall_tags(walls: List[dict], openings: List[dict], spans, to_m) -> List[dict]:
    """Tags on a wall with no opening drawn (#793): the storefront case.

    A full-length storefront or curtain wall is often drawn as a plain wall
    band with its schedule tag (SF-1) beside it. A tag-like span that is not
    within ``TAG_RADIUS_M`` of any opening, but is within it of a wall with no
    opening, is kept against that wall; every such tag is kept, since one wall
    can carry a storefront tag and a wall-type tag (#747). Whether the tag is scheduled, and
    whether its width explains the wall, is for the caller to decide.
    """
    op_lines = [LineString([o["a_m"], o["b_m"]]) for o in openings]
    has_op = {wid for o in openings for wid in o.get("walls", [])}
    free = [(w, LineString([w["a_m"], w["b_m"]])) for w in walls if w["id"] not in has_op]
    found: List[Tuple[str, float, str, Tuple[float, float]]] = []
    for text, (x0, y0, x1, y1) in spans:
        t = str(text).strip().upper()
        if not TAG_RE.match(t) or not free:
            continue
        c = Point(to_m(((x0 + x1) / 2, (y0 + y1) / 2)))
        if any(ln.distance(c) <= TAG_RADIUS_M for ln in op_lines):
            continue  # belongs to an opening (or would have)
        d, w = min(((ln.distance(c), w) for w, ln in free), key=lambda t: t[0])
        if d <= TAG_RADIUS_M:
            found.append((w["id"], d, t, (c.x, c.y)))
    return [
        {
            "wall": wid,
            "tag_text": t,
            "point_m": [round(x, 4), round(y, 4)],
            "tag_dist_m": round(d, 3),
        }
        for wid, d, t, (x, y) in sorted(found, key=lambda f: (f[0], f[3]))
    ]


def _filled_rects(sheet, t_min: float, t_max: float):
    """Solid poché walls: (index, centreline a, b, thickness) in sheet points."""
    out = []
    for i, p in enumerate(_get(sheet, "primitives")):
        # fill closes a path implicitly, so an unclosed filled polyline is a shape too
        if not _get(p, "filled") or _get(p, "kind") not in ("polygon", "polyline"):
            continue
        pts = [tuple(q) for kind, ps in _get(p, "segments") if kind in "ML" for q in ps]
        if len(pts) < 4:
            continue
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= 0:
            continue
        rect = poly.minimum_rotated_rectangle
        if poly.area / rect.area < RECT_FILL:
            continue
        c = list(rect.exterior.coords)[:4]
        e0, e1 = math.dist(c[0], c[1]), math.dist(c[1], c[2])
        if e0 >= e1:
            a = ((c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2)
            b = ((c[3][0] + c[0][0]) / 2, (c[3][1] + c[0][1]) / 2)
            t, length = e1, e0
        else:
            a = ((c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2)
            b = ((c[2][0] + c[3][0]) / 2, (c[2][1] + c[3][1]) / 2)
            t, length = e0, e1
        if t_min <= t <= t_max and length >= 2 * t:
            out.append((i, a, b, t))
    return out


# --------------------------------------------------------------- wall bands


@dataclass
class Band:
    theta: float  # direction in [0, pi)
    rho: float  # centreline offset along the normal
    u0: float  # extent along the direction
    u1: float
    t: float  # thickness
    source: str
    segs: Tuple[int, ...] = ()  # face segment indices (line pairs)
    glazed: bool = False  # merged across a shared face line: a window's glazing (#743)


def _frame(theta: float):
    d = (math.cos(theta), math.sin(theta))
    n = (-d[1], d[0])
    return d, n


def _dot(p, v) -> float:
    return p[0] * v[0] + p[1] * v[1]


def _angle_close(a: float, b: float, tol: float) -> bool:
    d = abs(a - b) % math.pi
    return min(d, math.pi - d) <= tol


def _in_frame(b: Band, theta: float) -> Tuple[float, float, float]:
    """(rho, u0, u1) of band ``b`` in the frame of direction ``theta``.

    Directions near 0 and near pi are the same line but flip the normal, so
    bands are always compared in one reference frame.
    """
    a, c = _endpoints(b.theta, b.rho, b.u0, b.u1)
    d, n = _frame(theta)
    ua, uc = _dot(a, d), _dot(c, d)
    return (_dot(a, n) + _dot(c, n)) / 2, min(ua, uc), max(ua, uc)


def _overlap(a: Band, b: Band) -> float:
    _, b0, b1 = _in_frame(b, a.theta)
    return min(a.u1, b1) - max(a.u0, b0)


LADDER_MULTIPLES = (-3, -2, -1, 2, 3, 4)  # spacings checked around a candidate pair
LADDER_MIN = 3  # this many equally spaced neighbours: stair treads or hatch, not a wall


def _pair_bands(segs: List[Seg], t_min: float, t_max: float, min_len: float) -> List[Band]:
    tol = math.radians(ANGLE_TOL_DEG)
    eps = max(t_min * 0.2, 1e-3)
    geoms = [LineString([s.a, s.b]) for s in segs]
    tree = STRtree(geoms)
    bands: List[Band] = []
    seen = set()
    for i, s in enumerate(segs):
        if s.length < min_len:
            continue
        d, n = _frame(s.theta)
        ps, ua, ub = _dot(s.a, n), _dot(s.a, d), _dot(s.b, d)
        s0, s1 = min(ua, ub), max(ua, ub)
        near = tree.query(geoms[i].buffer(4 * t_max))
        parallel = []
        for j in near:
            j = int(j)
            if j == i or not _angle_close(segs[j].theta, s.theta, tol):
                continue
            o = segs[j]
            off = (_dot(o.a, n) + _dot(o.b, n)) / 2 - ps
            va, vb = _dot(o.a, d), _dot(o.b, d)
            parallel.append((j, off, min(va, vb), max(va, vb)))
        for j, off, t0, t1 in parallel:
            if (min(i, j), max(i, j)) in seen or not (t_min <= abs(off) <= t_max):
                continue
            u0, u1 = max(s0, t0), min(s1, t1)
            if u1 - u0 < min_len:
                continue
            between = any(
                k != j
                and eps < (off2 if off > 0 else -off2) < abs(off) - eps
                and min(k1, u1) - max(k0, u0) > 0.5 * (u1 - u0)
                for k, off2, k0, k1 in parallel
            )
            if between:
                continue
            ladder = sum(
                any(
                    abs(off2 - m * off) <= 0.1 * abs(off) + eps
                    and min(k1, u1) - max(k0, u0) > 0.5 * (u1 - u0)
                    for _k, off2, k0, k1 in parallel
                )
                for m in LADDER_MULTIPLES
            )
            if ladder >= LADDER_MIN:
                continue
            seen.add((min(i, j), max(i, j)))
            f0, f1 = ps, ps + off
            bands.append(
                Band(
                    s.theta,
                    (f0 + f1) / 2,
                    u0,
                    u1,
                    abs(off),
                    "line_pair",
                    (i, j),
                )
            )
    return _merge_glazing(_drop_gaps(bands), t_max)


def _drop_gaps(bands: List[Band]) -> List[Band]:
    """Drop a band whose two faces each bound a thinner wall on the far side.

    Two walls with a narrow chase between them pair up three ways; the middle
    pair is the chase, not a wall.
    """
    by_seg: Dict[int, List[int]] = {}
    for bi, b in enumerate(bands):
        for sgi in b.segs:
            by_seg.setdefault(sgi, []).append(bi)

    def walled(bi: int, face: int, other: int) -> bool:
        b = bands[bi]
        return any(
            ci != bi
            and other not in bands[ci].segs
            and bands[ci].t < b.t
            and _overlap(b, bands[ci]) > 0.5 * (b.u1 - b.u0)
            for ci in by_seg.get(face, [])
        )

    return [
        b
        for bi, b in enumerate(bands)
        if not (
            len(b.segs) == 2
            and walled(bi, b.segs[0], b.segs[1])
            and walled(bi, b.segs[1], b.segs[0])
        )
    ]


def _merge_glazing(bands: List[Band], t_max: float) -> List[Band]:
    """Adjacent bands sharing a face line (a window's glazing) become one wall."""
    changed = True
    while changed:
        changed = False
        by_seg: Dict[int, List[int]] = {}
        for bi, b in enumerate(bands):
            for sgi in b.segs:
                by_seg.setdefault(sgi, []).append(bi)
        for idx in by_seg.values():
            if len(idx) != 2:
                continue
            a, b = bands[idx[0]], bands[idx[1]]
            rb, b0, b1 = _in_frame(b, a.theta)
            tol = 0.1 * min(a.t, b.t)
            # a reveal return meeting one face inside the opening cuts that
            # half short of its jamb (#743): allow up to one wall thickness
            etol = max(tol, min(a.t + b.t, t_max) / 2)
            if abs(a.u0 - b0) > etol or abs(a.u1 - b1) > etol:
                continue
            if min(a.u1, b1) - max(a.u0, b0) < 0.5 * max(a.u1 - a.u0, b1 - b0):
                continue
            if abs(abs(a.rho - rb) - (a.t + b.t) / 2) > tol:  # same side: not a shared face
                continue
            lo = min(a.rho - a.t / 2, rb - b.t / 2)
            hi = max(a.rho + a.t / 2, rb + b.t / 2)
            if hi - lo > t_max:
                continue
            merged = Band(
                a.theta, (lo + hi) / 2, min(a.u0, b0), max(a.u1, b1), hi - lo, "line_pair",
                tuple(sorted(set(a.segs) | set(b.segs))), True,
            )  # fmt: skip
            bands = [x for k, x in enumerate(bands) if k not in idx] + [merged]
            changed = True
            break
    return bands


def _band_from_rect(a: Pt, b: Pt, t: float) -> Band:
    theta = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
    d, n = _frame(theta)
    rho = (_dot(a, n) + _dot(b, n)) / 2
    ua, ub = _dot(a, d), _dot(b, d)
    return Band(theta, rho, min(ua, ub), max(ua, ub), t, "filled")


# ---------------------------------------------------------------- wall runs


@dataclass
class Wall:
    id: str
    a: Pt  # sheet points
    b: Pt
    t: float
    source: str


@dataclass
class Bridge:
    a: Pt
    b: Pt
    t: float
    walls: Tuple[str, str]


def _endpoints(theta, rho, u0, u1) -> Tuple[Pt, Pt]:
    d, n = _frame(theta)
    return (
        (d[0] * u0 + n[0] * rho, d[1] * u0 + n[1] * rho),
        (d[0] * u1 + n[0] * rho, d[1] * u1 + n[1] * rho),
    )


def _runs(bands: List[Band], max_gap: float, tol: float):
    """Group collinear bands of one thickness; join overlaps, keep gaps as bridges."""
    atol = math.radians(ANGLE_TOL_DEG)
    groups: List[List[Band]] = []
    for b in sorted(bands, key=lambda b: (b.theta, b.rho)):
        for g in groups:
            r = g[0]
            if (
                _angle_close(r.theta, b.theta, atol)
                and abs(r.rho - _in_frame(b, r.theta)[0]) <= max(tol, 0.15 * r.t)
                and abs(r.t - b.t) <= 0.25 * max(r.t, b.t)
            ):
                g.append(b)
                break
        else:
            groups.append([b])
    walls: List[Wall] = []
    gaps: List[Tuple[Pt, Pt, float, str, str]] = []
    for g in groups:
        theta = g[0].theta
        fr = [_in_frame(b, theta) for b in g]
        rho = sum(r * (u1 - u0) for r, u0, u1 in fr) / sum(u1 - u0 for _r, u0, u1 in fr)
        t = max(b.t for b in g)
        src = "filled" if all(b.source == "filled" for b in g) else "line_pair"
        ivs = sorted((u0, u1) for _r, u0, u1 in fr)
        merged = [list(ivs[0])]
        for u0, u1 in ivs[1:]:
            if u0 <= merged[-1][1] + tol:
                merged[-1][1] = max(merged[-1][1], u1)
            else:
                merged.append([u0, u1])
        prev = None
        for u0, u1 in merged:
            a, b = _endpoints(theta, rho, u0, u1)
            w = Wall(f"W{len(walls) + 1}", a, b, t, src)
            walls.append(w)
            if prev is not None and u0 - prev[1] <= max_gap:
                ga, gb = _endpoints(theta, rho, prev[1], u0)
                gaps.append((ga, gb, t, prev[0].id, w.id))
            prev = (w, u1)
    return walls, gaps


def _drop_thickenings(walls: List[Wall], keep: set, tol: float) -> List[Wall]:
    """Drop a short band that overlaps a longer wall's band along its whole length (#740).

    A chase or bump drawn against a wall reads as a second, thicker line pair
    whose centreline sits inside the wall's band. Kept, it floats a few
    centimetres off the wall's centreline, so the walls meeting it never reach
    the real wall and the rooms either side read as one face. Dropped, their
    ends are free and join the real wall's centreline like any T-junction.
    It runs after collinear gaps are carried through junctions, so a wall cut
    where the chase meets it is whole again. Walls bridged across an opening
    (``keep``) are never dropped.
    """
    atol = math.radians(ANGLE_TOL_DEG)
    out = []
    for w in walls:
        Lw = math.dist(w.a, w.b)
        if w.id in keep or Lw <= tol:
            out.append(w)
            continue
        ang = math.atan2(w.b[1] - w.a[1], w.b[0] - w.a[0])
        covered = False
        for o in walls:
            Lo = math.dist(o.a, o.b)
            if o is w or Lo <= Lw:
                continue
            if not _angle_close(ang, math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0]), atol):
                continue
            line = LineString([o.a, o.b])
            if (
                line.distance(Point(w.a)) > o.t / 2 + tol
                or line.distance(Point(w.b)) > o.t / 2 + tol
            ):
                continue
            ua, ub = line.project(Point(w.a)), line.project(Point(w.b))
            if min(ua, ub) >= tol and max(ua, ub) <= Lo - tol:
                covered = True
                break
        if not covered:
            out.append(w)
    return out


def _line_x(p: Pt, q: Pt, r: Pt, s: Pt) -> Optional[Tuple[Pt, float, float]]:
    """Intersection of lines pq and rs: point, param along pq, param along rs."""
    d1 = (q[0] - p[0], q[1] - p[1])
    d2 = (s[0] - r[0], s[1] - r[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    w = (r[0] - p[0], r[1] - p[1])
    t1 = (w[0] * d2[1] - w[1] * d2[0]) / den
    t2 = (w[0] * d1[1] - w[1] * d1[0]) / den
    return (p[0] + t1 * d1[0], p[1] + t1 * d1[1]), t1, t2


def _snap(walls: List[Wall], tol: float) -> None:
    """Move each wall end to the centreline of a crossing wall within reach."""
    if not walls:
        return
    lines = [LineString([w.a, w.b]) for w in walls]
    tree = STRtree(lines)
    t_big = max(w.t for w in walls)
    moves = []
    for wi, w in enumerate(walls):
        L = math.dist(w.a, w.b)
        if L == 0:
            continue
        ang = math.atan2(w.b[1] - w.a[1], w.b[0] - w.a[0])
        for end in ("a", "b"):
            p = getattr(w, end)
            best = None
            for oi in tree.query(Point(p).buffer(0.75 * max(w.t, t_big) + tol)):
                o = walls[int(oi)]
                if o is w:
                    continue
                if _angle_close(
                    ang, math.atan2(o.b[1] - o.a[1], o.b[0] - o.a[0]), math.radians(10)
                ):
                    continue
                hit = _line_x(w.a, w.b, o.a, o.b)
                if hit is None:
                    continue
                x, t1, t2 = hit
                reach = 0.75 * max(w.t, o.t) + tol
                Lo = math.dist(o.a, o.b)
                if not (-reach / Lo <= t2 <= 1 + reach / Lo):
                    continue
                dist = abs(t1) * L if end == "a" else abs(1 - t1) * L
                if dist <= reach and (best is None or dist < best[0]):
                    best = (dist, x)
            if best:
                moves.append((w, end, best[1]))
    for w, end, x in moves:
        setattr(w, end, x)


def _join_free_ends(
    pairs: List[Tuple[Pt, Pt]], ts: List[float], tol: float, max_open: float = 0.0
) -> Tuple[List[Tuple[Pt, Pt]], List[Tuple[int, int, Pt, Pt]]]:
    """Close wall ends that touch nothing (#740).

    Returns ``(joins, corner_openings)``.

    A join runs from a free end to the nearest point on another wall's
    centreline when that point is within the two walls' thicknesses added
    together: a T-junction that stopped at the far face, a wall that changes
    thickness, or a wall that steps sideways along its run.

    Otherwise, when the wall carried on along its own line would cross another
    wall within ``max_open``, the gap is a door beside a corner: the far side
    of the opening is a crossing wall, not a collinear one, so the collinear
    gap bridging never sees it. ``corner_openings`` holds
    ``(pair index, crossed pair index, end, crossing point)``.
    """
    if not pairs:
        return [], []
    lines = [LineString(p) for p in pairs]
    tree = STRtree(lines)
    t_big = max(ts)
    joins: List[Tuple[Pt, Pt]] = []
    corners: List[Tuple[int, int, Pt, Pt]] = []
    for i, (a, b) in enumerate(pairs):
        for p, q0 in ((a, b), (b, a)):
            pt = Point(p)
            near = [int(j) for j in tree.query(pt.buffer(2 * t_big + tol)) if int(j) != i]
            if any(lines[j].distance(pt) <= tol for j in near):
                continue  # already meets another wall
            best = None
            for j in near:
                d = lines[j].distance(pt)
                if d <= ts[i] + ts[j] + tol and (best is None or d < best[0]):
                    best = (d, j)
            if best:
                q = lines[best[1]].interpolate(lines[best[1]].project(pt))
                joins.append((p, (q.x, q.y)))
                continue
            n = math.dist(p, q0)
            if max_open <= 0 or n <= tol:
                continue
            ux, uy = (p[0] - q0[0]) / n, (p[1] - q0[1]) / n
            ray = LineString([p, (p[0] + ux * max_open, p[1] + uy * max_open)])
            hit = None
            for j in tree.query(ray):
                j = int(j)
                if j == i:
                    continue
                x = ray.intersection(lines[j])
                if x.is_empty or x.geom_type != "Point":
                    continue
                (x0, y0), (x1, y1) = lines[j].coords[0], lines[j].coords[-1]
                cross = abs((x1 - x0) * uy - (y1 - y0) * ux) / max(
                    math.hypot(x1 - x0, y1 - y0), 1e-9
                )
                if cross < 0.5:  # within 30 degrees of parallel: not a crossing wall
                    continue
                d = pt.distance(x)
                if d > tol and (hit is None or d < hit[0]):
                    hit = (d, j, (x.x, x.y))
            if hit:
                corners.append((i, hit[1], p, hit[2]))
    return joins, corners


def _ends_to_openings(
    walls: List[Tuple[Pt, Pt]],
    ts: List[float],
    others: List[Tuple[Pt, Pt]],
    opens: List[Tuple[Pt, Pt, float]],
    tol: float,
) -> List[Tuple[Pt, Pt]]:
    """Carry a free wall end onto an opening line it stops just short of (#740).

    A partition between two doors meets the line of the bridged openings, not
    a drawn wall, so ``_snap`` (which runs before any opening exists) leaves
    its end a few centimetres short and the two rooms read as one face. A free
    end gets a short join to the opening line ahead of it when that line
    crosses its own within the same reach ``_snap`` uses for walls.
    """
    if not walls or not opens:
        return []
    every = [LineString(p) for p in walls + others] + [LineString((a, b)) for a, b, _t in opens]
    tree = STRtree(every)
    olines = [LineString((a, b)) for a, b, _t in opens]
    otree = STRtree(olines)
    t_big = max(ts + [t for *_x, t in opens])
    joins: List[Tuple[Pt, Pt]] = []
    for i, (a, b) in enumerate(walls):
        L = math.dist(a, b)
        if L <= tol:
            continue
        for p, q0 in ((a, b), (b, a)):
            pt = Point(p)
            if any(
                int(j) != i and every[int(j)].distance(pt) <= tol
                for j in tree.query(pt.buffer(tol))
            ):
                continue  # already meets something
            ux, uy = (p[0] - q0[0]) / L, (p[1] - q0[1]) / L
            best = None
            for j in otree.query(pt.buffer(0.75 * t_big + tol)):
                (oa, ob, ot) = opens[int(j)]
                reach = 0.75 * max(ts[i], ot) + tol
                hit = _line_x(p, (p[0] + ux, p[1] + uy), oa, ob)
                if hit is None:
                    continue
                x, t1, t2 = hit
                Lo = math.dist(oa, ob)
                cross = abs((ob[0] - oa[0]) * uy - (ob[1] - oa[1]) * ux) / max(Lo, 1e-9)
                if cross < 0.5 or not (0 < t1 <= reach) or not (0 <= t2 <= 1):
                    continue  # parallel, behind the end, too far, or past the opening
                if best is None or t1 < best[0]:
                    best = (t1, x)
            if best:
                joins.append((p, best[1]))
    return joins


def _node_lines(pairs: List[Tuple[Pt, Pt]], tol: float) -> List[LineString]:
    """Make wall ends that meet share exact coordinates.

    Snapped ends land on a corner or on another wall's centreline only up to
    float error, which leaves the graph unnoded. Ends within ``tol`` collapse
    to one point, and an end lying on another line becomes a vertex of it.
    """
    nodes: List[Pt] = []
    grid: Dict[Tuple[int, int], List[int]] = {}

    def node(p: Pt) -> Pt:
        gx, gy = int(math.floor(p[0] / tol)), int(math.floor(p[1] / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for qi in grid.get((gx + dx, gy + dy), []):
                    if math.dist(p, nodes[qi]) <= tol:
                        return nodes[qi]
        grid.setdefault((gx, gy), []).append(len(nodes))
        nodes.append(p)
        return p

    pairs = [(node(a), node(b)) for a, b in pairs]
    if not nodes:
        return []
    pts = [Point(q) for q in nodes]
    tree = STRtree(pts)
    out = []
    for a, b in pairs:
        ln = LineString([a, b])
        inner = [
            nodes[int(qi)]
            for qi in tree.query(ln.buffer(tol))
            if nodes[int(qi)] not in (a, b) and ln.distance(pts[int(qi)]) <= tol
        ]
        inner.sort(key=lambda q: ln.project(Point(q)))
        out.append(LineString([a, *inner, b]))
    return out


# ---------------------------------------------------------------- results


@dataclass
class Room:
    id: str
    polygon_m: List[Pt]  # centreline (gross) polygon, metres, y up
    holes_m: List[List[Pt]]  # islands inside the room (a column, a shaft); not in the area
    net_polygon_m: List[Pt]
    area_m2: float
    net_area_m2: float
    label: Optional[dict] = None
    needs_review: bool = False
    reasons: List[str] = field(default_factory=list)
    merged_m2: List[float] = field(default_factory=list)  # unlabeled slivers folded in


@dataclass
class PlanWalls:
    walls: List[dict]
    openings: List[dict]
    rooms: List[Room]
    review: List[dict]
    m_per_pt: Optional[float]
    stats: Dict[str, float] = field(default_factory=dict)
    wall_tags: List[dict] = field(default_factory=list)  # tags on walls with no opening (#793)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "m_per_pt": self.m_per_pt,
            "walls": self.walls,
            "openings": self.openings,
            "rooms": [asdict(r) for r in self.rooms],
            "review": self.review,
            "stats": self.stats,
            "wall_tags": self.wall_tags,
        }


# a room number: "101", "B12", "12A", or a level-wing number such as "1A04", "1DC8" or
# "2CC4" (level digit, one or two wing letters, then the room digits), but never a
# lumber size like "2X4" written inside a note (#740)
_ROOM_NO = re.compile(r"\b(?:\d(?!X\d)[A-Z]{1,2}\d{1,3}|[A-Z]?\d{2,4}[A-Z]?)\b")


def _merge_slivers(faces: List[Polygon], spans, door_lines, max_area: float, tol: float):
    """Fold small unlabeled faces into a neighbouring face; nothing is dropped (#740).

    Wall jogs (a thick-wall corner, a thickness change) close small loops that
    are not rooms. Each unlabeled face under ``max_area`` merges, smallest
    first, by the closet/shaft rule: into the neighbour it shares a door with,
    else into the neighbour it shares the most boundary with. A face with a
    room label stays a room however small. Returns ``(faces, merged)``, where
    ``merged[k]`` lists the areas (sheet units) folded into ``faces[k]``.
    """
    faces = list(faces)
    merged: List[List[float]] = [[] for _ in faces]
    alive = [True] * len(faces)
    doors = unary_union([d.buffer(tol) for d in door_lines]) if door_lines else None
    for i in sorted(range(len(faces)), key=lambda k: faces[k].area):
        f = faces[i]
        if f.area >= max_area:
            break
        if _label_for(f, spans)[0]:
            continue
        best = None
        for j, g in enumerate(faces):
            if j == i or not alive[j] or not f.envelope.intersects(g.envelope):
                continue
            shared = f.boundary.intersection(g.boundary)
            if shared.length <= tol:
                continue
            door = doors is not None and shared.intersection(doors).length > tol
            key = (door, shared.length)
            if best is None or key > best[0]:
                best = (key, j)
        if best is None:
            continue
        j = best[1]
        u = unary_union([faces[j], f])
        if u.geom_type != "Polygon":
            continue
        faces[j] = u
        merged[j] += [f.area] + merged[i]
        alive[i] = False
    keep = [k for k in range(len(faces)) if alive[k]]
    return [faces[k] for k in keep], [merged[k] for k in keep]


def _label_for(face: Polygon, spans) -> Tuple[Optional[dict], List[str]]:
    from room_labels import looks_like_dimension, parse_room_label

    inside = []
    for txt, bb in spans:
        c = Point((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
        if face.contains(c) and not looks_like_dimension(txt):
            inside.append((txt, bb))
    if not inside:
        return None, []
    inside.sort(key=lambda s: (round(s[1][1]), s[1][0]))
    numbers = {n for t, _ in inside for n in _ROOM_NO.findall(t.upper())}
    reasons = []
    if len(numbers) > 1:
        reasons.append(
            f"face holds room numbers {', '.join(sorted(numbers))}: a wall may be missing"
        )
    raw = " ".join(t for t, _ in inside)
    name, number, conf = parse_room_label(raw)
    return {"name": name, "number": number, "raw_text": raw, "parse_confidence": conf}, reasons


def extract_walls(sheet, m_per_pt: Optional[float]) -> PlanWalls:
    if not m_per_pt:
        return PlanWalls([], [], [], [{"kind": "no_scale", "reason": "sheet has no scale"}], None)
    k = 1.0 / m_per_pt  # points per metre
    t_min, t_max = WALL_T_MIN_M * k, WALL_T_MAX_M * k
    tol = max(0.02 * k, 0.5)
    H = float(_get(sheet, "height_pt"))

    rects = _filled_rects(sheet, t_min, t_max)
    segs = _segments(sheet, skip={r[0] for r in rects})
    bands = _pair_bands(segs, t_min, t_max, MIN_WALL_LEN_M * k)
    bands += [_band_from_rect(a, b, t) for _, a, b, t in rects]
    review: List[dict] = []

    def to_m(p: Pt) -> Pt:
        return (round(p[0] * m_per_pt, 4), round((H - p[1]) * m_per_pt, 4))

    if not bands:
        review.append({"kind": "no_walls", "reason": "no wall line pairs or solid walls found"})
        return PlanWalls([], [], [], review, m_per_pt)

    walls, gaps = _runs(bands, max(MAX_OPENING_M, WIDE_OPENING_M) * k, tol)
    _snap(walls, tol)
    by_id = {w.id: w for w in walls}

    bridges: List[Bridge] = []
    openings: List[dict] = []
    ends = [Point(p) for w in walls for p in (w.a, w.b)]
    alias: Dict[str, str] = {}

    def resolve(wid: str) -> Wall:
        while wid in alias:
            wid = alias[wid]
        return by_id[wid]

    for _ga, _gb, t, wa, wb in gaps:
        # gap ends follow the snapped wall ends
        left, right = resolve(wa), by_id[wb]
        a, b = left.b, right.a
        gap = LineString([a, b])
        if _thin_fill(walls, a, b, t, tol):
            # a thinner wall on the same line fills the gap: a glazed bay or a
            # change of wall type, so the thick run stops at it (#793)
            continue
        junction = math.dist(a, b) <= tol or any(
            gap.distance(e) <= 0.6 * t and e.distance(Point(a)) > tol and e.distance(Point(b)) > tol
            for e in ends
        )
        wide = math.dist(a, b) > MAX_OPENING_M * k + tol
        if junction and wide:
            # too far to carry the wall through a junction; leave it to the joins
            continue
        if junction:
            # a wall ending against this one: the run continues through it
            left.b = right.b
            alias[wb] = left.id
            continue
        bridges.append(Bridge(a, b, t, (left.id, wb)))
        openings.append(
            {
                "walls": [left.id, wb],
                "a_m": to_m(a),
                "b_m": to_m(b),
                "width_m": round(math.dist(a, b) * m_per_pt, 3),
                **({"air_wall": True} if wide else {}),
                "_ab": (a, b, t),
            }
        )
        if wide:
            review.append(
                {
                    "kind": "air_wall",
                    "reason": (
                        f"{math.dist(a, b) * m_per_pt:.1f} m gap in a wall line closed with an air "
                        "wall; check it is a window, storefront or open edge"
                    ),
                    "a_m": to_m(a),
                    "b_m": to_m(b),
                }
            )
    walls = [w for w in walls if w.id not in alias]
    walls = _drop_thickenings(walls, {wid for br in bridges for wid in br.walls}, tol)

    pairs = [(w.a, w.b) for w in walls if math.dist(w.a, w.b) > tol] + [
        (br.a, br.b) for br in bridges
    ]
    pair_walls = [w for w in walls if math.dist(w.a, w.b) > tol]
    pair_ids = [[w.id] for w in pair_walls] + [list(br.walls) for br in bridges]
    joins, corners = _join_free_ends(
        pairs, [w.t for w in pair_walls] + [br.t for br in bridges], tol, WIDE_OPENING_M * k
    )
    spans = [(_get(t, "text"), tuple(_get(t, "bbox"))) for t in _get(sheet, "text")]
    near, far = [], []
    for c in corners:
        (far if math.dist(c[2], c[3]) > MAX_OPENING_M * k + tol else near).append(c)
    base = pairs + joins + [(a, b) for _i, _j, a, b in near]
    # a wall that stops more than a door width short of the wall ahead closes with an air
    # wall only where it then parts two labelled rooms: an open counter or half wall
    # between named spaces. Elsewhere it is an open area and stays one room.
    faces = polygonize_full(unary_union(_node_lines(base + [(a, b) for *_x, a, b in far], tol)))[0]
    keep = []
    for c in far:
        edge = LineString([c[2], c[3]])
        sides = [
            f
            for f in getattr(faces, "geoms", [])
            if f.boundary.intersection(edge).length > 0.5 * edge.length
        ]
        if len(sides) == 2 and all(_label_for(f, spans)[0] for f in sides):
            keep.append(c)
    for i, j, a, b in near + keep:
        t = pair_walls[i].t if i < len(pair_walls) else bridges[i - len(pair_walls)].t
        wide = math.dist(a, b) > MAX_OPENING_M * k + tol
        bridges.append(Bridge(a, b, t, (pair_ids[i][0], pair_ids[j][0])))
        openings.append(
            {
                "walls": [pair_ids[i][0], pair_ids[j][0]],
                "a_m": to_m(a),
                "b_m": to_m(b),
                "width_m": round(math.dist(a, b) * m_per_pt, 3),
                "beside_corner": True,
                **({"air_wall": True} if wide else {}),
                "_ab": (a, b, t),
            }
        )
        if wide:
            review.append(
                {
                    "kind": "air_wall",
                    "reason": (
                        f"wall ends {math.dist(a, b) * m_per_pt:.1f} m short of the wall ahead "
                        "between two labelled rooms; closed with an air wall, check it is an "
                        "open edge (counter, half wall) and not a missing wall"
                    ),
                    "a_m": to_m(a),
                    "b_m": to_m(b),
                }
            )
    carry = _ends_to_openings(
        [(w.a, w.b) for w in pair_walls],
        [w.t for w in pair_walls],
        joins + [(a, b) for *_x, a, b in near + keep],
        [(br.a, br.b, br.t) for br in bridges],
        tol,
    )
    lines = _node_lines(base + [(a, b) for *_x, a, b in keep] + carry, tol)
    noded = unary_union(lines)
    faces, _cuts, dangles, _invalid = polygonize_full(noded)
    for dg in getattr(dangles, "geoms", []):
        a, b = dg.coords[0], dg.coords[-1]
        review.append(
            {
                "kind": "unclosed_wall",
                "reason": "wall end connects to nothing; a room may be open here",
                "a_m": to_m(a),
                "b_m": to_m(b),
            }
        )

    wall_area = unary_union(
        [
            LineString([w.a, w.b]).buffer(w.t / 2, cap_style="square", join_style="mitre")
            for w in walls
        ]
        + [LineString([br.a, br.b]).buffer(br.t / 2, cap_style="flat") for br in bridges]
    )
    face_list, merged = _merge_slivers(
        list(getattr(faces, "geoms", [])),
        spans,
        [LineString([br.a, br.b]) for br in bridges],
        SLIVER_M2 / m_per_pt**2,
        tol,
    )
    rooms: List[Room] = []
    for face, folded in sorted(zip(face_list, merged), key=lambda fm: -fm[0].area):
        area = face.area * m_per_pt**2
        net = face.difference(wall_area)
        if net.geom_type == "MultiPolygon":
            net = max(net.geoms, key=lambda g: g.area)
        r = Room(
            id=f"R{len(rooms) + 1}",
            polygon_m=[to_m(p) for p in face.exterior.coords],
            holes_m=[[to_m(p) for p in ring.coords] for ring in face.interiors],
            net_polygon_m=[to_m(p) for p in net.exterior.coords] if not net.is_empty else [],
            area_m2=round(area, 3),
            net_area_m2=round(net.area * m_per_pt**2, 3) if not net.is_empty else 0.0,
            merged_m2=[round(a * m_per_pt**2, 3) for a in folded],
        )
        r.label, reasons = _label_for(face, spans)
        if area < MIN_ROOM_M2:
            reasons.append(f"face is {area:.2f} m2, smaller than a room")
        if reasons:
            r.needs_review = True
            r.reasons = reasons
            review.append({"kind": "room", "room": r.id, "reason": "; ".join(reasons)})
        rooms.append(r)

    wall_out = []
    for w in walls:
        wall_out.append(
            {
                "id": w.id,
                "a_m": to_m(w.a),
                "b_m": to_m(w.b),
                "thickness_m": round(w.t * m_per_pt, 3),
                "length_m": round(math.dist(w.a, w.b) * m_per_pt, 3),
                "source": w.source,
            }
        )
    # door swings drawn in a gap make it a door (#743): vector path, no
    # trained weights; a gap with no swing stays unclassified
    arcs = _arcs(sheet)
    doors = 0
    for op in openings:
        a, b, t = op.pop("_ab")
        if op.get("air_wall"):
            continue
        hit = _classify_door(arcs, a, b, t)
        if hit:
            swing, hinges = hit
            op.update(
                kind="door",
                swing=swing,
                hinges_m=[to_m(h) for h in hinges],
                door_confidence=DOOR_CONFIDENCE[swing],
            )
            doors += 1
    windows = _glazing_windows(bands, walls, k, tol, segs, arcs)
    for w, a, b, src, in_glass in windows:
        op = {
            "walls": [w.id],
            "a_m": to_m(a),
            "b_m": to_m(b),
            "width_m": round(math.dist(a, b) * m_per_pt, 3),
            "kind": "window",
            "source": src,
            "window_confidence": {
                "glazing_mullions": STOREFRONT_CONFIDENCE,
                "glazing_layer": LAYER_GLAZING_CONFIDENCE,
            }.get(src, WINDOW_CONFIDENCE),
        }
        if in_glass:
            # a door drawn inside the glazing (#793): its width is in the run
            op["doors_in_glazing"] = [
                {
                    "a_m": to_m(da),
                    "b_m": to_m(db),
                    "width_m": round(math.dist(da, db) * m_per_pt, 3),
                }
                for da, db in in_glass
            ]
        openings.append(op)
    thin = set(_thin_bands(walls, openings, tol, THIN_BAND_MIN_M * k))
    # a thin band whose faces are on a glazing CAD layer is glass, not a
    # question (#793): a window over the whole band instead of maybe_glazing
    layer_bands = {w.id for w in walls if w.id in thin and _glazing_layer_band(w, segs, tol)}
    for w in walls:
        if w.id in layer_bands:
            openings.append(
                {
                    "walls": [w.id],
                    "a_m": to_m(w.a),
                    "b_m": to_m(w.b),
                    "width_m": round(math.dist(w.a, w.b) * m_per_pt, 3),
                    "kind": "window",
                    "source": "glazing_layer_band",
                    "window_confidence": LAYER_GLAZING_CONFIDENCE,
                }
            )
    thin -= layer_bands
    # tags attach after every opening exists, so a storefront tag beside a
    # glazing-layer band labels that window instead of becoming a wall tag
    # that would add the same glass a second time (#793)
    tagged = _attach_tags(openings, spans, to_m)
    wall_tags = _wall_tags(wall_out, openings, spans, to_m)
    for wo in wall_out:
        if wo["id"] in thin:
            wo["maybe_glazing"] = True
            review.append(
                {
                    "kind": "maybe_glazing",
                    "wall": wo["id"],
                    "reason": (
                        f"{wo['length_m']:.1f} m wall {wo['thickness_m']:.2f} m thick runs on "
                        "from thicker walls at both ends with no opening drawn; it may be "
                        "storefront glazing drawn as a wall band"
                    ),
                    "a_m": wo["a_m"],
                    "b_m": wo["b_m"],
                }
            )
    stats = {
        "segments": len(segs),
        "bands": len(bands),
        "walls": len(walls),
        "openings": len(openings),
        "doors": doors,
        "windows": len(windows) + len(layer_bands),
        "layers": sorted({sg.layer for sg in segs if sg.layer}),
        "tagged": tagged,
        "wall_tags": len(wall_tags),
        "maybe_glazing": len(thin),
        "rooms": len(rooms),
        "wall_length_m": round(sum(x["length_m"] for x in wall_out), 2),
    }
    return PlanWalls(wall_out, openings, rooms, review, m_per_pt, stats, wall_tags)


# -------------------------------------------------------------- measurement


def compare_rooms(pred: Sequence[Sequence[Pt]], truth: Sequence[Sequence[Pt]], min_iou=0.5) -> dict:
    """Room count, per-room area error (matched by IoU) for a held-out sheet."""
    P = [Polygon(p) for p in pred]
    T = [Polygon(t) for t in truth]
    pairs = []
    for i, p in enumerate(P):
        for j, t in enumerate(T):
            inter = p.intersection(t).area
            if inter > 0:
                iou = inter / p.union(t).area
                if iou >= min_iou:
                    pairs.append((iou, i, j))
    pairs.sort(reverse=True)
    used_p, used_t, per = set(), set(), []
    for iou, i, j in pairs:
        if i in used_p or j in used_t:
            continue
        used_p.add(i)
        used_t.add(j)
        err = abs(P[i].area - T[j].area) / T[j].area
        per.append({"pred": i, "truth": j, "iou": round(iou, 4), "area_err": round(err, 4)})
    errs = sorted(x["area_err"] for x in per)
    return {
        "n_pred": len(P),
        "n_truth": len(T),
        "matched": len(per),
        "missed": len(T) - len(per),
        "extra": len(P) - len(per),
        "area_err_median": errs[len(errs) // 2] if errs else None,
        "area_err_max": errs[-1] if errs else None,
        "per_room": per,
    }


def wall_length_error(pred_m: float, truth_m: float) -> float:
    return abs(pred_m - truth_m) / truth_m if truth_m else float("nan")


# ------------------------------------------------------------------ folder


def walls_for_sheets(sheet_dir) -> Dict[str, dict]:
    """Walls and rooms for each takeoff sheet in an ingest folder.

    Uses ``scale.json`` (required for a sheet to be read) and, when present,
    ``sheet_index.json`` to keep only floor plans marked for takeoff. Writes
    ``walls_NNN.json`` beside each sheet.
    """
    d = Path(sheet_dir)
    scales = json.loads((d / "scale.json").read_text()) if (d / "scale.json").exists() else {}
    keep = None
    if (d / "sheet_index.json").exists():
        idx = json.loads((d / "sheet_index.json").read_text())
        keep = {s["file"] for s in idx["sheets"] if s.get("use_for_takeoff")}
    out = {}
    for p in sorted(d.glob("sheet_[0-9][0-9][0-9].json")):
        if keep is not None and p.name not in keep:
            continue
        sc = scales.get(p.name, {})
        res = extract_walls(json.loads(p.read_text()), sc.get("m_per_pt"))
        name = p.name.replace("sheet_", "walls_")
        (d / name).write_text(json.dumps(res.to_dict(), indent=2))
        out[p.name] = res.to_dict()
    return out
