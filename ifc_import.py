"""IFC import frontend, Tier 0: IFC -> canonical BuildingModel.

Reads everything recoverable WITHOUT IfcRelSpaceBoundary (ROADMAP.md
item 7):

  * spatial hierarchy IfcProject -> IfcSite -> IfcBuilding ->
    IfcBuildingStorey via IfcRelAggregates
  * IfcSpace identity (name/number) + their own 3D geometry
    (footprint polygon + volume)
  * IfcWall / IfcSlab / IfcRoof / IfcColumn / IfcBeam / IfcCurtainWall /
    IfcDuctSegment geometry + placement
  * openings via IfcOpeningElement + IfcRelVoidsElement +
    IfcRelFillsElement -> window/door dims, sill, host wall, along-wall
    position
  * IfcRelAssociatesMaterial -> IfcMaterialLayerSet -> per-layer thickness
    + material names (the analytical wall-thickness answer)
  * containment via IfcRelContainedInSpatialStructure
  * opportunistically: IfcZone (via IfcRelAssignsToGroup)

Coordinate frame: IFC is Z-up. The canonical model is y-down (drawing
frame); bem_export flips y on the way out (north-up), so the importer
mirrors it back: canonical = (x_ifc, -y_ifc, z_ifc). For foreign IFC
files this is a documented handedness choice, recorded in provenance.

Every fact carries Provenance (method="ifc_import:tier0:<aspect>") and the
source GlobalId, so IFC -> model -> gbXML/IFC round-trips stay traceable.

Tier 1 (geometric space<->element adjacency inference) is intentionally
out of scope here; see infer_adjacency() stub + docs/ifc_import.md.
"""

import math
import re
from pathlib import Path

from building_model import (
    BimElement,
    BimOpening,
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceLighting,
    SpaceOpening,
    Zone,
)


def _ensure_ifc():
    import importlib.util

    if importlib.util.find_spec("ifcopenshell") is None:
        raise RuntimeError("IfcOpenShell is not installed; install with `pip install ifcopenshell`")
    import ifcopenshell  # noqa: F401


# ---------------------------------------------------------------------------
# Units: IFC length unit -> meters
# ---------------------------------------------------------------------------

_SI_PREFIX = {
    "": 1.0,
    "YOTTA": 1e24,
    "ZETTA": 1e21,
    "EXA": 1e18,
    "PETA": 1e15,
    "TERA": 1e12,
    "GIGA": 1e9,
    "MEGA": 1e6,
    "KILO": 1e3,
    "HECTO": 1e2,
    "DECA": 1e1,
    "DECI": 1e-1,
    "CENTI": 1e-2,
    "MILLI": 1e-3,
    "MICRO": 1e-6,
    "NANO": 1e-9,
    "PICO": 1e-12,
    "FEMTO": 1e-15,
    "ATTO": 1e-18,
    "ZEPTO": 1e-21,
    "YOCTO": 1e-24,
}


def _si_scale(unit) -> float:
    """Meters per unit for an IfcSIUnit (1.0 for non-length, best effort)."""
    if unit.is_a("IfcSIUnit") and unit.UnitType == "LENGTHUNIT":
        return _SI_PREFIX.get((unit.Prefix or ""), 1.0)
    return 1.0


def _length_scale(f) -> float:
    """File length unit -> meters. Defaults to 1.0 (meters assumed)."""
    try:
        ua = f.by_type("IfcUnitAssignment")[0]
    except IndexError:
        return 1.0
    for u in ua.Units or []:
        utype = getattr(u, "UnitType", None)
        if utype != "LENGTHUNIT":
            continue
        if u.is_a("IfcSIUnit"):
            return _SI_PREFIX.get((u.Prefix or ""), 1.0)
        if u.is_a("IfcConversionBasedUnit"):
            # e.g. feet: factor * the SI unit it converts from
            cf = u.ConversionFactor
            try:
                return float(cf.ValueComponent) * _si_scale(cf.UnitComponent)
            except Exception:
                return 1.0
    return 1.0


# ---------------------------------------------------------------------------
# Placements: compose IfcLocalPlacement chains -> (angle, tx, ty, tz)
# ---------------------------------------------------------------------------


def _axis2placement_2d(ax):
    """IfcAxis2Placement3D -> (angle rad about Z, x, y, z).

    Assumes the Axis is ~vertical (true for our files and the common
    BIM authoring case); only the XY rotation from RefDirection is used.
    """
    loc = ax.Location.Coordinates
    x, y, z = float(loc[0]), float(loc[1]), float(loc[2])
    ang = 0.0
    rd = getattr(ax, "RefDirection", None)
    if rd is not None:
        dr = rd.DirectionRatios
        dx, dy = float(dr[0]), float(dr[1])
        if dx or dy:
            ang = math.atan2(dy, dx)
    return ang, x, y, z


def _placement_transform(el, scale):
    """Compose the element's IfcLocalPlacement chain, root first.

    Returns (angle_rad, tx, ty, tz) in meters (IFC frame, Z-up).
    """
    chain = []
    pl = getattr(el, "ObjectPlacement", None)
    while pl is not None and pl.is_a("IfcLocalPlacement"):
        rp = pl.RelativePlacement
        if rp is not None and rp.is_a("IfcAxis2Placement3D"):
            chain.append(rp)
        pl = pl.PlacementRelTo
    ang, tx, ty, tz = 0.0, 0.0, 0.0, 0.0
    for ax in reversed(chain):
        a2, x2, y2, z2 = _axis2placement_2d(ax)
        ca, sa = math.cos(ang), math.sin(ang)
        tx, ty = (tx + (ca * x2 - sa * y2) * scale, ty + (sa * x2 + ca * y2) * scale)
        tz += z2 * scale
        ang += a2
    return ang, tx, ty, tz


def _world_xy(ang, tx, ty, lx, ly):
    ca, sa = math.cos(ang), math.sin(ang)
    return tx + ca * lx - sa * ly, ty + sa * lx + ca * ly


def _compose(t1, t2):
    """Rigid-transform composition: apply t1, then t2. Each (ang, tx, ty, tz)."""
    a1, x1, y1, z1 = t1
    a2, x2, y2, z2 = t2
    ca, sa = math.cos(a2), math.sin(a2)
    return (a1 + a2, x2 + ca * x1 - sa * y1, y2 + sa * x1 + ca * y1, z1 + z2)


def _invert(t):
    """Inverse rigid transform."""
    a, x, y, z = t
    ca, sa = math.cos(a), math.sin(a)
    return (-a, -(ca * x + sa * y), sa * x - ca * y, -z)


def _apply(t, x, y, z):
    a, tx, ty, tz = t
    ca, sa = math.cos(a), math.sin(a)
    return (tx + ca * x - sa * y, ty + sa * x + ca * y, tz + z)


def _to_canonical(wx, wy):
    """IFC (x=east, y=north) -> canonical (x, y-down). Mirrors the
    bem_export north-up flip; see module docstring."""
    return (wx, -wy)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _body_extrusions(el):
    """Body-representation IfcExtrudedAreaSolid items, if any."""
    rep = getattr(el, "Representation", None)
    if rep is None:
        return []
    out = []
    for r in rep.Representations or []:
        if r.RepresentationIdentifier not in ("Body",):
            continue
        for it in r.Items or []:
            if it.is_a("IfcExtrudedAreaSolid"):
                out.append(it)
    return out


def _rect_profile_dims(solid, scale):
    """(xdim, ydim, depth) in meters, or None."""
    p = solid.SweptArea
    if p.is_a("IfcRectangleProfileDef"):
        return (float(p.XDim) * scale, float(p.YDim) * scale, float(solid.Depth) * scale)
    return None


def _polyline_footprint(solid, scale):
    """([(x, y)] solid-local, depth_m) for arbitrary closed profiles."""
    p = solid.SweptArea
    if not p.is_a("IfcArbitraryClosedProfileDef"):
        return None
    oc = p.OuterCurve
    pts = [(float(c.Coordinates[0]) * scale, float(c.Coordinates[1]) * scale) for c in oc.Points]
    # drop a duplicated closing point if present
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts, float(solid.Depth) * scale


def _geom_verts(el):
    """Product-LOCAL vertex list via the geometry kernel (file units).

    NOTE: IfcOpenShell 0.8.5's create_shape does NOT apply the product's
    placement -- vertices are in the element's local frame. Callers
    compose placements themselves via _placement_transform/_compose.
    Returns None on kernel failure.
    """
    try:
        import ifcopenshell.geom as _g

        shape = _g.create_shape(_g.settings(), el)
        v = shape.geometry.verts
        return [float(x) for x in v]
    except Exception:
        return None


def _local_extents(el, scale):
    """(dx, dy, dz) extents in the element's own local frame.

    Kernel vertices are already product-local, so no placement math is
    needed -- just the unit scale. Returns None when the kernel fails.
    """
    verts = _geom_verts(el)
    if not verts:
        return None
    xs = [verts[i] * scale for i in range(0, len(verts), 3)]
    ys = [verts[i + 1] * scale for i in range(0, len(verts), 3)]
    zs = [verts[i + 2] * scale for i in range(0, len(verts), 3)]
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def _shoelace(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _point_in_polygon(pt, poly):
    """Ray-casting point-in-polygon. poly is [(x, y), ...].
    Points on the polygon boundary are treated as inside (returns True).
    """
    x, y = pt
    inside = False
    n = len(poly)
    if n == 0:
        return False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # Boundary check: collinear and within segment bounds
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if abs(cross) < 1e-12:
            if min(x1, x2) - 1e-12 <= x <= max(x1, x2) + 1e-12:
                if min(y1, y2) - 1e-12 <= y <= max(y1, y2) + 1e-12:
                    return True  # on boundary -> treat as inside
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                inside = not inside
    return inside


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------


def _material_layers(el, scale):
    """([(material, thickness_m)], total_m or None) from associations."""
    layers = []
    for rel in getattr(el, "HasAssociations", None) or []:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        rm = rel.RelatingMaterial
        if rm is None:
            continue
        if rm.is_a("IfcMaterialLayerSetUsage"):
            rm = rm.ForLayerSet
        if rm is None or not rm.is_a("IfcMaterialLayerSet"):
            continue  # IfcMaterialList etc: out of scope for v1
        for lay in rm.MaterialLayers or []:
            mname = lay.Material.Name if lay.Material is not None else ""
            layers.append(
                {"material": mname or "(unnamed)", "thickness_m": float(lay.LayerThickness) * scale}
            )
    total = sum(l["thickness_m"] for l in layers) or None
    return layers, total


# ---------------------------------------------------------------------------
# Quantities (fallback when geometry is absent)
# ---------------------------------------------------------------------------


def _quantities(el, qset_name, scale):
    """{quantity_name: value} in meters/m^2/m^3 for an IfcElementQuantity."""
    out = {}
    for rel in getattr(el, "IsDefinedBy", None) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        psd = rel.RelatingPropertyDefinition
        if psd is None or not psd.is_a("IfcElementQuantity"):
            continue
        if psd.Name != qset_name:
            continue
        for q in psd.Quantities or []:
            if q.is_a("IfcQuantityArea"):
                out[q.Name] = float(q.AreaValue) * scale**2
            elif q.is_a("IfcQuantityVolume"):
                out[q.Name] = float(q.VolumeValue) * scale**3
            elif q.is_a("IfcQuantityLength"):
                out[q.Name] = float(q.LengthValue) * scale
    return out


# ---------------------------------------------------------------------------
# Relationship helpers
# ---------------------------------------------------------------------------


def _aggregated(obj, want=None):
    out = []
    for rel in getattr(obj, "IsDecomposedBy", None) or []:
        if rel.is_a("IfcRelAggregates"):
            for o in rel.RelatedObjects or []:
                if want is None or o.is_a(want):
                    out.append(o)
    return out


def _contained(storey):
    out = []
    for rel in getattr(storey, "ContainsElements", None) or []:
        if rel.is_a("IfcRelContainedInSpatialStructure"):
            out.extend(rel.RelatedElements or [])
    return out


_ROOMNUM = re.compile(r"^[A-Za-z]?\d+[A-Za-z]?$")


def _parse_space_name(name):
    """Split 'OPEN OFFICE 101' -> ('OPEN OFFICE', '101').

    Convention-dependent heuristic (documented): a trailing token that
    looks like a room number becomes Space.number.
    """
    name = (name or "").strip()
    if not name:
        return "", None
    toks = name.split()
    last = toks[-1]
    if _ROOMNUM.match(last) and any(c.isdigit() for c in last):
        return " ".join(toks[:-1]), last
    return name, None


_TAG_RE = re.compile(r"^(.+?)\s*\(")


def _parse_fill_tag(name):
    """'A (window 1.50x1.20 m)' -> 'A'. Convention-dependent."""
    m = _TAG_RE.match((name or "").strip())
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Helpers for import_ifc complexity reduction
# ---------------------------------------------------------------------------


def _extract_space_geometry(sp, scale, sid, sp_name, prov_fn, model):
    """Extract polygon/area/volume from an IfcSpace entity.

    Tries in order: IfcExtrudedAreaSolid, IfcGeometricCurveSet,
    Qto_SpaceBaseQuantities.  Returns (polygon, area, volume, conf, method,
    note).  Flags for review when geometry is insufficient.
    """
    polygon, area, volume, conf, method, note = (
        [],
        None,
        None,
        0.4,
        "ifc_import:tier0:space",
        "no geometry; identity only",
    )
    solids = _body_extrusions(sp)
    fp = _polyline_footprint(solids[0], scale) if solids else None
    if fp is not None:
        pts_local, depth = fp
        ang, tx, ty, _tz = _placement_transform(sp, scale)
        polygon = [_to_canonical(*_world_xy(ang, tx, ty, lx, ly)) for lx, ly in pts_local]
        area = abs(_shoelace(polygon))
        volume = area * depth
        conf, method = 0.95, "ifc_import:tier0:space:solid"
        note = f"footprint from IfcExtrudedAreaSolid ({len(polygon)} pts)"
    else:
        polygon = _try_curve_set_footprint(sp, scale)
        if polygon:
            area = abs(_shoelace(polygon))
            volume = None
            conf, method = 0.9, "ifc_import:tier0:space:curve_set"
            note = f"footprint from IfcGeometricCurveSet ({len(polygon)} pts)"
        else:
            q = _quantities(sp, "Qto_SpaceBaseQuantities", scale)
            if "GrossFloorArea" in q:
                area = q["GrossFloorArea"]
                volume = q.get("GrossVolume")
                conf, method = 0.6, "ifc_import:tier0:space:quantity"
                note = "no solid geometry; area from GrossFloorArea"
            model.flag_for_review(
                "space_no_geometry",
                f"space {sid} ({sp_name}) has no solid geometry",
                conf,
                prov_fn("ifc_import:tier0:space", conf, note),
            )
    return polygon, area, volume, conf, method, note


def _try_curve_set_footprint(sp, scale):
    """Try to read a 2-D IfcGeometricCurveSet footprint from sp."""
    rep = getattr(sp, "Representation", None)
    if not rep:
        return []
    for shape_rep in getattr(rep, "Representations", None) or []:
        for item in getattr(shape_rep, "Items", None) or []:
            if not item.is_a("IfcGeometricCurveSet"):
                continue
            for curve in getattr(item, "Elements", None) or []:
                if not curve.is_a("IfcPolyline"):
                    continue
                pts = [
                    (float(p.Coordinates[0]) * scale, float(p.Coordinates[1]) * scale)
                    for p in getattr(curve, "Points", None) or []
                ]
                if len(pts) >= 3:
                    return [_to_canonical(x, y) for x, y in pts]
    return []


def _read_lighting(sp):
    """Read Pset_SpaceLighting.LightingPower from sp. Returns SpaceLighting or None."""
    for rel in getattr(sp, "IsDefinedBy", None) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        psd = rel.RelatingPropertyDefinition
        if psd is None or not psd.is_a("IfcPropertySet"):
            continue
        if psd.Name != "Pset_SpaceLighting":
            continue
        for prop in psd.HasProperties or []:
            if not prop.is_a("IfcPropertySingleValue"):
                continue
            if prop.Name != "LightingPower":
                continue
            try:
                return SpaceLighting(
                    fixtures=[], total_w=float(prop.NominalValue.wrappedValue)
                )
            except Exception:
                pass
    return None


def _wall_direction_from_envelope(el, model):
    """Try to get wall direction from an envelope wall of matching length."""
    length_m = el.length_m
    if length_m is None:
        return None
    for ew in model.envelope:
        ew_len = math.sqrt((ew.to_m[0] - ew.from_m[0]) ** 2 + (ew.to_m[1] - ew.from_m[1]) ** 2)
        if abs(ew_len - length_m) < 0.1:
            dx = ew.to_m[0] - ew.from_m[0]
            dy = ew.to_m[1] - ew.from_m[1]
            norm = math.sqrt(dx * dx + dy * dy)
            if norm > 1e-9:
                return (dx / norm, dy / norm)
    return None


def _wall_direction_from_entity(el):
    """Derive wall direction from the raw IFC entity's RefDirection."""
    raw = getattr(el, "_raw_ifc", None)
    if raw is None:
        return None
    op_pl = getattr(raw, "ObjectPlacement", None)
    if op_pl is None:
        return None
    rel = getattr(op_pl, "RelativePlacement", None)
    if rel is None or not rel.is_a("IfcAxis2Placement3D"):
        return None
    rd = getattr(rel, "RefDirection", None)
    if rd is None:
        return None
    dr = getattr(rd, "DirectionRatios", None)
    if not dr:
        return None
    dx, dy = float(dr[0]), float(dr[1])
    norm = math.sqrt(dx * dx + dy * dy)
    if norm <= 1e-9:
        return None
    return (dx / norm, dy / norm)


def _attach_openings_to_spaces(model):
    """Tier-1 fallback: attach BEM openings to the spaces whose polygons contain them.

    Uses the opening's along-wall position (s_center_m) to determine which space
    the opening belongs to, correctly handling exterior walls that span multiple
    spaces.
    """
    for el in model.bim_elements:
        if not el.openings or not el.placement_m:
            continue
        if el.ifc_class not in ("IfcWall", "IfcWallStandardCase"):
            continue
        cx, cy = el.placement_m[0], el.placement_m[1]
        length_m = el.length_m
        if length_m is None:
            continue

        wall_dir = _wall_direction_from_envelope(el, model)
        if wall_dir is None:
            wall_dir = _wall_direction_from_entity(el)
        if wall_dir is None:
            continue

        for bo in el.openings:
            if bo.s_center_m is None:
                continue
            ox = cx + wall_dir[0] * (bo.s_center_m - length_m / 2)
            oy = cy + wall_dir[1] * (bo.s_center_m - length_m / 2)
            for sp in model.spaces.values():
                if sp.polygon_m and _point_in_polygon((ox, oy), sp.polygon_m):
                    sp.openings.append(
                        SpaceOpening(
                            id=bo.id,
                            tag=bo.tag,
                            category=bo.category,
                            width_m=bo.width_m or 0.0,
                            height_m=bo.height_m or 0.0,
                            sill_m=bo.sill_m,
                            s_center_m=bo.s_center_m,
                            provenance=bo.provenance,
                        )
                    )
                    break


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def import_ifc(path, sheet_id=None, revision=1) -> BuildingModel:
    """Import an IFC file (Tier 0) into the canonical BuildingModel.

    No IfcRelSpaceBoundary is read or required. Returns a BuildingModel
    with levels, spaces (+footprint polygons where the IFC carries solid
    geometry), bim_elements inventory, envelope walls, openings hosted on
    walls (unattached to spaces -- Tier 1), and opportunistic zones.
    """
    _ensure_ifc()
    import ifcopenshell

    path = Path(path)
    f = ifcopenshell.open(str(path))
    if f.schema != "IFC4":
        raise ValueError(f"expected IFC4 schema, got {f.schema}")
    sheet = sheet_id or path.name
    scale = _length_scale(f)

    def prov(method, conf, note="", gid=""):
        n = f"GlobalId={gid} {note}".strip() if gid else note
        return Provenance(sheet_id=sheet, revision=revision, method=method, confidence=conf, note=n)

    projects = f.by_type("IfcProject")
    project = projects[0] if projects else None
    bname = ""
    if project is not None:
        for site in _aggregated(project, "IfcSite"):
            for bldg in _aggregated(site, "IfcBuilding"):
                bname = bldg.Name or ""
    model = BuildingModel(name=bname or (project.Name if project else "") or path.stem)

    # --- spatial hierarchy ------------------------------------------------
    storeys = []
    if project is not None:
        for site in _aggregated(project, "IfcSite"):
            for bldg in _aggregated(site, "IfcBuilding"):
                storeys.extend(_aggregated(bldg, "IfcBuildingStorey"))
    if not storeys:
        # degenerate but non-empty file: fall back to a flat scan
        storeys = f.by_type("IfcBuildingStorey")

    def _storey_elevation(s):
        try:
            return float(getattr(s, "Elevation", 0.0) or 0.0)
        except (RuntimeError, TypeError, ValueError):
            return 0.0

    storeys.sort(key=_storey_elevation)

    # --- openings indexed by host -----------------------------------------
    voids = {}  # opening GlobalId -> host element
    for rel in f.by_type("IfcRelVoidsElement"):
        voids[rel.RelatedOpeningElement.GlobalId] = rel.RelatingBuildingElement
    fills = {}  # opening GlobalId -> fill element
    for rel in f.by_type("IfcRelFillsElement"):
        fills[rel.RelatingOpeningElement.GlobalId] = rel.RelatedBuildingElement

    space_by_gid = {}
    openings_by_gid = {o.GlobalId: o for o in f.by_type("IfcOpeningElement")}
    unlabeled = 0

    for li, storey in enumerate(storeys):
        level_id = f"L{li + 1}"
        try:
            elev = float(getattr(storey, "Elevation", 0.0) or 0.0) * scale
        except (RuntimeError, TypeError, ValueError):
            elev = 0.0
        level = Level(id=level_id, name=storey.Name or "", elevation_z_m=elev)
        model.levels.append(level)

        # --- spaces -------------------------------------------------------
        for sp in _aggregated(storey, "IfcSpace"):
            label, number = _parse_space_name(sp.Name)
            if number is None:
                unlabeled += 1
                sid = f"{level_id}-UNLABELED-{unlabeled}"
            else:
                sid = f"{level_id}-{number}"
            gid = sp.GlobalId
            space_by_gid[gid] = sid

            polygon, area, volume, conf, method, note = _extract_space_geometry(
                sp, scale, sid, sp.Name, prov, model
            )

            space = Space(
                id=sid,
                level_id=level_id,
                name=label,
                number=number or "",
                polygon_m=[[round(x, 4), round(y, 4)] for x, y in polygon],
                area_m2=round(area, 4) if area is not None else None,
                volume_m3=round(volume, 4) if volume is not None else None,
                core_provenance=prov(method, conf, note, gid),
                label_confidence=0.9 if number else 0.6,
            )
            space.lighting = _read_lighting(sp)
            model.spaces[sid] = space

        # --- elements -----------------------------------------------------
        elements = _contained(storey)
        wall_heights = []
        for el in elements:
            cls = el.is_a()
            if cls not in (
                "IfcWall",
                "IfcWallStandardCase",
                "IfcSlab",
                "IfcRoof",
                "IfcColumn",
                "IfcBeam",
                "IfcCurtainWall",
                "IfcDuctSegment",
                "IfcDistributionElement",
            ):
                continue
            gid = el.GlobalId
            ang, tx, ty, tz = _placement_transform(el, scale)
            layers, layers_total = _material_layers(el, scale)

            length_m = width_m = height_m = thick_m = None
            area_m2 = volume_m3 = None
            conf, method, note = (
                0.5,
                "ifc_import:tier0:element:placement",
                "placement only; no usable solid",
            )
            solids = _body_extrusions(el)
            dims = _rect_profile_dims(solids[0], scale) if solids else None
            dims_note = "IfcExtrudedAreaSolid/IfcRectangleProfileDef"
            if dims is None and solids:
                ext = _local_extents(el, scale)
                if ext is not None:
                    dims = ext
                    dims_note = "local extents via geometry kernel (axis-aligned solid assumed)"
            if dims is not None:
                xd, yd, dep = dims
                if cls in ("IfcWall", "IfcWallStandardCase"):
                    length_m, thick_m, height_m = xd, yd, dep
                    area_m2, volume_m3 = xd * dep, xd * yd * dep
                else:
                    length_m, width_m, height_m = xd, yd, dep
                    area_m2, volume_m3 = xd * yd, xd * yd * dep
                conf, method = 0.95, "ifc_import:tier0:element:solid"
                note = dims_note
            if thick_m is None and layers_total:
                thick_m = layers_total
                note += "; thickness from material layers"
            if layers:
                conf = max(conf, 0.9)

            cx, cy = _to_canonical(tx, ty)
            be = BimElement(
                global_id=gid,
                ifc_class=cls,
                name=el.Name or "",
                level_id=level_id,
                length_m=_r4(length_m),
                width_m=_r4(width_m),
                height_m=_r4(height_m),
                thickness_m=_r4(thick_m),
                area_m2=_r4(area_m2),
                volume_m3=_r4(volume_m3),
                material_layers=layers,
                placement_m=[round(cx, 4), round(cy, 4), round(tz, 4)],
                provenance=prov(method, conf, note, gid),
            )
            model.bim_elements.append(be)

            # walls also feed the BEM envelope (facade classification
            # needs adjacency -> Tier 1, so facade stays empty here)
            if cls in ("IfcWall", "IfcWallStandardCase") and length_m:
                dx, dy = math.cos(ang), -math.sin(ang)  # canonical frame
                p0 = (cx, cy)
                p1 = (cx + dx * length_m, cy + dy * length_m)
                wall_heights.append(height_m or 0.0)
                model.envelope.append(
                    EnvelopeWall(
                        id=f"env-{len(model.envelope) + 1:03d}",
                        facade="",
                        from_m=[round(p0[0], 4), round(p0[1], 4)],
                        to_m=[round(p1[0], 4), round(p1[1], 4)],
                        length_m=round(length_m, 4),
                        height_m=round(height_m, 4) if height_m else None,
                        area_m2=round(length_m * height_m, 4) if height_m else None,
                        provenance=prov(
                            "ifc_import:tier0:envelope",
                            0.95,
                            "wall centerline segment; facade TBD (Tier 1)",
                            gid,
                        ),
                    )
                )

            # openings hosted in this element
            if cls in ("IfcWall", "IfcWallStandardCase"):
                for ogid, host in voids.items():
                    if host.GlobalId != gid:
                        continue
                    opening = openings_by_gid.get(ogid)
                    if opening is None:
                        continue
                    be.openings.append(
                        _read_opening(
                            f,
                            opening,
                            fills.get(ogid),
                            (ang, tx, ty, tz),
                            length_m,
                            sheet,
                            revision,
                            scale,
                            host_gid=gid,
                        )
                    )

        if wall_heights:
            level.wall_height_m = round(max(wall_heights), 4)

    # --- zones (opportunistic) ---------------------------------------------
    for z in f.by_type("IfcZone"):
        sids = []
        for rel in getattr(z, "IsGroupedBy", None) or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            for o in rel.RelatedObjects or []:
                if o.is_a("IfcSpace") and o.GlobalId in space_by_gid:
                    sids.append(space_by_gid[o.GlobalId])
        if not sids:
            continue
        zid = z.Name or f"ZONE-{z.GlobalId[:8]}"
        model.zones[zid] = Zone(
            id=zid,
            level_id=model.levels[0].id if model.levels else "L1",
            space_ids=sids,
            provenance=prov(
                "ifc_import:tier0:zone",
                0.9,
                f"{len(sids)} spaces via IfcRelAssignsToGroup",
                z.GlobalId,
            ),
        )

    _attach_openings_to_spaces(model)

    model.log_revision(
        sheet,
        revision,
        "ingest",
        f"Tier-0 IFC import: {len(model.spaces)} spaces, "
        f"{len(model.bim_elements)} elements, "
        f"{sum(len(e.openings) for e in model.bim_elements)} openings, "
        f"{len(model.zones)} zones; scale={scale}",
    )
    return model


def _r4(v):
    return round(v, 4) if v is not None else None


def _read_opening(f, opening, fill, wall_world, wall_len, sheet, revision, scale, host_gid=""):
    """One BimOpening from void/fill relationships + opening geometry.

    Dimensions come from the opening's product-local vertices (geometry
    kernel), mapped into the host wall's frame via
    wall^-1 * opening placement composition: robust to whatever profile
    orientation the authoring tool used.
    """
    ogid = opening.GlobalId
    fgid = fill.GlobalId if fill is not None else ""
    if fill is None:
        category = "unknown"
    elif fill.is_a("IfcWindow"):
        category = "window"
    elif fill.is_a("IfcDoor"):
        category = "door"
    else:
        category = "unknown"
    tag = _parse_fill_tag(fill.Name) if fill is not None else ""

    # Extract s_center_m from opening placement (always available, even without geometry).
    # The opening's RelativePlacement gives (s_mid, 0, sill) in the wall's local frame.
    s_center_m = None
    op_pl = getattr(opening, "ObjectPlacement", None)
    if op_pl is not None:
        rel = getattr(op_pl, "RelativePlacement", None)
        if rel is not None and rel.is_a("IfcAxis2Placement3D"):
            loc = rel.Location
            if loc and hasattr(loc, "Coordinates"):
                coords = loc.Coordinates
                if coords:
                    s_center_m = float(coords[0]) * scale

    width_m = height_m = sill_m = None
    conf, method = 0.5, "ifc_import:tier0:opening:novolume"
    note = "void/fill relationships only; no opening solid"
    verts = _geom_verts(opening)
    if verts:
        rel = _compose(_invert(wall_world), _placement_transform(opening, scale))
        xs, zs = [], []
        for i in range(0, len(verts), 3):
            wx, wy, wz = _apply(rel, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale)
            xs.append(wx)
            zs.append(wz)
        if xs and zs:
            width_m = max(xs) - min(xs)
            height_m = max(zs) - min(zs)
            sill_m = min(zs)  # wall frame z=0 is the wall base
            s_center_m = 0.5 * (min(xs) + max(xs))
            conf, method = 0.95, "ifc_import:tier0:opening:solid"
            note = "dims from opening solid, wall-local frame"
            if wall_len and not (-0.01 <= min(xs) <= max(xs) <= wall_len + 0.01):
                note += " (outside wall extent -- flagged)"
    p = Provenance(
        sheet_id=sheet,
        revision=revision,
        method=method,
        confidence=conf,
        note=f"GlobalId={ogid} fill={fgid} {note}".strip(),
    )
    if tag:
        # tag itself is a convention-dependent parse of the fill Name
        p.note += f"; tag '{tag}' parsed from fill Name (0.80)"
    return BimOpening(
        id=ogid,
        category=category,
        tag=tag,
        width_m=_r4(width_m),
        height_m=_r4(height_m),
        sill_m=_r4(sill_m),
        s_center_m=_r4(s_center_m),
        host_global_id=host_gid,
        fill_global_id=fgid,
        provenance=p,
    )


# ---------------------------------------------------------------------------
# Tier 1 (out of scope for this module version): geometric adjacency
# ---------------------------------------------------------------------------


def infer_adjacency(model: BuildingModel):
    """Tier 1 (NOT IMPLEMENTED): geometric space<->element adjacency.

    Planned: for each space solid, find wall faces within tolerance of its
    boundary (proximity/clash queries); classify interior vs exterior via
    outward ray tests; attach BimOpenings to Spaces as SpaceOpenings;
    classify envelope facades. Every inference carries method + confidence
    and ambiguous cases go to the review queue. See docs/ifc_import.md.
    """
    raise NotImplementedError(
        "Tier 1 geometric adjacency inference is not implemented yet; "
        "see docs/ifc_import.md for the plan."
    )
