# bem_ifc4.py — IFC4 export and validation
# Extracted from bem_export.py for single-responsibility compliance.

from __future__ import annotations

import math
from pathlib import Path

from bem_geometry import BEMModel
from bem_helpers import (
    _distribute_openings,
    _place_openings_on_wall,
    _validate_out_path,
    _wall_edges,
)


def _ensure_ifc():
    """Import IfcOpenShell via importlib.util.find_spec probe."""
    import importlib.util

    if importlib.util.find_spec("ifcopenshell") is None:
        raise RuntimeError("IfcOpenShell is not installed; install with `pip install ifcopenshell`")
    import ifcopenshell  # noqa: F401


def write_ifc4(model: BEMModel, path: str | Path, wall_thickness_m: float = 0.2) -> Path:
    """Write a minimal but structurally valid IFC4 file.

    Contents: IfcProject/Site/Building/BuildingStorey hierarchy, one IfcWall
    per simplified envelope edge (real SweptSolid geometry, 0.2 m thick),
    one IfcSpace per room (placement at centroid; no solid geometry in v1),
    and per opening an IfcOpeningElement hosted in its wall
    (IfcRelVoidsElement) filled by an IfcWindow/IfcDoor (IfcRelFillsElement).

    Opening placement reuses _place_openings_on_wall, so IFC and gbXML agree
    on positions (s measured from the wall start point p0 along the edge).
    """
    _ensure_ifc()
    import ifcopenshell
    import ifcopenshell.api.aggregate as _Ag
    import ifcopenshell.api.context as _C
    import ifcopenshell.api.geometry as _Gm
    import ifcopenshell.api.group as _Grp
    import ifcopenshell.api.project as _P
    import ifcopenshell.api.root as _R
    import ifcopenshell.api.spatial as _Sp
    import ifcopenshell.api.unit as _U

    h = model.wall_height_m
    f = _P.create_file("IFC4")
    proj = _R.create_entity(f, ifc_class="IfcProject", name=model.building_name)
    length = _U.add_si_unit(f, unit_type="LENGTHUNIT")  # metre
    area = _U.add_si_unit(f, unit_type="AREAUNIT")
    volume = _U.add_si_unit(f, unit_type="VOLUMEUNIT")
    _U.assign_unit(f, units=[length, area, volume])

    ctx = _C.add_context(f, context_type="Model")
    body = _C.add_context(
        f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx
    )

    def placement(xyz, ref_dir=None, parent=None):
        pt = f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in xyz))
        kw = {"Location": pt}
        if ref_dir is not None:
            kw["Axis"] = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
            kw["RefDirection"] = f.create_entity(
                "IfcDirection", DirectionRatios=tuple(float(v) for v in ref_dir)
            )
        ax = f.create_entity("IfcAxis2Placement3D", **kw)
        return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=ax)

    site = _R.create_entity(f, ifc_class="IfcSite", name="Site")
    bldg = _R.create_entity(f, ifc_class="IfcBuilding", name=model.building_name)
    storey = _R.create_entity(f, ifc_class="IfcBuildingStorey", name="Level 1")
    storey.Elevation = 0.0
    _Ag.assign_object(f, products=[site], relating_object=proj)
    _Ag.assign_object(f, products=[bldg], relating_object=site)
    _Ag.assign_object(f, products=[storey], relating_object=bldg)
    storey_pl = placement((0.0, 0.0, 0.0))
    storey.ObjectPlacement = storey_pl

    # --- walls ------------------------------------------------------------
    edges = _wall_edges(model.ring_m)
    opening_assign = _distribute_openings(model.openings, edges)
    walls = []
    for i, (p0, p1) in enumerate(edges):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        wall = _R.create_entity(f, ifc_class="IfcWall", name=f"Wall-{i + 1}")
        wall.ObjectPlacement = placement(
            (p0[0], p0[1], 0.0), ref_dir=(dx / L, dy / L, 0.0), parent=storey_pl
        )
        rep = _Gm.add_wall_representation(
            f, context=body, length=L, height=h, thickness=wall_thickness_m
        )
        _Gm.assign_representation(f, product=wall, representation=rep)
        _Sp.assign_container(f, products=[wall], relating_structure=storey)
        walls.append((wall, p0, p1, L))

        # openings hosted in this wall
        placements, _ = _place_openings_on_wall(opening_assign[i], L, h)
        for pl_ in placements:
            u = pl_["unit"]
            s_mid = (pl_["s0"] + pl_["s1"]) / 2.0
            opening = _R.create_entity(f, ifc_class="IfcOpeningElement", name=f"{u.tag} opening")
            # opening local frame: wall frame translated along the wall
            opening.ObjectPlacement = placement(
                (s_mid, 0.0, pl_["sill"]), parent=wall.ObjectPlacement
            )
            f.create_entity(
                "IfcRelVoidsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingBuildingElement=wall,
                RelatedOpeningElement=opening,
            )
            fill_class = "IfcWindow" if u.category == "window" else "IfcDoor"
            fill = _R.create_entity(
                f,
                ifc_class=fill_class,
                name=f"{u.tag} ({u.category} {u.width_m:.2f}x{u.height_m:.2f} m)",
            )
            fill.ObjectPlacement = placement((0.0, 0.0, 0.0), parent=opening.ObjectPlacement)
            f.create_entity(
                "IfcRelFillsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingOpeningElement=opening,
                RelatedBuildingElement=fill,
            )
            _Sp.assign_container(f, products=[fill], relating_structure=storey)

    # --- spaces -----------------------------------------------------------
    ifc_space_by_sid = {}  # sid -> IfcSpace entity for zone assignment
    for sp in model.spaces:
        n = len(sp.polygon_m)
        cx = sum(p[0] for p in sp.polygon_m) / n
        cy = sum(p[1] for p in sp.polygon_m) / n
        space = _R.create_entity(f, ifc_class="IfcSpace", name=sp.name)
        space.ObjectPlacement = placement((cx, cy, 0.0), parent=storey_pl)
        try:
            space.PredefinedType = "SPACE"
        except Exception:
            pass
        # spaces decompose the storey spatially (IfcRelAggregates), they are
        # not "contained products" (IfcSpace has no ContainedInStructure)
        _Ag.assign_object(f, products=[space], relating_object=storey)
        # gross floor area as a quantity set (best effort)
        try:
            import ifcopenshell.api.pset as _Ps

            qto = _Ps.add_qto(f, product=space, name="Qto_SpaceBaseQuantities")
            _Ps.edit_qto(f, qto=qto, properties={"GrossFloorArea": sp.area_m2})
        except Exception:
            pass  # quantities are enrichment, not core validity
        # footprint geometry: IfcGeometricCurveSet so the space polygon survives
        # round-trip (IfcSpace has no solid body in v1; this is the 2D footprint).
        if len(sp.polygon_m) >= 3:
            try:
                pts = [
                    f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y)))
                    for x, y in sp.polygon_m
                ]
                polyline = f.create_entity("IfcPolyline", Points=pts)
                curve_set = f.create_entity("IfcGeometricCurveSet", Elements=[polyline])
                footprint_shape = f.create_entity(
                    "IfcShapeRepresentation",
                    ContextOfItems=body,
                    RepresentationIdentifier="FootPrint",
                    RepresentationType="GeometricCurveSet",
                    Items=[curve_set],
                )
                pds = f.create_entity(
                    "IfcProductDefinitionShape",
                    Representations=[footprint_shape],
                )
                space.Representation = pds
            except Exception:
                pass  # footprint is enrichment, not required for validity
        # lighting power as a property (best effort)
        if sp.lighting_w > 0:
            try:
                import ifcopenshell.api.pset as _Ps

                pset = _Ps.add_pset(f, product=space, name="Pset_SpaceLighting")
                _Ps.edit_pset(f, pset=pset, properties={"LightingPower": sp.lighting_w})
            except Exception:
                pass
        ifc_space_by_sid[sp.sid] = space

    # --- zones ------------------------------------------------------------
    for zone_id, zone_space_ids in model.zones:
        zone = f.create_entity("IfcZone", Name=zone_id)
        zone_spaces = [ifc_space_by_sid[sid] for sid in zone_space_ids if sid in ifc_space_by_sid]
        if zone_spaces:
            _Grp.assign_group(f, products=zone_spaces, group=zone)

    path = _validate_out_path(path)
    f.write(str(path))
    model.notes.append(
        f"IFC4: {len(walls)} walls, {len(model.spaces)} "
        f"spaces, {len(model.openings)} openings hosted, {len(model.zones)} zones."
    )
    return path


def validate_ifc4(path: str | Path) -> tuple[bool, list]:
    """Structural validation of an IFC4 file: round-trip parse + checks.

    Verifies schema, entity counts, wall geometry presence, space
    containment, and opening void/fill relationship integrity.
    """
    _ensure_ifc()
    import ifcopenshell

    errors: list[str] = []
    try:
        f = ifcopenshell.open(str(path))
    except Exception as e:
        return False, [f"could not parse IFC file: {e}"]
    if f.schema != "IFC4":
        errors.append(f"schema is {f.schema}, expected IFC4")

    walls = f.by_type("IfcWall")
    spaces = f.by_type("IfcSpace")
    storeys = f.by_type("IfcBuildingStorey")
    openings = f.by_type("IfcOpeningElement")
    windows = f.by_type("IfcWindow")
    doors = f.by_type("IfcDoor")
    if not walls:
        errors.append("no IfcWall entities")
    if not spaces:
        errors.append("no IfcSpace entities")
    if not storeys:
        errors.append("no IfcBuildingStorey entities")

    for w in walls:
        reps = w.Representation.Representations if w.Representation else []
        if not any(r.RepresentationType == "SweptSolid" for r in reps):
            errors.append(f"{w.Name or w.id()}: wall has no SweptSolid body")

    contained = set()
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        for el in rel.RelatedElements:
            contained.add(el.id())
    aggregated = set()
    for rel in f.by_type("IfcRelAggregates"):
        for el in rel.RelatedObjects:
            aggregated.add(el.id())
    for sp in spaces:
        if sp.id() not in aggregated:
            errors.append(f"space '{sp.Name}' not aggregated under a storey")

    voided = {
        r.RelatedOpeningElement.id(): r.RelatingBuildingElement.id()
        for r in f.by_type("IfcRelVoidsElement")
    }
    filled = {r.RelatingOpeningElement.id() for r in f.by_type("IfcRelFillsElement")}
    for o in openings:
        if o.id() not in voided:
            errors.append(f"opening '{o.Name}' voids no wall")
        if o.id() not in filled:
            errors.append(f"opening '{o.Name}' has no filling element")
    for el in list(windows) + list(doors):
        if not el.ContainedInStructure:
            errors.append(f"{el.is_a()} '{el.Name}' not in a spatial container")

    return not errors, errors
