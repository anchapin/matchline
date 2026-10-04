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
  * IfcShadingDevice -> ShadingSurface, hosted on the wall line under it

Coordinate frame: IFC is Z-up. The canonical model is y-down (drawing
frame); bem_export flips y on the way out (north-up), so the importer
mirrors it back: canonical = (x_ifc, -y_ifc, z_ifc). For foreign IFC
files this is a documented handedness choice, recorded in provenance.

Every fact carries Provenance (method="ifc_import:tier0:<aspect>") and the
source GlobalId, so IFC -> model -> gbXML/IFC round-trips stay traceable.
"""

import math
import os
import re
from pathlib import Path

from building_model import (
    BimElement,
    BimOpening,
    BuildingModel,
    ComponentRef,
    Construction,
    EnvelopeWall,
    Level,
    Provenance,
    ShadingSurface,
    Space,
    SpaceLighting,
    SpaceOpening,
    Zone,
)
from pipeline_exceptions import PipelineDependencyError
from run_pipeline import StageError


def _ensure_ifc():

    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (likely a broken binary or "
            "missing system library). Install with: pip install ifcopenshell --force-reinstall"
        ) from e
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (likely a broken binary or "
            "missing system library). Install with: pip install ifcopenshell --force-reinstall"
        ) from e
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (likely a broken binary or "
            "missing system library). Install with: pip install ifcopenshell --force-reinstall"
        ) from e


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
    """File length unit -> meters."""
    try:
        ua = f.by_type("IfcUnitAssignment")[0]
    except IndexError:
        raise StageError(
            stage_name="ifc_import",
            stage_index=1,
            msg="No IfcUnitAssignment found in IFC file",
            hint="IFC files should define a length unit via IfcUnitAssignment",
        )
    for u in ua.Units or []:
        utype = getattr(u, "UnitType", None)
        if utype != "LENGTHUNIT":
            continue
        if u.is_a("IfcSIUnit"):
            return _SI_PREFIX.get((u.Prefix or ""), 1.0)
        if u.is_a("IfcConversionBasedUnit"):
            cf = u.ConversionFactor
            try:
                return float(cf.ValueComponent) * _si_scale(cf.UnitComponent)
            except Exception as exc:
                raise StageError(
                    stage_name="ifc_import",
                    stage_index=1,
                    msg=f"Cannot read length unit conversion: {exc}",
                    hint="Check that IfcConversionBasedUnit has a valid ConversionFactor",
                )
    raise StageError(
        stage_name="ifc_import",
        stage_index=1,
        msg="No LENGTHUNIT found in IfcUnitAssignment",
        hint="Ensure the IFC file defines a length unit (e.g. meters, feet)",
    )


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
    except RuntimeError:
        # ifcopenshell geometry kernel failure — return None and let caller
        # degrade to no-volume fallback with provenance.
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


def _read_lighting(sp, model, provenance):
    """Read Pset_SpaceLighting.LightingPower from sp. Adds ReviewItem on failure."""
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
            return SpaceLighting(fixtures=[], total_w=float(prop.NominalValue.wrappedValue))
    model.flag_for_review(
        kind="fixture_schedule",
        description=f"Could not parse lighting power for space {getattr(sp, 'Name', sp.id)}",
        confidence=0.3,
        provenance=provenance,
    )
    return None


# Endpoint-coincidence tolerance (m) when matching a wall to its envelope edge.
# Envelope edges are built from the wall's own placement, so the wall origin and
# the edge endpoint are equal up to rounding. This is the same 0.1 m window the
# previous length-based match used.
_ENVELOPE_TOL_M = 0.1

# Failure reasons for _wall_direction_from_envelope, used to populate
# OpeningAttachmentSummary in _attach_openings_to_spaces.
_WALL_DIR_NO_EDGE = "no_envelope_edge"  # no edge endpoint matched wall placement
_WALL_DIR_AMBIGUOUS_TIE = "ambiguous_tie"  # multiple same-length edges tied
_WALL_DIR_NO_REF = "no_ref_direction"  # both envelope and entity RefDirection failed


def _envelope_edge_dir(ew, origin):
    """Unit direction of envelope edge ``ew``, oriented to start at ``origin``.

    Returns None for a degenerate edge.
    """
    fx, fy = float(ew.from_m[0]), float(ew.from_m[1])
    tx, ty = float(ew.to_m[0]), float(ew.to_m[1])
    if math.dist((fx, fy), origin) <= math.dist((tx, ty), origin):
        dx, dy = tx - fx, ty - fy
    else:
        dx, dy = fx - tx, fy - ty
    norm = math.hypot(dx, dy)
    if norm <= 1e-9:
        return None
    return (dx / norm, dy / norm)


def _own_envelope_edge(el, model):
    """The Tier 0 envelope segment read from wall ``el`` itself, or None.

    Matched on the GlobalId the segment's provenance records, and only when
    the segment still starts at the wall's placement (so a later edit that
    moved it cannot hand back a stale direction). Exactly one match or None.
    """
    gid = getattr(el, "global_id", "") or ""
    if not gid:
        return None
    token = f"GlobalId={gid}"
    origin = (float(el.placement_m[0]), float(el.placement_m[1]))
    hits = [
        ew
        for ew in model.envelope
        if ew.provenance is not None
        and ew.provenance.method == "ifc_import:tier0:envelope"
        and (ew.provenance.note or "").split(" ", 1)[0] == token
        and ew.from_m
        and math.dist(ew.from_m, origin) <= _ENVELOPE_TOL_M
    ]
    return hits[0] if len(hits) == 1 else None


def _wall_direction_from_envelope(el, model):
    """Direction of the wall's own envelope edge, or None if it cannot be pinned down.

    The wall is matched to its envelope edge by *position*: an edge qualifies
    only if one of its endpoints coincides with the wall's placement, which is
    exactly how envelope edges are constructed from walls (see
    ``_read_bim_elements``). Length is used only to break ties between edges
    that share an endpoint, and the returned direction is oriented to start at
    the wall's own placement, so its sign comes from geometry rather than from
    the order the exporter happened to write walls in.

    Matching on length alone (the previous behaviour) is ambiguous for every
    rectangle, since opposite sides are equal: the winner depended on
    ``model.envelope`` ordering, and a wrong edge gives a wrong direction, which
    puts the opening on the wrong side of the wall and can attach it to the
    wrong space (#505). Refusing to guess is preferred: the caller then falls
    back to the wall's own RefDirection, and the opening is left unattached
    rather than silently mis-attributed.

    Returns
    -------
    tuple
        (direction, None) on success, where direction is a unit (dx, dy) tuple.
        (None, reason) on failure, where reason is one of the _WALL_DIR_* constants.
    """
    if el.length_m is None or not el.placement_m:
        return (None, _WALL_DIR_NO_EDGE)
    origin = (float(el.placement_m[0]), float(el.placement_m[1]))
    # Identity first: the Tier 0 envelope segment built from this very wall
    # carries its GlobalId. Position alone ties wherever two collinear runs
    # meet at the wall's origin (a facade split into one segment per space),
    # since both neighbours touch that point and can share a length.
    own = _own_envelope_edge(el, model)
    if own is not None:
        d = _envelope_edge_dir(own, origin)
        if d is not None:
            return (d, None)
    best_rank, dirs = 2, set()
    for ew in model.envelope:
        d = _envelope_edge_dir(ew, origin)
        if d is None:
            continue
        if min(math.dist(ew.from_m, origin), math.dist(ew.to_m, origin)) > _ENVELOPE_TOL_M:
            continue
        edge_len = math.dist(ew.from_m, ew.to_m)
        rank = 0 if abs(edge_len - el.length_m) < _ENVELOPE_TOL_M else 1
        if rank < best_rank:
            best_rank, dirs = rank, {d}
        elif rank == best_rank:
            dirs.add(d)
    if len(dirs) == 0:
        return (None, _WALL_DIR_NO_EDGE)
    if len(dirs) != 1:
        return (None, _WALL_DIR_AMBIGUOUS_TIE)
    return (dirs.pop(), None)


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


def _attach_skylights_to_spaces(model):
    """Attach roof-hosted skylights to the space under their plan centre.

    Same level as the host slab when both carry a level. A skylight over no
    space is left unattached and flagged for review, never forced onto the
    nearest room.
    """
    for el in model.bim_elements:
        if el.ifc_class not in ("IfcSlab", "IfcRoof"):
            continue
        for bo in el.openings:
            if bo.category != "skylight" or not bo.plan_center_m:
                continue
            pt = (bo.plan_center_m[0], bo.plan_center_m[1])
            host = None
            for sp in model.spaces.values():
                if el.level_id and sp.level_id and sp.level_id != el.level_id:
                    continue
                if sp.polygon_m and _point_in_polygon(pt, sp.polygon_m):
                    host = sp
                    break
            if host is None:
                model.flag_for_review(
                    kind="opening_attachment",
                    description=(
                        f"Skylight {bo.tag or bo.id} on {el.ifc_class} {el.global_id} "
                        f"sits over no space on {el.level_id or 'its level'}; left unattached."
                    ),
                    confidence=0.3,
                    provenance=bo.provenance
                    or Provenance(
                        sheet_id="ifc",
                        revision=0,
                        method="ifc_import:tier1:skylight_attachment",
                        confidence=0.3,
                        note=f"GlobalId={bo.id}",
                    ),
                )
                continue
            w, h = bo.width_m, bo.height_m
            host.openings.append(
                SpaceOpening(
                    id=bo.id,
                    tag=bo.tag,
                    category="skylight",
                    width_m=w or 0.0,
                    height_m=h or 0.0,
                    host_facade="roof",
                    area_m2=round(w * h, 4) if w and h else None,
                    provenance=bo.provenance,
                )
            )


def _attach_openings_to_spaces(model):
    """Tier-1 fallback: attach BEM openings to the spaces whose polygons contain them.

    Uses the opening's along-wall position (s_center_m) to determine which space
    the opening belongs to, correctly handling exterior walls that span multiple
    spaces.

    Tracks unattached openings in ``model.opening_attachment_summary`` with
    per-reason breakdown: ``no_envelope_edge``, ``ambiguous_tie``,
    ``no_ref_direction``.
    """
    _attach_skylights_to_spaces(model)
    for el in model.bim_elements:
        if not el.openings or not el.placement_m:
            continue
        if el.ifc_class not in ("IfcWall", "IfcWallStandardCase"):
            continue
        cx, cy = el.placement_m[0], el.placement_m[1]
        length_m = el.length_m
        if length_m is None:
            continue

        env_dir, env_reason = _wall_direction_from_envelope(el, model)
        wall_dir = env_dir
        failure_reason = None  # one of the _WALL_DIR_* constants, or None

        if wall_dir is None:
            wall_dir = _wall_direction_from_entity(el)
            if wall_dir is None:
                # The host wall's axis is unknown, so the openings' positions along it
                # cannot be reconstructed. Leave them unattached rather than
                # attaching them to a space the geometry does not support.
                failure_reason = env_reason or _WALL_DIR_NO_REF

        if wall_dir is None:
            # Record the failure reason for import-summary reporting.
            num_openings = len(el.openings)
            if failure_reason == _WALL_DIR_NO_EDGE:
                model.opening_attachment_summary.no_envelope_edge += num_openings
                reason_desc = "no envelope edge matched wall placement"
            elif failure_reason == _WALL_DIR_AMBIGUOUS_TIE:
                model.opening_attachment_summary.ambiguous_tie += num_openings
                reason_desc = "ambiguous tie (multiple same-length edges)"
            else:
                model.opening_attachment_summary.no_ref_direction += num_openings
                reason_desc = "no RefDirection fallback available"
            model.flag_for_review(
                kind="opening_attachment",
                description=(
                    f"Wall {el.global_id} ({el.ifc_class}) hosts "
                    f"{num_openings} opening(s) but its along-wall direction could "
                    f"not be determined ({reason_desc}); openings left unattached."
                ),
                confidence=0.3,
                provenance=el.openings[0].provenance
                or el.provenance
                or Provenance(
                    sheet_id="ifc",
                    revision=0,
                    method="ifc_import:tier1:opening_attachment",
                    confidence=0.3,
                    note=f"GlobalId={el.global_id}",
                ),
            )
            continue

        for bo in el.openings:
            if bo.s_center_m is None:
                continue
            # s_center_m is measured from the wall's own origin along its local X
            # axis -- _read_opening validates it against 0 <= s <= length_m -- and
            # el.placement_m is that same origin. Subtracting half the length here
            # displaced every opening by length/2 along its wall, which dropped
            # openings outright and could land them in the wrong space (#505).
            ox = cx + wall_dir[0] * bo.s_center_m
            oy = cy + wall_dir[1] * bo.s_center_m
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


# ---------------------------------------------------------------------------
# Shading devices (roadmap item 5, wave 4)
# ---------------------------------------------------------------------------

SHADE_HOST_TOL_M = 0.3  # inner edge of a shade within this of a wall line hosts it
_SHADE_FLAT_EPS_M = 1e-3  # extent below this counts as zero (plate is planar)
_SHADE_KINDS = ("overhang", "fin", "balcony", "other")


def _shading_device_quad(dev, scale):
    """Base-face corners of a rectangular swept shading plate, IFC world frame (m).

    Reads the first Body IfcExtrudedAreaSolid with an IfcRectangleProfileDef
    and applies profile position, solid position and the full 3D placement
    chain. Returns four (x, y, z) or None when the body is not that shape.
    """
    import ifcopenshell.util.placement as _Pl

    solids = _body_extrusions(dev)
    if not solids or not solids[0].SweptArea.is_a("IfcRectangleProfileDef"):
        return None
    solid = solids[0]
    prof = solid.SweptArea
    hx, hy = float(prof.XDim) / 2.0, float(prof.YDim) / 2.0
    cx = cy = 0.0
    rx, ry = 1.0, 0.0
    pos2 = getattr(prof, "Position", None)
    if pos2 is not None:
        cx, cy = (float(c) for c in pos2.Location.Coordinates[:2])
        if getattr(pos2, "RefDirection", None) is not None:
            d = pos2.RefDirection.DirectionRatios
            n = math.hypot(d[0], d[1]) or 1.0
            rx, ry = d[0] / n, d[1] / n
    local = [
        (cx + rx * a - ry * b, cy + ry * a + rx * b, 0.0)
        for a, b in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))
    ]
    m_solid = _Pl.get_axis2placement(solid.Position) if solid.Position is not None else None
    m_obj = _Pl.get_local_placement(dev.ObjectPlacement)
    out = []
    for x, y, z in local:
        v = [x, y, z, 1.0]
        if m_solid is not None:
            v = [sum(m_solid[i][j] * v[j] for j in range(4)) for i in range(4)]
        v = [sum(m_obj[i][j] * v[j] for j in range(4)) for i in range(4)]
        out.append((v[0] * scale, v[1] * scale, v[2] * scale))
    return out


def _read_shading_device(dev, walls, elev, scale, prov, taken_ids):
    """One IfcShadingDevice -> ShadingSurface hosted on the nearest wall line.

    The host is found geometrically: the wall whose centreline the plate's
    inner edge lies on (within SHADE_HOST_TOL_M) and within its length.
    Placement is then measured in that wall's own frame. A plate with no such
    wall comes back unhosted with no placement, so the shading_host_reference
    check flags it instead of the importer guessing.
    """
    import ifcopenshell.util.element as _El

    gid = dev.GlobalId
    psets = _El.get_psets(dev) or {}
    ref = (psets.get("Pset_ShadingDeviceCommon") or {}).get("Reference") or ""
    sid = ref if ref and ref not in taken_ids else f"SH-{gid[:8]}"
    obj_type = (getattr(dev, "ObjectType", None) or "").strip().lower()
    quad = _shading_device_quad(dev, scale)
    if quad is None:
        return ShadingSurface(
            id=sid,
            kind=obj_type if obj_type in _SHADE_KINDS else "other",
            provenance=prov(
                "ifc_import:tier0:shading", 0.3, "no rectangular swept body; unplaced", gid
            ),
        )
    pts = [(*_to_canonical(x, y), z) for x, y, z in quad]
    zs = [p[2] for p in pts]
    z_rng = max(zs) - min(zs)

    best = None
    for w in walls:
        if not w.from_m or not w.to_m:
            continue
        p0, p1 = w.from_m, w.to_m
        L = math.dist(p0, p1)
        if L < 1e-6:
            continue
        ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
        ss = [(p[0] - p0[0]) * ux + (p[1] - p0[1]) * uy for p in pts]
        ds = [abs((p[0] - p0[0]) * uy - (p[1] - p0[1]) * ux) for p in pts]
        near = min(ds)
        if near > SHADE_HOST_TOL_M:
            continue
        if max(ss) < -SHADE_HOST_TOL_M or min(ss) > L + SHADE_HOST_TOL_M:
            continue
        if best is None or near < best[0]:
            best = (near, w, ss, ds)

    kind_from_type = obj_type if obj_type in _SHADE_KINDS else ""
    if best is None:
        return ShadingSurface(
            id=sid,
            kind=kind_from_type or "other",
            provenance=prov(
                "ifc_import:tier0:shading", 0.5, "no wall line under the plate; unhosted", gid
            ),
        )
    _, w, ss, ds = best
    s_rng = max(ss) - min(ss)
    depth = max(ds) - min(ds)
    z0 = min(zs) - elev
    if z_rng <= _SHADE_FLAT_EPS_M:
        kind = kind_from_type if kind_from_type in ("overhang", "balcony", "other") else "overhang"
        return ShadingSurface(
            id=sid,
            kind=kind,
            host_wall_id=w.id,
            along_m=round(min(ss), 4),
            width_m=round(s_rng, 4),
            z_m=round(z0, 4),
            depth_m=round(depth, 4),
            provenance=prov(
                "ifc_import:tier0:shading", 0.9, f"horizontal plate on wall {w.id}", gid
            ),
        )
    if s_rng <= _SHADE_FLAT_EPS_M:
        return ShadingSurface(
            id=sid,
            kind="fin",
            host_wall_id=w.id,
            along_m=round(sum(ss) / len(ss), 4),
            z_m=round(z0, 4),
            depth_m=round(depth, 4),
            height_m=round(z_rng, 4),
            provenance=prov("ifc_import:tier0:shading", 0.9, f"vertical fin on wall {w.id}", gid),
        )
    return ShadingSurface(
        id=sid,
        kind=kind_from_type or "other",
        host_wall_id=w.id,
        provenance=prov(
            "ifc_import:tier0:shading",
            0.5,
            f"plate on wall {w.id} is tilted; placement not read",
            gid,
        ),
    )


# --- Tier 1: envelope classification (roadmap item 7) -----------------------
# Probe distances either side of a wall centerline. Several are tried because
# the space polygon edge sits half a wall thickness (or more) off the
# centerline; the nearest hit on each side wins.
_ENV_PROBE_M = (0.1, 0.25, 0.5, 1.0)
# fractions along the wall at which the sides are sampled
_ENV_SAMPLE_T = (0.1, 0.3, 0.5, 0.7, 0.9)


def _facade_of(nx, ny):
    """Cardinal facade of an outward normal in the canonical (y-down) frame."""
    if abs(nx) >= abs(ny):
        return "east" if nx > 0 else "west"
    return "south" if ny > 0 else "north"


def _wall_thermal_transmittance(wall):
    """Pset_WallCommon.ThermalTransmittance in W/m2K, or None.

    Read as written (IFC's ThermalTransmittance is SI unless the file
    declares otherwise, which no tested authoring tool does). Non-numeric
    or non-positive values are ignored rather than coerced.
    """
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(wall) or {}
    except (ImportError, AttributeError, RuntimeError):
        return None
    u = (psets.get("Pset_WallCommon") or {}).get("ThermalTransmittance")
    try:
        u = float(u)
    except (TypeError, ValueError):
        return None
    return u if u > 0 and math.isfinite(u) else None


# ISO 6946 surface resistances for horizontal heat flow (walls), m2K/W.
RSI_WALL_M2K_W = 0.13
RSE_WALL_M2K_W = 0.04


def _material_conductivity(material):
    """Pset_MaterialThermal.ThermalConductivity of an IfcMaterial in W/mK, or None."""
    if material is None:
        return None
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(material) or {}
    except (ImportError, AttributeError, RuntimeError):
        return None
    k = (psets.get("Pset_MaterialThermal") or {}).get("ThermalConductivity")
    try:
        k = float(k)
    except (TypeError, ValueError):
        return None
    return k if k > 0 and math.isfinite(k) else None


def _layered_wall_u(wall, scale, use_lookup=False):
    """Wall U in W/m2K from its IfcMaterialLayerSet, as (u, looked_up) or None.

    U = 1 / (Rsi + sum(t_i / k_i) + Rse), ISO 6946 surface resistances.
    Computed only when the wall has exactly one layer set and EVERY layer has
    a positive thickness and a known conductivity and is not ventilated. A
    layer's conductivity comes from its Pset_MaterialThermal; with
    ``use_lookup`` a layer without one falls back to ``materials``: an air
    layer (IsVentilated UNKNOWN, which IFC4 defines as an air gap without air
    exchange, or a material/layer name saying air, cavity, void or gap) gets
    the ISO 6946 Table 2 resistance for its thickness, anything else a
    conductivity by material name. ``looked_up`` lists those
    (name, entry id). IsVentilated TRUE (an air gap open to outside air) and
    any layer still unknown mean no value: never guessed.
    """
    from materials import air_layer_resistance, is_air_name, lookup_conductivity

    sets = []
    for rel in getattr(wall, "HasAssociations", None) or []:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        rm = rel.RelatingMaterial
        if rm is not None and rm.is_a("IfcMaterialLayerSetUsage"):
            rm = rm.ForLayerSet
        if rm is not None and rm.is_a("IfcMaterialLayerSet"):
            sets.append(rm)
    if len(sets) != 1 or not sets[0].MaterialLayers:
        return None
    r = RSI_WALL_M2K_W + RSE_WALL_M2K_W
    looked_up = []
    for lay in sets[0].MaterialLayers:
        if getattr(lay, "IsVentilated", None) is True:
            return None
        try:
            t = float(lay.LayerThickness) * scale
        except (TypeError, ValueError):
            return None
        k = _material_conductivity(lay.Material)
        mname = (lay.Material.Name or "") if lay.Material is not None else ""
        lname = getattr(lay, "Name", None) or ""
        if k is None and use_lookup:
            air = getattr(lay, "IsVentilated", None) == "UNKNOWN" or (
                is_air_name(mname) or is_air_name(lname)
            )
            if air:
                r_air = air_layer_resistance(t)
                if r_air is None:
                    return None
                r += r_air
                looked_up.append((mname or lname or "(air gap)", "air_iso6946"))
                continue
            entry = lookup_conductivity(mname) if mname else None
            if entry is not None:
                k = entry.conductivity_w_mk
                looked_up.append((mname, entry.id))
        if k is None or not (t > 0 and math.isfinite(t)):
            return None
        r += t / k
    return 1.0 / r, looked_up


def _roof_thermal_transmittance(el):
    """ThermalTransmittance (W/m2K) stated on a roof element, or None.

    IfcSlab reads Pset_SlabCommon, IfcRoof reads Pset_RoofCommon. Same rules
    as walls: read as written, non-numeric or non-positive values ignored.
    """
    pset_name = "Pset_RoofCommon" if el.is_a("IfcRoof") else "Pset_SlabCommon"
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(el) or {}
    except (ImportError, AttributeError, RuntimeError):
        return None
    u = (psets.get(pset_name) or {}).get("ThermalTransmittance")
    try:
        u = float(u)
    except (TypeError, ValueError):
        return None
    return u if u > 0 and math.isfinite(u) else None


ROOF_U_AGREE_TOL = 1e-6  # W/m2K; roof elements stating U must agree this closely


def _read_roof_construction(model, f, prov):
    """Set ``model.roof_construction_id`` from the roofs' stated U.

    Roof elements are IfcRoof and IfcSlab with PredefinedType ROOF. When
    every one that states a U agrees, one ``IFC-RU<value>`` construction is
    made (``ifc_import:tier0:roof_u``, conf 0.9). Disagreeing values are not
    averaged: the roof stays unset and the returned note (logged in the
    import revision summary) says why. Returns that note, or "".
    """
    roofs = list(f.by_type("IfcRoof")) + [
        s for s in f.by_type("IfcSlab") if getattr(s, "PredefinedType", None) == "ROOF"
    ]
    stated = [(r.GlobalId, u) for r in roofs if (u := _roof_thermal_transmittance(r)) is not None]
    if not stated:
        return ""
    us = [u for _, u in stated]
    if max(us) - min(us) > ROOF_U_AGREE_TOL:
        detail = ", ".join(f"{g}={u:g}" for g, u in stated[:5])
        return f"roof elements state different U-values ({detail}); roof left generic"
    gid, u = stated[0]
    cid = f"IFC-RU{u:.4f}"
    if cid not in model.constructions:
        model.constructions[cid] = Construction(
            id=cid,
            name=f"IFC roof, ThermalTransmittance {u:.4f} W/m2K",
            u_value_w_m2k=round(u, 6),
            provenance=prov(
                "ifc_import:tier0:roof_u",
                0.9,
                f"roof ThermalTransmittance; {len(stated)} roof element(s) agree",
                gid,
            ),
        )
    model.roof_construction_id = cid
    return ""


def _wall_construction(model, u, provenance, source="pset"):
    """Construction id for a wall with this U, one construction per distinct U
    and source.

    ``source`` is "pset" (Pset_WallCommon.ThermalTransmittance, id IFC-U...),
    "layers" (computed from the file's own layer conductivities, IFC-UL...)
    or "lookup" (at least one layer's conductivity from the materials table,
    IFC-UM...), so stated, derived and looked-up values never share a
    construction. ``provenance`` records the first wall seen carrying it.
    """
    if source == "lookup":
        cid = f"IFC-UM{u:.4f}"
        name = f"IFC wall, U {u:.4f} W/m2K from material layers + conductivity lookup"
    elif source == "layers":
        cid = f"IFC-UL{u:.4f}"
        name = f"IFC wall, U {u:.4f} W/m2K from material layers (ISO 6946)"
    else:
        cid = f"IFC-U{u:.4f}"
        name = f"IFC wall, ThermalTransmittance {u:.4f} W/m2K"
    if cid not in model.constructions:
        model.constructions[cid] = Construction(
            id=cid,
            name=name,
            u_value_w_m2k=round(u, 6),
            provenance=provenance,
        )
    return cid


def _wall_construction_id(model, wall, scale, prov, gid):
    """Construction for an imported wall: the stated U first, else the U
    derived from its layers, else "" (left for the coverage check)."""
    u = _wall_thermal_transmittance(wall)
    if u is not None:
        return _wall_construction(
            model,
            u,
            prov(
                "ifc_import:tier0:wall_u",
                0.9,
                "Pset_WallCommon.ThermalTransmittance; first wall carrying it",
                gid,
            ),
        )
    got = _layered_wall_u(wall, scale)
    if got is not None:
        return _wall_construction(
            model,
            got[0],
            prov(
                "ifc_import:tier0:wall_u_layers",
                0.8,
                "U from IfcMaterialLayerSet thickness / Pset_MaterialThermal "
                "conductivity, ISO 6946 Rsi 0.13 + Rse 0.04; first wall carrying it",
                gid,
            ),
            source="layers",
        )
    got = _layered_wall_u(wall, scale, use_lookup=True)
    if got is not None:
        from materials import AIR_SOURCE, SOURCE

        names = "; ".join(f"{n!r}->{eid}" for n, eid in got[1])
        sources = SOURCE + (
            f"; air layers per {AIR_SOURCE}"
            if any(eid == "air_iso6946" for _, eid in got[1])
            else ""
        )
        return _wall_construction(
            model,
            got[0],
            prov(
                "ifc_import:tier0:wall_u_lookup",
                0.6,
                f"U from IfcMaterialLayerSet, ISO 6946 Rsi 0.13 + Rse 0.04; "
                f"conductivity looked up by material name ({names}) in {sources}; "
                "first wall carrying it",
                gid,
            ),
            source="lookup",
        )
    return ""


def _classify_envelope(model):
    """Tier 1: decide exterior/interior and facade for imported wall segments.

    For each segment, probe points either side of its midpoint against the
    space footprints on its level. Exactly one side inside a space: exterior,
    the other side is outward, facade = nearest cardinal of that normal, and
    space_id = the space on the inside. Both sides inside: interior wall, so
    it leaves the envelope (its BimElement stays). Neither side inside: left
    in the envelope unclassified (facade empty) for review. Never guessed.

    Returns a dict of counts for the import summary.
    """
    from shapely.geometry import Point, Polygon

    polys = {}
    for sp in model.spaces.values():
        if len(sp.polygon_m) >= 3:
            try:
                pg = Polygon(sp.polygon_m)
            except Exception:  # noqa: BLE001 -- malformed footprint, skip
                continue
            if pg.is_valid and pg.area > 0:
                polys.setdefault(sp.level_id, []).append((sp.id, pg))

    def side_space(level_id, x, y, nx, ny):
        for d in _ENV_PROBE_M:
            pt = Point(x + nx * d, y + ny * d)
            for sid, pg in polys.get(level_id, []):
                if pg.contains(pt):
                    return sid
        return None

    counts = {"exterior": 0, "interior": 0, "unclassified": 0}
    keep = []
    for w in model.envelope:
        if w.facade or len(w.from_m) < 2 or len(w.to_m) < 2:
            keep.append(w)
            continue
        level_id = w.id.split("-EW")[0] if "-EW" in w.id else ""
        (x0, y0), (x1, y1) = w.from_m, w.to_m
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            keep.append(w)
            counts["unclassified"] += 1
            continue
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L
        # sample along the wall, not just the midpoint: a midpoint can sit
        # exactly on a partition line between two rooms
        lefts, rights = [], []
        for t in _ENV_SAMPLE_T:
            mx, my = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            sl = side_space(level_id, mx, my, nx, ny)
            sr = side_space(level_id, mx, my, -nx, -ny)
            if sl:
                lefts.append(sl)
            if sr:
                rights.append(sr)
        left = sorted(set(lefts)) or None
        right = sorted(set(rights)) or None
        prov = w.provenance
        if left and right:
            # interior partition: not envelope. Its BimElement stays, so the
            # wall is still in the takeoff inventory.
            counts["interior"] += 1
            continue
        if left or right:
            inside = left or right
            ox, oy = (-nx, -ny) if left else (nx, ny)
            w.facade = _facade_of(ox, oy)
            counts["exterior"] += 1
            if len(inside) == 1:
                w.space_id = inside[0]
                encl = f"encloses {inside[0]}"
            else:
                # one segment along several rooms: no single space to name
                encl = f"spans {', '.join(inside)} (space_id left empty)"
            if prov is not None:
                prov.method = "ifc_import:tier1:facade"
                prov.confidence = min(prov.confidence, 0.85)
                prov.note += f"; exterior, {encl}, outward normal -> {w.facade}"
        else:
            counts["unclassified"] += 1
            if prov is not None:
                prov.confidence = min(prov.confidence, 0.5)
                prov.note += "; no space found on either side -- facade unclassified, review"
        keep.append(w)
    model.envelope[:] = keep
    return counts


def _facades_onto_openings(model):
    """Give attached wall openings the facade Tier 1 found for their host wall.

    Host wall -> envelope segment by GlobalId (recorded at the start of the
    segment's provenance note). Openings whose host wall stayed unclassified
    or left the envelope keep an empty facade.
    """
    wall_facade = {}
    for w in model.envelope:
        note = (w.provenance.note if w.provenance else "") or ""
        if w.facade and note.startswith("GlobalId="):
            wall_facade[note.split()[0][len("GlobalId=") :]] = w.facade
    host_of = {o.id: e.global_id for e in model.bim_elements for o in e.openings}
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.host_facade or op.category == "skylight":
                continue
            f = wall_facade.get(host_of.get(op.id, ""))
            if f:
                op.host_facade = f


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
    if not path.is_absolute():
        path_resolved = path.resolve()
        cwd_resolved = Path.cwd().resolve()
        try:
            path_resolved.relative_to(cwd_resolved)
        except ValueError:
            raise ValueError(
                f"Input path '{path}' resolves to '{path_resolved}' which escapes "
                f"the working directory '{cwd_resolved}'. "
                "Rejecting to prevent path traversal."
            )
    max_mb = int(os.environ.get("MATCHLINE_MAX_IFC_SIZE_MB", 100))
    file_size_mb = path.stat().st_size / (1024 * 1024)
    if file_size_mb > max_mb:
        raise ValueError(
            f"IFC file '{path}' is {file_size_mb:.1f} MB, "
            f"exceeds the {max_mb} MB limit. "
            "Set MATCHLINE_MAX_IFC_SIZE_MB to increase the limit."
        )
    f = ifcopenshell.open(str(path))

    max_elements = int(os.environ.get("MATCHLINE_MAX_IFC_ELEMENTS", 500_000))
    # Count all entities via the native 0.9 API (wrapped_data was removed in 0.9.0).
    total_elements = len(f.entity_names())
    if total_elements > max_elements:
        raise ValueError(
            f"IFC file '{path}' has {total_elements} elements, "
            f"exceeds the {max_elements} element limit. "
            "Set MATCHLINE_MAX_IFC_ELEMENTS to increase the limit."
        )
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
            raise StageError(
                stage_name="ifc_import",
                stage_index=1,
                msg=f"Failed to read elevation from storey: {getattr(s, 'Name', 'unknown')}",
                hint="Ensure storeys have a valid Elevation attribute in the IFC file",
            )

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
        elev = _storey_elevation(storey) * scale
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
            lighting = _read_lighting(sp, model, prov("lighting_import", 0.3))
            if lighting is not None:
                space.lighting = lighting
            model.spaces[sid] = space

        # --- elements -----------------------------------------------------
        elements = _contained(storey)
        wall_heights = []
        env_start = len(model.envelope)
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
                        # level-prefixed like drawing-path ids, so the
                        # per-level envelope checks see these walls
                        id=f"{level_id}-EW{len(model.envelope) - env_start + 1}",
                        facade="",
                        from_m=[round(p0[0], 4), round(p0[1], 4)],
                        to_m=[round(p1[0], 4), round(p1[1], 4)],
                        length_m=round(length_m, 4),
                        height_m=round(height_m, 4) if height_m else None,
                        area_m2=round(length_m * height_m, 4) if height_m else None,
                        construction_id=_wall_construction_id(model, el, scale, prov, gid),
                        provenance=prov(
                            "ifc_import:tier0:envelope",
                            0.95,
                            "wall centerline segment; facade from Tier 1 classification",
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

            # skylights: windows hosted in a roof or slab (roadmap item 3).
            # Only window fills are read; an unfilled slab void is a shaft
            # or stair hole, not glazing, and stays out of the opening count.
            elif cls in ("IfcSlab", "IfcRoof"):
                for ogid, host in voids.items():
                    if host.GlobalId != gid:
                        continue
                    opening = openings_by_gid.get(ogid)
                    fill = fills.get(ogid)
                    if opening is None or fill is None or not fill.is_a("IfcWindow"):
                        continue
                    be.openings.append(
                        _read_roof_opening(opening, fill, sheet, revision, scale, host_gid=gid)
                    )

        if wall_heights:
            level.wall_height_m = round(max(wall_heights), 4)

        # spaces exported as footprints only carry no solid: derive volume as
        # area x storey wall height, recorded as derived (lower confidence)
        if level.wall_height_m:
            for space in model.spaces.values():
                if (
                    space.level_id == level_id
                    and space.volume_m3 is None
                    and space.area_m2 is not None
                ):
                    space.volume_m3 = round(space.area_m2 * level.wall_height_m, 4)
                    if space.core_provenance is not None:
                        space.core_provenance.note += (
                            f"; volume derived as area x wall height "
                            f"{level.wall_height_m:g} m (no space solid)"
                        )

        # --- shading devices (roadmap item 5) -----------------------------
        storey_walls = model.envelope[env_start:]
        for el in elements:
            if not el.is_a("IfcShadingDevice"):
                continue
            taken = {sh.id for sh in model.shading}
            model.shading.append(_read_shading_device(el, storey_walls, elev, scale, prov, taken))

    # --- zones (opportunistic) ---------------------------------------------
    terminals_seen = {}  # IfcAirTerminal GlobalId -> ComponentRef (one per terminal)
    terminal_sids = {}  # IfcAirTerminal GlobalId -> spaces of every zone it serves
    for z in f.by_type("IfcZone"):
        sids, terms = [], []
        for rel in getattr(z, "IsGroupedBy", None) or []:
            if not rel.is_a("IfcRelAssignsToGroup"):
                continue
            for o in rel.RelatedObjects or []:
                if o.is_a("IfcSpace") and o.GlobalId in space_by_gid:
                    sids.append(space_by_gid[o.GlobalId])
                elif o.is_a("IfcAirTerminal"):
                    terms.append(o)
        if not sids:
            continue
        zid = z.Name or f"ZONE-{z.GlobalId[:8]}"
        for sid in sids:  # reciprocal space -> zone link
            zl = model.spaces[sid].hvac.zone_ids
            if zid not in zl:
                zl.append(zid)
        diffusers = []
        for t in terms:
            ref = terminals_seen.get(t.GlobalId)
            if ref is None:
                ref = _read_air_terminal(t, scale, prov)
                terminals_seen[t.GlobalId] = ref
            terminal_sids.setdefault(t.GlobalId, []).extend(sids)
            diffusers.append(ref)
        model.zones[zid] = Zone(
            id=zid,
            level_id=model.levels[0].id if model.levels else "L1",
            space_ids=sids,
            diffusers=diffusers,
            provenance=prov(
                "ifc_import:tier0:zone",
                0.9,
                f"{len(sids)} spaces, {len(diffusers)} air terminals via IfcRelAssignsToGroup",
                z.GlobalId,
            ),
        )

    for gid, ref in terminals_seen.items():
        _assign_air_terminal_to_space(ref, terminal_sids.get(gid, []), model)

    _attach_openings_to_spaces(model)
    facade_summary = _classify_envelope(model)
    if model.constructions:
        # space_id is known only after classification; interior walls have
        # left the envelope, so the rollup sees exterior segments only
        from constructions import apply_wall_u_rollup

        apply_wall_u_rollup(model)
    roof_note = _read_roof_construction(model, f, prov)
    _facades_onto_openings(model)

    total_openings = sum(len(e.openings) for e in model.bim_elements)
    unattached = model.opening_attachment_summary
    summary_parts = [
        f"Tier-0 IFC import: {len(model.spaces)} spaces",
        f"{len(model.bim_elements)} elements",
        f"{total_openings} openings",
        f"{len(model.zones)} zones",
        f"{len(terminals_seen)} air terminals",
        f"{len(model.shading)} shading devices",
        (
            f"envelope: {facade_summary['exterior']} exterior, "
            f"{facade_summary['interior']} interior removed, "
            f"{facade_summary['unclassified']} unclassified"
        ),
        f"scale={scale}",
    ]
    if not unattached.is_empty():
        summary_parts.append(unattached.summary_line())
    if model.roof_construction_id:
        summary_parts.append(f"roof construction {model.roof_construction_id}")
    if roof_note:
        summary_parts.append(roof_note)
    model.log_revision(sheet, revision, "ingest", "; ".join(summary_parts))
    return model


def _r4(v):
    return round(v, 4) if v is not None else None


def _read_air_terminal(term, scale, prov):
    """IfcAirTerminal grouped into an IfcZone -> diffuser ComponentRef.

    Position is the placement origin, in the canonical frame.
    """
    _ang, tx, ty, _tz = _placement_transform(term, scale)
    x, y = _to_canonical(tx, ty)
    return ComponentRef(
        id=term.Name or f"AT-{term.GlobalId[:8]}",
        type="diffuser",
        x_m=x,
        y_m=y,
        tag=getattr(term, "Tag", None) or "",
        provenance=prov(
            "ifc_import:tier0:air_terminal",
            0.9,
            f"IfcAirTerminal at ({x:.2f}, {y:.2f})",
            term.GlobalId,
        ),
    )


def _assign_air_terminal_to_space(ref, zone_sids, model):
    """List a diffuser on the one served space whose footprint contains it.

    Candidates are the spaces of every zone the terminal is grouped into.
    With zero or several hits it stays on the zone(s) only, and the
    provenance note says so; the space is not guessed.
    """
    cands = list(dict.fromkeys(zone_sids))
    hits = [
        sid
        for sid in cands
        if len(model.spaces[sid].polygon_m) >= 3
        and _point_in_polygon((ref.x_m, ref.y_m), model.spaces[sid].polygon_m)
    ]
    if len(hits) == 1:
        model.spaces[hits[0]].hvac.diffusers.append(ref)
        suffix = f"; inside {hits[0]}"
    else:
        suffix = f"; inside {len(hits)} served spaces, not assigned to a space"
    if ref.provenance is not None:
        ref.provenance.note = (ref.provenance.note or "") + suffix


def _read_roof_opening(opening, fill, sheet, revision, scale, host_gid=""):
    """One skylight BimOpening from a window hosted in a slab or roof.

    Plan size and centre come from the opening solid in world coordinates
    (placement chain composed, like _read_opening does for walls). With no
    solid, the centre falls back to the opening placement and the size to the
    window's OverallWidth/OverallHeight, at lower confidence.
    """
    ogid = opening.GlobalId
    fgid = fill.GlobalId
    tag = _parse_fill_tag(fill.Name)
    t = _placement_transform(opening, scale)
    width_m = height_m = None
    center = None
    conf, method = 0.6, "ifc_import:tier0:skylight:placement"
    note = "opening placement only; size from window OverallWidth/OverallHeight"
    verts = _geom_verts(opening)
    if verts:
        xs, ys = [], []
        for i in range(0, len(verts), 3):
            wx, wy, _ = _apply(t, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale)
            xs.append(wx)
            ys.append(wy)
        if xs:
            width_m, height_m = max(xs) - min(xs), max(ys) - min(ys)
            center = (0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys)))
            conf, method = 0.95, "ifc_import:tier0:skylight:solid"
            note = "plan size from opening solid, world frame"
    if center is None:
        center = (t[1], t[2])
        ow, oh = getattr(fill, "OverallWidth", None), getattr(fill, "OverallHeight", None)
        width_m = ow * scale if ow else None
        height_m = oh * scale if oh else None
    ptype = getattr(fill, "PredefinedType", None)
    if ptype and ptype != "SKYLIGHT":
        note += f"; fill PredefinedType {ptype}, read as skylight because its host is a roof/slab"
    cx, cy = _to_canonical(*center)
    p = Provenance(
        sheet_id=sheet,
        revision=revision,
        method=method,
        confidence=conf,
        note=f"GlobalId={ogid} fill={fgid} {note}".strip(),
    )
    if tag:
        p.note += f"; tag '{tag}' parsed from fill Name (0.80)"
    return BimOpening(
        id=ogid,
        category="skylight",
        tag=tag,
        width_m=_r4(width_m),
        height_m=_r4(height_m),
        host_global_id=host_gid,
        fill_global_id=fgid,
        provenance=p,
        plan_center_m=[round(cx, 4), round(cy, 4)],
    )


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
