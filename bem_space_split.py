"""Per-space envelope pieces for the single-storey gbXML writer (#765).

Before #765 every exterior wall went whole to one space, and the roof and the
ground slab went whole to the largest space, so the other spaces had no floor
area once imported into OpenStudio. These helpers cut the envelope along the
space boundaries: each wall piece, roof and slab belongs to the space it
encloses, and every boundary two spaces share becomes an interior wall.

Pure geometry on the plan polygons (metres); deterministic.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Polygon

# a space edge within this distance of a wall or of another space counts as on it
TOL_M = 0.05
# pieces shorter than this are noise from simplification, not a real boundary
MIN_PIECE_M = 0.1
# spaces must cover the footprint this closely for roof/slab to be cut per space
COVER_TOL = 0.005


def _poly(ring) -> Polygon:
    p = Polygon(ring)
    return p if p.is_valid else p.buffer(0)


def _lines(geom):
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if hasattr(geom, "geoms"):
        return [g for part in geom.geoms for g in _lines(part)]
    return []


def split_edge(p0, p1, spaces, fallback):
    """``[(t0, t1, space), ...]`` covering the wall p0 -> p1 by distance along it.

    One piece (the ``fallback`` space, i.e. the old assignment) unless at
    least two spaces each touch the wall over ``MIN_PIECE_M`` or more. Gaps
    and overlaps between the spaces are settled at their midpoints so the
    pieces always add up to the whole wall.
    """
    seg = LineString([p0, p1])
    L = seg.length
    ivals = []
    for sp in spaces:
        hit = seg.intersection(_poly(sp.polygon_m).buffer(TOL_M))
        for part in _lines(hit):
            ts = sorted(seg.project(_pt(c)) for c in part.coords)
            t0, t1 = ts[0], ts[-1]
            if t1 - t0 >= MIN_PIECE_M:
                ivals.append([t0, t1, sp])
    ivals.sort(key=lambda v: (v[0] + v[1]) / 2.0)
    merged = []
    for v in ivals:
        if merged and merged[-1][2] is v[2]:
            merged[-1][1] = max(merged[-1][1], v[1])
        else:
            merged.append(v)
    if len(merged) < 2:
        return [(0.0, L, fallback)]
    cuts = [(merged[k][1] + merged[k + 1][0]) / 2.0 for k in range(len(merged) - 1)]
    bounds = [0.0] + cuts + [L]
    return [(bounds[k], bounds[k + 1], merged[k][2]) for k in range(len(merged))]


def _pt(c):
    from shapely.geometry import Point

    return Point(c)


def keep_openings_whole(pieces, spans):
    """Move cuts out of openings: ``spans`` are (s0, s1) along the wall.

    A cut inside an opening moves to the opening's nearer jamb, so no window
    or door is split between two spaces. Pieces that shrink to nothing drop.
    """
    if len(pieces) < 2:
        return pieces
    bounds = [pieces[0][0]] + [p[1] for p in pieces]
    for k in range(1, len(bounds) - 1):
        c = bounds[k]
        for s0, s1 in spans:
            if s0 < c < s1:
                c = s0 if c - s0 <= s1 - c else s1
        bounds[k] = c
    out = []
    for k, (_t0, _t1, sp) in enumerate(pieces):
        a, b = bounds[k], bounds[k + 1]
        if b - a > 1e-6:
            out.append((a, b, sp))
    return out


def covers_footprint(ring, spaces) -> bool:
    """Do the spaces tile the footprint (no gap, no overlap) within COVER_TOL?"""
    from shapely.ops import unary_union

    foot = _poly(ring)
    polys = [_poly(sp.polygon_m) for sp in spaces]
    if not polys or foot.area <= 0:
        return False
    union = unary_union(polys)
    total = sum(p.area for p in polys)
    return (
        abs(union.area - foot.area) <= COVER_TOL * foot.area
        and abs(total - foot.area) <= COVER_TOL * foot.area
        and foot.symmetric_difference(union).area <= COVER_TOL * foot.area
    )


def shared_walls(spaces, ring, skip_pairs=frozenset()):
    """``[(space a, space b, q0, q1), ...]``: straight boundaries a and b share.

    Wound so ``b`` lies to the right of q0 -> q1, which is the side the
    writer's (q0, q1, top) loop faces: the normal points from a into b.
    Boundaries on the exterior ring are walls already and are left out.
    """
    outer = _poly(ring).boundary
    out = []
    for i, a in enumerate(spaces):
        pa = _poly(a.polygon_m)
        for b in spaces[i + 1 :]:
            if frozenset((a.sid, b.sid)) in skip_pairs:
                continue
            pb = _poly(b.polygon_m)
            if not pa.buffer(TOL_M).intersects(pb):
                continue
            shared = pa.boundary.intersection(pb.buffer(TOL_M))
            for line in _lines(shared):
                cs = list(line.simplify(0.01).coords)
                for q0, q1 in zip(cs, cs[1:]):
                    if math.dist(q0, q1) < MIN_PIECE_M:
                        continue
                    mid = _pt(((q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0))
                    if outer.distance(mid) <= TOL_M:
                        continue  # on the exterior ring: an exterior wall already
                    out.append(_wind(a, b, pb, q0, q1))
    return out


def _wind(a, b, pb, q0, q1):
    L = math.dist(q0, q1)
    nx, ny = (q1[1] - q0[1]) / L, -(q1[0] - q0[0]) / L  # right of q0 -> q1
    mx, my = (q0[0] + q1[0]) / 2.0, (q0[1] + q1[1]) / 2.0
    if not pb.contains(_pt((mx + nx * 0.2, my + ny * 0.2))):
        q0, q1 = q1, q0
    return (a, b, tuple(q0), tuple(q1))
