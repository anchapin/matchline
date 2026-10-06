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
    _opening_type,
    _place_openings_on_wall,
    _place_skylights_on_roof,
    _wall_edges,
)
from bem_ifc4 import validate_ifc4, write_ifc4  # noqa: F401
from bem_roof import is_sloped, roof_pieces, shell_volume, space_shell, wall_top
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


def write_gbxml(model: BEMModel, path: str | Path) -> Path:
    """Write a gbXML 6.01 file for the model. Returns the path written."""
    ET.register_namespace("", GBXML_NS)
    h = model.wall_height_m
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

    bldg = _el(campus, "Building", id="bldg-1", buildingType="Office")
    _el(bldg, "Name", model.building_name)
    storey = _el(bldg, "BuildingStorey", id="storey-1")
    _el(storey, "Name", "Level 1")
    _el(storey, "Level", "0")

    zone = _el(root, "Zone", id="zone-1")
    _el(zone, "Name", "Zone 1")

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
    for cid, cname, uval in (
        ("const-wall", "Generic exterior wall", "0.50"),
        roof_row,
        slab_row,
    ):
        co = _el(root, "Construction", id=cid)
        _el(co, "Name", cname)
        _el(co, "U-value", uval, unit="WPerSquareMeterK")
    # Per-space exterior wall constructions (roadmap item 6): a space with an
    # area-weighted wall U gets its own construction; its walls reference it.
    wall_cons = _space_wall_constructions(model.spaces)
    for sid, (cid, u) in wall_cons.items():
        co = _el(root, "Construction", id=cid)
        _el(co, "Name", f"Exterior wall, area-weighted ({sid})")
        _el(co, "U-value", _fmt(u), unit="WPerSquareMeterK")
    if getattr(model, "shades", None):
        # gbXML requires constructionIdRef on every Surface, Shade included.
        # Shading carries no heat; this construction only names the surface
        # type. Written only when shades exist so plain exports are unchanged.
        co = _el(root, "Construction", id="const-shade")
        _el(co, "Name", "Shading device (no heat transfer modeled)")

    # --- spaces (children of Building in gbXML) ------------------------------
    roofs = list(getattr(model, "roof_planes", None) or [])
    sloped = is_sloped(roofs)
    placement_notes = []
    for sp in model.spaces:
        se = _el(bldg, "Space", id=sp.sid, buildingStoreyIdRef="storey-1", zoneIdRef="zone-1")
        _el(se, "Name", sp.name)
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
    edges = _wall_edges(model.ring_m)
    opening_assign = _distribute_openings(
        model.openings,
        edges,
        getattr(model, "ring_facades", None),
        _edge_spaces(edges, model.spaces),
    )
    surf_count = 0
    open_count = 0
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
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"wall-{i + 1:03d}",
            surfaceType="ExteriorWall",
            constructionIdRef=wall_cons.get(sp.sid, ("const-wall", None))[0],
        )
        _el(su, "Name", f"Wall {i + 1}")
        _el(su, "AdjacentSpaceId", spaceIdRef=sp.sid)
        rg = _el(su, "RectangularGeometry")
        _el(rg, "Azimuth", _fmt(az))
        _el(rg, "Tilt", "90")
        _cartesian(rg, bl[0], bl[1], 0.0)
        _cartesian(rg, br[0], br[1], 0.0)
        _cartesian(rg, br[0], br[1], h)
        _cartesian(rg, bl[0], bl[1], h)
        h_open = h
        if sloped:
            # wall under a sloped roof (#618): the true outline runs up to
            # the roof, peaked under a gable; openings stay under its lowest
            # point so none pokes through the slope
            top, _unc = wall_top(p0, p1, roofs, h)
            if len(top) != 2 or any(abs(z - h) > 1e-9 for _, _, z in top):
                wpg = _el(su, "PlanarGeometry")
                wpl = _el(wpg, "PolyLoop")
                for x, y, z in [(p0[0], p0[1], 0.0), (p1[0], p1[1], 0.0)] + list(reversed(top)):
                    _cartesian(wpl, x, y, z)
                h_open = min([h] + [z for _, _, z in top])
        # openings on this wall (local coords from parent bottom-left)
        units = opening_assign[i]
        bl_is_p0 = bl == p0
        placements, notes = _place_openings_on_wall(units, L, h_open)
        for n in notes:
            placement_notes.append(f"wall-{i + 1:03d}: {n}")
        for pl_ in placements:
            u = pl_["unit"]
            s0, s1 = pl_["s0"], pl_["s1"]
            if not bl_is_p0:
                # mirror into the bottom-left frame (BL == p1 here)
                s0, s1 = L - s1, L - s0
            open_count += 1
            op = _el(
                su, "Opening", id=f"op-{open_count:04d}", openingType=_opening_type(u.category)
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            org = _el(op, "RectangularGeometry")
            # 2-D local coords per schema doc; no Azimuth/Tilt on openings
            _cartesian(org, s0, pl_["sill"])
            _cartesian(org, s1, pl_["sill"])
            _cartesian(org, s1, pl_["sill"] + pl_["height"])
            _cartesian(org, s0, pl_["sill"] + pl_["height"])

    sky_units = [u for u in model.openings if u.category == "skylight"]
    if sloped:
        surf_count, open_count, sky_placed = _write_sloped_roofs(
            campus, model, roofs, sky_units, surf_count, open_count, placement_notes
        )
    else:
        # roof (outward +z): CCW from above
        surf_count += 1
        su = _el(
            campus, "Surface", id="roof-001", surfaceType="Roof", constructionIdRef="const-roof"
        )
        _el(su, "Name", "Roof")
        _el(su, "AdjacentSpaceId", spaceIdRef=max(model.spaces, key=lambda s: s.area_m2).sid)
        pg = _el(su, "PlanarGeometry")
        pl = _el(pg, "PolyLoop")
        for x, y in model.ring_m:
            _cartesian(pl, x, y, h)
        # skylights on the roof (roadmap item 3). Flat roof, so absolute 3-D
        # coordinates at z = h; CCW from above keeps the outward normal +z.
        sky_placed, sky_notes = _place_skylights_on_roof(
            sky_units, model.ring_m, regions={sp.sid: sp.polygon_m for sp in model.spaces}
        )
        for n in sky_notes:
            placement_notes.append(f"roof-001: {n}")
        for pl_ in sky_placed:
            u = pl_["unit"]
            open_count += 1
            op = _el(
                su,
                "Opening",
                id=f"op-{open_count:04d}",
                openingType=_opening_type(u.category),
                coordinatesAbsolute="true",
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            opg = _el(op, "PlanarGeometry")
            opl = _el(opg, "PolyLoop")
            for x, y in pl_["rect"]:
                _cartesian(opl, x, y, h)

    # ground floor (outward -z): clockwise from above
    surf_count += 1
    su = _el(
        campus, "Surface", id="floor-001", surfaceType="SlabOnGrade", constructionIdRef="const-slab"
    )
    _el(su, "Name", "Ground Floor")
    _el(su, "AdjacentSpaceId", spaceIdRef=max(model.spaces, key=lambda s: s.area_m2).sid)
    pg = _el(su, "PlanarGeometry")
    pl = _el(pg, "PolyLoop")
    for x, y in reversed(model.ring_m):
        _cartesian(pl, x, y, 0.0)

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
        + "Interior partitions omitted (v1 gap). "
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


def _write_sloped_roofs(campus, model, roofs, sky_units, surf_count, open_count, notes):
    """One Roof surface per roof plane, clipped to the envelope ring (#618).

    Each surface carries its plane's tilt and azimuth and its true 3-D
    outline, wound CCW from above so the outward normal points up and out.
    Skylights are laid out in plan as on a flat roof, then lifted onto the
    plane that covers each one's centre. Overhang past the ring is not
    envelope and is dropped with a note.
    """
    from shapely.geometry import Polygon

    from bem_roof import plane_z

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
    # Lay skylights out one roof surface at a time, largest first, so each
    # sits wholly on one plane and never straddles a ridge or hip; units
    # that do not fit carry on to the next surface.
    kept = []
    left = list(sky_units)
    for su, r, plan, sid in sorted(surfaces, key=lambda t: (-t[2].area, t[3])):
        if not left:
            break
        sub = {}
        for k_, poly in regions.items():
            g = Polygon(poly).intersection(plan)
            if isinstance(g, Polygon) and g.area > 1e-6:
                sub[k_] = list(g.exterior.coords)[:-1]
        mine = [
            u for u in left if not u.space_sid or u.space_sid in sub or u.space_sid not in regions
        ]
        if not mine:
            continue
        ring2 = list(plan.exterior.coords)[:-1]
        placed, sky_notes = _place_skylights_on_roof(mine, ring2, regions=sub)
        done = {id(pl_["unit"]) for pl_ in placed}
        left = [u for u in left if id(u) not in done]
        for pl_ in placed:
            u = pl_["unit"]
            open_count += 1
            op = _el(
                su,
                "Opening",
                id=f"op-{open_count:04d}",
                openingType=_opening_type(u.category),
                coordinatesAbsolute="true",
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            opg = _el(op, "PlanarGeometry")
            opl = _el(opg, "PolyLoop")
            for x, y in pl_["rect"]:
                _cartesian(opl, x, y, plane_z(r, x, y))
            kept.append(pl_)
    for u in left:
        notes.append(f"roof: skylight {u.tag} did not fit on any roof plane; not exported")
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
        for attr in ("spaceIdRef", "buildingStoreyIdRef", "zoneIdRef"):
            ref = el.get(attr)
            if ref and ref not in ids:
                errs.append(f"{el.tag} references unknown id '{ref}' ({attr})")
    return errs
