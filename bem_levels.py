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
  is unresolved is left out with a note rather than guessed.

Single-storey models never come here, so their exports are unchanged.
"""

from __future__ import annotations

from typing import List

from shapely.affinity import scale
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import split, unary_union

from bem_geometry import BEMHorizontal, BEMLevel

CLOSE_GAP_M = 0.35  # close interior wall gaps between rooms (Pascal MAX_SEPARATOR_GAP)
COVER_TOL = 0.02  # envelope loop must cover the level's rooms to within 2 %
MIN_AREA_M2 = 1e-6


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

    ist = match_interstory(model, flag=False)
    bt = boundary_types(model, ist, flag=False)
    below = set(bt.below_grade_levels)

    for lv in levels:
        lsp = [sp for sp in bem.spaces if sp.level_id == lv.id]
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
