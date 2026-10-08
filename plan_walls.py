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
within reach, closing L and T junctions.

Nothing is dropped silently: wall ends that connect to nothing, faces too
small to be rooms, and faces holding two different room numbers go to
``review``. The raster fallback for scanned sheets, label-less face
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
ANGLE_TOL_DEG = 1.0
MIN_ROOM_M2 = 1.0
RECT_FILL = 0.90  # filled shape area / its rotated bounding rectangle

Pt = Tuple[float, float]


# --------------------------------------------------------------- primitives


@dataclass
class Seg:
    a: Pt
    b: Pt
    src: int  # primitive index

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
        cur = start = None
        for kind, pts in _get(p, "segments"):
            if kind == "M":
                cur = start = tuple(pts[0])
            elif kind == "L" and cur is not None:
                nxt = tuple(pts[0])
                out.append(Seg(cur, nxt, i))
                cur = nxt
            elif kind == "C" and cur is not None:
                cur = tuple(pts[-1])  # curves (door swings) are not wall faces
            elif kind == "Z" and cur is not None and start is not None:
                if cur != start:
                    out.append(Seg(cur, start, i))
                cur = start
    return out


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
            if abs(a.u0 - b0) > tol or abs(a.u1 - b1) > tol:
                continue
            if abs(abs(a.rho - rb) - (a.t + b.t) / 2) > tol:  # same side: not a shared face
                continue
            lo = min(a.rho - a.t / 2, rb - b.t / 2)
            hi = max(a.rho + a.t / 2, rb + b.t / 2)
            if hi - lo > t_max:
                continue
            merged = Band(
                a.theta, (lo + hi) / 2, min(a.u0, b0), max(a.u1, b1), hi - lo, "line_pair",
                tuple(sorted(set(a.segs) | set(b.segs))),
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


def _join_free_ends(pairs: List[Tuple[Pt, Pt]], ts: List[float], tol: float) -> List[Tuple[Pt, Pt]]:
    """Connectors from a free wall end to a wall it stops against (#740).

    A wall end that touches nothing but lies within half of both thicknesses of
    another wall's centreline is drawn as meeting it: a T-junction whose end
    stopped at the other wall's face, or a wall that changes thickness or
    steps sideways along its run. The connector runs from the end to the
    nearest point on that centreline.
    """
    if not pairs:
        return []
    lines = [LineString(p) for p in pairs]
    tree = STRtree(lines)
    t_big = max(ts)
    out: List[Tuple[Pt, Pt]] = []
    for i, (a, b) in enumerate(pairs):
        for p in (a, b):
            pt = Point(p)
            near = [int(j) for j in tree.query(pt.buffer(t_big + tol)) if int(j) != i]
            if any(lines[j].distance(pt) <= tol for j in near):
                continue  # already meets another wall
            best = None
            for j in near:
                d = lines[j].distance(pt)
                if d <= (ts[i] + ts[j]) / 2 + tol and (best is None or d < best[0]):
                    best = (d, j)
            if best:
                q = lines[best[1]].interpolate(lines[best[1]].project(pt))
                out.append((p, (q.x, q.y)))
    return out


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


@dataclass
class PlanWalls:
    walls: List[dict]
    openings: List[dict]
    rooms: List[Room]
    review: List[dict]
    m_per_pt: Optional[float]
    stats: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "m_per_pt": self.m_per_pt,
            "walls": self.walls,
            "openings": self.openings,
            "rooms": [asdict(r) for r in self.rooms],
            "review": self.review,
            "stats": self.stats,
        }


_ROOM_NO = re.compile(r"\b[A-Z]?\d{2,4}[A-Z]?\b")


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

    walls, gaps = _runs(bands, MAX_OPENING_M * k, tol)
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
        junction = math.dist(a, b) <= tol or any(
            gap.distance(e) <= 0.6 * t and e.distance(Point(a)) > tol and e.distance(Point(b)) > tol
            for e in ends
        )
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
            }
        )
    walls = [w for w in walls if w.id not in alias]

    pairs = [(w.a, w.b) for w in walls if math.dist(w.a, w.b) > tol] + [
        (br.a, br.b) for br in bridges
    ]
    joins = _join_free_ends(
        pairs, [w.t for w in walls if math.dist(w.a, w.b) > tol] + [br.t for br in bridges], tol
    )
    lines = _node_lines(pairs + joins, tol)
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
    spans = [(_get(t, "text"), tuple(_get(t, "bbox"))) for t in _get(sheet, "text")]
    rooms: List[Room] = []
    for face in sorted(getattr(faces, "geoms", []), key=lambda f: -f.area):
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
    stats = {
        "segments": len(segs),
        "bands": len(bands),
        "walls": len(walls),
        "openings": len(openings),
        "rooms": len(rooms),
        "wall_length_m": round(sum(x["length_m"] for x in wall_out), 2),
    }
    return PlanWalls(wall_out, openings, rooms, review, m_per_pt, stats)


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
