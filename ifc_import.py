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
    REVIEW_CONFIDENCE,
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


# ---------------------------------------------------------------------------
# Wall body centreline vs reference line (#579)
# ---------------------------------------------------------------------------

_AXIS_SAME_M = 1e-4  # offsets below this leave the segment where it is
_USAGE_AGREE_M = 1e-3  # body vs layer-set usage disagreement worth a note


def _unit_ok(direction, want, tol=1e-6):
    if direction is None:
        return True
    r = [float(v) for v in direction.DirectionRatios]
    n = math.sqrt(sum(v * v for v in r)) or 1.0
    return all(abs(a / n - b) <= tol for a, b in zip(r, want))


def _body_axis_local(el, solids, from_extents, scale):
    """(x0, y_mid, source) of the wall body's centreline in the wall's own frame.

    x0 is where the body starts along local X, y_mid its middle across local
    Y. From an axis-aligned rectangle profile (solid and profile positions
    read), else from the geometry kernel's local extents when those supplied
    the wall's dimensions. None when the body cannot be located without
    guessing (a rotated profile or solid, or no geometry).
    """
    if solids and not from_extents:
        sol = solids[0]
        p = sol.SweptArea
        if not p.is_a("IfcRectangleProfileDef"):
            return None
        lx = ly = 0.0
        pos = getattr(sol, "Position", None)
        if pos is not None:
            if not (
                _unit_ok(pos.Axis, (0.0, 0.0, 1.0)) and _unit_ok(pos.RefDirection, (1.0, 0.0, 0.0))
            ):
                return None
            c = pos.Location.Coordinates
            lx, ly = float(c[0]), float(c[1])
        px = py = 0.0
        ppos = getattr(p, "Position", None)
        if ppos is not None:
            if not _unit_ok(getattr(ppos, "RefDirection", None), (1.0, 0.0)):
                return None
            c = ppos.Location.Coordinates
            px, py = float(c[0]), float(c[1])
        cx, cy = (lx + px) * scale, (ly + py) * scale
        return cx - float(p.XDim) * scale / 2, cy, "body profile"
    if from_extents:
        verts = _geom_verts(el)
        if not verts:
            return None
        xs = [verts[i] * scale for i in range(0, len(verts), 3)]
        ys = [verts[i + 1] * scale for i in range(0, len(verts), 3)]
        return min(xs), 0.5 * (min(ys) + max(ys)), "body geometry extents"
    return None


def _layer_usage_mid(el, scale):
    """Middle of the layer band across the wall from IfcMaterialLayerSetUsage.

    Measured from the reference line (the wall's local X axis): POSITIVE
    sense puts the layers at [offset, offset + total], NEGATIVE at
    [offset - total, offset]. Only LayerSetDirection AXIS2 (across a wall)
    is read. None when the wall has no such usage or no layer thickness.
    """
    for rel in getattr(el, "HasAssociations", None) or []:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        u = rel.RelatingMaterial
        if u is None or not u.is_a("IfcMaterialLayerSetUsage"):
            continue
        if str(u.LayerSetDirection or "").upper() != "AXIS2" or u.ForLayerSet is None:
            continue
        total = (
            sum(float(l.LayerThickness or 0) for l in u.ForLayerSet.MaterialLayers or []) * scale
        )
        off = float(u.OffsetFromReferenceLine or 0) * scale
        if not (total > 0 and math.isfinite(off)):
            continue
        if str(u.DirectionSense or "").upper() == "NEGATIVE":
            return off - total / 2
        return off + total / 2
    return None


def _wall_centreline_fix(el, solids, from_extents, scale):
    """(x0, y_mid, note) moving a wall's axis onto its body centreline.

    The body geometry is what the file draws, so it wins; the layer-set
    usage is the fallback, and a disagreement between the two is noted.
    With neither, the axis stays (0, 0) and the note says so.
    """
    body = _body_axis_local(el, solids, from_extents, scale)
    usage = _layer_usage_mid(el, scale)
    if body is not None:
        x0, ym, src = body
        note = f"body centreline {ym:+.3f} m across, {x0:+.3f} m along the axis (from {src})"
        if usage is not None and abs(usage - ym) > _USAGE_AGREE_M:
            note += (
                f"; layer-set usage puts the layers' middle at {usage:+.3f} m, body geometry used"
            )
        return x0, ym, note
    if usage is not None:
        return 0.0, usage, f"body centreline {usage:+.3f} m across the axis (from layer-set usage)"
    return 0.0, 0.0, "body not located and no layer-set usage; wall axis used as centreline"


def _apply_wall_centrelines(model, fixes):
    """Shift Tier 0 IFC envelope segments onto their wall body centrelines.

    Runs after opening attachment (which matches segments to wall placements
    by start point) and before linings, joins, wall loops and facade
    classification, which all assume centrelines (#579). Returns how many
    segments moved.
    """
    moved = 0
    for w in model.envelope:
        if w.provenance is None or w.provenance.method != "ifc_import:tier0:envelope":
            continue
        gid = (w.provenance.note or "").split(" ", 1)[0][len("GlobalId=") :]
        fix = fixes.get(gid)
        if fix is None or len(w.from_m) < 2 or len(w.to_m) < 2:
            continue
        x0, ym, note = fix
        (ax, ay), (bx, by) = w.from_m, w.to_m
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-9:
            continue
        dx, dy = (bx - ax) / L, (by - ay) / L
        # local +Y in the canonical (y-down) frame is (dy, -dx)
        sx, sy = dx * x0 + dy * ym, dy * x0 - dx * ym
        if abs(x0) > _AXIS_SAME_M or abs(ym) > _AXIS_SAME_M:
            w.from_m = [round(ax + sx, 4), round(ay + sy, 4)]
            w.to_m = [round(bx + sx, 4), round(by + sy, 4)]
            moved += 1
            w.provenance.note += f"; moved onto {note}"
        elif "wall axis used" in note:
            w.provenance.note += f"; {note}"
    return moved


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


_ELEMENT_CLASSES = (
    "IfcWall",
    "IfcWallStandardCase",
    "IfcSlab",
    "IfcRoof",
    "IfcColumn",
    "IfcBeam",
    "IfcCurtainWall",
    "IfcDuctSegment",
    "IfcDistributionElement",
)
STOREY_ELEVATION_TOL_M = 0.1  # Pascal storey-semantics.ts


def _storey_above_ground(storey):
    """Pset_BuildingStoreyCommon.AboveGround as True/False, or None when the
    file leaves it absent or UNKNOWN (#634). Never inferred from elevation."""
    import ifcopenshell.util.element as _El

    v = ((_El.get_psets(storey) or {}).get("Pset_BuildingStoreyCommon") or {}).get("AboveGround")
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.upper() in (".T.", "TRUE", ".F.", "FALSE"):
        return v.upper() in (".T.", "TRUE")
    return None


def _storey_by_elevation(z, elevations, tol=STOREY_ELEVATION_TOL_M):
    """Index of the one storey whose band holds height ``z`` (#585).

    A storey's band runs from its elevation (less ``tol``) up to the next
    higher storey's elevation (less ``tol``); the top band is open. Returns
    ``(index, "")`` when exactly one storey fits, else ``(None, reason)``:
    "below" when ``z`` is under the lowest band, "tie" when two storeys
    share the fitting elevation. Never falls back to the lowest storey.
    Idea from Pascal's storey-semantics.ts (MIT, Copyright (c) 2026 Pascal
    Group Inc., commit 67f8041).
    """
    fit = [i for i, e in enumerate(elevations) if e <= z + tol + 1e-9]
    if not fit:
        return None, "below"
    top = max(elevations[i] for i in fit)
    best = [i for i in fit if abs(elevations[i] - top) <= 1e-9]
    if len(best) > 1:
        return None, "tie"
    return best[0], ""


def _storey_less_elements(f, storeys, classes):
    """Elements of ``classes`` no listed storey contains: contained in the
    building or site, or not contained anywhere. GlobalId order."""
    held = {id(s) for s in storeys}
    out = []
    for cls in classes:
        for el in f.by_type(cls, include_subtypes=False):
            rels = [
                r
                for r in getattr(el, "ContainedInStructure", None) or []
                if r.is_a("IfcRelContainedInSpatialStructure")
            ]
            if any(id(r.RelatingStructure) in held for r in rels):
                continue
            if any(
                r.RelatingStructure.is_a("IfcSpatialStructureElement")
                and not r.RelatingStructure.is_a("IfcBuilding")
                and not r.RelatingStructure.is_a("IfcSite")
                for r in rels
            ):
                continue  # in a space or zone: its own route, not this fallback
            out.append(el)
    return sorted(out, key=lambda e: e.GlobalId)


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


def _space_name_number(sp):
    """(name, number, source) for an IfcSpace.

    IFC convention (Revit, ArchiCAD): LongName is the room name and Name is
    the room number (#580). Used when LongName is set and Name is a single
    room-number token; otherwise the convention-dependent Name parse runs,
    keeping LongName as the name when there is one.
    """
    raw_name = (getattr(sp, "Name", None) or "").strip()
    long_name = (getattr(sp, "LongName", None) or "").strip()
    if long_name:
        if raw_name and _ROOMNUM.match(raw_name) and any(c.isdigit() for c in raw_name):
            return long_name, raw_name, "ifc_longname"
        _label, number = _parse_space_name(raw_name)
        return long_name, number, "ifc_longname+name_parse"
    label, number = _parse_space_name(raw_name)
    return label, number, "name_parse"


_TAG_RE = re.compile(r"^(.+?)\s*\(")


# IFC spaces are authored, so their class comes from what the author named
# them and nothing else (Alex, 2026-10-05, #574 option A): no size-based
# defaults. Word match on Name + LongName; shaft wins over closet.
_IFC_SHAFT_RE = re.compile(r"\b(shaft|chase)s?\b", re.IGNORECASE)
_IFC_CLOSET_RE = re.compile(r"\b(closet|storage)s?\b", re.IGNORECASE)
IFC_SHAFT_CONFIDENCE = 0.90
IFC_CLOSET_CONFIDENCE = 0.85


def _ifc_space_class(sp):
    """('shaft'|'closet', confidence, word) from IfcSpace Name/LongName, or None."""
    text = " ".join(str(v) for v in (getattr(sp, "Name", None), getattr(sp, "LongName", None)) if v)
    m = _IFC_SHAFT_RE.search(text)
    if m:
        return "shaft", IFC_SHAFT_CONFIDENCE, m.group(1).lower()
    m = _IFC_CLOSET_RE.search(text)
    if m:
        return "closet", IFC_CLOSET_CONFIDENCE, m.group(1).lower()
    return None


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
    height = None  # stated full height (#640): solid depth, else Qto Height
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
        height = float(depth)
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
    if height is None:
        qh = _quantities(sp, "Qto_SpaceBaseQuantities", scale).get("Height")
        if qh:
            height = float(qh)
    return polygon, area, volume, conf, method, note, height


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
    the wall's placement still lies near one end of the segment, within the
    wall thickness of its line and of that end (so an edit that moved it elsewhere
    cannot hand back a stale direction). Centreline moves (#579) and end
    joins (#575) keep a segment on its wall. Exactly one match or None.
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
        # facade classification re-stamps the method but keeps the note
        and ew.provenance.method in ("ifc_import:tier0:envelope", "ifc_import:tier1:facade")
        and (ew.provenance.note or "").split(" ", 1)[0] == token
        and ew.from_m
        and ew.to_m
        and _on_own_line(ew, origin, _ENVELOPE_TOL_M + float(getattr(el, "thickness_m", 0) or 0))
    ]
    return hits[0] if len(hits) == 1 else None


def _on_own_line(ew, origin, tol):
    """True when ``origin`` lies within ``tol`` of segment ``ew``'s line, near one end."""
    fx, fy = float(ew.from_m[0]), float(ew.from_m[1])
    tx, ty = float(ew.to_m[0]), float(ew.to_m[1])
    L = math.hypot(tx - fx, ty - fy)
    if L <= 1e-9:
        return math.dist((fx, fy), origin) <= tol
    ux, uy = (tx - fx) / L, (ty - fy) / L
    ox, oy = origin[0] - fx, origin[1] - fy
    along = ox * ux + oy * uy
    perp = abs(ox * uy - oy * ux)
    return perp <= tol and (abs(along) <= tol or abs(along - L) <= tol)


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
                    plan_center_m=list(bo.plan_center_m),
                    construction_id=_opening_construction_id(model, bo),
                )
            )


def _apply_skylight_daylight(model):
    """Daylight area under each space's skylights (roadmap item 3).

    Ceiling height is taken as the storey wall height: IFC spaces here carry
    no separate ceiling, and the wall height is the floor-to-structure
    height the import already derives. Spaces without skylights are untouched.
    """
    from daylight_skylights import compute_skylight_daylight

    heights = {lv.id: lv.wall_height_m for lv in model.levels}
    for sp in model.spaces.values():
        if any(o.category == "skylight" for o in sp.openings):
            compute_skylight_daylight(sp, heights.get(sp.level_id))


# Tier 1 (#666): an opening placed by the host wall's own RefDirection, when no
# envelope edge settled the wall's direction, attaches just above the review
# threshold (REVIEW_CONFIDENCE 0.80) so it is used but still visibly weaker
# than an envelope-matched attachment.
_REF_DIRECTION_CONFIDENCE = 0.85
_REF_DIRECTION_METHOD = "ifc_ref_direction"
# How far past the wall face each side probe reaches into the room (m).
_SIDE_PROBE_M = 0.05


def _space_at(pt, level_id, spaces):
    """First space (by id) on ``level_id`` whose polygon contains ``pt``, or None."""
    for sp in spaces:
        if level_id and sp.level_id and sp.level_id != level_id:
            continue
        if sp.polygon_m and len(sp.polygon_m) >= 3 and _point_in_polygon(pt, sp.polygon_m):
            return sp
    return None


def _opening_sides(ox, oy, wall_dir, thickness_m, level_id, spaces):
    """Spaces on either side of a wall opening at (ox, oy), lowest id first.

    Probes just past each wall face along the wall normal, so an opening in an
    interior wall finds both rooms and one in an exterior wall finds one. When
    neither probe lands in a room (a wall of unknown thickness drawn away from
    the room edge), the opening centre itself is tried, as before #666.
    """
    off = float(thickness_m or 0.0) / 2.0 + _SIDE_PROBE_M
    nx, ny = -wall_dir[1], wall_dir[0]
    sides = []
    for sgn in (1.0, -1.0):
        sp = _space_at((ox + sgn * nx * off, oy + sgn * ny * off), level_id, spaces)
        if sp is not None and sp not in sides:
            sides.append(sp)
    if not sides:
        sp = _space_at((ox, oy), level_id, spaces)
        if sp is not None:
            sides.append(sp)
    return sorted(sides, key=lambda sp: sp.id)


# probes this far inside each opening edge, so a jamb that lands exactly on a
# partition line does not count as reaching the next room
_EDGE_INSET_M = 0.05


def _span_other_spaces(el, wall_dir, interval, sides, cx, cy, level_id, spaces):
    """Ids of rooms an opening's edges reach that its centre does not (#681).

    Re-runs the side probes just inside each end of ``interval`` (along the host
    wall from its origin). A room found there but not at the centre means the
    opening straddles a room boundary, so which room it opens into is
    ambiguous. Openings with no width, or narrower than two insets, are not
    checked.
    """
    if not interval or interval[1] - interval[0] <= 2 * _EDGE_INSET_M:
        return []
    centre = {sp.id for sp in sides}
    found = set()
    for s in (interval[0] + _EDGE_INSET_M, interval[1] - _EDGE_INSET_M):
        px, py = cx + wall_dir[0] * s, cy + wall_dir[1] * s
        for sp in _opening_sides(px, py, wall_dir, el.thickness_m, level_id, spaces):
            if sp.id not in centre:
                found.add(sp.id)
    return sorted(found)


def _attach_openings_to_spaces(model):
    """Tier 1: attach IFC wall openings to the spaces they open into (#665, #666).

    The host wall's along-wall direction comes from its matching envelope edge,
    falling back to the wall's own RefDirection (attached at confidence 0.85,
    method ``ifc_ref_direction``, counted in ``ref_direction_fallback``). The
    opening's point is then probed on both wall faces: an interior opening is
    stored ONCE, on the lower-id space, with ``adjacent_space_id`` naming the
    other, so doors are never double-counted. Spaces on other levels are never
    candidates.

    Openings left unattached are counted in ``model.opening_attachment_summary``
    by reason (``no_envelope_edge``, ``ambiguous_tie``, ``no_ref_direction``,
    ``outside_spaces``) and each gets an ``opening_attachment`` review item.
    Each attached opening gets ``host_interval_m`` along its host wall (same
    frame as ``s_center_m``); one whose edges reach a room its centre does not
    is attached by its centre and flagged ``adjacency_ambiguous`` (#681).
    The ``space_opening_attachment`` validation check reports them per level.
    """
    _attach_skylights_to_spaces(model)
    summary = model.opening_attachment_summary
    spaces = sorted(model.spaces.values(), key=lambda sp: sp.id)
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
        via_ref_direction = False
        failure_reason = None  # one of the _WALL_DIR_* constants, or None

        if wall_dir is None:
            wall_dir = _wall_direction_from_entity(el)
            if wall_dir is None:
                # The host wall's axis is unknown, so the openings' positions along it
                # cannot be reconstructed. Leave them unattached rather than
                # attaching them to a space the geometry does not support.
                failure_reason = env_reason or _WALL_DIR_NO_REF
            else:
                via_ref_direction = True

        if wall_dir is None:
            # Record the failure reason for import-summary reporting.
            num_openings = len(el.openings)
            if failure_reason == _WALL_DIR_NO_EDGE:
                summary.no_envelope_edge += num_openings
                reason_desc = "no envelope edge matched wall placement"
            elif failure_reason == _WALL_DIR_AMBIGUOUS_TIE:
                summary.ambiguous_tie += num_openings
                reason_desc = "ambiguous tie (multiple same-length edges)"
            else:
                summary.no_ref_direction += num_openings
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
            # el.placement_m is that same origin (#505).
            ox = cx + wall_dir[0] * bo.s_center_m
            oy = cy + wall_dir[1] * bo.s_center_m
            sides = _opening_sides(ox, oy, wall_dir, el.thickness_m, el.level_id, spaces)
            if not sides:
                summary.outside_spaces += 1
                model.flag_for_review(
                    kind="opening_attachment",
                    description=(
                        f"Opening {bo.id} ({bo.category} {bo.tag or 'untagged'}) on wall "
                        f"{el.global_id} lies in no space on "
                        f"{el.level_id or 'its level'} on either side of the wall; "
                        "left unattached."
                    ),
                    confidence=0.3,
                    provenance=bo.provenance
                    or el.provenance
                    or Provenance(
                        sheet_id="ifc",
                        revision=0,
                        method="ifc_import:tier1:opening_attachment",
                        confidence=0.3,
                        note=f"GlobalId={bo.id}",
                    ),
                )
                continue
            prov = bo.provenance
            if via_ref_direction:
                summary.ref_direction_fallback += 1
                prov = Provenance(
                    sheet_id=prov.sheet_id if prov else "ifc",
                    revision=prov.revision if prov else 0,
                    method=_REF_DIRECTION_METHOD,
                    confidence=_REF_DIRECTION_CONFIDENCE,
                    note=(
                        f"GlobalId={bo.id}; host wall {el.global_id} direction from "
                        "its own RefDirection (no envelope edge matched)"
                    ),
                )
            width = float(bo.width_m or 0.0)
            interval = None
            if width > 0:
                s0 = max(0.0, bo.s_center_m - width / 2.0)
                s1 = min(float(length_m), bo.s_center_m + width / 2.0)
                interval = [round(s0, 3), round(s1, 3)]
            others = _span_other_spaces(el, wall_dir, interval, sides, cx, cy, el.level_id, spaces)
            ambiguous = bool(others)
            if ambiguous:
                summary.adjacency_ambiguous += 1
                model.flag_for_review(
                    kind="adjacency_ambiguous",
                    description=(
                        f"Opening {bo.id} ({bo.category} {bo.tag or 'untagged'}) on wall "
                        f"{el.global_id} spans a room boundary: its centre opens into "
                        f"{', '.join(sp.id for sp in sides)} but its edge reaches "
                        f"{', '.join(others)}. Attached to {sides[0].id}; confirm."
                    ),
                    confidence=0.5,
                    provenance=prov
                    or Provenance(
                        sheet_id="ifc",
                        revision=0,
                        method="ifc_import:tier1:opening_attachment",
                        confidence=0.5,
                        note=f"GlobalId={bo.id}",
                    ),
                )
            sides[0].openings.append(
                SpaceOpening(
                    id=bo.id,
                    tag=bo.tag,
                    category=bo.category,
                    width_m=width,
                    height_m=bo.height_m or 0.0,
                    sill_m=bo.sill_m,
                    s_center_m=bo.s_center_m,
                    host_interval_m=interval,
                    provenance=prov,
                    needs_review=ambiguous or prov is None or prov.confidence < REVIEW_CONFIDENCE,
                    adjacent_space_id=sides[1].id if len(sides) > 1 else None,
                    construction_id=_opening_construction_id(model, bo),
                )
            )


def _ifc_doors(model):
    """Door connectors for space_merge from IFC door openings with a plan centre."""
    out = []
    for el in model.bim_elements:
        for bo in el.openings:
            if bo.category == "door" and bo.plan_center_m:
                out.append(
                    {"id": bo.id, "level_id": el.level_id, "plan_center_m": bo.plan_center_m}
                )
    return out


IDENTITY_PSET = "Matchline_Identity"  # written by bem_ifc4 on export (#588)


def _matchline_identity(*els):
    """Matchline_Identity props of the first element carrying a MatchlineId, or None."""
    try:
        import ifcopenshell.util.element as _El
    except ImportError:
        return None
    for el in els:
        if el is None:
            continue
        try:
            props = (_El.get_psets(el) or {}).get(IDENTITY_PSET) or {}
        except (AttributeError, RuntimeError):
            continue
        if props.get("MatchlineId"):
            return props
    return None


def _rejoin_split_spaces(model, groups, space_by_gid, authored_spaces, prov):
    """Put rooms split at Appendix G block lines (#638) back together.

    ``groups`` maps a parent id (Matchline_Identity.SplitFrom) to the ids of
    its pieces on one storey. Pieces that union to one polygon without a hole
    become one space with the parent id, at the first piece's place in
    ``model.spaces``: polygon is the union, area/volume/lighting watts summed,
    everything else from the first piece by id. Anything else (the parent id
    already taken, pieces that do not touch) keeps the pieces and goes to
    review.
    """
    import dataclasses

    from shapely.geometry import Polygon as _Poly
    from shapely.geometry.polygon import orient as _orient
    from shapely.ops import unary_union as _union

    for parent in sorted(groups):
        sids = sorted(groups[parent])
        pieces = [model.spaces[s] for s in sids if s in model.spaces]
        why = None
        if parent in model.spaces:
            why = "the parent id is already a space"
        geoms = [_Poly(p.polygon_m) for p in pieces if len(p.polygon_m) >= 3]
        u = _union([g.buffer(0) for g in geoms]) if geoms and why is None else None
        if why is None and (len(geoms) != len(pieces) or u.geom_type != "Polygon" or u.interiors):
            why = "the pieces do not join into one polygon"
        if why:
            model.flag_for_review(
                kind="matchline_identity",
                description=(
                    f"Spaces {', '.join(sids)} were split from {parent} but not rejoined: {why}"
                ),
                confidence=0.5,
                provenance=prov("ifc_import:tier0:matchline_identity", 0.5, "", parent),
            )
            continue
        first = pieces[0]
        ring = [[round(x, 4), round(y, 4)] for x, y in list(_orient(u, 1.0).exterior.coords)[:-1]]
        areas = [p.area_m2 for p in pieces]
        vols = [p.volume_m3 for p in pieces]
        area = round(sum(areas), 4) if None not in areas else round(u.area, 4)
        lighting = first.lighting
        total_w = sum(p.lighting.total_w for p in pieces)
        if total_w > 0:
            lpd = total_w / area if area else None
            lighting = dataclasses.replace(
                first.lighting,
                fixtures=[f for p in pieces for f in p.lighting.fixtures],
                total_w=total_w,
                lpd_w_m2=lpd,
                lpd_w_ft2=lpd / 10.7639104 if lpd is not None else None,
                provenance=next(
                    (p.lighting.provenance for p in pieces if p.lighting.provenance), None
                ),
                unmatched_tags=sorted({t for p in pieces for t in p.lighting.unmatched_tags}),
            )
        merged = []
        for p in pieces:
            merged += [m for m in p.merged_from if m not in merged]
        prov_ = dataclasses.replace(first.core_provenance) if first.core_provenance else None
        if prov_ is not None:
            prov_.note += (
                f"; rejoined from {len(pieces)} pieces split at Appendix G block lines "
                f"(#638): {', '.join(sids)}"
            )
        joined = dataclasses.replace(
            first,
            id=parent,
            polygon_m=ring,
            area_m2=area,
            volume_m3=round(sum(vols), 4) if None not in vols else None,
            lighting=lighting,
            merged_from=merged,
            core_provenance=prov_,
        )
        rebuilt = {}
        for k, v in model.spaces.items():
            if k == first.id:
                rebuilt[parent] = joined
            elif k not in sids:
                rebuilt[k] = v
        model.spaces.clear()
        model.spaces.update(rebuilt)
        for gid, sid in list(space_by_gid.items()):
            if sid in sids:
                space_by_gid[gid] = parent
        authored_spaces.difference_update(sids)
        authored_spaces.add(parent)


def _merge_ifc_closets_and_shafts(model, skip_ids=()):
    """Apply the closet/shaft rule (space_merge) to name-classified IFC spaces.

    Runs before envelope classification so walls, openings and daylight land
    on the merged space. Returns a summary fragment, or "" when the model has
    no closets or shafts.
    """
    if not any(s.poly_type in ("closet", "shaft") for s in model.spaces.values()):
        return ""
    from space_merge import merge_closets_and_shafts

    res = merge_closets_and_shafts(model, doors=_ifc_doors(model), skip_ids=skip_ids)
    kept = sum(1 for it in model.review_queue if it.kind == "space_merge")
    return f"space_merge: {len(res.merged)} merged, {kept} kept for review"


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

    The host is found geometrically: the wall segment whose line the plate's
    inner edge lies nearest (within SHADE_HOST_TOL_M) and within its length.
    Placement is then measured in that segment's own frame; the gap from the
    segment line to the plate's inner edge (half the wall when the segment
    is a centreline, #579) is kept as ``offset_m`` so depth stays measured
    from the wall face. A plate with no such
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
    near, w, ss, ds = best
    s_rng = max(ss) - min(ss)
    depth = max(ds) - min(ds)
    # a centreline host sits half a wall inside the face the plate touches
    offset = round(near, 4) if near > 1e-4 else 0.0
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
            offset_m=offset,
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
            offset_m=offset,
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


# An outward normal whose larger component is below cos(40 deg) points within
# 5 deg of a diagonal: the nearest cardinal is kept (exports and the facade
# area budget need one) but the call goes to review as ``facade_unclear``.
_FACADE_DIAGONAL_COS = math.cos(math.radians(40.0))


def _facade_is_unclear(nx, ny):
    """True when an outward normal is too close to a diagonal to call a facade."""
    n = math.hypot(nx, ny)
    if n < 1e-9:
        return True
    return max(abs(nx), abs(ny)) / n < _FACADE_DIAGONAL_COS


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
# ...and for upward heat flow (roofs, heating case), ISO 6946 Table 1.
RSI_ROOF_M2K_W = 0.10
RSE_ROOF_M2K_W = 0.04


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


def _layer_sets(el):
    """The IfcMaterialLayerSets associated with an element (usage unwrapped)."""
    sets = []
    for rel in getattr(el, "HasAssociations", None) or []:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        rm = rel.RelatingMaterial
        if rm is not None and rm.is_a("IfcMaterialLayerSetUsage"):
            rm = rm.ForLayerSet
        if rm is not None and rm.is_a("IfcMaterialLayerSet"):
            sets.append(rm)
    return sets


def _layered_wall_u(wall, scale, use_lookup=False):
    """Wall U from its layers, horizontal heat flow. See ``_layered_u``."""
    return _layered_u(wall, scale, use_lookup, RSI_WALL_M2K_W, RSE_WALL_M2K_W, "horizontal")


def _layered_roof_u(roof, scale, use_lookup=False):
    """Roof U from its layers, upward heat flow. See ``_layered_u``."""
    return _layered_u(roof, scale, use_lookup, RSI_ROOF_M2K_W, RSE_ROOF_M2K_W, "upward")


def _layered_u(el, scale, use_lookup, rsi, rse, air_direction):
    """Element U in W/m2K from its IfcMaterialLayerSet, as (u, looked_up) or None.

    U = 1 / (Rsi + sum(t_i / k_i) + Rse), ISO 6946 surface resistances for
    the heat flow direction (walls horizontal, roofs upward; air layers use
    the matching Table 2 column). Computed only when the element has exactly
    one layer set and EVERY layer has
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

    sets = _layer_sets(el)
    if len(sets) != 1 or not sets[0].MaterialLayers:
        return None
    r = rsi + rse
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
                r_air = air_layer_resistance(t, air_direction)
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


def _read_roof_construction(model, f, prov, scale=1.0):
    """Set ``model.roof_construction_id`` from the roofs' stated U.

    Roof elements are IfcRoof and IfcSlab with PredefinedType ROOF. When
    every one that states a U agrees, one ``IFC-RU<value>`` construction is
    made (``ifc_import:tier0:roof_u``, conf 0.9). Disagreeing values are not
    averaged: the roof stays unset and the returned note (logged in the
    import revision summary) says why. Returns that note, or "".

    With no stated U anywhere, the U is derived from roof layer sets (ISO
    6946 upward heat flow): ``IFC-RUL<value>`` from the file's own
    conductivities (``roof_u_layers``, 0.8) or ``IFC-RUM<value>`` when any
    layer needed the materials table (``roof_u_lookup``, 0.6). Every roof
    element WITH a layer set must yield a value and all must agree; one that
    cannot be derived leaves the roof generic too, since a single roof
    construction would then guess for it. Elements without a layer set (an
    IfcRoof aggregating its slabs) are skipped.
    """
    roofs = list(f.by_type("IfcRoof")) + [
        s for s in f.by_type("IfcSlab") if getattr(s, "PredefinedType", None) == "ROOF"
    ]
    stated = [(r.GlobalId, u) for r in roofs if (u := _roof_thermal_transmittance(r)) is not None]
    if not stated:
        return _derive_roof_construction(model, roofs, prov, scale)
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


MAX_FINISH_THICKNESS_M = 0.06  # Pascal room-first.ts
FLOOR_TOP_TOL_M = 0.02  # Pascal room-first.ts FLOOR_TOP_TOLERANCE
FINISH_ON_SLAB_FRAC = 0.95  # finish plan area that must lie on the slab
FINISH_SPACE_FRAC = 0.5  # space floor share a finish must cover to be recorded


def _slab_shape(el, scale):
    """(plan hull in the canonical frame, z bottom, z top) or None."""
    from shapely.geometry import MultiPoint

    verts = _geom_verts(el)
    if not verts:
        return None
    t = _placement_transform(el, scale)
    pts, zs = [], []
    for i in range(0, len(verts), 3):
        wx, wy, wz = _apply(t, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale)
        pts.append(_to_canonical(wx, wy))
        zs.append(wz)
    hull = MultiPoint(pts).convex_hull
    if hull.area < 1e-6:
        return None
    return hull, min(zs), max(zs)


def _classify_finish_slabs(model, f, scale):
    """Mark finish floors so they never count as a second floor (#584).

    A slab no thicker than MAX_FINISH_THICKNESS_M is a finish when it sits on
    a thicker slab of the same level (its bottom within FLOOR_TOP_TOL_M of
    that slab's top) and at least FINISH_ON_SLAB_FRAC of its plan lies on it.
    A thin slab with nothing under it stays an ordinary slab. Finishes get
    ``role="finish"``, stay out of the ground-slab U, and are recorded on the
    spaces whose floor they mostly cover. Plans are convex hulls of the
    solids. Idea from Pascal's room-first.ts (MIT, Copyright (c) 2026 Pascal
    Group Inc., commit 67f8041). Returns (finish GlobalIds, thin slabs left).
    """
    from shapely.geometry import Polygon

    slabs = []
    for e in model.bim_elements:
        if e.ifc_class != "IfcSlab" or e.role:
            continue
        try:
            el = f.by_guid(e.global_id)
        except RuntimeError:
            continue
        shape = _slab_shape(el, scale)
        if shape is not None:
            slabs.append((e, *shape))
    finishes, left = set(), 0
    for e, hull, z0, z1 in sorted(slabs, key=lambda s: s[0].global_id):
        if z1 - z0 > MAX_FINISH_THICKNESS_M + 1e-9:
            continue
        under = [
            (s, h)
            for s, h, b0, b1 in slabs
            if s is not e
            and s.level_id == e.level_id
            and b1 - b0 > MAX_FINISH_THICKNESS_M + 1e-9
            and abs(b1 - z0) <= FLOOR_TOP_TOL_M + 1e-9
        ]
        on = max((hull.intersection(h).area for _, h in under), default=0.0)
        if not under or on < FINISH_ON_SLAB_FRAC * hull.area:
            left += 1
            continue
        e.role = "finish"
        thick = round(z1 - z0, 4)
        if e.provenance is not None:
            e.provenance.note += (
                f"; finish floor ({thick} m) on a structural slab, not a floor surface"
            )
        finishes.add(e.global_id)
        for sp in model.spaces.values():
            if sp.level_id != e.level_id or not sp.polygon_m or len(sp.polygon_m) < 3:
                continue
            poly = Polygon(sp.polygon_m)
            if poly.area > 0 and poly.intersection(hull).area >= FINISH_SPACE_FRAC * poly.area:
                sp.floor_finishes.append({"global_id": e.global_id, "thickness_m": thick})
    return finishes, left


CEILING_FLAT_TOL_M = 0.01  # every vertex within this of the bottom or top face
CEILING_AGREE_TOL_M = 0.01  # coverings over one space must agree this closely
CEILING_SPACE_FRAC = 0.5  # space floor share a covering must cover


def _flat_shape(el, scale):
    """(hull, z bottom, z top) of a flat solid, ("sloped", None, None), or None."""
    shape = _slab_shape(el, scale)
    if shape is None:
        return None
    _, z0, z1 = shape
    t = _placement_transform(el, scale)
    v = _geom_verts(el) or []
    for i in range(0, len(v), 3):
        z = _apply(t, v[i] * scale, v[i + 1] * scale, v[i + 2] * scale)[2]
        if min(abs(z - z0), abs(z - z1)) > CEILING_FLAT_TOL_M:
            return ("sloped", None, None)
    return shape


def _read_ceilings(model, f, scale, space_by_gid, level_by_storey, prov):
    """Ceiling height and plenum depth per space from IfcCovering CEILING (#583).

    A covering serves the spaces named by IfcRelCoversSpaces, else the spaces
    on its storey whose floor it covers by at least CEILING_SPACE_FRAC.
    ``ceiling_height_m`` is the covering underside above the level elevation;
    ``plenum_depth_m`` runs from the covering top to the underside of the
    lowest flat slab or roof above it over that space (None when there is
    none). A sloped covering, or coverings over one space that disagree, leave
    the space at None with a review item: never averaged. Idea from Pascal's
    ceiling handling (MIT, Copyright (c) 2026 Pascal Group Inc., commit
    67f8041). Returns (spaces with a ceiling, spaces without, coverings read).
    """
    from shapely.geometry import Polygon

    covs = [c for c in f.by_type("IfcCovering") if getattr(c, "PredefinedType", None) == "CEILING"]
    if not covs:
        return 0, 0, 0
    named = {}  # covering GlobalId -> [space id]
    for rel in f.by_type("IfcRelCoversSpaces"):
        sid = space_by_gid.get(getattr(rel.RelatingSpace, "GlobalId", None))
        for c in rel.RelatedCoverings or []:
            if sid:
                named.setdefault(c.GlobalId, []).append(sid)
    level_of = {}
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        lid = level_by_storey.get(getattr(rel.RelatingStructure, "GlobalId", None))
        for o in rel.RelatedElements or []:
            if lid:
                level_of[o.GlobalId] = lid
    polys = {
        sid: Polygon(sp.polygon_m)
        for sid, sp in model.spaces.items()
        if sp.polygon_m and len(sp.polygon_m) >= 3
    }
    above = []  # flat slabs/roofs: (hull, z bottom)
    for e in model.bim_elements:
        if e.ifc_class not in ("IfcSlab", "IfcRoof") or e.role:
            continue
        try:
            shape = _flat_shape(f.by_guid(e.global_id), scale)
        except RuntimeError:
            continue
        if shape and shape[0] != "sloped":
            above.append((shape[0], shape[1]))
    found = {}  # space id -> [(GlobalId, "sloped") | (GlobalId, underside, top)]
    for c in sorted(covs, key=lambda c: c.GlobalId):
        shape = _flat_shape(c, scale)
        if shape is None:
            continue
        if c.GlobalId in named:
            sids = named[c.GlobalId]
        elif shape[0] == "sloped":
            continue  # no plan footprint to place it by; nothing claimed
        else:
            lid = level_of.get(c.GlobalId)
            sids = [
                sid
                for sid, poly in polys.items()
                if model.spaces[sid].level_id == lid
                and poly.area > 0
                and poly.intersection(shape[0]).area >= CEILING_SPACE_FRAC * poly.area
            ]
        for sid in sids:
            if shape[0] == "sloped":
                found.setdefault(sid, []).append((c.GlobalId, "sloped"))
            else:
                found.setdefault(sid, []).append((c.GlobalId, shape[1], shape[2]))
    with_c = 0
    for sid, hits in sorted(found.items()):
        sp = model.spaces[sid]
        gids = ", ".join(h[0] for h in hits)
        why = ""
        if any(h[1] == "sloped" for h in hits):
            why = f"sloped ceiling covering ({gids}); height not averaged"
        elif max(h[1] for h in hits) - min(h[1] for h in hits) > CEILING_AGREE_TOL_M:
            why = f"ceiling coverings at different heights ({gids}); left unresolved"
        if why:
            model.flag_for_review(
                kind="ceiling",
                description=f"Space {sid}: {why}",
                confidence=0.4,
                provenance=prov("ifc_import:tier0:ceiling", 0.4, why, hits[0][0]),
            )
            continue
        gid, under, top = hits[0]
        elev = next((lv.elevation_z_m for lv in model.levels if lv.id == sp.level_id), None)
        if elev is None:
            continue
        sp.ceiling_height_m = round(under - elev, 4)
        poly = polys.get(sid)
        over = [
            z0
            for h, z0 in above
            if z0 >= top - CEILING_FLAT_TOL_M
            and poly is not None
            and poly.area > 0
            and poly.intersection(h).area >= CEILING_SPACE_FRAC * poly.area
        ]
        sp.plenum_depth_m = round(min(over) - top, 4) if over else None
        note = f"ceiling {sp.ceiling_height_m} m from IfcCovering CEILING"
        if sp.plenum_depth_m is not None:
            note += f"; plenum {sp.plenum_depth_m} m to the slab above"
        sp.history.append(prov("ifc_import:tier0:ceiling", 0.9, note, gid))
        with_c += 1
    return with_c, len(model.spaces) - with_c, len(covs)


def _plan_geom(el, scale):
    """Canonical-frame plan footprint (shapely convex hull, any dimension) or None."""
    from shapely.geometry import MultiPoint

    verts = _geom_verts(el)
    if not verts:
        return None
    t = _placement_transform(el, scale)
    pts = []
    for i in range(0, len(verts), 3):
        wx, wy, _ = _apply(t, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale)
        pts.append(_to_canonical(wx, wy))
    hull = MultiPoint(pts).convex_hull
    return None if hull.is_empty else hull


def _find_virtual_borders(model, f, scale, level_by_storey, sheet, revision):
    """Wall-less space borders (#582); see ifc_space_borders."""
    from ifc_space_borders import find_virtual_borders

    level_of = {}
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        lid = level_by_storey.get(getattr(rel.RelatingStructure, "GlobalId", None))
        for o in rel.RelatedElements or []:
            if lid:
                level_of[o.GlobalId] = lid
    walls = {}
    for e in model.bim_elements:
        if not e.ifc_class.startswith(("IfcWall", "IfcCurtainWall")):
            continue
        try:
            g = _plan_geom(f.by_guid(e.global_id), scale)
        except RuntimeError:
            continue
        if g is not None:
            walls.setdefault(e.level_id, []).append(g)
    virtual = {}
    for v in sorted(f.by_type("IfcVirtualElement"), key=lambda v: v.GlobalId):
        lid = level_of.get(v.GlobalId)
        g = _plan_geom(v, scale) if lid else None
        if g is not None:
            virtual.setdefault(lid, []).append((v.GlobalId, g))
    return find_virtual_borders(model, walls, virtual, sheet=sheet, revision=revision)


def _read_slab_construction(model, f, prov, skip=()):
    """Set ``model.slab_construction_id`` from ground slabs' stated U.

    Ground slabs are IfcSlab with PredefinedType BASESLAB (a FLOOR slab may be
    suspended or intermediate, so it is not assumed to touch the ground).
    Only a stated Pset_SlabCommon.ThermalTransmittance is used: a slab on
    grade's U depends on the ground (ISO 13370), so it is never derived from
    layers with ISO 6946. Agreeing values make one ``IFC-SU<value>``
    construction (``ifc_import:tier0:slab_u``, conf 0.9); disagreeing values
    leave the slab generic and the returned note says why.
    """
    slabs = [
        s
        for s in f.by_type("IfcSlab")
        if getattr(s, "PredefinedType", None) == "BASESLAB" and s.GlobalId not in skip
    ]
    stated = [(s.GlobalId, u) for s in slabs if (u := _roof_thermal_transmittance(s)) is not None]
    if not stated:
        return ""
    us = [u for _, u in stated]
    if max(us) - min(us) > ROOF_U_AGREE_TOL:
        detail = ", ".join(f"{g}={u:g}" for g, u in stated[:5])
        return f"ground slabs state different U-values ({detail}); slab left generic"
    gid, u = stated[0]
    cid = f"IFC-SU{u:.4f}"
    if cid not in model.constructions:
        model.constructions[cid] = Construction(
            id=cid,
            name=f"IFC ground slab, ThermalTransmittance {u:.4f} W/m2K",
            u_value_w_m2k=round(u, 6),
            provenance=prov(
                "ifc_import:tier0:slab_u",
                0.9,
                f"BASESLAB ThermalTransmittance; {len(stated)} slab(s) agree",
                gid,
            ),
        )
    model.slab_construction_id = cid
    return ""


def _derive_roof_construction(model, roofs, prov, scale):
    """Layer-derived roof construction (see ``_read_roof_construction``)."""
    derived, failed = [], []
    for r in roofs:
        if not _layer_sets(r):
            continue
        got, src = _layered_roof_u(r, scale), "layers"
        if got is None:
            got, src = _layered_roof_u(r, scale, use_lookup=True), "lookup"
        if got is None:
            failed.append(r.GlobalId)
        else:
            derived.append((r.GlobalId, got[0], src, got[1]))
    if failed:
        return (
            f"roof layer U not derivable for {len(failed)} roof element(s) "
            f"({', '.join(failed[:5])}); roof left generic"
        )
    if not derived:
        return ""
    us = [u for _, u, _, _ in derived]
    if max(us) - min(us) > ROOF_U_AGREE_TOL:
        detail = ", ".join(f"{g}={u:.4f}" for g, u, _, _ in derived[:5])
        return f"roof layer sets give different U-values ({detail}); roof left generic"
    gid, u = derived[0][0], derived[0][1]
    lookups = [x for _, _, s, lu in derived if s == "lookup" for x in lu]
    if lookups:
        from materials import AIR_SOURCE_UPWARD, SOURCE

        names = "; ".join(f"{n!r}->{eid}" for n, eid in dict.fromkeys(lookups))
        air = any(eid == "air_iso6946" for _, eid in lookups)
        cid, conf, method = f"IFC-RUM{u:.4f}", 0.6, "ifc_import:tier0:roof_u_lookup"
        name = f"IFC roof, U {u:.4f} W/m2K from material layers + conductivity lookup"
        why = (
            f"U from roof layer sets, ISO 6946 Rsi 0.10 + Rse 0.04 (upward); "
            f"looked up {names}; {SOURCE}"
            + (f"; air layers per {AIR_SOURCE_UPWARD}" if air else "")
        )
    else:
        cid, conf, method = f"IFC-RUL{u:.4f}", 0.8, "ifc_import:tier0:roof_u_layers"
        name = f"IFC roof, U {u:.4f} W/m2K from material layers (ISO 6946)"
        why = (
            "U from roof IfcMaterialLayerSet thickness / Pset_MaterialThermal "
            "conductivity, ISO 6946 Rsi 0.10 + Rse 0.04 (upward)"
        )
    why += f"; {len(derived)} roof element(s) agree"
    if cid not in model.constructions:
        model.constructions[cid] = Construction(
            id=cid,
            name=name,
            u_value_w_m2k=round(u, 6),
            provenance=prov(method, conf, why, gid),
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


def _ratio(v):
    """A 0..1 ratio from a property value, or None."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and 0.0 < v <= 1.0 else None


def _opening_thermal(fill):
    """(u, shgc, vt, reference, note) stated on an IfcWindow/IfcDoor (#787).

    U from Pset_WindowCommon (windows, skylights) or Pset_DoorCommon (doors)
    ThermalTransmittance, read as written in W/m2K like walls; SHGC and VT from
    Pset_DoorWindowGlazingType, windows only. Type psets apply and the
    occurrence overrides them. Values that are non-numeric, non-positive, or
    (SHGC, VT) above 1 are ignored, never coerced.
    """
    none = (None, None, None, "", "")
    if fill is None:
        return none
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(fill) or {}
    except (ImportError, AttributeError, RuntimeError):
        return none
    is_window = fill.is_a("IfcWindow")
    common = psets.get("Pset_WindowCommon" if is_window else "Pset_DoorCommon") or {}
    try:
        u = float(common.get("ThermalTransmittance"))
    except (TypeError, ValueError):
        u = None
    if u is not None and not (u > 0 and math.isfinite(u)):
        u = None
    shgc = vt = None
    if is_window:
        glz = psets.get("Pset_DoorWindowGlazingType") or {}
        shgc = _ratio(glz.get("SolarHeatGainTransmittance"))
        vt = _ratio(glz.get("VisibleLightTransmittance"))
    ref = str(common.get("Reference") or "") if u is not None else ""
    if u is None:
        return none
    parts = [f"U {u:g} W/m2K"]
    if shgc is not None:
        parts.append(f"SHGC {shgc:g}")
    if vt is not None:
        parts.append(f"VT {vt:g}")
    note = (
        "stated "
        + ", ".join(parts)
        + " ("
        + ("Pset_WindowCommon" if is_window else "Pset_DoorCommon")
    )
    note += ", Pset_DoorWindowGlazingType)" if (shgc is not None or vt is not None) else ")"
    if ref:
        note += f"; Reference {ref!r}"
    return u, shgc, vt, ref, note


def _opening_construction_id(model, bo):
    """Construction id for an opening with a stated U, else "" (#787).

    One construction per distinct (category, U, SHGC, VT); the id comes from the
    values, not the pset Reference, so a re-imported Table 5.5 default
    ("t55-window") never collides with or relabels the library's own row.
    """
    u = getattr(bo, "u_value_w_m2k", None)
    if u is None:
        return ""
    shgc, vt = bo.shgc, bo.vt
    cid = f"IFC-{bo.category.upper()}-U{u:.4f}"
    if shgc is not None:
        cid += f"-S{shgc:.4f}"
    if vt is not None:
        cid += f"-V{vt:.4f}"
    if cid not in model.constructions:
        name = f"IFC {bo.category}, ThermalTransmittance {u:.4f} W/m2K"
        if shgc is not None:
            name += f", SHGC {shgc:g}"
        if vt is not None:
            name += f", VT {vt:g}"
        note = f"stated on the IFC {bo.category}; first opening carrying it: GlobalId={bo.id}"
        if bo.thermal_reference:
            note += f"; Reference {bo.thermal_reference!r}"
        model.constructions[cid] = Construction(
            id=cid,
            name=name,
            u_value_w_m2k=round(u, 6),
            provenance=Provenance(
                sheet_id="ifc",
                revision=0,
                method="ifc_import:tier0:opening_u",
                confidence=0.9,
                note=note,
            ),
            shgc=shgc,
            vt=vt,
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
    return _wall_type_construction(model, wall, scale, prov, gid)


def _wall_type_name(wall) -> str:
    """The wall's type name: ``ObjectType``, else its IfcTypeObject's Name."""
    name = (getattr(wall, "ObjectType", None) or "").strip()
    if name:
        return name
    rels = list(getattr(wall, "IsTypedBy", None) or []) + [
        r for r in getattr(wall, "IsDefinedBy", None) or [] if r.is_a("IfcRelDefinesByType")
    ]
    for rel in rels:
        t = getattr(rel, "RelatingType", None)
        if t is not None and (t.Name or "").strip():
            return t.Name.strip()
    return ""


def _layer_class(names):
    """(class, why) from material layer names, or (None, why) (#747).

    Each layer is read on its own with separators flattened ("Metal - Stud
    Layer" -> "metal stud layer"). Exactly one class across the layers
    resolves; a framing layer beside a mass layer (CMU with stud furring) is
    ambiguous and resolves to nothing, because the library's framing-governs
    rule is for one description, not a stack of layers.
    """
    from construction_library import classify

    found = {}
    for n in names:
        flat = re.sub(r"[\s\-_/,:]+", " ", n or "").strip()
        ctype, why = classify(flat, "ExteriorWall")
        if ctype is not None:
            found.setdefault(ctype, (n, why))
    if len(found) == 1:
        ((ctype, (n, why)),) = found.items()
        return ctype, f"layer {n!r} {why}"
    if found:
        return None, "layers name more than one class: " + ", ".join(sorted(found))
    return None, "no layer names a construction class"


def _wall_type_construction(model, wall, scale, prov, gid):
    """An unset-U construction named for the wall's type or layers (#747).

    Used only when no U is stated or derivable. It carries no value: the
    cited construction library resolves its U from Table 5.5 once a climate
    zone is known, and reports it otherwise. Returns "" when neither the type
    name nor the layers name exactly one construction class, so the wall stays
    unassigned and the Appendix G baseline default can still reach it.
    """
    from construction_library import classify

    tname = _wall_type_name(wall)
    ctype, why = classify(tname, "ExteriorWall") if tname else (None, "")
    if ctype is not None:
        text, how = tname, f"IFC wall type name {tname!r}"
    else:
        layers = [lay["material"] for lay in _material_layers(wall, scale)[0]]
        ctype, why = _layer_class(layers)
        if ctype is None:
            return ""
        flat = "; ".join(re.sub(r"[\s\-_/,:]+", " ", n).strip().lower() for n in layers)
        text, how = f"layers {flat}", f"IFC material layers of {tname or 'an untyped wall'!r}"
    slug = re.sub(r"[^a-z0-9]+", "-", (tname or text).lower()).strip("-")[:48] or "wall"
    cid = f"IFC-TYPE-{slug}"
    if cid not in model.constructions:
        model.constructions[cid] = Construction(
            id=cid,
            name=f"IFC wall, {text}",
            u_value_w_m2k=None,
            provenance=prov(
                "ifc_import:tier0:wall_type",
                0.5,
                f"no U stated or derivable; construction class {ctype} from {how} "
                f"({why}); U left for the cited construction library; first wall carrying it",
                gid,
            ),
        )
    return cid


LINING_FULL_FRAC = 0.999  # a lining covering this share of its host's area is full (#597)
LINING_MIN_FRAC = 0.25  # below this share of the host's area a lining goes to review (#597)


def _direct_material(el):
    """The single IfcMaterial associated directly (not as a layer set), or None."""
    mats = []
    for rel in getattr(el, "HasAssociations", None) or []:
        if rel.is_a("IfcRelAssociatesMaterial") and rel.RelatingMaterial is not None:
            if rel.RelatingMaterial.is_a("IfcMaterial"):
                mats.append(rel.RelatingMaterial)
    return mats[0] if len(mats) == 1 else None


def _lining_resistance(el, scale, thickness_m):
    """(R m2K/W, looked_up, how) of a lining wall's own material, or None.

    Its layer set first (file conductivities, then the lookup, same rules as
    ``_layered_u`` with no surface resistances); else one IfcMaterial over the
    wall's thickness. Nothing usable: None, never guessed.
    """
    for lookup in (False, True):
        got = _layered_u(el, scale, lookup, 0.0, 0.0, "horizontal")
        if got is not None:
            names = ", ".join(f"{n!r}->{eid}" for n, eid in got[1])
            return 1.0 / got[0], bool(got[1]), "layers" + (f" ({names})" if names else "")
    mat = _direct_material(el)
    if mat is None or not thickness_m or thickness_m <= 0:
        return None
    k = _material_conductivity(mat)
    if k is not None:
        return thickness_m / k, False, f"{thickness_m:g} m / {k:g} W/mK"
    from materials import lookup_conductivity

    entry = lookup_conductivity(mat.Name or "") if mat.Name else None
    if entry is None:
        return None
    k = entry.conductivity_w_mk
    return thickness_m / k, True, f"{thickness_m:g} m / {k:g} W/mK ({entry.id})"


LINING_BODY_TOL_M = 0.03  # slack around a crossing wall's body (#597)


def _corner_credit(cover, lining, host_gid, segs, thickness_by_gid):
    """Count the host's run hidden inside a corner wall as covered (#597).

    A host's run reaches into the walls that cross it at its ends, so a
    lining that stops at a crossing wall's face leaves a strip on paper that
    has no exposed face. The strip between the host's end and the lining's
    end is credited as covered only when both its ends lie within one other
    wall's body on the same level (centreline distance at most half its
    thickness plus LINING_BODY_TOL_M). Anything else stays bare.
    """
    import math

    host = segs[host_gid][0]
    h0, h1 = host.from_m, host.to_m
    hL = math.dist(h0, h1)
    if hL < 1e-9 or cover <= 0.0:
        return cover
    d = ((h1[0] - h0[0]) / hL, (h1[1] - h0[1]) / hL)

    def proj(p):
        return (p[0] - h0[0]) * d[0] + (p[1] - h0[1]) * d[1]

    def at(t):
        return (h0[0] + d[0] * t, h0[1] + d[1] * t)

    def seg_dist(p, q0, q1):
        vx, vy = q1[0] - q0[0], q1[1] - q0[1]
        L2 = vx * vx + vy * vy
        u = (
            0.0
            if L2 < 1e-12
            else max(0.0, min(1.0, ((p[0] - q0[0]) * vx + (p[1] - q0[1]) * vy) / L2))
        )
        return math.dist(p, (q0[0] + u * vx, q0[1] + u * vy))

    lo, hi = sorted((proj(lining[0]), proj(lining[1])))
    lo, hi = max(lo, 0.0), min(hi, hL)
    level = host.id.split("-EW")[0]

    def inside_one_body(p, q):
        for g, ws in segs.items():
            if g == host_gid or ws[0].id.split("-EW")[0] != level:
                continue
            t = thickness_by_gid.get(g)
            if not t:
                continue
            lim = t / 2 + LINING_BODY_TOL_M
            for w in ws:
                if seg_dist(p, w.from_m, w.to_m) <= lim and seg_dist(q, w.from_m, w.to_m) <= lim:
                    return True
        return False

    credit = 0.0
    if lo > 0.0 and inside_one_body(at(0.0), at(lo)):
        credit += lo
    if hi < hL and inside_one_body(at(hL), at(hi)):
        credit += hL - hi
    return min(1.0, cover + credit / hL)


def _linings_in_series(model, f, scale, pairs, thickness_by_gid, sheet, revision):
    """Add excluded face-to-face and cladding linings to their host's U (#597).

    Embedded (and run-through) linings add nothing. A host whose U is stated
    (IFC-U) keeps it with a note. A host whose U came from its layers
    (IFC-UL / IFC-UM) gets ``1 / (Rsi + R_host + sum R_lining + Rse)`` as a
    new construction IFC-ULL (all file conductivities) or IFC-UML (any
    lookup). Coverage is the lining's share of the host's face area (run
    overlap x the lower of the two heights). A single lining covering less
    than the whole face gives the parallel-path area-weighted
    ``f * U_lined + (1 - f) * U_bare``. A lining with no usable material,
    no height, or under LINING_MIN_FRAC of the host's area, and several
    linings on one host when any is partial (their zones may overlap),
    leave the host as it was plus a review item. A cladding run adds its
    resistance to every host it lies on.
    Returns {"combined": hosts changed, "review": review items}.
    """
    from ifc_wall_linings import _gid, cladding_hosts, host_cover

    out = {"combined": 0, "review": 0}
    if not pairs:
        return out
    segs = {}
    for w in model.envelope:
        g = _gid(w)
        if g:
            segs.setdefault(g, []).append(w)
    hosts_geo = [
        (g, ws[0].id.split("-EW")[0], ws[0].from_m, ws[0].to_m, thickness_by_gid.get(g))
        for g, ws in segs.items()
    ]
    per_host = {}
    for lg, hg, kind, lw in pairs:
        level = lw.id.split("-EW")[0]
        if kind == "cladding":
            covers = cladding_hosts(
                (lw.from_m, lw.to_m), hosts_geo, thickness_by_gid.get(lg) or 0.0, level
            )
        elif hg in segs:
            covers = [
                (hg, host_cover((lw.from_m, lw.to_m), (segs[hg][0].from_m, segs[hg][0].to_m)))
            ]
        else:
            covers = []
        for h, c in covers:
            c = _corner_credit(c, (lw.from_m, lw.to_m), h, segs, thickness_by_gid)
            per_host.setdefault(h, []).append((lg, kind, c, lw.height_m))
    elements = {e.global_id: e for e in model.bim_elements}

    def note(gid, text):
        el = elements.get(gid)
        if el is not None and el.provenance is not None:
            el.provenance.note += "; " + text

    def review(gid, text):
        out["review"] += 1
        model.flag_for_review(
            kind="lining_u",
            description=text,
            confidence=0.4,
            provenance=Provenance(
                sheet_id=sheet,
                revision=revision,
                method="ifc_import:tier0:wall_u_lining",
                confidence=0.4,
                note=f"GlobalId={gid} {text}",
            ),
        )

    for hg in sorted(per_host):
        linings = sorted(per_host[hg])
        adding = [x for x in linings if x[1] in ("face to face", "cladding")]
        for lg, kind, _, _ in linings:
            if kind not in ("face to face", "cladding"):
                note(hg, f"{kind} lining {lg} adds no resistance (#597)")
        if not adding:
            continue
        cid = segs[hg][0].construction_id
        ids = ", ".join(lg for lg, _, _, _ in adding)
        if cid.startswith(("IFC-UL", "IFC-UM")):
            pass
        elif cid.startswith("IFC-U"):
            note(hg, f"lining(s) {ids} present; stated ThermalTransmittance kept (#597)")
            continue
        else:
            note(hg, f"lining(s) {ids} present; host has no U to add them to (#597)")
            continue
        host_h = segs[hg][0].height_m
        no_h = [lg for lg, _, _, h in adding if not h or not host_h]
        if no_h:
            review(
                hg,
                f"lining {no_h[0]} or host wall {hg} has no height, so its covered area "
                "is unknown; host U left as it was",
            )
            continue
        fracs = {lg: min(1.0, c * min(1.0, h / host_h)) for lg, _, c, h in adding}
        small = [lg for lg in fracs if fracs[lg] < LINING_MIN_FRAC]
        if small:
            review(
                hg,
                f"lining {small[0]} covers {fracs[small[0]]:.0%} of host wall {hg}'s area; "
                "host U left as it was",
            )
            continue
        partial = [lg for lg in fracs if fracs[lg] < LINING_FULL_FRAC]
        if partial and len(adding) > 1:
            review(
                hg,
                f"host wall {hg} has {len(adding)} linings and {partial[0]} covers "
                f"{fracs[partial[0]]:.0%} of its area; where they overlap is unknown, "
                "host U left as it was",
            )
            continue
        frac = fracs[partial[0]] if partial else 1.0
        host_el = f.by_guid(hg)
        got = _layered_wall_u(host_el, scale, cid.startswith("IFC-UM"))
        if got is None:
            continue
        r_host = 1.0 / got[0] - RSI_WALL_M2K_W - RSE_WALL_M2K_W
        looked = bool(got[1])
        parts, missing = [], []
        for lg, kind, _, _ in adding:
            r = _lining_resistance(f.by_guid(lg), scale, thickness_by_gid.get(lg))
            if r is None:
                missing.append(lg)
                continue
            looked = looked or r[1]
            parts.append((lg, kind, r[0], r[2]))
        if missing:
            review(
                hg,
                f"lining {missing[0]} on host wall {hg} has no usable layers or conductivity; "
                "host U left as it was",
            )
            continue
        u_lined = 1.0 / (RSI_WALL_M2K_W + r_host + sum(p[2] for p in parts) + RSE_WALL_M2K_W)
        u = frac * u_lined + (1.0 - frac) * got[0]
        detail = "; ".join(f"{lg} ({kind}) R {r:.4f} from {how}" for lg, kind, r, how in parts)
        if frac < 1.0:
            detail += (
                f"; lined U {u_lined:.4f} over {frac:.1%} of the area, bare U {got[0]:.4f} "
                "over the rest, area-weighted (parallel paths)"
            )
        prov = Provenance(
            sheet_id=sheet,
            revision=revision,
            method="ifc_import:tier0:wall_u_lining",
            confidence=0.6 if looked else 0.8,
            note=(
                f"GlobalId={hg} host R {r_host:.4f} ({cid}) + {detail}; "
                f"ISO 6946 Rsi {RSI_WALL_M2K_W} + Rse {RSE_WALL_M2K_W}, layers in series"
            ),
        )
        new = f"IFC-UML{u:.4f}" if looked else f"IFC-ULL{u:.4f}"
        if new not in model.constructions:
            model.constructions[new] = Construction(
                id=new,
                name=(
                    f"IFC wall + lining, U {u:.4f} W/m2K from layers in series (ISO 6946)"
                    + (", area-weighted for partial cover" if frac < 1.0 else "")
                ),
                u_value_w_m2k=round(u, 6),
                provenance=prov,
            )
        for w in segs[hg]:
            w.construction_id = new
        note(
            hg,
            f"U {u:.4f} with lining(s) {ids} in series ({new})"
            + (f", area-weighted at {frac:.1%} cover" if frac < 1.0 else "")
            + " (#597)",
        )
        out["combined"] += 1
    return out


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
            if _facade_is_unclear(ox, oy):
                counts["facade_unclear"] = counts.get("facade_unclear", 0) + 1
                if prov is not None:
                    prov.confidence = min(prov.confidence, 0.5)
                    prov.note += "; outward normal near a diagonal, facade unclear"
                model.flag_for_review(
                    kind="facade_unclear",
                    description=(
                        f"Envelope wall {w.id} faces within 5 degrees of a diagonal "
                        f"(outward normal {ox:+.2f}, {oy:+.2f}); filed as {w.facade}, "
                        "confirm the facade."
                    ),
                    confidence=0.5,
                    provenance=prov
                    or Provenance(
                        sheet_id="ifc",
                        revision=0,
                        method="ifc_import:tier1:facade",
                        confidence=0.5,
                        note=w.id,
                    ),
                )
        else:
            counts["unclassified"] += 1
            if prov is not None:
                prov.confidence = min(prov.confidence, 0.5)
                prov.note += "; no space found on either side -- facade unclassified, review"
        keep.append(w)
    model.envelope[:] = keep
    return counts


OPENING_DUPLICATE_TOL_M = 0.05  # Pascal cleanup.ts OPENING_DUPLICATE_TOLERANCE


def _identity_winners(pairs):
    """Map each Matchline_Identity id to the GlobalId that keeps it (#636).

    ``pairs`` is (GlobalId, identity props or None). When several elements
    carry the same id, the one whose GlobalId sorts first keeps it, the same
    tie-break #586 uses elsewhere, so the result does not depend on the order
    the importer happens to reach them.
    """
    winners = {}
    for gid, ident in pairs:
        if ident is None:
            continue
        mid = str(ident["MatchlineId"])
        if mid not in winners or gid < winners[mid]:
            winners[mid] = gid
    return winners


def _apply_opening_identity(bo, openings_by_gid, fills, taken, model, prov, winners=None):
    """Give an imported opening matchline's own id when the file carries one (#588).

    Reads Matchline_Identity off the IfcOpeningElement, else its fill.
    Returns True when the opening took the id (it is matchline-authored).
    An id already taken keeps the GlobalId and adds a review item. When the
    same id is on several openings, the lowest GlobalId keeps it (#636).
    """
    gid = bo.id
    ident = _matchline_identity(openings_by_gid.get(gid), fills.get(gid))
    if ident is None:
        taken.add(gid)
        return False
    mid = str(ident["MatchlineId"])
    if mid in taken or (winners is not None and winners.get(mid, gid) != gid):
        model.flag_for_review(
            kind="matchline_identity",
            description=(
                f"Opening {gid}: Matchline_Identity id {mid!r} already taken; kept GlobalId"
            ),
            confidence=0.4,
            provenance=prov("ifc_import:tier0:matchline_identity", 0.4, "", gid),
        )
        taken.add(gid)
        return False
    taken.add(mid)
    bo.id = mid
    if bo.provenance is not None:
        bo.provenance.note += (
            f"; id {mid} from {IDENTITY_PSET} (source "
            f"{ident.get('SourceMethod') or 'unknown'}, conf {ident.get('Confidence', 'unknown')})"
        )
    return True


def _drop_duplicate_openings(openings, keep=()):
    """Drop openings doubled on top of each other in one host wall (#578).

    Two openings are copies when they share category and tag and their
    width, height, sill and position along the wall all agree within
    OPENING_DUPLICATE_TOL_M. The copy whose GlobalId sorts first is kept, so
    the result does not depend on file order. An opening missing any of those
    values is never treated as a copy, nor is an opening whose id is in
    ``keep`` (matchline-authored, #588). Idea from Pascal's cleanup.ts (MIT,
    Copyright (c) 2026 Pascal Group Inc., commit 67f8041).
    """
    tol = OPENING_DUPLICATE_TOL_M + 1e-9
    keys = ("width_m", "height_m", "sill_m", "s_center_m")
    kept, dropped = [], []
    for o in sorted(openings, key=lambda o: o.id):
        vals = [getattr(o, k) for k in keys]
        twin = None
        if o.id not in keep and all(v is not None for v in vals):
            for k in kept:
                kv = [getattr(k, n) for n in keys]
                if (
                    k.id not in keep
                    and k.category == o.category
                    and k.tag == o.tag
                    and all(v is not None for v in kv)
                    and all(abs(a - b) <= tol for a, b in zip(vals, kv))
                ):
                    twin = k
                    break
        (dropped if twin is not None else kept).append(o)
    order = {o.id: i for i, o in enumerate(openings)}
    kept.sort(key=lambda o: order[o.id])
    return kept, dropped


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
    if project is not None:
        from solar_aperture import compound_angle_deg

        for site in _aggregated(project, "IfcSite"):
            lat = compound_angle_deg(getattr(site, "RefLatitude", None))
            if lat is not None and -90.0 <= lat <= 90.0:
                model.site_latitude_deg = round(lat, 9)
                break
    model.terrain = _terrain_triangles(f, project, scale)
    slab_voids = {}  # level_id -> plan rectangles of unfilled slab openings (#581)
    floor_voids = []  # (plan rectangle, z min, z max) of the same openings (#640)
    wall_axis_fix = {}  # GlobalId -> (x0, y_mid, note), body centreline (#579)

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
    authored_spaces = set()  # space ids from Matchline_Identity (#588)
    opening_ids = set()  # opening ids already taken (#588)
    level_by_storey = {}  # IfcBuildingStorey GlobalId -> level id
    openings_by_gid = {o.GlobalId: o for o in f.by_type("IfcOpeningElement")}
    # which opening/space keeps a duplicated Matchline_Identity id is decided
    # up front by lowest GlobalId, not by walk order (#636). Only openings the
    # walk imports compete: wall voids, and window-filled slab/roof voids.
    opening_winners = _identity_winners(
        (ogid, _matchline_identity(openings_by_gid.get(ogid), fills.get(ogid)))
        for ogid, host in voids.items()
        if ogid in openings_by_gid
        and (
            host.is_a() in ("IfcWall", "IfcWallStandardCase")
            or (
                host.is_a() in ("IfcSlab", "IfcRoof")
                and fills.get(ogid) is not None
                and fills[ogid].is_a("IfcWindow")
            )
        )
    )
    space_winners = _identity_winners(
        (sp.GlobalId, _matchline_identity(sp))
        for storey in storeys
        for sp in _aggregated(storey, "IfcSpace")
    )
    unlabeled = 0

    shade_jobs = []  # (IfcShadingDevice, level id, storey elevation)
    duplicate_openings = []  # BimOpenings dropped as doubled copies (#578)
    # elements no storey contains: assign by placement height when exactly
    # one storey band fits, else report them unassigned (#585)
    storey_elevs = [_storey_elevation(st) * scale for st in storeys]
    by_elevation = {}  # storey index -> [element]
    elevation_gids = set()
    storey_unassigned = []  # (GlobalId, reason)
    for el in _storey_less_elements(f, storeys, _ELEMENT_CLASSES) if storeys else []:
        z = _placement_transform(el, scale)[3]
        idx, why = _storey_by_elevation(z, storey_elevs)
        if idx is None:
            storey_unassigned.append((el.GlobalId, why))
        else:
            by_elevation.setdefault(idx, []).append(el)
            elevation_gids.add(el.GlobalId)
    for li, storey in enumerate(storeys):
        level_id = f"L{li + 1}"
        elev = _storey_elevation(storey) * scale
        level = Level(id=level_id, name=storey.Name or "", elevation_z_m=elev)
        above = _storey_above_ground(storey)
        if above is not None:
            level.above_ground = above
            level.above_ground_source = "ifc:Pset_BuildingStoreyCommon.AboveGround"
        model.levels.append(level)
        level_by_storey[storey.GlobalId] = level_id

        # --- spaces -------------------------------------------------------
        split_groups = {}  # parent id -> piece ids, from Matchline_Identity.SplitFrom (#638)
        for sp in _aggregated(storey, "IfcSpace"):
            label, number, name_source = _space_name_number(sp)
            if number is None:
                unlabeled += 1
                sid = f"{level_id}-UNLABELED-{unlabeled}"
            else:
                sid = f"{level_id}-{number}"
            gid = sp.GlobalId
            ident = _matchline_identity(sp)
            ident_note = ""
            if ident is not None:
                mid = str(ident["MatchlineId"])
                if (
                    space_winners.get(mid, gid) != gid
                    or mid in model.spaces
                    or mid in space_by_gid.values()
                ):
                    # the derived id may itself be the id another space keeps
                    # (the copy usually came from the room it duplicates):
                    # fall back to the GlobalId, as openings do (#636)
                    if space_winners.get(sid, gid) != gid:
                        sid = f"{level_id}-{gid}"
                    model.flag_for_review(
                        kind="matchline_identity",
                        description=(
                            f"Space {gid}: Matchline_Identity id {mid!r} already taken; "
                            f"kept derived id {sid}"
                        ),
                        confidence=0.4,
                        provenance=prov("ifc_import:tier0:matchline_identity", 0.4, "", gid),
                    )
                    ident = None
                else:
                    sid = mid
                    ident_note = (
                        f"; id {mid} from {IDENTITY_PSET} (source "
                        f"{ident.get('SourceMethod') or 'unknown'}, conf "
                        f"{ident.get('Confidence', 'unknown')})"
                    )
            elif space_winners.get(sid, gid) != gid:
                # an unauthored space whose derived id another space keeps
                sid = f"{level_id}-{gid}"
            space_by_gid[gid] = sid

            polygon, area, volume, conf, method, note, height = _extract_space_geometry(
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
                height_m=round(height, 4) if height else None,
                core_provenance=prov(
                    method, conf, f"{note}; name/number via {name_source}{ident_note}", gid
                ),
                label_confidence=(0.95 if name_source == "ifc_longname" else 0.9)
                if number
                else 0.6,
            )
            cls = None if ident is not None else _ifc_space_class(sp)
            if ident is not None:
                # authored by matchline: no name heuristics, keep what it wrote
                authored_spaces.add(sid)
                pt = ident.get("PolyType")
                if pt in ("room", "closet", "shaft", "elevator_core", "unassigned"):
                    space.poly_type = pt
                space.merged_from = [m for m in str(ident.get("MergedFrom") or "").split(",") if m]
                if ident.get("SplitFrom"):
                    split_groups.setdefault(str(ident["SplitFrom"]), []).append(sid)
            if cls is not None:
                space.poly_type, space.poly_type_confidence, word = cls
                space.core_provenance.note += (
                    f"; poly_type {cls[0]} from IfcSpace name word '{word}'"
                )
            lighting = _read_lighting(sp, model, prov("lighting_import", 0.3))
            if lighting is not None:
                space.lighting = lighting
            model.spaces[sid] = space

        if split_groups:
            _rejoin_split_spaces(model, split_groups, space_by_gid, authored_spaces, prov)

        # --- elements -----------------------------------------------------
        elements = _contained(storey) + by_elevation.get(li, [])
        wall_heights = []
        env_start = len(model.envelope)
        for el in elements:
            cls = el.is_a()
            if cls not in _ELEMENT_CLASSES:
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

            storey_method = ""
            if gid in elevation_gids:
                storey_method = "elevation"
                conf = min(conf, 0.6)
                note += "; storey from placement height (no storey containment)"
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
                storey_method=storey_method,
            )
            model.bim_elements.append(be)

            # walls also feed the BEM envelope (facade classification
            # needs adjacency -> Tier 1, so facade stays empty here)
            if cls in ("IfcWall", "IfcWallStandardCase") and length_m:
                wall_axis_fix[gid] = _wall_centreline_fix(
                    el, solids, dims_note.startswith("local extents"), scale
                )
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

                authored = {
                    o.id
                    for o in be.openings
                    if _apply_opening_identity(
                        o, openings_by_gid, fills, opening_ids, model, prov, opening_winners
                    )
                }
                be.openings, dup = _drop_duplicate_openings(be.openings, keep=authored)
                duplicate_openings.extend(dup)

            # skylights: windows hosted in a roof or slab (roadmap item 3).
            # Only window fills are read; an unfilled slab void is a shaft
            # or stair hole, not glazing, and stays out of the opening count.
            elif cls in ("IfcSlab", "IfcRoof"):
                for ogid, host in voids.items():
                    if host.GlobalId != gid:
                        continue
                    opening = openings_by_gid.get(ogid)
                    fill = fills.get(ogid)
                    if opening is not None and fill is None and cls == "IfcSlab":
                        got = _plan_rect_z(opening, scale)
                        if got is not None:
                            slab_voids.setdefault(level_id, []).append(got[0])
                            floor_voids.append(got)
                    if opening is None or fill is None or not fill.is_a("IfcWindow"):
                        continue
                    bo = _read_roof_opening(opening, fill, sheet, revision, scale, host_gid=gid)
                    _apply_opening_identity(
                        bo, openings_by_gid, fills, opening_ids, model, prov, opening_winners
                    )
                    be.openings.append(bo)

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
        # hosted after the envelope segments reach their final place
        # (centrelines, linings, joins, classification; #579)
        shade_jobs.extend((el, level_id, elev) for el in elements if el.is_a("IfcShadingDevice"))

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
    _apply_skylight_daylight(model)
    merge_note = _merge_ifc_closets_and_shafts(model, authored_spaces)
    # after opening attachment (it matches envelope segments to wall
    # placements by start point), before facade classification (#575)
    from ifc_wall_joins import connected_pairs_from_ifc, join_wall_ends
    from ifc_wall_linings import exclude_linings

    # openings are attached; segments may now leave the wall placement (#579)
    centreline_moved = _apply_wall_centrelines(model, wall_axis_fix)
    wall_t = {e.global_id: e.thickness_m for e in model.bim_elements if e.thickness_m}
    # linings first, so they neither join nor carve wall loops (#577)
    lining_pairs = []
    lining_counts = exclude_linings(model, wall_t, lining_pairs)
    lining_u = _linings_in_series(model, f, scale, lining_pairs, wall_t, sheet, revision)
    join_counts = join_wall_ends(model.envelope, wall_t, connected_pairs_from_ifc(f))
    # flush in-line continuations, then split at X/T junctions so facade
    # classification can name one space behind each piece (#576)
    # rooms stacked over slab openings are one atrium (#640)
    from atria import merge_atrium_stacks
    from ifc_wall_split import join_inline_ends, split_at_junctions

    n_atria = merge_atrium_stacks(model, floor_voids, sheet, revision)
    inline_counts = join_inline_ends(model.envelope, wall_t)
    n_junction_splits = split_at_junctions(model.envelope, wall_t)
    # needs interior walls too, so before classification drops them (#581)
    from ifc_wall_loops import flag_unclaimed_wall_loops

    loops_n, loops_area = flag_unclaimed_wall_loops(
        model,
        wall_t,
        slab_voids,
        sheet,
        revision,
    )
    facade_summary = _classify_envelope(model)
    for el, lid, elev in shade_jobs:
        storey_walls = [w for w in model.envelope if w.id.startswith(lid + "-")]
        taken = {sh.id for sh in model.shading}
        model.shading.append(_read_shading_device(el, storey_walls, elev, scale, prov, taken))
    if model.constructions:
        # space_id is known only after classification; interior walls have
        # left the envelope, so the rollup sees exterior segments only
        from constructions import apply_wall_u_rollup

        apply_wall_u_rollup(model)
    roof_note = _read_roof_construction(model, f, prov, scale)
    from ifc_roof_planes import orient_skylights, read_roof_planes

    n_roof_planes, n_roof_els, n_roof_flagged = read_roof_planes(model, f, level_by_storey, prov)
    n_sky_oriented = orient_skylights(model)
    finish_gids, thin_left = _classify_finish_slabs(model, f, scale)
    slab_note = _read_slab_construction(model, f, prov, skip=finish_gids)
    ceil_with, ceil_without, ceil_covs = _read_ceilings(
        model, f, scale, space_by_gid, level_by_storey, prov
    )
    borders = _find_virtual_borders(model, f, scale, level_by_storey, sheet, revision)
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
    if centreline_moved:
        summary_parts.append(
            f"wall centrelines: {centreline_moved} segments moved off the reference line"
        )
    if lining_u["combined"] or lining_u["review"]:
        summary_parts.append(
            f"lining U: {lining_u['combined']} host walls with linings in series"
            + (f", {lining_u['review']} left for review" if lining_u["review"] else "")
        )
    if lining_counts["lining"] or lining_counts["kept"]:
        summary_parts.append(
            f"linings: {lining_counts['lining']} walls kept out of the envelope"
            + (f", {lining_counts['kept']} kept (host openings)" if lining_counts["kept"] else "")
        )
    if join_counts["moved"] or join_counts["ambiguous"]:
        summary_parts.append(
            f"wall joins: {join_counts['moved']} ends moved onto neighbour centrelines"
            + (f", {join_counts['ambiguous']} left (tie)" if join_counts["ambiguous"] else "")
        )
    if inline_counts["joined"] or inline_counts["ambiguous"]:
        summary_parts.append(
            f"in-line walls: {inline_counts['joined']} ends joined"
            + (f", {inline_counts['ambiguous']} left (tie)" if inline_counts["ambiguous"] else "")
        )
    if n_junction_splits:
        summary_parts.append(f"wall splits: {n_junction_splits} at X/T junctions")
    if n_roof_els:
        part = f"roof planes: {n_roof_planes} from {n_roof_els} roof element(s)"
        if n_roof_flagged:
            part += f", {n_roof_flagged} flagged for review"
        if n_sky_oriented:
            part += f"; {n_sky_oriented} skylight(s) oriented"
        summary_parts.append(part)
    if elevation_gids or storey_unassigned:
        part = f"storey fallback: {len(elevation_gids)} by elevation"
        if storey_unassigned:
            shown = ", ".join(f"{g} ({why})" for g, why in storey_unassigned[:5])
            more = len(storey_unassigned) - 5
            part += f", {len(storey_unassigned)} unassigned: {shown}" + (
                f" +{more} more" if more > 0 else ""
            )
        summary_parts.append(part)
    if borders:
        n_file = sum(1 for b in borders if b.virtual_element_id)
        summary_parts.append(
            f"space borders: {len(borders)} virtual ({n_file} from IfcVirtualElement), "
            f"{sum(b.length_m for b in borders):.2f} m"
        )
    if ceil_covs:
        summary_parts.append(
            f"ceilings: {ceil_with} spaces from {ceil_covs} IfcCovering CEILING, "
            f"{ceil_without} without"
        )
    if finish_gids:
        summary_parts.append(f"finish slabs: {len(finish_gids)} on structural floors")
    if duplicate_openings:
        summary_parts.append(
            f"duplicate openings: {len(duplicate_openings)} doubled copies dropped"
        )
    if loops_n:
        summary_parts.append(
            f"unclaimed wall loops: {loops_n} ({loops_area:.2f} m^2) flagged for review"
        )
    if merge_note:
        summary_parts.append(merge_note)
    if n_atria:
        summary_parts.append(f"atria: {n_atria} room(s) over slab openings merged")
    if not unattached.is_empty():
        summary_parts.append(unattached.summary_line())
    if model.roof_construction_id:
        summary_parts.append(f"roof construction {model.roof_construction_id}")
    if roof_note:
        summary_parts.append(roof_note)
    if model.slab_construction_id:
        summary_parts.append(f"slab construction {model.slab_construction_id}")
    if slab_note:
        summary_parts.append(slab_note)
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
    tu, tshgc, tvt, tref, tnote = _opening_thermal(fill)
    if tnote:
        p.note += f"; {tnote}"
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
        u_value_w_m2k=tu,
        shgc=tshgc,
        vt=tvt,
        thermal_reference=tref,
    )


_DOOR_OP_UNDEFINED = {"NOTDEFINED", "USERDEFINED", ""}
_DOOR_OP_NO_LEAVES = {"REVOLVING", "ROLLINGUP"}


def _door_semantics(fill):
    """(operation_type, leaf_count, hinge_side, glazing_fraction, note) for an IfcDoor (#573).

    OperationType comes from the occurrence (IFC4), else its type (IfcDoorType,
    IFC2X3 IfcDoorStyle). Leaf count and hinge side are read straight off the
    enum; anything it does not state stays None. GlazingAreaFraction comes from
    Pset_DoorCommon (occurrence overrides type) and must lie in 0..1.
    Mapping idea from the Pascal editor IFC importer (MIT, door-semantics.ts).
    """
    import ifcopenshell.util.element as _El

    op = getattr(fill, "OperationType", None)
    src = "occurrence"
    if not op:
        try:
            typ = _El.get_type(fill)
        except Exception:  # noqa: BLE001 -- malformed typing, treat as untyped
            typ = None
        op = getattr(typ, "OperationType", None) if typ is not None else None
        src = "type"
    op = str(op) if op else None
    leaves = hinge = None
    notes = []
    if op and op not in _DOOR_OP_UNDEFINED:
        notes.append(f"OperationType {op} from {src}")
        if op not in _DOOR_OP_NO_LEAVES:
            leaves = 2 if op.startswith("DOUBLE_DOOR") else 1
        if "SWING" in op and op.endswith("_LEFT"):
            hinge = "left"
        elif "SWING" in op and op.endswith("_RIGHT"):
            hinge = "right"
    elif op:
        notes.append(f"OperationType {op}: leaves/hinge unknown")
    frac = None
    try:
        psets = _El.get_psets(fill) or {}
    except Exception:  # noqa: BLE001
        psets = {}
    raw = (psets.get("Pset_DoorCommon") or {}).get("GlazingAreaFraction")
    if raw is not None:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = None
        if v is not None and 0.0 <= v <= 1.0:
            frac = v
            notes.append(f"GlazingAreaFraction {v:g} from Pset_DoorCommon")
        else:
            notes.append(f"GlazingAreaFraction {raw!r} out of 0..1 -- ignored")
    return op, leaves, hinge, frac, "; ".join(notes)


def _terrain_triangles(f, project, scale):
    """Ground triangles (canonical frame) from IfcSite bodies and TERRAIN elements (#641)."""
    import ifcopenshell.geom as _g

    sources = []
    if project is not None:
        sources.extend(_aggregated(project, "IfcSite"))
    for ge in f.by_type("IfcGeographicElement"):
        if str(getattr(ge, "PredefinedType", "") or "").upper() == "TERRAIN":
            sources.append(ge)
    tris = []
    for el in sources:
        if not getattr(el, "Representation", None):
            continue
        try:
            shape = _g.create_shape(_g.settings(), el)
        except RuntimeError:
            continue
        v = [float(x) for x in shape.geometry.verts]
        faces = list(shape.geometry.faces)
        t = _placement_transform(el, scale)
        pts = []
        for i in range(0, len(v), 3):
            wx, wy, wz = _apply(t, v[i] * scale, v[i + 1] * scale, v[i + 2] * scale)
            cx, cy = _to_canonical(wx, wy)
            pts.append([round(cx, 4), round(cy, 4), round(wz, 4)])
        for i in range(0, len(faces) - 2, 3):
            tris.append([pts[faces[i]], pts[faces[i + 1]], pts[faces[i + 2]]])
    return tris


def _plan_rect(product, scale):
    """Canonical-frame plan bounding rectangle of a product's solid, or None."""
    got = _plan_rect_z(product, scale)
    return got[0] if got else None


def _plan_rect_z(product, scale):
    """(plan rectangle, z min, z max) of a product's solid in world metres, or None."""
    verts = _geom_verts(product)
    if not verts:
        return None
    to_world = _placement_transform(product, scale)
    xs, ys, zs = [], [], []
    for i in range(0, len(verts), 3):
        wx, wy, wz = _apply(to_world, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale)
        cx, cy = _to_canonical(wx, wy)
        xs.append(cx)
        ys.append(cy)
        zs.append(wz)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 < 1e-6 or y1 - y0 < 1e-6:
        return None
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], min(zs), max(zs)


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
        # opening -> world, then world -> wall (#587: was composed backwards,
        # so any opening not placed relative to an unrotated wall at the
        # origin landed off its wall)
        rel = _compose(_placement_transform(opening, scale), _invert(wall_world))
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
    plan_center = None
    if category == "door" and verts:
        # Plan centre of the door opening solid in the world frame, so the
        # closet rule can tell which spaces it connects. No solid, no centre:
        # the wall axis can sit on a face, and a guessed point is worse than none.
        to_world = _placement_transform(opening, scale)
        wxs, wys = [], []
        for i in range(0, len(verts), 3):
            wx, wy, _ = _apply(
                to_world, verts[i] * scale, verts[i + 1] * scale, verts[i + 2] * scale
            )
            wxs.append(wx)
            wys.append(wy)
        cx, cy = _to_canonical(0.5 * (min(wxs) + max(wxs)), 0.5 * (min(wys) + max(wys)))
        plan_center = [round(cx, 4), round(cy, 4)]
        note += "; plan centre from opening solid, world frame"
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
    tu, tshgc, tvt, tref, tnote = _opening_thermal(fill)
    if tnote:
        p.note += f"; {tnote}"
    op_type = leaves = hinge = glaze = glazed = None
    if category == "door":
        op_type, leaves, hinge, glaze, dnote = _door_semantics(fill)
        if dnote:
            p.note += f"; {dnote}"
        if glaze is not None and width_m and height_m:
            glazed = glaze * width_m * height_m
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
        plan_center_m=plan_center,
        operation_type=op_type,
        leaf_count=leaves,
        hinge_side=hinge,
        glazing_area_fraction=_r4(glaze),
        glazed_area_m2=_r4(glazed),
        u_value_w_m2k=tu,
        shgc=tshgc,
        vt=tvt,
        thermal_reference=tref,
    )
