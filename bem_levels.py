"""Storeys for the multi-storey BEM writers (#639).

``add_levels`` fills ``BEMModel.levels`` and ``BEMModel.horizontals`` from a
canonical BuildingModel with more than one level:

* each space gets its ``level_id`` and, when the source states no volume,
  area x that level's wall height;
* each level gets its exterior wall loops: the envelope walls of that level's
  spaces when they chain into a simple loop covering the level's rooms,
  otherwise the outline of the rooms themselves (closed over wall-thickness
  gaps up to ``CLOSE_GAP_M``), courtyards included as clockwise loops;
* floors, ceilings and roofs come from ``interstory.match_interstory`` and
  their gbXML types from ``below_grade.boundary_types``; a surface whose type
  is unresolved is left out with a note rather than guessed;
* an atrium (``atria.find_atria``, #640) stays one space on its base level
  with its full height and volume; it joins the wall loops of every level it
  opens through, and each room on those levels that borders it gets an air
  wall to it (the model carries no interior partitions, so none is invented).

Single-storey models never come here, so their exports are unchanged.
"""

from __future__ import annotations

from typing import List

from shapely.affinity import scale
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split, unary_union

from bem_geometry import BEMHorizontal, BEMLevel
from thermal_zoning import AirWall

CLOSE_GAP_M = 0.35  # close interior wall gaps between rooms (Pascal MAX_SEPARATOR_GAP)
COVER_TOL = 0.02  # envelope loop must cover the level's rooms to within 2 %
MIN_AREA_M2 = 1e-6
MIN_AIR_WALL_M = 0.05  # shorter shared edges with an atrium are dropped


def _flip(g):
    return scale(g, xfact=1.0, yfact=-1.0, origin=(0, 0))


def _coords(ring) -> list:
    pts = [(float(x), float(y)) for x, y in list(ring.coords)[:-1]]
    return pts


def _parts(g) -> List[Polygon]:
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g] if g.area > MIN_AREA_M2 else []
    if isinstance(g, MultiPolygon):
        return [p for p in g.geoms if p.area > MIN_AREA_M2]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and p.area > MIN_AREA_M2]


def hole_free(poly: Polygon) -> List[Polygon]:
    """Split a polygon with holes into hole-free pieces (gbXML loops carry no holes).

    Cuts on a north-south line through a point inside the first hole and
    recurses; the pieces tile the polygon exactly.
    """
    if not poly.interiors:
        return [poly]
    x = Polygon(poly.interiors[0]).representative_point().x
    _, miny, _, maxy = poly.bounds
    cut = LineString([(x, miny - 1.0), (x, maxy + 1.0)])
    out: List[Polygon] = []
    for part in _parts(split(poly, cut)):
        out.extend(hole_free(part))
    return out


def _key(p: Polygon):
    return (-round(p.area, 9), tuple(round(v, 9) for v in p.bounds))


def _room_rings(polys: List[Polygon]) -> list:
    closed = unary_union([p.buffer(CLOSE_GAP_M / 2, join_style="mitre") for p in polys]).buffer(
        -CLOSE_GAP_M / 2, join_style="mitre"
    )
    rings = []
    for part in sorted(_parts(closed), key=_key):
        part = orient(part, sign=1.0)  # exterior CCW, holes CW
        rings.append(_coords(part.exterior))
        rings.extend(_coords(h) for h in part.interiors)
    return rings


def _level_rings(model, lv, bem_spaces, build_ring, ensure_ccw):
    """(rings, note) for one level, in the BEM frame (y-north)."""
    polys = [Polygon(sp.polygon_m).buffer(0) for sp in bem_spaces if len(sp.polygon_m) >= 3]
    polys = [p for p in polys if p.area > MIN_AREA_M2]
    if not polys:
        return [], f"level {lv.id}: no rooms with a plan outline, no walls exported"
    rooms = unary_union(polys)
    ids = {sp.sid for sp in bem_spaces}
    walls = [w for w in model.envelope if w.space_id in ids]
    # an atrium folded from a stack (#640) owns walls on every level it spans:
    # take this level's own; another level's only for a space with none here
    own = [w for w in walls if w.id.startswith(lv.id + "-")]
    here = {w.space_id for w in own}
    walls = own + [w for w in walls if not w.id.startswith(lv.id + "-") and w.space_id not in here]
    if walls:
        ring = build_ring(walls)
        if len(ring) >= 3:
            ring = ensure_ccw(ring)
            loop = Polygon(ring)
            if loop.is_valid and rooms.difference(loop).area <= COVER_TOL * rooms.area:
                return [list(ring)], ""
    return _room_rings(polys), (
        f"level {lv.id}: walls follow the room outlines (no closed envelope loop for this level)"
    )


def _atrium_air_walls(bem, lv_id: str, atrium) -> List[AirWall]:
    """Air walls from each room on ``lv_id`` to the atrium it borders.

    A room edge counts when it runs alongside the atrium outline, within a
    wall-thickness gap (``CLOSE_GAP_M``) along its whole shared length; an
    edge that only touches it end-on is not a shared wall. Wound so the
    atrium lies to the right (normal from the room into the atrium).
    """
    a = Polygon(atrium.polygon_m).buffer(0)
    if a.area <= MIN_AREA_M2:
        return []
    zone = a.buffer(CLOSE_GAP_M, join_style="mitre")
    out: List[AirWall] = []
    rooms = sorted((sp for sp in bem.spaces if sp.level_id == lv_id), key=lambda sp: sp.sid)
    for room in rooms:
        ring = list(room.polygon_m)
        if len(ring) < 3:
            continue
        k = 0
        for i, p0 in enumerate(ring):
            p1 = ring[(i + 1) % len(ring)]
            edge = LineString([p0, p1])
            if edge.length <= MIN_AIR_WALL_M:
                continue
            hit = edge.intersection(zone)
            segs = [hit] if isinstance(hit, LineString) else list(getattr(hit, "geoms", []))
            for seg in segs:
                if not isinstance(seg, LineString) or seg.length <= MIN_AIR_WALL_M:
                    continue
                (x0, y0), (x1, y1) = seg.coords[0], seg.coords[-1]
                d0 = a.exterior.distance(Point(x0, y0))
                d1 = a.exterior.distance(Point(x1, y1))
                if abs(d0 - d1) > 0.5 * seg.length:
                    continue  # end-on touch, not a shared wall
                dx, dy = x1 - x0, y1 - y0
                L = (dx * dx + dy * dy) ** 0.5
                mx, my = (x0 + x1) / 2, (y0 + y1) / 2
                eps = min(0.05, CLOSE_GAP_M / 4)
                right = Point(mx + dy / L * eps, my - dx / L * eps)
                left = Point(mx - dy / L * eps, my + dx / L * eps)
                if a.distance(right) > a.distance(left):
                    x0, y0, x1, y1 = x1, y1, x0, y0
                k += 1
                out.append(
                    AirWall(
                        id=f"AW-{room.sid}-{atrium.sid}-{k}",
                        space_ids=(room.sid, atrium.sid),
                        p0=(round(x0, 4), round(y0, 4)),
                        p1=(round(x1, 4), round(y1, 4)),
                    )
                )
    return out


def add_levels(model, bem, sloped: bool = False) -> None:
    from below_grade import boundary_types
    from bem_geometry import _ensure_ccw
    from ifc_export import _build_ring
    from interstory import match_interstory

    levels = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    known = {lv.id for lv in levels}
    notes: List[str] = []
    by_sid = {sp.sid: sp for sp in bem.spaces}
    for sid, space in model.spaces.items():
        sp = by_sid.get(sid)
        if sp is None:
            continue
        if space.level_id in known:
            sp.level_id = space.level_id
        else:
            sp.level_id = levels[0].id
            notes.append(f"{sid}: no level stated, exported on {levels[0].id}")
        lv = next(lv for lv in levels if lv.id == sp.level_id)
        if space.volume_m3 is None and not sloped:
            sp.volume_m3 = (space.area_m2 or 0.0) * float(lv.wall_height_m)

    bem.terrain = [
        [(float(x), -float(y), float(z)) for x, y, z in tri]
        for tri in (getattr(model, "terrain", None) or [])
    ]
    ist = match_interstory(model, flag=False)
    bt = boundary_types(model, ist, flag=False)
    below = set(bt.below_grade_levels)

    # atria (#640): full height and volume on the base level
    elev = {lv.id: float(lv.elevation_z_m) for lv in levels}
    for sid, through in sorted(ist.atria.items()):
        sp = by_sid.get(sid)
        if sp is None:
            continue
        top = max((s.z_m for s in ist.surfaces if s.lower_space_id == sid), default=None)
        if top is None:
            continue
        sp.spans = list(through)
        sp.height_m = round(top - elev[sp.level_id], 4)
        if model.spaces[sid].volume_m3 is None and not sloped:
            sp.volume_m3 = (model.spaces[sid].area_m2 or 0.0) * sp.height_m
        notes.append(f"{sid}: atrium open through {', '.join(through)} ({sp.height_m:g} m tall)")

    for lv in levels:
        lsp = [sp for sp in bem.spaces if sp.level_id == lv.id or lv.id in sp.spans]
        rings, note = _level_rings(model, lv, lsp, _build_ring, _ensure_ccw)
        if note:
            notes.append(note)
        bem.levels.append(
            BEMLevel(
                id=lv.id,
                name=lv.name or lv.id,
                elevation_m=float(lv.elevation_z_m),
                height_m=float(lv.wall_height_m),
                rings=rings,
                wall_type="UndergroundWall" if lv.id in below else "ExteriorWall",
                above_ground=lv.above_ground,
            )
        )

    for sp in sorted((sp for sp in bem.spaces if sp.spans), key=lambda sp: sp.sid):
        for lv_id in sp.spans:
            bem.air_walls.extend(_atrium_air_walls(bem, lv_id, sp))

    unresolved = 0
    for s in ist.surfaces:
        stype = bt.horizontals.get(s.id)
        if not stype:
            unresolved += 1
            continue
        loops = []
        for part in sorted(_parts(_flip(s.geom)), key=_key):
            for piece in sorted(hole_free(part), key=_key):
                loops.append(_coords(orient(piece, sign=1.0).exterior))
        if loops:
            bem.horizontals.append(
                BEMHorizontal(
                    id=s.id,
                    surface_type=stype,
                    z_m=float(s.z_m),
                    lower_space_id=s.lower_space_id,
                    upper_space_id=s.upper_space_id,
                    loops=loops,
                )
            )
    if unresolved:
        notes.append(
            f"{unresolved} floor/roof surface(s) with an unresolved boundary type not exported"
        )
    if sloped:
        notes.append(
            "sloped roof planes are not yet written on multi-storey exports; roofs are flat"
        )
    bem.notes.extend(notes)
