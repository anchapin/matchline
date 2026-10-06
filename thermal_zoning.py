"""ASHRAE 90.1 Appendix G perimeter/core thermal blocks (#630).

G3.1.1: where the drawings define no HVAC zones, each floor plate is split
into perimeter zones 15 ft (4.572 m) deep, one per orientation of the
exterior walls, plus a core. A space within 15 ft of walls facing more than
one orientation is "divided proportionately"; here the corner is split along
the angle bisector, so every point of the perimeter band belongs to the wall
it is closest to across the corner.

All coordinates are plan metres in the BEM frame (x east, y north) and the
ring is CCW. Nothing here changes an export: it returns zone polygons and
each space's overlap with them. How exports use the blocks (split rooms on
zone lines vs assign whole rooms) is a separate decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

PERIMETER_DEPTH_M = 4.572  # 15 ft, Appendix G G3.1.1
AREA_TOL_REL = 1e-6  # zones must tile the plate to this relative tolerance
MIN_ZONE_M2 = 1e-6  # slivers below this are dropped from a zone
ORIENTATIONS = ("north", "east", "south", "west")


class ZoningError(ValueError):
    """The plate could not be tiled into perimeter and core zones."""


@dataclass
class ThermalBlock:
    id: str  # e.g. "L1-perimeter-north", "L1-core"
    kind: str  # "perimeter" | "core"
    orientation: Optional[str]  # compass bucket for perimeter, None for core
    polygon: list  # exterior ring [(x, y), ...]; holes dropped from listing
    area_m2: float
    geom: object = field(repr=False, default=None)  # shapely geometry


@dataclass
class SpaceZoning:
    sid: str
    fractions: Dict[str, float]  # block id -> share of the space's area
    majority: Optional[str]  # block id with the largest share


def outward_azimuth(p0: Sequence[float], p1: Sequence[float]) -> float:
    """Compass azimuth (deg clockwise from north) of a CCW edge's outward normal."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    # CCW ring: interior on the left, so outward normal is (dy, -dx)
    nx, ny = dy, -dx
    return math.degrees(math.atan2(nx, ny)) % 360.0


def orientation(azimuth_deg: float) -> str:
    """North 315 to <45, east 45 to <135, south 135 to <225, west 225 to <315."""
    return ORIENTATIONS[int(((azimuth_deg + 45.0) % 360.0) // 90.0)]


def _ccw(ring: Sequence[Sequence[float]]) -> List[Tuple[float, float]]:
    pts = [(float(x), float(y)) for x, y, *_ in ring]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if Polygon(pts).exterior.is_ccw:
        return pts
    return pts[::-1]


def _inward_normal(p0, p1):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    n = math.hypot(dx, dy)
    return (-dy / n, dx / n)


def _miter(na, nb):
    """Point offset from a vertex at unit distance from both edge lines."""
    d = 1.0 + na[0] * nb[0] + na[1] * nb[1]
    if d < 1e-9:  # a hairpin spike; fall back to the first edge's normal
        return na
    return ((na[0] + nb[0]) / d, (na[1] + nb[1]) / d)


def _half(g, c, centre, big):
    """Polygon standing in for the half-plane {p : g . p < c}; None if g ~ 0."""
    gn = math.hypot(g[0], g[1])
    if gn < 1e-12:
        return None
    ux, uy = g[0] / gn, g[1] / gn
    t = c / gn - (ux * centre[0] + uy * centre[1])
    fx, fy = centre[0] + t * ux, centre[1] + t * uy
    vx, vy = -uy, ux
    return Polygon(
        [
            (fx + big * vx, fy + big * vy),
            (fx - big * vx, fy - big * vy),
            (fx - big * vx - big * ux, fy - big * vy - big * uy),
            (fx + big * vx - big * ux, fy + big * vy - big * uy),
        ]
    )


def _polys(g) -> list:
    g = make_valid(g)
    if g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon" and p.area > 0]


def perimeter_core(
    ring: Sequence[Sequence[float]],
    depth_m: float = PERIMETER_DEPTH_M,
    prefix: str = "",
) -> List[ThermalBlock]:
    """Split one floor plate into Appendix G perimeter blocks plus a core.

    Each edge claims the wedge between it and the angle bisectors at its two
    ends, cut at ``depth_m``; wedges are clipped to the perimeter band (the
    plate minus its inward mitred offset). Any band left unclaimed, which
    only happens around short edges whose bisectors cross early, goes to the
    nearest edge. Blocks are merged per orientation; the core is whatever is
    deeper than ``depth_m``. Raises ZoningError if the blocks do not tile
    the plate.
    """
    pts = _ccw(ring)
    plate = make_valid(Polygon(pts))
    if plate.is_empty or plate.area <= 0:
        raise ZoningError("empty floor plate")
    core = plate.buffer(-depth_m, join_style="mitre", mitre_limit=1e6)
    band = plate.difference(core)
    n = len(pts)
    normals = [_inward_normal(pts[i], pts[(i + 1) % n]) for i in range(n)]
    miters = [_miter(normals[i - 1], normals[i]) for i in range(n)]
    # bounding box big enough to stand in for an unbounded half-plane
    minx, miny, maxx, maxy = plate.bounds
    big = 4.0 * (max(maxx - minx, maxy - miny) + depth_m)
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    wedges = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        ma, mb = miters[i], miters[(i + 1) % n]
        # both bisector rays are parameterised by distance from edge i's line,
        # so they meet (if at all) at one shared depth s
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        ex, ey = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        closing = (ma[0] - mb[0]) * ex + (ma[1] - mb[1]) * ey
        s_meet = L / closing if closing > 1e-12 else math.inf
        if s_meet < depth_m:
            w = Polygon([a, b, (a[0] + s_meet * ma[0], a[1] + s_meet * ma[1])])
        else:
            w = Polygon(
                [
                    a,
                    b,
                    (b[0] + depth_m * mb[0], b[1] + depth_m * mb[1]),
                    (a[0] + depth_m * ma[0], a[1] + depth_m * ma[1]),
                ]
            )
        wedges.append(unary_union(_polys(w)) if _polys(w) else Polygon())
    by_edge: List[object] = []
    claimed = None
    for i in range(n):
        piece = wedges[i]
        if piece.is_empty:
            by_edge.append(piece)
            continue
        ni, ai = normals[i], pts[i]
        for j in range(n):
            if j == i or wedges[j].is_empty or not piece.intersects(wedges[j]):
                continue
            nj, aj = normals[j], pts[j]
            # where wedges i and j overlap, the overlap goes to the edge whose
            # line is nearer. Only the overlap: a point outside wedge j (round
            # a reflex corner, past the end of edge j) is none of edge j's
            # business even if its line is nearer
            closer_j = _half(
                (nj[0] - ni[0], nj[1] - ni[1]),
                nj[0] * aj[0] + nj[1] * aj[1] - ni[0] * ai[0] - ni[1] * ai[1],
                (cx, cy),
                big,
            )
            if closer_j is None:
                continue  # parallel, same facing: order below settles it
            piece = piece.difference(closer_j.intersection(wedges[j]))
            if piece.is_empty:
                break
        if not piece.is_empty:
            piece = piece.intersection(band)
        if claimed is not None and not piece.is_empty:
            piece = piece.difference(claimed)
        by_edge.append(piece)
        if not piece.is_empty:
            claimed = piece if claimed is None else claimed.union(piece)
    left = band if claimed is None else band.difference(claimed)
    for p in _polys(left):
        if p.area < MIN_ZONE_M2:
            continue
        c = p.representative_point()
        k = min(range(n), key=lambda i: LineString([pts[i], pts[(i + 1) % n]]).distance(c))
        by_edge[k] = by_edge[k].union(p)
    groups: Dict[str, list] = {}
    for i in range(n):
        if by_edge[i].is_empty:
            continue
        o = orientation(outward_azimuth(pts[i], pts[(i + 1) % n]))
        groups.setdefault(o, []).append(by_edge[i])
    blocks: List[ThermalBlock] = []
    for o in ORIENTATIONS:
        if o not in groups:
            continue
        g = unary_union(groups[o])
        if g.area < MIN_ZONE_M2:
            continue
        blocks.append(_block(f"{prefix}perimeter-{o}", "perimeter", o, g))
    if core.area >= MIN_ZONE_M2:
        blocks.append(_block(f"{prefix}core", "core", None, core))
    total = sum(b.area_m2 for b in blocks)
    if abs(total - plate.area) > AREA_TOL_REL * max(plate.area, 1.0):
        raise ZoningError(f"blocks cover {total:.6f} m2 of a {plate.area:.6f} m2 plate")
    return blocks


def _block(bid, kind, o, g) -> ThermalBlock:
    largest = max(_polys(g), key=lambda p: p.area)
    return ThermalBlock(
        id=bid,
        kind=kind,
        orientation=o,
        polygon=[(round(x, 6), round(y, 6)) for x, y in list(largest.exterior.coords)[:-1]],
        area_m2=g.area,
        geom=g,
    )


def assign_spaces(blocks: Sequence[ThermalBlock], spaces: Sequence) -> List[SpaceZoning]:
    """Each space's share of its area in every block, and its majority block.

    ``spaces`` are anything with ``sid`` and ``polygon_m`` (BEMSpace works).
    Shares are of the space's own area, so they sum to 1 for a space wholly
    inside the plate. Ties go to the block listed first.
    """
    out = []
    for sp in spaces:
        poly = make_valid(Polygon([(x, y) for x, y, *_ in sp.polygon_m]))
        a = poly.area
        fr = {}
        if a > 0:
            for b in blocks:
                ov = poly.intersection(b.geom).area / a
                if ov > MIN_ZONE_M2:
                    fr[b.id] = ov
        maj = max(fr, key=lambda k: (fr[k], -list(fr).index(k))) if fr else None
        out.append(SpaceZoning(sid=sp.sid, fractions=fr, majority=maj))
    return out


def appendix_g_zoning(model, depth_m: float = PERIMETER_DEPTH_M):
    """Blocks and space shares for a single-storey BEMModel's ring."""
    blocks = perimeter_core(model.ring_m, depth_m=depth_m)
    return blocks, assign_spaces(blocks, model.spaces)
