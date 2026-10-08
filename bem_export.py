"""BEM export: gbXML 6.01 (primary) + IFC4 (via IfcOpenShell) from takeoff results.

Input contracts (from the pipeline):
  * ``takeoff`` : datasets_adapter.TakeoffResult
      - ``takeoff.lines`` : TakeoffLine per schedule tag (tag, category,
        count, width_m, height_m) -> window/door Openings
      - ``takeoff.scale.m_per_px`` : required for spaces + envelope geometry.
        (The count x dims path needs no scale, but BEM geometry does.)
  * ``labeled`` : room_labels.LabeledTakeoff
      - ``labeled.spaces`` : LabeledSpace (polygon_px, name, number)
  * ``sres`` : geometry_simplify.SimplifyResult (ring in source px)

Pipeline:
  model_from_takeoff(...) -> BEMModel (units: meters, x=east, y=north, z=up)
  write_gbxml(model, path)   -> gbXML 6.01 file
  validate_gbxml(path)       -> (ok, errors) via lxml against the 6.01 XSD
  write_ifc4(model, path)    -> IFC4 file (IfcOpenShell)
  validate_ifc4(path)        -> (ok, errors) round-trip structural checks

Coordinate mapping (documented assumption):
  drawing pixel (x right, y DOWN) -> meters (x east, y north = -y_px * s,
  z up). I.e. "up" on a north-up sheet is north. Azimuths follow the gbXML
  convention: degrees clockwise from north of the outward normal.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

# bem_export — unified entry point for gbXML and IFC4 BEM export.
# bem_export focuses purely on serialization and export format handling.
# Intermediate model classes: bem_geometry.py
# Shared helpers: bem_helpers.py
# IFC helpers: bem_ifc4.py
from pathlib import Path

from bem_geometry import (
    BEMModel,
    BEMOpeningUnit,  # noqa: F401
    BEMSpace,  # noqa: F401
    _ensure_ccw,  # noqa: F401
    _shoelace,  # noqa: F401
    model_from_takeoff,  # noqa: F401
)
from bem_helpers import (
    DOOR_SILL_M,
    GBXML_NS,
    WINDOW_SILL_M,
    _assign_wall_to_space,
    _cartesian,
    _distribute_openings,
    _edge_spaces,
    _el,
    _fmt,
    _opening_refs,
    _opening_type,
    _place_openings_on_wall,
    _place_skylights_on_roof,
    _wall_edges,
    glazing_exportable,
)
from bem_ifc4 import validate_ifc4, write_ifc4  # noqa: F401
from bem_layers import write_layered, write_partition
from bem_loads import write_schedules, write_space_loads
from bem_roof import is_sloped, roof_pieces, shell_volume, space_shell, wall_top
from bem_space_split import covers_footprint, keep_openings_whole, shared_walls, split_edge
from room_labels import point_in_polygon as _pip
from safe_xml import safe_xml_parse, safe_xml_parser

__all__ = [
    "BEMModel",
    "BEMOpeningUnit",
    "BEMSpace",
    "model_from_takeoff",
    "validate_gbxml",
    "write_gbxml",
    "validate_ifc4",
    "write_ifc4",
]

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "GreenBuildingXML_Ver6.01.xsd"


def _validate_out_path(path: str | Path) -> Path:
    """Validate output path is safe: reject traversal beyond cwd.

    Allows absolute paths (user-intended destinations).
    For relative paths: resolves symlinks and rejects if the result escapes cwd.
    """
    path = Path(path)
    if not path.is_absolute():
        resolved = path.resolve()
        if not str(resolved).startswith(str(Path.cwd())):
            raise ValueError(f"Path escapes working directory: {path}")
    return path


# ---- gbXML helpers ----


def centroid(sp: BEMSpace):
    xs = [p[0] for p in sp.polygon_m]
    ys = [p[1] for p in sp.polygon_m]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


# ---- write_gbxml ----


def _space_wall_constructions(spaces) -> dict:
    """sid -> (construction id, U) for spaces with a usable wall U-value."""
    out = {}
    for sp in spaces:
        u = getattr(sp, "wall_u_value_w_m2k", None)
        if u is not None and u > 0:
            out[sp.sid] = (f"const-wall-{sp.sid}", float(u))
    return out


def _gbxml_root(model: BEMModel):
    """gbXML root and Campus with its Location (shared by the writers)."""
    ET.register_namespace("", GBXML_NS)
    root = ET.Element(
        f"{{{GBXML_NS}}}gbXML",
        {
            "temperatureUnit": "C",
            "lengthUnit": "Meters",
            "areaUnit": "SquareMeters",
            "volumeUnit": "CubicMeters",
            "version": "6.01",
            "useSIUnitsForResults": "true",
        },
    )
    campus = _el(root, "Campus", id="campus-1")
    _el(campus, "Name", model.building_name)
    _el(
        campus,
        "Description",
        f"Exported by Jesse-Vision prototype. Envelope simplified "
        f"{model.area_delta_pct:+.3f}% area delta (tolerance "
        f"{model.simplify_tolerance:.1f}%).",
    )
    loc = _el(campus, "Location")
    _el(loc, "Name", "Unknown")
    _el(loc, "ZipcodeOrPostalCode", "00000")
    _el(loc, "Latitude", "0")
    _el(loc, "Longitude", "0")
    _el(loc, "Elevation", "0")
    return root, campus


def _write_zones(root, model: BEMModel):
    """Zone elements; returns (thermal zones, space id -> zone id)."""
    # Appendix G thermal zones (#638) when the model carries them; otherwise
    # the single default zone, as before
    tzones = list(getattr(model, "thermal_zones", None) or [])
    zone_of: dict = {}
    if tzones:
        for zid, sids in tzones:
            zone = _el(root, "Zone", id=f"zone-{zid}")
            _el(zone, "Name", zid)
            for sid in sids:
                zone_of.setdefault(sid, f"zone-{zid}")
    else:
        zone = _el(root, "Zone", id="zone-1")
        _el(zone, "Name", "Zone 1")
    return tzones, zone_of


def _write_constructions(root, model: BEMModel, air_walls) -> dict:
    """Construction elements; returns the per-space wall constructions."""
    # Placeholder constructions (v1): drawings carry no assembly data, so
    # every surface references a generic construction. Real U-values /
    # layered assemblies are a v2 enrichment from the spec or user input.
    roof_u = getattr(model, "roof_u_value_w_m2k", None)
    roof_row = (
        ("const-roof", "Exterior roof (from model)", f"{float(roof_u):.6g}")
        if roof_u is not None and roof_u > 0
        else ("const-roof", "Generic roof", "0.30")
    )
    slab_u = getattr(model, "slab_u_value_w_m2k", None)
    slab_row = (
        ("const-slab", "Slab on grade (from model)", f"{float(slab_u):.6g}")
        if slab_u is not None and slab_u > 0
        else ("const-slab", "Generic slab on grade", "0.40")
    )
    # each opaque construction also carries one no-mass layer that reproduces
    # its U-value with the air films, so the model simulates as exported (#765)
    layer_notes = []
    for cid, cname, uval, stype in (
        ("const-wall", "Generic exterior wall", "0.50", "ExteriorWall"),
        roof_row + ("Roof",),
        slab_row + ("SlabOnGrade",),
    ):
        note = write_layered(root, cid, cname, uval, stype)
        if note:
            layer_notes.append(note)
    # Per-space exterior wall constructions (roadmap item 6): a space with an
    # area-weighted wall U gets its own construction; its walls reference it.
    wall_cons = _space_wall_constructions(model.spaces)
    for sid, (cid, u) in wall_cons.items():
        note = write_layered(
            root, cid, f"Exterior wall, area-weighted ({sid})", _fmt(u), "ExteriorWall"
        )
        if note:
            layer_notes.append(note)
    # Opening constructions (#747): doors as layered Constructions, glazing
    # as WindowType (U-value and SHGC, which OpenStudio reads as simple
    # glazing). Written only when openings carry one.
    opening_cons = getattr(model, "opening_constructions", None) or {}
    for cid, info in sorted(opening_cons.items()):
        if info["category"] == "door":
            note = write_layered(root, cid, info["name"], _fmt(info["u"]), "ExteriorWall")
            if note:
                layer_notes.append(note)
    for cid, info in sorted(opening_cons.items()):
        if info["category"] == "door":
            continue
        if not glazing_exportable(info):
            layer_notes.append(
                f"{cid}: glazing has no visible transmittance (or SHGC); its "
                f"{info['category']}s are written with no WindowType, as before"
            )
            continue
        wt = _el(root, "WindowType", id=cid)
        _el(wt, "Name", info["name"])
        _el(wt, "U-value", _fmt(info["u"]), unit="WPerSquareMeterK")
        _el(wt, "SolarHeatGainCoeff", _fmt(info["shgc"]), unit="Fraction")
        _el(
            wt,
            "Transmittance",
            _fmt(info["vt"]),
            unit="Fraction",
            type="Visible",
            surfaceType="Both",
        )
    if layer_notes:
        model.notes.extend(layer_notes)
    if getattr(model, "shades", None):
        # gbXML requires constructionIdRef on every Surface, Shade included.
        # Shading carries no heat; this construction only names the surface
        # type. Written only when shades exist so plain exports are unchanged.
        co = _el(root, "Construction", id="const-shade")
        _el(co, "Name", "Shading device (no heat transfer modeled)")
    if air_walls:
        # air walls (#638) join pieces of a room split at an Appendix G block
        # line; no mass, no resistance. Written only when air walls exist.
        co = _el(root, "Construction", id="const-air")
        _el(co, "Name", "Air wall (virtual boundary between pieces of one room)")
    return wall_cons


def write_gbxml(model: BEMModel, path: str | Path) -> Path:
    """Write a gbXML 6.01 file for the model. Returns the path written."""
    if len(getattr(model, "levels", None) or []) > 1:
        # several storeys (#639): its own writer; single-storey output is unchanged
        from bem_multistorey import write_gbxml_levels

        return write_gbxml_levels(model, path)
    h = model.wall_height_m
    root, campus = _gbxml_root(model)

    bldg = _el(campus, "Building", id="bldg-1", buildingType="Office")
    _el(bldg, "Name", model.building_name)
    storey = _el(bldg, "BuildingStorey", id="storey-1")
    _el(storey, "Name", "Level 1")
    _el(storey, "Level", "0")

    tzones, zone_of = _write_zones(root, model)
    schedule_ids = write_schedules(root, model.spaces)  # #691
    air_walls = list(getattr(model, "air_walls", None) or [])

    roofs = list(getattr(model, "roof_planes", None) or [])
    sloped = is_sloped(roofs)
    edges = _wall_edges(model.ring_m)
    opening_assign = _distribute_openings(
        model.openings,
        edges,
        getattr(model, "ring_facades", None),
        _edge_spaces(edges, model.spaces),
    )
    grade = _grade_plan(model, edges, opening_assign, roofs, sloped, h)

    wall_cons = _write_constructions(root, model, air_walls)
    # per-space envelope (#765): roof and slab cut per space when the spaces
    # tile the footprint; shared boundaries become interior walls
    per_space = (
        len(model.spaces) > 1 and not sloped and covers_footprint(model.ring_m, model.spaces)
    )
    if len(model.spaces) > 1 and not sloped and not per_space:
        model.notes.append(
            "gbXML: spaces do not tile the footprint, so the roof and slab stay "
            "whole on the largest space"
        )
    int_walls = shared_walls(
        model.spaces,
        model.ring_m,
        skip_pairs={frozenset(aw.space_ids) for aw in air_walls},
    )
    if int_walls:
        write_partition(root)
    if any(t == "UndergroundWall" for pieces, _ in grade.values() for t, _ in pieces):
        # walls cut at grade from the site terrain (#649); no U invented
        co = _el(root, "Construction", id="const-ugwall")
        _el(co, "Name", "Underground wall (no U-value stated)")

    # --- spaces (children of Building in gbXML) ------------------------------
    placement_notes = []
    for sp in model.spaces:
        zref = zone_of.get(sp.sid) if tzones else "zone-1"
        attrs = {"id": sp.sid, "buildingStoreyIdRef": "storey-1"}
        if zref:
            attrs["zoneIdRef"] = zref
        se = _el(bldg, "Space", **attrs)
        _el(se, "Name", sp.name)
        write_space_loads(se, sp, schedule_ids)  # #691
        if sp.number:
            _el(se, "CADObjectId", sp.number)
        _el(se, "Area", _fmt(sp.area_m2))
        if sloped:
            # sloped roof (#618): the shell follows the roof planes and the
            # volume comes from that closed shell, not area x wall height
            loops, shell_notes = space_shell(sp.polygon_m, roofs, h)
            placement_notes.extend(f"{sp.sid}: {n}" for n in shell_notes)
            _el(se, "Volume", _fmt(shell_volume(loops)))
            shell = _el(se, "ShellGeometry", id=f"{sp.sid}-shell")
            cs = _el(shell, "ClosedShell")
            for lp in loops:
                pl = _el(cs, "PolyLoop")
                for x, y, z in lp:
                    _cartesian(pl, x, y, z)
            continue
        _el(se, "Volume", _fmt(sp.volume_m3))
        # closed shell: floor + roof + wall quads (outward normals)
        shell = _el(se, "ShellGeometry", id=f"{sp.sid}-shell")
        cs = _el(shell, "ClosedShell")
        ring = sp.polygon_m
        n = len(ring)
        # floor (normal -z): clockwise from above
        pl = _el(cs, "PolyLoop")
        for x, y in reversed(ring):
            _cartesian(pl, x, y, 0.0)
        # roof (normal +z): CCW from above
        pl = _el(cs, "PolyLoop")
        for x, y in ring:
            _cartesian(pl, x, y, h)
        # walls: (b0, b1, t1, t0) has outward normal (verified in docs)
        for i in range(n):
            x0, y0 = ring[i]
            x1, y1 = ring[(i + 1) % n]
            pl = _el(cs, "PolyLoop")
            _cartesian(pl, x0, y0, 0.0)
            _cartesian(pl, x1, y1, 0.0)
            _cartesian(pl, x1, y1, h)
            _cartesian(pl, x0, y0, h)

    # --- envelope surfaces ------------------------------------------------
    surf_count = 0
    open_count = 0
    n_split = 0
    n_space_split = 0
    for i, (p0, p1) in enumerate(edges):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        nx, ny = dy / L, -dx / L  # outward (CCW ring)
        az = math.degrees(math.atan2(nx, ny)) % 360.0
        # screen-right when facing from outside: r = (-ny, nx)... (v x u)
        rx, ry = -ny, nx
        d = (dx / L, dy / L)
        if d[0] * rx + d[1] * ry > 0:
            bl, br = p0, p1
        else:
            bl, br = p1, p0
        sp = _assign_wall_to_space(p0, p1, model.spaces)
        cons = wall_cons.get(sp.sid, ("const-wall", None))[0]
        stype = "ExteriorWall"
        pieces, ops = grade.get(i, (None, None))
        if pieces and len(pieces) > 1:
            # part-buried wall (#649): one surface per part, cut at grade
            n_split += 1
            surf_count, open_count = _write_grade_pieces(
                campus,
                i,
                sp.sid,
                cons,
                pieces,
                ops,
                bl,
                br,
                L,
                az,
                surf_count,
                open_count,
                placement_notes,
                model=model,
            )
            continue
        ug = bool(pieces and pieces[0][0] == "UndergroundWall")
        h_open = h
        if sloped:
            # wall under a sloped roof (#618): openings stay under the lowest
            # point of its true outline so none pokes through the slope
            _top, _unc = wall_top(p0, p1, roofs, h)
            h_open = min([h] + [z for _, _, z in _top])
        # openings on this wall, placed along the whole wall (p0 frame)
        placements, notes = _place_openings_on_wall(opening_assign[i], L, h_open)
        for n in notes:
            placement_notes.append(f"wall-{i + 1:03d}: {n}")
        # one piece per space the wall encloses (#765); a cut never splits an opening
        parts = [(0.0, L, sp)] if ug else split_edge(p0, p1, model.spaces, sp)
        parts = keep_openings_whole(parts, [(q["s0"], q["s1"]) for q in placements])
        if len(parts) > 1:
            n_space_split += 1
        bl_is_p0 = bl == p0
        ux, uy = dx / L, dy / L
        for k, (t0, t1, psp) in enumerate(parts):
            q0 = p0 if t0 == 0.0 else (p0[0] + ux * t0, p0[1] + uy * t0)
            q1 = p1 if t1 == L else (p0[0] + ux * t1, p0[1] + uy * t1)
            Lp = t1 - t0
            qbl, qbr = (q0, q1) if bl_is_p0 else (q1, q0)
            one = len(parts) == 1
            wid = f"wall-{i + 1:03d}" if one else f"wall-{i + 1:03d}{chr(97 + k)}"
            pcons = "const-ugwall" if ug else wall_cons.get(psp.sid, ("const-wall", None))[0]
            surf_count += 1
            su = _el(
                campus,
                "Surface",
                id=wid,
                surfaceType="UndergroundWall" if ug else stype,
                constructionIdRef=pcons,
            )
            _el(su, "Name", f"Wall {i + 1}" if one else f"Wall {i + 1}{chr(97 + k)}")
            _el(su, "AdjacentSpaceId", spaceIdRef=psp.sid)
            rg = _el(su, "RectangularGeometry")
            _el(rg, "Azimuth", _fmt(az))
            _el(rg, "Tilt", "90")
            _cartesian(rg, qbl[0], qbl[1], 0.0)
            _cartesian(rg, qbr[0], qbr[1], 0.0)
            _cartesian(rg, qbr[0], qbr[1], h)
            _cartesian(rg, qbl[0], qbl[1], h)
            top = [(q0[0], q0[1], h), (q1[0], q1[1], h)]
            if sloped:
                # the true outline runs up to the roof, peaked under a gable
                top, _unc = wall_top(q0, q1, roofs, h)
            # PlanarGeometry on every wall: importers such as OpenStudio read
            # only the PolyLoop and drop a wall that has RectangularGeometry
            # alone (#627). Under a sloped roof the loop is the true outline.
            wpg = _el(su, "PlanarGeometry")
            wpl = _el(wpg, "PolyLoop")
            for x, y, z in [(q0[0], q0[1], 0.0), (q1[0], q1[1], 0.0)] + list(reversed(top)):
                _cartesian(wpl, x, y, z)
            # openings whose middle falls on this piece (local coords from
            # the piece's bottom-left)
            last = k == len(parts) - 1
            for pl_ in placements:
                mid = (pl_["s0"] + pl_["s1"]) / 2.0
                if not (t0 <= mid < t1 or (last and mid == t1)):
                    continue
                u = pl_["unit"]
                s0, s1 = pl_["s0"] - t0, pl_["s1"] - t0
                if not bl_is_p0:
                    # mirror into the bottom-left frame (BL == q1 here)
                    s0, s1 = Lp - s1, Lp - s0
                open_count += 1
                op = _el(
                    su,
                    "Opening",
                    id=f"op-{open_count:04d}",
                    openingType=_opening_type(u.category),
                    **_opening_refs(model, u),
                )
                _el(op, "Name", f"{u.tag} ({u.category})")
                org = _el(op, "RectangularGeometry")
                # 2-D local coords per schema doc; no Azimuth/Tilt on openings
                _cartesian(org, s0, pl_["sill"])
                _cartesian(org, s1, pl_["sill"])
                _cartesian(org, s1, pl_["sill"] + pl_["height"])
                _cartesian(org, s0, pl_["sill"] + pl_["height"])
                # absolute 3-D outline too, for importers that read only
                # PlanarGeometry (#627); wound like the host wall (outward)
                vx, vy = (qbr[0] - qbl[0]) / Lp, (qbr[1] - qbl[1]) / Lp
                z0, z1 = pl_["sill"], pl_["sill"] + pl_["height"]
                opg = _el(op, "PlanarGeometry")
                opl = _el(opg, "PolyLoop")
                for sv, zv in ((s0, z0), (s1, z0), (s1, z1), (s0, z1)):
                    _cartesian(opl, qbl[0] + sv * vx, qbl[1] + sv * vy, zv)

    if n_split:
        placement_notes.append(f"{n_split} wall(s) split at grade from the site terrain")
    if n_space_split:
        placement_notes.append(f"{n_space_split} wall(s) split between the spaces they enclose")
    sky_units = [u for u in model.openings if u.category == "skylight"]
    if sloped:
        surf_count, open_count, sky_placed = _write_sloped_roofs(
            campus, model, roofs, sky_units, surf_count, open_count, placement_notes
        )
    else:
        # roof (outward +z): CCW from above. One roof per space when the
        # spaces tile the footprint (#765), else the whole roof on the
        # largest space as before.
        roof_parts = (
            [(sp.sid, _ensure_ccw(list(sp.polygon_m))) for sp in model.spaces]
            if per_space
            else [(max(model.spaces, key=lambda s: s.area_m2).sid, list(model.ring_m))]
        )
        sky_placed, sky_notes = _place_skylights_on_roof(
            sky_units, model.ring_m, regions={sp.sid: sp.polygon_m for sp in model.spaces}
        )
        roof_els = []
        for k, (sid, ring) in enumerate(roof_parts):
            surf_count += 1
            rid = f"roof-{k + 1:03d}"
            su = _el(campus, "Surface", id=rid, surfaceType="Roof", constructionIdRef="const-roof")
            _el(su, "Name", "Roof" if len(roof_parts) == 1 else f"Roof ({sid})")
            _el(su, "AdjacentSpaceId", spaceIdRef=sid)
            pg = _el(su, "PlanarGeometry")
            pl = _el(pg, "PolyLoop")
            for x, y in ring:
                _cartesian(pl, x, y, h)
            roof_els.append((rid, su, ring))
        # skylights on the roof (roadmap item 3). Flat roof, so absolute 3-D
        # coordinates at z = h; CCW from above keeps the outward normal +z.
        # Each goes on the roof piece under its middle.
        for n in sky_notes:
            placement_notes.append(f"roof-001: {n}")
        for pl_ in sky_placed:
            u = pl_["unit"]
            rect = pl_["rect"]
            cx = sum(x for x, _ in rect) / len(rect)
            cy = sum(y for _, y in rect) / len(rect)
            host = roof_els[0][1]
            for _rid, rsu, ring in roof_els:
                if _pip((cx, cy), ring):
                    host = rsu
                    break
            open_count += 1
            op = _el(
                host,
                "Opening",
                id=f"op-{open_count:04d}",
                openingType=_opening_type(u.category),
                **_opening_refs(model, u),
                coordinatesAbsolute="true",
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            opg = _el(op, "PlanarGeometry")
            opl = _el(opg, "PolyLoop")
            for x, y in rect:
                _cartesian(opl, x, y, h)

    # ground floor (outward -z): clockwise from above; per space as the roof
    floor_parts = (
        [(sp.sid, _ensure_ccw(list(sp.polygon_m))) for sp in model.spaces]
        if per_space
        else [(max(model.spaces, key=lambda s: s.area_m2).sid, list(model.ring_m))]
    )
    for k, (sid, ring) in enumerate(floor_parts):
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"floor-{k + 1:03d}",
            surfaceType="SlabOnGrade",
            constructionIdRef="const-slab",
        )
        _el(su, "Name", "Ground Floor" if len(floor_parts) == 1 else f"Ground Floor ({sid})")
        _el(su, "AdjacentSpaceId", spaceIdRef=sid)
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y in reversed(ring):
            _cartesian(pl, x, y, 0.0)

    # interior walls (#765): every boundary two spaces share, not already an
    # air wall; both spaces adjacent, normal from the first into the second
    for k, (a, b, q0, q1) in enumerate(int_walls):
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"intwall-{k + 1:03d}",
            surfaceType="InteriorWall",
            constructionIdRef="const-intwall",
        )
        _el(su, "Name", f"Interior wall {a.sid} / {b.sid}")
        _el(su, "AdjacentSpaceId", spaceIdRef=a.sid)
        _el(su, "AdjacentSpaceId", spaceIdRef=b.sid)
        top = [(q0[0], q0[1], h), (q1[0], q1[1], h)]
        if sloped:
            top, _unc = wall_top(q0, q1, roofs, h)
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y, z in [(q0[0], q0[1], 0.0), (q1[0], q1[1], 0.0)] + list(reversed(top)):
            _cartesian(pl, x, y, z)

    # air walls (#638): one vertical surface per shared edge between pieces
    # of a split room, both pieces adjacent; PlanarGeometry only (importers
    # read the PolyLoop, #627), wound so the normal points from the first
    # adjacent space into the second, and up to the roof when it slopes
    for k, aw in enumerate(air_walls):
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
        top = [(aw.p0[0], aw.p0[1], h), (aw.p1[0], aw.p1[1], h)]
        if sloped:
            top, _unc = wall_top(aw.p0, aw.p1, roofs, h)
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y, z in [(aw.p0[0], aw.p0[1], 0.0), (aw.p1[0], aw.p1[1], 0.0)] + list(reversed(top)):
            _cartesian(pl, x, y, z)

    # shading surfaces (roadmap item 5): detached Shade surfaces, absolute
    # coordinates, no AdjacentSpaceId -- shading is not envelope.
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
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y, z in sh.vertices:
            _cartesian(pl, x, y, z)

    comment = (
        f"Jesse-Vision BEM export. Simplification area delta "
        f"{model.area_delta_pct:+.3f}% (tol {model.simplify_tolerance:.1f}%). "
        f"Openings: {len(model.openings) - len(sky_units)} placed by largest-remainder "
        f"apportionment across {len(edges)} walls proportional to wall "
        f"length, evenly spaced per wall; window sill {WINDOW_SILL_M} m, "
        f"door sill {DOOR_SILL_M} m. "
        + (
            f"Skylights: {len(sky_placed)} of {len(sky_units)} placed on the "
            f"{'sloped roof planes' if sloped else 'flat roof'}, "
            f"spread from the roof interior outward, each kept over its own space "
            f"when the model knows it. "
            if sky_units
            else ""
        )
        + (
            f"Shading: {len(model.shades)} Shade surface(s) placed off their host "
            f"walls' exterior faces. "
            if getattr(model, "shades", None)
            else ""
        )
        + (
            f"Air walls: {len(air_walls)} between pieces of rooms split at Appendix G block lines. "
            if air_walls
            else ""
        )
        + (f"Interior walls: {len(int_walls)} on boundaries spaces share. " if int_walls else "")
        + (" ".join(model.notes) + " " if model.notes else "")
        + (
            "Placement notes: " + "; ".join(placement_notes)
            if placement_notes
            else "No placement clamps."
        )
    )
    path = _validate_out_path(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f"<!-- {comment} -->\n")
        f.write(ET.tostring(root, encoding="unicode"))
        f.write("\n")
    model.notes.append(
        f"gbXML: {len(model.spaces)} spaces, {surf_count} surfaces, {open_count} openings."
    )
    return path


def _grade_plan(model, edges, opening_assign, roofs, sloped, h) -> dict:
    """{edge index: (grade pieces, openings)} for walls the site terrain covers (#649).

    Single-storey twin of the multi-storey grade split (#641): the same
    ``_grade_pieces`` cut, with each wall's true outline under a sloped roof.
    Openings are ``(unit, s0, s1, sill, top)`` in the wall's bottom-left frame.
    Empty without a terrain, so plain exports are unchanged.
    """
    from shapely.geometry import Polygon

    from bem_multistorey import _grade_pieces, _wall_frame
    from grade import Terrain

    terrain = Terrain(getattr(model, "terrain", None) or [])
    if not terrain:
        return {}
    out = {}
    for i, (p0, p1) in enumerate(edges):
        if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) < 1e-6:
            continue
        bl, br, L, _az = _wall_frame(p0, p1)
        h_open, outline = h, None
        if sloped:
            top, _unc = wall_top(p0, p1, roofs, h)
            h_open = min([h] + [z for _, _, z in top])
            ux, uy = (br[0] - bl[0]) / L, (br[1] - bl[1]) / L

            def s_of(x, y):
                return (x - bl[0]) * ux + (y - bl[1]) * uy

            ring = [(s_of(*p0), 0.0), (s_of(*p1), 0.0)]
            ring += [(s_of(x, y), z) for x, y, z in reversed(top)]
            outline = Polygon(ring).buffer(0)
        placements, _ = _place_openings_on_wall(opening_assign[i], L, h_open)
        ops = []
        for pl_ in placements:
            s0, s1 = pl_["s0"], pl_["s1"]
            if bl != p0:
                s0, s1 = L - s1, L - s0
            ops.append((pl_["unit"], s0, s1, pl_["sill"], pl_["sill"] + pl_["height"]))
        pieces = _grade_pieces(terrain, bl, br, L, 0.0, h, [o[1:] for o in ops], outline=outline)
        if pieces is not None:
            out[i] = (pieces, ops)
    return out


def _write_grade_pieces(
    campus, i, sid, cons, pieces, ops, bl, br, L, az, surf_count, open_count, notes, model=None
):
    """Write one wall cut at grade (#649); openings go on the exposed part holding them."""
    from shapely.geometry import box

    from bem_multistorey import _write_piece

    ux, uy = (br[0] - bl[0]) / L, (br[1] - bl[1]) / L
    left = list(ops)
    for k, (stype, poly) in enumerate(pieces):
        surf_count += 1
        su, ox, oz = _write_piece(
            campus,
            f"wall-{i + 1:03d}-{k + 1}",
            f"Wall {i + 1} part {k + 1} ({stype})",
            stype,
            cons if stype == "ExteriorWall" else "const-ugwall",
            sid,
            bl,
            br,
            L,
            az,
            0.0,
            poly,
        )
        if stype != "ExteriorWall":
            continue
        area = poly.buffer(1e-6)
        for o in list(left):
            u, s0, s1, sill, top = o
            if not area.contains(box(s0, sill, s1, top)):
                continue
            left.remove(o)
            open_count += 1
            op = _el(
                su,
                "Opening",
                id=f"op-{open_count:04d}",
                openingType=_opening_type(u.category),
                **_opening_refs(model, u),
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            org = _el(op, "RectangularGeometry")
            for sv, zv in ((s0, sill), (s1, sill), (s1, top), (s0, top)):
                _cartesian(org, sv - ox, zv - oz)
            opl = _el(_el(op, "PlanarGeometry"), "PolyLoop")
            for sv, zv in ((s0, sill), (s1, sill), (s1, top), (s0, top)):
                _cartesian(opl, bl[0] + sv * ux, bl[1] + sv * uy, zv)
    if left:
        notes.append(
            f"wall-{i + 1:03d}: {len(left)} opening(s) on no exposed part of the wall not exported"
        )
    return surf_count, open_count


def _covers(loop3d, pt) -> bool:
    from shapely.geometry import Point, Polygon

    return Polygon([(x, y) for x, y, _ in loop3d]).buffer(1e-6).contains(Point(pt[0], pt[1]))


def _write_sloped_roofs(campus, model, roofs, sky_units, surf_count, open_count, notes):
    """One Roof surface per roof plane, clipped to the envelope ring (#618).

    Each surface carries its plane's tilt and azimuth and its true 3-D
    outline, wound CCW from above so the outward normal points up and out.
    Skylights are laid out in plan as on a flat roof, then lifted onto the
    plane that covers each one's centre. Overhang past the ring is not
    envelope and is dropped with a note.
    """
    from shapely.geometry import Polygon

    from bem_roof import place_skylights_on_pieces

    pieces, overhang = roof_pieces(model.ring_m, roofs)
    if overhang > 1e-4:
        notes.append(f"roof: {overhang:.2f} m2 of roof overhang past the walls not exported")
    regions = {sp.sid: sp.polygon_m for sp in model.spaces}
    surfaces = []
    per_roof = {}
    for r, lp in pieces:
        per_roof.setdefault(r.id, []).append(lp)
    k = 0
    for r, lp in pieces:
        k += 1
        group = per_roof[r.id]
        sid = f"roof-{k:03d}"
        plan = Polygon([(x, y) for x, y, _ in lp])
        host = max(
            model.spaces,
            key=lambda sp: Polygon(sp.polygon_m).intersection(plan).area,
        )
        surf_count += 1
        su = _el(campus, "Surface", id=sid, surfaceType="Roof", constructionIdRef="const-roof")
        name = r.id if len(group) == 1 else f"{r.id} part {group.index(lp) + 1}"
        _el(su, "Name", f"Roof {name}")
        _el(su, "AdjacentSpaceId", spaceIdRef=host.sid)
        rg = _el(su, "RectangularGeometry")
        _el(rg, "Azimuth", _fmt(r.azimuth_deg if r.azimuth_deg is not None else 0.0))
        _el(rg, "Tilt", _fmt(r.tilt_deg))
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y, z in lp:
            _cartesian(pl, x, y, z)
        surfaces.append((su, r, plan, sid))
    # Skylights: one plane at a time, true width x height laid on the slope
    # (bem_roof.place_skylights_on_pieces, shared with the IFC writer).
    by_piece = {id(lp): (su, sid) for (su, r, plan, sid), (_r, lp) in zip(surfaces, pieces)}
    placed, sky_notes = place_skylights_on_pieces(sky_units, pieces, regions)
    kept = []
    for pl_ in placed:
        su, sid = next(
            by_piece[id(lp)]
            for (r_, lp) in pieces
            if r_ is pl_["roof"] and _covers(lp, pl_["centre"])
        )
        u = pl_["unit"]
        open_count += 1
        op = _el(
            su,
            "Opening",
            id=f"op-{open_count:04d}",
            openingType=_opening_type(u.category),
            **_opening_refs(model, u),
            coordinatesAbsolute="true",
        )
        _el(op, "Name", f"{u.tag} ({u.category})")
        opg = _el(op, "PlanarGeometry")
        opl = _el(opg, "PolyLoop")
        for x, y, z in pl_["corners"]:
            _cartesian(opl, x, y, z)
        kept.append(pl_)
    notes.extend(f"roof: {n}" for n in sky_notes)
    return surf_count, open_count, kept


def validate_gbxml(path: str | Path, xsd_path: str | Path = SCHEMA_PATH) -> tuple[bool, list]:
    """Validate a gbXML file against the 6.01 XSD with lxml.

    Returns (ok, [error strings]). Falls back to well-formedness + key
    element checks if lxml or the XSD is unavailable.
    """
    path = str(path)
    errors: list[str] = []
    try:
        from lxml import etree
    except ImportError:
        errors.append("lxml not installed; skipping XSD validation")
        return _gbxml_smoke_check(path, errors)
    if not Path(xsd_path).exists():
        errors.append(f"XSD not found at {xsd_path}; skipping XSD validation")
        return _gbxml_smoke_check(path, errors)
    try:
        # Harden against entity-expansion / XXE: user-supplied gbXML and XSD
        # files are untrusted input (see AGENTS.md untrusted-input policy).
        safe_parser = safe_xml_parser()
        schema = etree.XMLSchema(etree.parse(str(xsd_path), safe_parser))
        tree = safe_xml_parse(path)
        doc = tree.getroot()
        if tree.docinfo.internalDTD is not None:
            entities = list(tree.docinfo.internalDTD.iterentities())
            if entities:
                return False, [
                    f"DOCTYPE with entity declaration rejected for security "
                    f"({len(entities)} entity/entities found)"
                ]
        ok = schema.validate(doc)
        for e in schema.error_log:
            errors.append(f"line {e.line}: {e.message}")
        if ok:
            # semantic spot checks beyond the XSD
            errors.extend(_gbxml_semantic_checks(tree))
        return ok and not errors, errors
    except etree.XMLSyntaxError as e:
        return False, [f"not well-formed: {e}"]
    except ValueError as e:
        return False, [f"file rejected: {e}"]


# ---- gbXML validation helpers ----


def _gbxml_smoke_check(path: str, errors: list) -> tuple[bool, list]:
    try:
        from lxml import etree as lxml_etree

        root = safe_xml_parse(path).getroot()
    except ImportError:
        return False, errors + ["lxml not available; cannot parse gbXML"]
    except lxml_etree.XMLSyntaxError as e:
        return False, errors + [f"not well-formed: {e}"]
    except ValueError as e:
        return False, errors + [f"file rejected: {e}"]
    ns = {"g": GBXML_NS}
    for tag in ("Campus", "Building", "Space", "Surface"):
        if not root.findall(f".//g:{tag}", ns):
            errors.append(f"smoke check: no {tag} elements found")
    return not errors, errors


def _gbxml_semantic_checks(doc) -> list:
    """Checks the XSD cannot express: id uniqueness, ref integrity."""
    errs = []
    ids = {}
    for el in doc.getroot().iter():
        i = el.get("id")
        if i:
            if i in ids:
                errs.append(f"duplicate id '{i}'")
            ids[i] = el.tag
    for el in doc.getroot().iter():
        for attr in (
            "spaceIdRef",
            "buildingStoreyIdRef",
            "zoneIdRef",
            "lightScheduleIdRef",
            "peopleScheduleIdRef",
            "equipmentScheduleIdRef",
            "dayScheduleIdRef",
            "weekScheduleIdRef",
        ):
            ref = el.get(attr)
            if ref and ref not in ids:
                errs.append(f"{el.tag} references unknown id '{ref}' ({attr})")
    return errs
