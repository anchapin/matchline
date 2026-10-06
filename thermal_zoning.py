"""ASHRAE 90.1 Appendix G perimeter/core thermal blocks (#630).

G3.1.1: where the drawings define no HVAC zones, each floor plate is split
into perimeter zones 15 ft (4.572 m) deep, one per orientation of the
exterior walls, plus a core. A space within 15 ft of walls facing more than
one orientation is "divided proportionately"; here the corner is split along
the angle bisector, so every point of the perimeter band belongs to the wall
it is closest to across the corner.

All coordinates are plan metres in the BEM frame (x east, y north) and the
ring is CCW. ``perimeter_core`` and ``assign_spaces`` only return zone
polygons and each space's overlap with them. ``split_spaces`` and
``appendix_g_split`` (#638) act on Alex's 2026-10-06 decision: a room a
block line cuts through is split into one space per block, and the pieces
are joined by air walls so the energy model exchanges heat between them.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, unary_union
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


AIR_WALL_MIN_M = 1e-4  # shared-edge fragments shorter than this are not walls
_SNAP_M = 1e-6  # pieces of one room share block lines to this tolerance


@dataclass
class AirWall:
    """A virtual boundary between two pieces of one split room (#638).

    ``p0 -> p1`` is wound so ``space_ids[1]`` lies to its right: the surface
    normal points from the first space into the second.
    """

    id: str
    space_ids: Tuple[str, str]
    p0: Tuple[float, float]
    p1: Tuple[float, float]

    @property
    def length_m(self) -> float:
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1])


@dataclass
class SplitResult:
    spaces: list  # BEMSpace, split pieces in place of their parent
    air_walls: List[AirWall]
    zones: List[Tuple[str, List[str]]]  # block id -> space ids, in block order
    split_from: Dict[str, str]  # piece sid -> parent sid
    notes: List[str] = field(default_factory=list)


def _ring(p: Polygon) -> list:
    return [(float(x), float(y)) for x, y in list(orient(p, 1.0).exterior.coords)[:-1]]


def _segments(g) -> list:
    """Straight 2-point segments of the linear parts of ``g``."""
    lines = []
    stack = [g]
    while stack:
        part = stack.pop()
        if part.is_empty:
            continue
        if part.geom_type == "LineString":
            if part.length > 0:
                lines.append(part)
        elif hasattr(part, "geoms"):
            stack.extend(part.geoms)
    if not lines:
        return []
    merged = linemerge(lines)
    merged = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    merged.sort(key=lambda ln: tuple(round(c, 9) for c in ln.coords[0]))
    out = []
    for ln in merged:
        cs = list(ln.coords)
        for a, b in zip(cs, cs[1:]):
            if math.hypot(b[0] - a[0], b[1] - a[1]) >= AIR_WALL_MIN_M:
                out.append(((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))))
    return out


def split_spaces(blocks: Sequence[ThermalBlock], spaces: Sequence) -> SplitResult:
    """Split every space a block line cuts through into one space per block.

    A space wholly inside one block is returned unchanged (same object, same
    id). A split space becomes pieces ``<sid>-<block id>`` (``-1``, ``-2``
    when one block holds several disjoint pieces), each a dataclass copy of
    the parent: area, volume and lighting power by area share, everything
    else (name, number, wall U, provenance) copied, ``split_from`` set to the
    parent id. Pieces tile the parent; a space that would not tile (it
    reaches outside the plate, or a piece has a hole) is left whole with a
    note rather than losing area. Shared edges between pieces of one parent
    become air walls.
    """
    out_spaces: list = []
    air: List[AirWall] = []
    members: Dict[str, List[str]] = {b.id: [] for b in blocks}
    split_from: Dict[str, str] = {}
    notes: List[str] = []
    shares = {z.sid: z for z in assign_spaces(blocks, spaces)}
    for sp in spaces:
        poly = make_valid(Polygon([(x, y) for x, y, *_ in sp.polygon_m]))
        a = poly.area
        pieces = []
        for b in blocks if a > 0 else []:
            parts = [q for q in _polys(poly.intersection(b.geom)) if q.area >= MIN_ZONE_M2]
            parts.sort(key=lambda q: (round(q.centroid.x, 6), round(q.centroid.y, 6)))
            for k, q in enumerate(parts):
                suffix = f"-{k + 1}" if len(parts) > 1 else ""
                pieces.append((b, f"{sp.sid}-{b.id}{suffix}", q))
        maj = shares[sp.sid].majority
        if len(pieces) <= 1:
            out_spaces.append(sp)
            if maj is not None:
                members[maj].append(sp.sid)
            continue
        total = sum(q.area for _, _, q in pieces)
        why = None
        if abs(total - a) > AREA_TOL_REL * max(a, 1.0):
            why = f"pieces cover {total:.6f} of {a:.6f} m2 (reaches outside the plate)"
        elif any(q.interiors for _, _, q in pieces):
            why = "a piece would have a hole"
        if why:
            notes.append(f"{sp.sid}: not split at Appendix G block lines, {why}")
            out_spaces.append(sp)
            if maj is not None:
                members[maj].append(sp.sid)
            continue
        ident = getattr(sp, "identity", None)
        made = []
        for b, psid, q in pieces:
            f = q.area / total
            new_ident = None
            if ident and ident.get("id"):
                new_ident = dict(
                    ident, id=f"{ident['id']}{psid[len(sp.sid) :]}", split_from=ident["id"]
                )
            piece = dataclasses.replace(
                sp,
                sid=psid,
                polygon_m=_ring(q),
                area_m2=sp.area_m2 * f,
                volume_m3=sp.volume_m3 * f,
                lighting_w=sp.lighting_w * f,
                identity=new_ident,
                split_from=sp.sid,
            )
            made.append((piece, q))
            members[b.id].append(psid)
            split_from[psid] = sp.sid
        out_spaces.extend(p for p, _ in made)
        k = 0
        for i in range(len(made)):
            for j in range(i + 1, len(made)):
                (si, qi), (sj, qj) = made[i], made[j]
                shared = qi.boundary.intersection(qj.buffer(_SNAP_M, join_style=2))
                for p0, p1 in _segments(shared):
                    # keep the normal pointing from piece i into piece j:
                    # probe just right of the midpoint, swap if it is not in j
                    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
                    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                    e = min(1e-3, L / 4)
                    probe = Point(mx + e * (p1[1] - p0[1]) / L, my - e * (p1[0] - p0[0]) / L)
                    if not qj.buffer(_SNAP_M).contains(probe):
                        p0, p1 = p1, p0
                    k += 1
                    air.append(AirWall(f"{sp.sid}-air-{k}", (si.sid, sj.sid), p0, p1))
    zones = [(b.id, members[b.id]) for b in blocks if members[b.id]]
    return SplitResult(out_spaces, air, zones, split_from, notes)


def appendix_g_split(model, depth_m: float = PERIMETER_DEPTH_M):
    """A copy of a single-storey BEMModel with rooms split at block lines.

    Returns ``(new_model, SplitResult)``. The new model carries the pieces,
    ``thermal_zones`` (one per block) and ``air_walls``; wall openings owned
    by a split room move to the piece that owns that room's exterior wall
    on the opening's facade (or any of its exterior walls when the facade is
    unknown). The input model is not modified.
    """
    from bem_helpers import _edge_spaces, _wall_edges

    blocks = perimeter_core(model.ring_m, depth_m=depth_m)
    res = split_spaces(blocks, model.spaces)
    edges = _wall_edges(model.ring_m)
    owners = _edge_spaces(edges, res.spaces)
    facades = list(getattr(model, "ring_facades", None) or [])
    by_parent: Dict[str, Dict[str, float]] = {}
    by_parent_facade: Dict[Tuple[str, str], Dict[str, float]] = {}
    for i, sid in enumerate(owners):
        parent = res.split_from.get(sid)
        if parent is None:
            continue
        (p0, p1) = edges[i]
        ln = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        by_parent.setdefault(parent, {}).setdefault(sid, 0.0)
        by_parent[parent][sid] += ln
        if i < len(facades) and facades[i]:
            d = by_parent_facade.setdefault((parent, facades[i]), {})
            d[sid] = d.get(sid, 0.0) + ln

    def _best(d):
        return max(sorted(d), key=lambda s: d[s]) if d else ""

    split_parents = set(res.split_from.values())
    openings = []
    for u in model.openings:
        if u.category != "skylight" and u.space_sid in split_parents:
            d = by_parent_facade.get((u.space_sid, u.host_facade)) or by_parent.get(u.space_sid)
            u = dataclasses.replace(u, space_sid=_best(d or {}))
        elif u.category == "skylight" and u.space_sid in split_parents:
            # over the room: the largest piece keeps it
            pieces = [s for s in res.spaces if res.split_from.get(s.sid) == u.space_sid]
            u = dataclasses.replace(u, space_sid=max(pieces, key=lambda s: (s.area_m2, s.sid)).sid)
        openings.append(u)
    n_split = len(split_parents)
    note = (
        f"Appendix G zoning: {len(res.zones)} thermal zones; {n_split} room(s) split at block "
        f"lines into {len(res.split_from)} pieces joined by {len(res.air_walls)} air wall(s)."
    )
    new = dataclasses.replace(
        model,
        spaces=res.spaces,
        openings=openings,
        thermal_zones=res.zones,
        air_walls=res.air_walls,
        notes=list(model.notes) + [note] + res.notes,
    )
    return new, res
