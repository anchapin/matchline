"""Wall-less borders between spaces on IFC import (#582).

Open-plan floors split into several IfcSpaces have no wall between them, so
nothing in the envelope says the spaces touch. Two spaces on one level whose
edges run parallel within MAX_SEPARATOR_GAP_M and overlap along their length
share a border; the parts of its midline not inside an IFC wall footprint are
recorded as ``SpaceAdjacency(boundary="virtual")``. A border meeting a wall
that crosses it (an exterior wall at its end) stops at that wall's face. A virtual border adds no
wall area and no U-value.

When the file models IfcVirtualElement separators, a border lying along one
names it and takes conf 0.9; a border found from the polygons alone is
conf 0.7. Curtain-wall facades closing a space's open side are out of scope.

Idea from Pascal's room-first.ts ``addSpaceSeparators`` (MIT, Copyright (c)
2026 Pascal Group Inc., commit 67f8041); no code ported verbatim.
"""

from __future__ import annotations

import math

from building_model import Provenance, SpaceAdjacency

MAX_SEPARATOR_GAP_M = 0.35  # Pascal MAX_SEPARATOR_GAP
PARALLEL_SINE = 0.05  # edges this close to parallel can share a border
WALL_TOL_M = 0.001  # numeric slack; a border ends at the face of a wall crossing it
MIN_BORDER_M = 0.08  # shorter virtual pieces are dropped (Pascal MIN_WALL_LENGTH)
VIRTUAL_COVER_FRAC = 0.5  # share of a border an IfcVirtualElement must lie along
METHOD_FILE = "ifc_import:tier0:virtual_element"
METHOD_DERIVED = "ifc_import:tier1:space_border"


def _edges(poly):
    pts = [tuple(map(float, p)) for p in poly]
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    return [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]


def _shared(e1, e2):
    """Midline segment two edges share, or None."""
    (ax, ay), (bx, by) = e1
    dx, dy = bx - ax, by - ay
    la = math.hypot(dx, dy)
    (cx, cy), (ex, ey) = e2
    lb = math.hypot(ex - cx, ey - cy)
    if la < 1e-9 or lb < 1e-9:
        return None
    ux, uy = dx / la, dy / la
    if abs(ux * (ey - cy) - uy * (ex - cx)) / lb > PARALLEL_SINE:
        return None
    nx, ny = -uy, ux
    dc = (cx - ax) * nx + (cy - ay) * ny
    de = (ex - ax) * nx + (ey - ay) * ny
    if max(abs(dc), abs(de)) > MAX_SEPARATOR_GAP_M + 1e-9:
        return None
    tc = (cx - ax) * ux + (cy - ay) * uy
    te = (ex - ax) * ux + (ey - ay) * uy
    t0, t1 = max(0.0, min(tc, te)), min(la, max(tc, te))
    if t1 - t0 < MIN_BORDER_M:
        return None
    off = (dc + de) / 4.0  # halfway between the two edges

    def at(t):
        return (ax + ux * t + nx * off, ay + uy * t + ny * off)

    return at(t0), at(t1)


def _open_pieces(seg, walls):
    """Parts of ``seg`` not within WALL_TOL_M of any wall footprint."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    line = LineString(seg)
    if walls:
        line = line.difference(unary_union([w.buffer(WALL_TOL_M) for w in walls]))
    parts = getattr(line, "geoms", [line])
    return [list(p.coords) for p in parts if not p.is_empty and p.length >= MIN_BORDER_M]


def find_virtual_borders(model, walls_by_level, virtual_by_level=None, sheet="", revision=0):
    """Append virtual SpaceAdjacency records to ``model``; return them.

    ``walls_by_level``: level id -> wall footprints (shapely geometries).
    ``virtual_by_level``: level id -> [(GlobalId, shapely geometry)] for
    IfcVirtualElement separators.
    """
    from shapely.geometry import LineString

    virtual_by_level = virtual_by_level or {}
    spaces = sorted(
        (sp for sp in model.spaces.values() if sp.polygon_m and len(sp.polygon_m) >= 3),
        key=lambda sp: sp.id,
    )
    out = []
    for i, a in enumerate(spaces):
        for b in spaces[i + 1 :]:
            if a.level_id != b.level_id:
                continue
            walls = walls_by_level.get(a.level_id, [])
            for ea in _edges(a.polygon_m):
                for eb in _edges(b.polygon_m):
                    seg = _shared(ea, eb)
                    if seg is None:
                        continue
                    for piece in _open_pieces(seg, walls):
                        p0, p1 = piece[0], piece[-1]
                        line = LineString([p0, p1])
                        vid = ""
                        for gid, geom in virtual_by_level.get(a.level_id, []):
                            near = line.intersection(geom.buffer(MAX_SEPARATOR_GAP_M / 2))
                            if near.length >= VIRTUAL_COVER_FRAC * line.length:
                                vid = gid
                                break
                        length = round(line.length, 4)
                        if vid:
                            prov = Provenance(
                                sheet_id=sheet,
                                revision=revision,
                                method=METHOD_FILE,
                                confidence=0.9,
                                note=f"GlobalId={vid} IfcVirtualElement between {a.id} and {b.id}",
                            )
                        else:
                            prov = Provenance(
                                sheet_id=sheet,
                                revision=revision,
                                method=METHOD_DERIVED,
                                confidence=0.7,
                                note=(
                                    f"{a.id} and {b.id} share a {length} m border with no wall "
                                    f"(edges within {MAX_SEPARATOR_GAP_M} m)"
                                ),
                            )
                        adj = SpaceAdjacency(
                            space_a=a.id,
                            space_b=b.id,
                            level_id=a.level_id,
                            from_m=[round(p0[0], 4), round(p0[1], 4)],
                            to_m=[round(p1[0], 4), round(p1[1], 4)],
                            length_m=length,
                            virtual_element_id=vid,
                            provenance=prov,
                        )
                        model.space_adjacencies.append(adj)
                        out.append(adj)
    return out
