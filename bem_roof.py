"""Sloped-roof geometry for the BEM writers (roadmap item 2, #618).

Everything here works in the BEM frame: x east, y north, z metres above the
level's floor. Roof planes come in as ``BEMRoof`` (3-D outline, tilt,
azimuth). The writers use three things:

- ``roof_pieces``: each roof plane clipped to a plan polygon (the envelope
  ring, or one space), lifted back onto its plane and wound CCW from above
  so its normal points out of the shell.
- ``wall_top``: the top edge of a vertical wall under the roof. A wall
  under a gable end gets a peaked top; a wall under an eave gets a level
  top at eave height. Breakpoints sit wherever a roof-plane boundary
  crosses the wall, so wall tops and roof pieces share every vertex and the
  space shell closes.
- ``space_shell`` / ``shell_volume``: floor + walls + roof pieces as
  PolyLoops, and the enclosed volume from the divergence theorem.

Roof planes are top-of-roof faces (what ``ifc_roof_planes`` reads), so the
shell runs to the outside of the roof. Overhangs past the plan polygon are
clipped off here and reported; they are not envelope.
"""

from __future__ import annotations

import math

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

FLAT_TILT_DEG = 0.5  # same as roof_geometry.FLAT_TILT_DEG
ON_TOL_M = 1e-6  # point-on-plan tolerance
MIN_PIECE_M2 = 1e-6
KEY_DIGITS = 6


def is_sloped(roofs) -> bool:
    """True when at least one roof plane is tilted.

    All-flat roof planes keep the flat-roof export at wall height unchanged:
    a flat roof plane adds nothing a flat gbXML roof does not already say.
    """
    return any((r.tilt_deg or 0.0) > FLAT_TILT_DEG for r in roofs or [])


def _plane(r):
    """(n, d) with n.p = d for the roof plane, n pointing up."""
    v = r.vertices
    nx = ny = nz = 0.0
    k = len(v)
    for i in range(k):
        x0, y0, z0 = v[i]
        x1, y1, z1 = v[(i + 1) % k]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    if nz < 0:
        nx, ny, nz = -nx, -ny, -nz
    L = math.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / L, ny / L, nz / L
    x, y, z = v[0]
    return (nx, ny, nz), nx * x + ny * y + nz * z


def plane_z(r, x: float, y: float) -> float:
    (nx, ny, nz), d = _plane(r)
    return (d - nx * x - ny * y) / nz


def _plan(r) -> Polygon:
    p = Polygon([(x, y) for x, y, _ in r.vertices])
    return p if p.is_valid else p.buffer(0)


def roof_z(roofs, x: float, y: float):
    """Roof height over (x, y): the highest plane whose plan holds it, or None."""
    pt = Point(x, y)
    best = None
    for r in roofs:
        if _plan(r).buffer(ON_TOL_M).contains(pt):
            z = plane_z(r, x, y)
            best = z if best is None else max(best, z)
    return best


def _ccw(ring):
    s = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[(i + 1) % len(ring)][0], ring[(i + 1) % len(ring)][1]
        s += x0 * y1 - x1 * y0
    return list(ring) if s > 0 else list(reversed(ring))


def roof_pieces(poly2d, roofs):
    """Clip every roof plane to ``poly2d`` (CCW plan ring).

    Returns ``(pieces, overhang_m2)``; each piece is ``(roof, loop3d)`` with
    the loop CCW from above (outward normal up). ``overhang_m2`` is the
    plan area of roof outside the polygon, dropped from the envelope.
    """
    target = Polygon(poly2d)
    if not target.is_valid:
        target = target.buffer(0)
    pieces = []
    overhang = 0.0
    for r in roofs:
        plan = _plan(r)
        overhang += plan.difference(target).area
        clip = plan.intersection(target)
        parts = getattr(clip, "geoms", [clip])
        for g in parts:
            if not isinstance(g, Polygon) or g.area < MIN_PIECE_M2:
                continue
            ring2 = _ccw(list(g.exterior.coords)[:-1])
            pieces.append((r, [(x, y, plane_z(r, x, y)) for x, y in ring2]))
    return pieces, overhang


def wall_top(p0, p1, roofs, h: float):
    """Top edge of the wall p0 -> p1 under ``roofs``.

    Returns ``(points, uncovered)``: ``points`` is a list of (x, y, z) from
    p0 to p1 with a breakpoint wherever a roof-plane boundary crosses the
    wall; ``uncovered`` is True when some stretch had no roof over it and
    fell back to wall height ``h``.
    """
    edge = LineString([p0, p1])
    L = edge.length
    ts = {0.0, 1.0}
    for r in roofs:
        hit = edge.intersection(_plan(r).exterior)
        for g in getattr(hit, "geoms", [hit]):
            if g.is_empty:
                continue
            for c in g.coords:
                ts.add(round(edge.project(Point(c)) / L, 9) if L > 0 else 0.0)
    pts = []
    uncovered = False
    for t in sorted(ts):
        x = p0[0] + t * (p1[0] - p0[0])
        y = p0[1] + t * (p1[1] - p0[1])
        z = roof_z(roofs, x, y)
        if z is None:
            z, uncovered = h, True
        if pts and abs(pts[-1][0] - x) < 1e-9 and abs(pts[-1][1] - y) < 1e-9:
            continue
        pts.append((x, y, z))
    # drop collinear interior points so a level top stays four corners
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        ab = math.hypot(b[0] - a[0], b[1] - a[1])
        ac = math.hypot(c[0] - a[0], c[1] - a[1])
        if ac > 0 and abs((b[2] - a[2]) - (c[2] - a[2]) * ab / ac) < 1e-9:
            continue
        out.append(b)
    out.append(pts[-1])
    return out, uncovered


def space_shell(poly2d, roofs, h: float):
    """Closed shell of one space: floor, walls with roof-following tops, roof.

    ``poly2d`` is CCW from above. Returns ``(loops, notes)``; every loop is
    a list of (x, y, z) wound with its normal pointing out of the space.
    """
    ring = _ccw(poly2d)
    loops = [[(x, y, 0.0) for x, y in reversed(ring)]]
    notes = []
    n = len(ring)
    for i in range(n):
        p0, p1 = ring[i], ring[(i + 1) % n]
        top, unc = wall_top(p0, p1, roofs, h)
        if unc:
            notes.append(f"edge {i}: part of the wall has no roof plane over it; top held at {h} m")
        loops.append([(p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0)] + list(reversed(top)))
    pieces, _ = roof_pieces(ring, roofs)
    covered = unary_union([Polygon([(x, y) for x, y, _ in lp]) for _, lp in pieces])
    gap = Polygon(ring).difference(covered).area if pieces else Polygon(ring).area
    if gap > 1e-4:
        notes.append(f"{gap:.3f} m2 of plan has no roof plane over it; shell left open there")
    for _, lp in pieces:
        # the roof piece must carry every wall-top breakpoint on its boundary
        loops.append(_with_wall_points(lp, loops[1 : n + 1]))
    return loops, notes


def _with_wall_points(lp, walls):
    """Insert wall-top vertices lying on the piece's edges (T-junctions)."""
    tops = []
    for w in walls:
        tops.extend(w[2:])
    out = []
    k = len(lp)
    for i in range(k):
        a, b = lp[i], lp[(i + 1) % k]
        out.append(a)
        seg = LineString([(a[0], a[1]), (b[0], b[1])])
        mids = []
        for q in tops:
            pq = Point(q[0], q[1])
            if seg.distance(pq) < 1e-7:
                t = seg.project(pq) / seg.length if seg.length > 0 else 0.0
                if 1e-7 < t < 1 - 1e-7:
                    mids.append((t, (q[0], q[1], a[2] + t * (b[2] - a[2]))))
        for _, q in sorted(mids):
            if not any(math.dist(q, o) < 1e-7 for o in out):
                out.append(q)
    return out


def shell_volume(loops) -> float:
    """Enclosed volume of a closed, outward-wound shell (divergence theorem)."""
    v = 0.0
    for lp in loops:
        a = lp[0]
        for i in range(1, len(lp) - 1):
            b, c = lp[i], lp[i + 1]
            v += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
    return v


def open_edges(loops):
    """Directed edges with no opposite partner. Empty for a closed shell."""

    def key(p):
        return tuple(round(c, KEY_DIGITS) for c in p)

    edges = {}
    for lp in loops:
        for i in range(len(lp)):
            a, b = key(lp[i]), key(lp[(i + 1) % len(lp)])
            if a != b:
                edges[(a, b)] = edges.get((a, b), 0) + 1
    return [e for e, c in edges.items() if edges.get((e[1], e[0]), 0) != c]
