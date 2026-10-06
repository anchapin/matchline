"""Multi-storey gbXML writer (#639).

Used by ``bem_export.write_gbxml`` when the model carries more than one
``BEMLevel``; single-storey models keep the original writer, byte for byte.

* one BuildingStorey per level, each space referencing its own storey and
  shelled from that level's elevation to elevation + wall height;
* each level's exterior wall loops become walls at that level's height,
  ``UndergroundWall`` on a stated below-grade level, openings spread over the
  walls of their own space's level;
* every floor, ceiling and roof between or bounding levels is one surface per
  hole-free loop: an ``InteriorFloor`` lists the space below first and the
  space above second (normal up, from the first into the second); roofs and
  underground ceilings face up from the space below; slabs and floors over
  outdoors face down from the space above;
* constructions for the new surface types carry a name only: no U-value is
  stated anywhere, so none is invented.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from bem_helpers import (
    _assign_wall_to_space,
    _cartesian,
    _distribute_openings,
    _edge_spaces,
    _el,
    _fmt,
    _opening_type,
    _place_openings_on_wall,
    _place_skylights_on_roof,
    _wall_edges,
)

EXTRA_CONSTRUCTIONS = {
    "UndergroundWall": ("const-ugwall", "Underground wall (no U-value stated)"),
    "UndergroundSlab": ("const-ugslab", "Underground slab (no U-value stated)"),
    "InteriorFloor": ("const-intfloor", "Interior floor (no U-value stated)"),
    "RaisedFloor": ("const-raisedfloor", "Floor over outdoors (no U-value stated)"),
    "UndergroundCeiling": ("const-ugceiling", "Underground ceiling (no U-value stated)"),
}
CONSTRUCTION_OF = {
    "ExteriorWall": "const-wall",
    "Roof": "const-roof",
    "SlabOnGrade": "const-slab",
    **{k: v[0] for k, v in EXTRA_CONSTRUCTIONS.items()},
}
UP_FROM_LOWER = ("Roof", "UndergroundCeiling")
DOWN_FROM_UPPER = ("SlabOnGrade", "UndergroundSlab", "RaisedFloor")


def _nc(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))


def _signed_area(ring) -> float:
    n = len(ring)
    return 0.5 * sum(
        ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n)
    )


def _facade(p0, p1) -> str:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    nx, ny = dy, -dx  # rings are wound so the right of travel is outdoors
    if abs(nx) >= abs(ny):
        return "east" if nx > 0 else "west"
    return "north" if ny > 0 else "south"


def _write_wall(campus, wid, name, stype, cons, sid, p0, p1, z0, h):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    nx, ny = dy / L, -dx / L
    az = math.degrees(math.atan2(nx, ny)) % 360.0
    rx, ry = -ny, nx
    bl, br = (p0, p1) if (dx / L) * rx + (dy / L) * ry > 0 else (p1, p0)
    su = _el(campus, "Surface", id=wid, surfaceType=stype, constructionIdRef=cons)
    _el(su, "Name", name)
    _el(su, "AdjacentSpaceId", spaceIdRef=sid)
    rg = _el(su, "RectangularGeometry")
    _el(rg, "Azimuth", _fmt(az))
    _el(rg, "Tilt", "90")
    _cartesian(rg, bl[0], bl[1], z0)
    _cartesian(rg, br[0], br[1], z0)
    _cartesian(rg, br[0], br[1], z0 + h)
    _cartesian(rg, bl[0], bl[1], z0 + h)
    pl = _el(_el(su, "PlanarGeometry"), "PolyLoop")
    for x, y, z in (
        (p0[0], p0[1], z0),
        (p1[0], p1[1], z0),
        (p1[0], p1[1], z0 + h),
        (p0[0], p0[1], z0 + h),
    ):
        _cartesian(pl, x, y, z)
    return su, bl, br, L


def level_walls(model, levels, space_level, notes):
    """Exterior walls of each storey, shared by the gbXML and IFC4 writers.

    Returns ``[(level, edges, openings per edge, owning space per edge)]`` for
    every level with rooms and wall loops. Each wall opening goes to the walls
    of its own space's level; one with no known space goes to the lowest
    above-grade level (noted). Courtyard loops are clockwise, so their owner
    is looked up with the edge reversed (``_assign_wall_to_space`` nudges to
    the left of travel).
    """
    wall_units = [u for u in model.openings if u.category != "skylight"]
    fallback = next((lv for lv in levels if lv.wall_type == "ExteriorWall"), levels[0])
    by_level = {lv.id: [] for lv in levels}
    orphans = 0
    for u in wall_units:
        lv = space_level.get(u.space_sid)
        if lv is None:
            orphans += 1
            lv = fallback
        by_level[lv.id].append(u)
    if orphans:
        notes.append(f"{orphans} opening(s) with no space placed on level {fallback.id}")
    out = []
    for lv in levels:
        lsp = [sp for sp in model.spaces if space_level.get(sp.sid) is lv]
        if not lsp or not lv.rings:
            continue
        edges, facades, flipped = [], [], []
        for ring in lv.rings:
            cw = _signed_area(ring) < 0
            for p0, p1 in _wall_edges(ring):
                edges.append((p0, p1))
                facades.append(_facade(p0, p1))
                flipped.append(cw)
        owner = [
            _assign_wall_to_space(p1, p0, lsp) if cw else _assign_wall_to_space(p0, p1, lsp)
            for (p0, p1), cw in zip(edges, flipped)
        ]
        assign = _distribute_openings(by_level[lv.id], edges, facades, _edge_spaces(edges, lsp))
        out.append((lv, edges, assign, owner))
    return out


def space_levels(model, levels) -> dict:
    """Space id -> its BEMLevel (the lowest level when the space names none)."""
    level_of = {lv.id: lv for lv in levels}
    return {sp.sid: level_of.get(sp.level_id) or levels[0] for sp in model.spaces}


def write_gbxml_levels(model, path: str | Path) -> Path:
    from bem_export import _gbxml_root, _validate_out_path, _write_constructions, _write_zones

    root, campus = _gbxml_root(model)
    bldg = _el(campus, "Building", id="bldg-1", buildingType="Office")
    _el(bldg, "Name", model.building_name)
    levels = sorted(model.levels, key=lambda lv: (lv.elevation_m, lv.id))
    for lv in levels:
        st = _el(bldg, "BuildingStorey", id=f"storey-{_nc(lv.id)}")
        _el(st, "Name", lv.name or lv.id)
        _el(st, "Level", _fmt(lv.elevation_m))

    tzones, zone_of = _write_zones(root, model)
    air_walls = list(getattr(model, "air_walls", None) or [])
    wall_cons = _write_constructions(root, model, air_walls)
    used = {lv.wall_type for lv in levels if lv.rings} | {
        hz.surface_type for hz in model.horizontals
    }
    for stype, (cid, cname) in EXTRA_CONSTRUCTIONS.items():
        if stype in used:
            _el(_el(root, "Construction", id=cid), "Name", cname)

    space_level = space_levels(model, levels)
    for sp in model.spaces:
        lv = space_level[sp.sid]
        z0, h = lv.elevation_m, lv.height_m
        attrs = {"id": sp.sid, "buildingStoreyIdRef": f"storey-{_nc(lv.id)}"}
        zref = zone_of.get(sp.sid) if tzones else "zone-1"
        if zref:
            attrs["zoneIdRef"] = zref
        se = _el(bldg, "Space", **attrs)
        _el(se, "Name", sp.name)
        if sp.number:
            _el(se, "CADObjectId", sp.number)
        _el(se, "Area", _fmt(sp.area_m2))
        _el(se, "Volume", _fmt(sp.volume_m3))
        cs = _el(_el(se, "ShellGeometry", id=f"{sp.sid}-shell"), "ClosedShell")
        ring = sp.polygon_m
        pl = _el(cs, "PolyLoop")
        for x, y in reversed(ring):
            _cartesian(pl, x, y, z0)
        pl = _el(cs, "PolyLoop")
        for x, y in ring:
            _cartesian(pl, x, y, z0 + h)
        for i in range(len(ring)):
            (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % len(ring)]
            pl = _el(cs, "PolyLoop")
            for x, y, z in ((x0, y0, z0), (x1, y1, z0), (x1, y1, z0 + h), (x0, y0, z0 + h)):
                _cartesian(pl, x, y, z)

    notes = []
    wall_units = [u for u in model.openings if u.category != "skylight"]
    sky_units = [u for u in model.openings if u.category == "skylight"]
    surf_count = open_count = 0
    n_edges = 0
    for lv, edges, assign, owner in level_walls(model, levels, space_level, notes):
        n_edges += len(edges)
        for i, (p0, p1) in enumerate(edges):
            if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < 1e-6:
                continue
            sp = owner[i]
            cons = (
                wall_cons.get(sp.sid, ("const-wall", None))[0]
                if lv.wall_type == "ExteriorWall"
                else CONSTRUCTION_OF[lv.wall_type]
            )
            wid = f"wall-{_nc(lv.id)}-{i + 1:03d}"
            surf_count += 1
            su, bl, br, L = _write_wall(
                campus,
                wid,
                f"Wall {lv.id} {i + 1}",
                lv.wall_type,
                cons,
                sp.sid,
                p0,
                p1,
                lv.elevation_m,
                lv.height_m,
            )
            placements, pnotes = _place_openings_on_wall(assign[i], L, lv.height_m)
            notes.extend(f"{wid}: {n}" for n in pnotes)
            ux, uy = (br[0] - bl[0]) / L, (br[1] - bl[1]) / L
            for pl_ in placements:
                u = pl_["unit"]
                s0, s1 = pl_["s0"], pl_["s1"]
                if bl != p0:
                    s0, s1 = L - s1, L - s0
                open_count += 1
                op = _el(
                    su, "Opening", id=f"op-{open_count:04d}", openingType=_opening_type(u.category)
                )
                _el(op, "Name", f"{u.tag} ({u.category})")
                org = _el(op, "RectangularGeometry")
                sill, top = pl_["sill"], pl_["sill"] + pl_["height"]
                for sv, zv in ((s0, sill), (s1, sill), (s1, top), (s0, top)):
                    _cartesian(org, sv, zv)
                opl = _el(_el(op, "PlanarGeometry"), "PolyLoop")
                for sv, zv in ((s0, sill), (s1, sill), (s1, top), (s0, top)):
                    _cartesian(opl, bl[0] + sv * ux, bl[1] + sv * uy, lv.elevation_m + zv)

    # floors, ceilings and roofs between and around the storeys
    roof_loops = {}  # space id -> [(area, surface, loop, z)]
    n_horiz = 0
    for hz in model.horizontals:
        cons = CONSTRUCTION_OF.get(hz.surface_type)
        if cons is None:
            notes.append(f"{hz.id}: surface type {hz.surface_type} not written")
            continue
        for k, loop in enumerate(hz.loops):
            sid = hz.id if len(hz.loops) == 1 else f"{hz.id}-p{k + 1}"
            surf_count += 1
            n_horiz += 1
            su = _el(
                campus, "Surface", id=_nc(sid), surfaceType=hz.surface_type, constructionIdRef=cons
            )
            _el(su, "Name", f"{hz.surface_type} {sid}")
            if hz.surface_type == "InteriorFloor":
                adj, pts = [hz.lower_space_id, hz.upper_space_id], loop
            elif hz.surface_type in UP_FROM_LOWER:
                adj, pts = [hz.lower_space_id], loop
            else:
                adj, pts = [hz.upper_space_id], list(reversed(loop))
            for a in adj:
                _el(su, "AdjacentSpaceId", spaceIdRef=a)
            pl = _el(_el(su, "PlanarGeometry"), "PolyLoop")
            for x, y in pts:
                _cartesian(pl, x, y, hz.z_m)
            if hz.surface_type == "Roof":
                area = abs(_signed_area(loop))
                roof_loops.setdefault(hz.lower_space_id, []).append((area, su, loop, hz.z_m))

    # skylights on the roof over their own space (largest roof piece)
    sky_placed = 0
    groups = {}
    for u in sky_units:
        groups.setdefault(u.space_sid if u.space_sid in roof_loops else None, []).append(u)
    for key, units in sorted(groups.items(), key=lambda kv: kv[0] or ""):
        cands = roof_loops.get(key) if key else [c for v in roof_loops.values() for c in v]
        if not cands:
            notes.append(f"{len(units)} skylight(s) with no roof to sit on not exported")
            continue
        _a, su, loop, z = max(cands, key=lambda c: c[0])
        placed, pnotes = _place_skylights_on_roof(units, loop)
        notes.extend(f"{su.get('id')}: {n}" for n in pnotes)
        for pl_ in placed:
            u = pl_["unit"]
            open_count += 1
            sky_placed += 1
            op = _el(
                su,
                "Opening",
                id=f"op-{open_count:04d}",
                openingType=_opening_type(u.category),
                coordinatesAbsolute="true",
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            opl = _el(_el(op, "PlanarGeometry"), "PolyLoop")
            for x, y in pl_["rect"]:
                _cartesian(opl, x, y, z)

    for k, aw in enumerate(air_walls):
        lv = space_level.get(aw.space_ids[0], levels[0])
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"air-{k + 1:03d}",
            surfaceType="Air",
            constructionIdRef="const-air",
        )
        _el(su, "Name", f"Air wall {aw.id}")
        _el(su, "AdjacentSpaceId", spaceIdRef=aw.space_ids[0])
        _el(su, "AdjacentSpaceId", spaceIdRef=aw.space_ids[1])
        z0, z1 = lv.elevation_m, lv.elevation_m + lv.height_m
        pl = _el(_el(su, "PlanarGeometry"), "PolyLoop")
        for x, y, z in (
            (aw.p0[0], aw.p0[1], z0),
            (aw.p1[0], aw.p1[1], z0),
            (aw.p1[0], aw.p1[1], z1),
            (aw.p0[0], aw.p0[1], z1),
        ):
            _cartesian(pl, x, y, z)

    for k, sh in enumerate(getattr(model, "shades", []) or []):
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"shade-{k + 1:03d}",
            surfaceType="Shade",
            constructionIdRef="const-shade",
        )
        _el(su, "Name", f"{sh.kind} {sh.id} on {sh.host_wall_id}")
        pl = _el(_el(su, "PlanarGeometry"), "PolyLoop")
        for x, y, z in sh.vertices:
            _cartesian(pl, x, y, z)

    comment = (
        f"Jesse-Vision BEM export, {len(levels)} storeys. "
        f"{n_edges} exterior wall(s), {n_horiz} floor/ceiling/roof surface(s), "
        f"{len(wall_units)} wall opening(s) on their own level"
        + (f", {sky_placed} of {len(sky_units)} skylight(s) placed" if sky_units else "")
        + ". Interior partitions omitted (v1 gap). "
        + (" ".join(model.notes) + " " if model.notes else "")
        + ("Placement notes: " + "; ".join(notes) if notes else "No placement clamps.")
    )
    path = _validate_out_path(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f"<!-- {comment} -->\n")
        f.write(ET.tostring(root, encoding="unicode"))
    return path
