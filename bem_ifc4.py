# bem_ifc4.py — IFC4 export and validation
# Extracted from bem_export.py for single-responsibility compliance.

from __future__ import annotations

import math
from pathlib import Path

from bem_geometry import BEMModel
from bem_helpers import (
    _assign_wall_to_space,
    _distribute_openings,
    _edge_spaces,
    _place_openings_on_wall,
    _place_skylights_on_roof,
    _roof_outline,
    _validate_out_path,
    _wall_edges,
)
from bem_roof import is_sloped, place_skylights_on_pieces, plane_frame, roof_pieces

ROOF_SLAB_THICKNESS_M = 0.2  # matches the wall default; drawings carry no roof build-up
GROUND_SLAB_THICKNESS_M = 0.15  # nominal; geometry only, the U-value is what matters
SHADE_THICKNESS_M = 0.05  # drawings give shade extents, not build-up; thin plate


def _shade_frame(verts):
    """Local frame of a rectangular shade quad: origin, x axis, y axis, normal, extents.

    ``verts`` are four (x, y, z) corners in order, as bem_shading builds them
    (a rectangle, so edges v0->v1 and v0->v3 are perpendicular). Returns None
    for a degenerate quad.
    """
    v0, v1, _, v3 = (tuple(float(c) for c in v) for v in verts[:4])
    e1 = tuple(b - a for a, b in zip(v0, v1))
    e3 = tuple(b - a for a, b in zip(v0, v3))
    l1 = math.sqrt(sum(c * c for c in e1))
    l3 = math.sqrt(sum(c * c for c in e3))
    if l1 < 1e-6 or l3 < 1e-6:
        return None
    x = tuple(c / l1 for c in e1)
    y = tuple(c / l3 for c in e3)
    nz = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
    ln = math.sqrt(sum(c * c for c in nz))
    if ln < 1e-6:
        return None
    return v0, x, y, tuple(c / ln for c in nz), l1, l3


def _ensure_ifc():
    """Import IfcOpenShell via importlib.util.find_spec probe."""
    import importlib.util

    if importlib.util.find_spec("ifcopenshell") is None:
        raise PipelineDependencyError(
            "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
        )
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (broken binary or missing "
            "system library). Reinstall with: pip install ifcopenshell --force-reinstall"
        ) from e


IDENTITY_PSET = "Matchline_Identity"  # #588; "Pset_" is reserved for buildingSMART sets


def _write_identity(f, products, ident):
    """Stamp matchline's own id on exported products (#588). No-op without one."""
    if not ident or not ident.get("id"):
        return
    import ifcopenshell.api.pset as _Ps

    props = {"MatchlineId": str(ident["id"]), "SourceMethod": str(ident.get("method") or "")}
    if ident.get("confidence") is not None:
        props["Confidence"] = float(ident["confidence"])
    if "merged_from" in ident:
        props["MergedFrom"] = ",".join(ident.get("merged_from") or [])
    if ident.get("split_from"):
        props["SplitFrom"] = str(ident["split_from"])
    if ident.get("poly_type"):
        props["PolyType"] = str(ident["poly_type"])
    for product in products:
        pset = _Ps.add_pset(f, product=product, name=IDENTITY_PSET)
        _Ps.edit_pset(f, pset=pset, properties=props)


def write_ifc4(model: BEMModel, path: str | Path, wall_thickness_m: float = 0.2) -> Path:
    """Write a minimal but structurally valid IFC4 file.

    Contents: IfcProject/Site/Building/BuildingStorey hierarchy, one IfcWall
    per simplified envelope edge (real SweptSolid geometry, 0.2 m thick),
    one IfcSpace per room (placement at centroid; no solid geometry in v1),
    and per opening an IfcOpeningElement hosted in its wall
    (IfcRelVoidsElement) filled by an IfcWindow/IfcDoor (IfcRelFillsElement).
    Shading surfaces (model.shades) become IfcShadingDevice plates.

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
    levels = sorted(getattr(model, "levels", None) or [], key=lambda lv: (lv.elevation_m, lv.id))
    multi = len(levels) > 1
    storey_of = {}  # level id -> (IfcBuildingStorey, its placement), multi-storey only
    if multi:
        # several storeys (#639): one IfcBuildingStorey per level at its
        # elevation; AboveGround only where the source stated it (#634)
        _Ag.assign_object(f, products=[site], relating_object=proj)
        _Ag.assign_object(f, products=[bldg], relating_object=site)
        for lv in levels:
            st = _R.create_entity(f, ifc_class="IfcBuildingStorey", name=lv.name or lv.id)
            st.Elevation = float(lv.elevation_m)
            _Ag.assign_object(f, products=[st], relating_object=bldg)
            st_pl = placement((0.0, 0.0, float(lv.elevation_m)))
            st.ObjectPlacement = st_pl
            if lv.above_ground is not None:
                # written by hand: the pset API rejects a Python bool for this
                # IfcLogical property; IfcBoolean reads back as True/False
                prop = f.create_entity(
                    "IfcPropertySingleValue",
                    Name="AboveGround",
                    NominalValue=f.create_entity("IfcBoolean", bool(lv.above_ground)),
                )
                ps = f.create_entity(
                    "IfcPropertySet",
                    GlobalId=ifcopenshell.guid.new(),
                    Name="Pset_BuildingStoreyCommon",
                    HasProperties=[prop],
                )
                f.create_entity(
                    "IfcRelDefinesByProperties",
                    GlobalId=ifcopenshell.guid.new(),
                    RelatedObjects=[st],
                    RelatingPropertyDefinition=ps,
                )
            storey_of[lv.id] = (st, st_pl)
        base = next((lv for lv in levels if lv.wall_type == "ExteriorWall"), levels[0])
        storey, storey_pl = storey_of[base.id]
    else:
        storey = _R.create_entity(f, ifc_class="IfcBuildingStorey", name="Level 1")
        storey.Elevation = 0.0
        _Ag.assign_object(f, products=[site], relating_object=proj)
        _Ag.assign_object(f, products=[bldg], relating_object=site)
        _Ag.assign_object(f, products=[storey], relating_object=bldg)
        storey_pl = placement((0.0, 0.0, 0.0))
        storey.ObjectPlacement = storey_pl
    space_level = {}
    if multi:
        from bem_multistorey import level_walls, space_levels

        space_level = space_levels(model, levels)

    def storey_for(sid):
        lv = space_level.get(sid)
        return storey_of[lv.id] if lv is not None else (storey, storey_pl)

    # --- walls ------------------------------------------------------------
    walls = []
    level_notes = []
    if multi:
        wall_groups = [
            (*storey_of[lv.id], lv.height_m, edges, assign, owner, f"{lv.id}-")
            for lv, edges, assign, owner in level_walls(model, levels, space_level, level_notes)
        ]
    else:
        edges = _wall_edges(model.ring_m)
        opening_assign = _distribute_openings(
            model.openings,
            edges,
            getattr(model, "ring_facades", None),
            _edge_spaces(edges, model.spaces),
        )
        wall_groups = [(storey, storey_pl, h, edges, opening_assign, None, "")]
    for w_storey, w_pl, w_h, edges, opening_assign, owners, prefix in wall_groups:
        for i, (p0, p1) in enumerate(edges):
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            wall = _R.create_entity(f, ifc_class="IfcWall", name=f"Wall-{prefix}{i + 1}")
            wall.ObjectPlacement = placement(
                (p0[0], p0[1], 0.0), ref_dir=(dx / L, dy / L, 0.0), parent=w_pl
            )
            # centred on the ring edge: the ring is the wall centreline, which is
            # what import reads back from the wall body (#579), so a re-import
            # gets the same footprint (#609). offset=0 put the whole body on one
            # side and shrank the re-imported ring by half a thickness.
            rep = _Gm.add_wall_representation(
                f,
                context=body,
                length=L,
                height=w_h,
                thickness=wall_thickness_m,
                offset=-wall_thickness_m / 2.0,
            )
            _Gm.assign_representation(f, product=wall, representation=rep)
            _Sp.assign_container(f, products=[wall], relating_structure=w_storey)
            walls.append((wall, p0, p1, L))
            # per-space area-weighted wall U (roadmap item 6), best effort
            host = owners[i] if owners else _assign_wall_to_space(p0, p1, model.spaces)
            u = getattr(host, "wall_u_value_w_m2k", None)
            if u is not None and u > 0:
                try:
                    import ifcopenshell.api.pset as _Ps

                    pset = _Ps.add_pset(f, product=wall, name="Pset_WallCommon")
                    _Ps.edit_pset(
                        f,
                        pset=pset,
                        properties={"ThermalTransmittance": float(u), "IsExternal": True},
                    )
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    pass  # thermal property is enrichment, not core validity

            # openings hosted in this wall
            placements, _ = _place_openings_on_wall(opening_assign[i], L, w_h)
            for pl_ in placements:
                u = pl_["unit"]
                s_mid = (pl_["s0"] + pl_["s1"]) / 2.0
                opening = _R.create_entity(
                    f, ifc_class="IfcOpeningElement", name=f"{u.tag} opening"
                )
                # opening local frame: wall frame translated along the wall
                opening.ObjectPlacement = placement(
                    (s_mid, 0.0, pl_["sill"]), parent=wall.ObjectPlacement
                )
                # real void solid through the wall (as for skylights), so
                # importers can read width/height/sill from geometry instead of
                # getting a dimensionless opening
                _o_prof = f.create_entity(
                    "IfcRectangleProfileDef",
                    ProfileType="AREA",
                    XDim=float(pl_["s1"] - pl_["s0"]),
                    YDim=float(2.0 * wall_thickness_m + 0.2),
                    Position=f.create_entity(
                        "IfcAxis2Placement2D",
                        Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0)),
                    ),
                )
                _o_solid = f.create_entity(
                    "IfcExtrudedAreaSolid",
                    SweptArea=_o_prof,
                    Position=f.create_entity(
                        "IfcAxis2Placement3D",
                        Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
                    ),
                    ExtrudedDirection=f.create_entity(
                        "IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)
                    ),
                    Depth=float(pl_["height"]),
                )
                opening.Representation = f.create_entity(
                    "IfcProductDefinitionShape",
                    Representations=[
                        f.create_entity(
                            "IfcShapeRepresentation",
                            ContextOfItems=body,
                            RepresentationIdentifier="Body",
                            RepresentationType="SweptSolid",
                            Items=[_o_solid],
                        )
                    ],
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
                _Sp.assign_container(f, products=[fill], relating_structure=w_storey)
                _write_identity(f, [opening, fill], getattr(u, "identity", None))

    # --- ground slab -------------------------------------------------------
    # Written only when the model knows its U (Pset_SlabCommon), so files
    # without one are unchanged. BASESLAB under the footprint, top at z=0.
    slab_u = getattr(model, "slab_u_value_w_m2k", None)
    slab_u = float(slab_u) if slab_u is not None and slab_u > 0 else None
    if slab_u is not None and not multi:
        ground = _R.create_entity(
            f, ifc_class="IfcSlab", name="Ground slab", predefined_type="BASESLAB"
        )
        ground.ObjectPlacement = placement((0.0, 0.0, -GROUND_SLAB_THICKNESS_M), parent=storey_pl)
        rep = _Gm.add_slab_representation(
            f,
            context=body,
            depth=GROUND_SLAB_THICKNESS_M,
            polyline=[(float(x), float(y)) for x, y in model.ring_m],
        )
        _Gm.assign_representation(f, product=ground, representation=rep)
        _Sp.assign_container(f, products=[ground], relating_structure=storey)
        try:
            import ifcopenshell.api.pset as _Ps

            pset = _Ps.add_pset(f, product=ground, name="Pset_SlabCommon")
            _Ps.edit_pset(
                f, pset=pset, properties={"ThermalTransmittance": slab_u, "IsExternal": True}
            )
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass  # thermal property is enrichment, not core validity

    def _skylights(roof, placed, st):
        """Skylight openings voiding ``roof``, filled by IfcWindow SKYLIGHT, in ``st``."""
        for pl_ in placed:
            u = pl_["unit"]
            xs_ = [pt[0] for pt in pl_["rect"]]
            ys_ = [pt[1] for pt in pl_["rect"]]
            sx, sy = (min(xs_) + max(xs_)) / 2.0, (min(ys_) + max(ys_)) / 2.0
            opening = _R.create_entity(f, ifc_class="IfcOpeningElement", name=f"{u.tag} opening")
            opening.ObjectPlacement = placement((sx, sy, 0.0), parent=roof.ObjectPlacement)
            # real void solid: a box through the slab, so importers (ours
            # included) can read the skylight's size from geometry
            prof = f.create_entity(
                "IfcRectangleProfileDef",
                ProfileType="AREA",
                XDim=float(u.width_m),
                YDim=float(u.height_m),
                Position=f.create_entity(
                    "IfcAxis2Placement2D",
                    Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0)),
                ),
            )
            solid = f.create_entity(
                "IfcExtrudedAreaSolid",
                SweptArea=prof,
                Position=f.create_entity(
                    "IfcAxis2Placement3D",
                    Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, -0.05)),
                ),
                ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
                Depth=ROOF_SLAB_THICKNESS_M + 0.1,
            )
            opening.Representation = f.create_entity(
                "IfcProductDefinitionShape",
                Representations=[
                    f.create_entity(
                        "IfcShapeRepresentation",
                        ContextOfItems=body,
                        RepresentationIdentifier="Body",
                        RepresentationType="SweptSolid",
                        Items=[solid],
                    )
                ],
            )
            f.create_entity(
                "IfcRelVoidsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingBuildingElement=roof,
                RelatedOpeningElement=opening,
            )
            fill = _R.create_entity(
                f,
                ifc_class="IfcWindow",
                name=f"{u.tag} (skylight {u.width_m:.2f}x{u.height_m:.2f} m)",
                predefined_type="SKYLIGHT",
            )
            fill.OverallWidth = float(u.width_m)
            fill.OverallHeight = float(u.height_m)
            fill.ObjectPlacement = placement((0.0, 0.0, 0.0), parent=opening.ObjectPlacement)
            f.create_entity(
                "IfcRelFillsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingOpeningElement=opening,
                RelatedBuildingElement=fill,
            )
            _Sp.assign_container(f, products=[fill], relating_structure=st)
            _write_identity(f, [opening, fill], getattr(u, "identity", None))

    # --- roof slab + skylights (roadmap item 3, wave 2b) ------------------
    # Flat-roof convention from #540: one roof over the footprint at the wall
    # top. The slab is written only when it has skylights to host or a known
    # roof U (Pset_SlabCommon.ThermalTransmittance), so files with neither are
    # unchanged. Skylights reuse the gbXML layout
    # (_place_skylights_on_roof), so both exports put them in the same spots.
    sky_units = [u for u in model.openings if u.category == "skylight"]
    regions = {sp.sid: sp.polygon_m for sp in model.spaces}
    roof_u = getattr(model, "roof_u_value_w_m2k", None)
    roof_u = float(roof_u) if roof_u is not None and roof_u > 0 else None
    roofs = list(getattr(model, "roof_planes", None) or [])
    sloped = is_sloped(roofs) and not multi
    if sloped:
        # sloped roof (#619): one IfcSlab ROOF per plane under one IfcRoof
        sky_placed, sky_notes = _write_sloped_roof(
            f, model, roofs, sky_units, regions, roof_u, body, storey, storey_pl, _R, _Gm, _Sp
        )
    elif multi:
        sky_placed, sky_notes = [], []
    else:
        sky_placed, sky_notes = _place_skylights_on_roof(sky_units, model.ring_m, regions=regions)
    if not multi and not sloped and (sky_placed or roof_u is not None):
        roof = _R.create_entity(f, ifc_class="IfcSlab", name="Roof", predefined_type="ROOF")
        roof.ObjectPlacement = placement((0.0, 0.0, h), parent=storey_pl)
        rep = _Gm.add_slab_representation(
            f,
            context=body,
            depth=ROOF_SLAB_THICKNESS_M,
            polyline=_roof_outline(model.ring_m, regions),
        )
        _Gm.assign_representation(f, product=roof, representation=rep)
        _Sp.assign_container(f, products=[roof], relating_structure=storey)
        if roof_u is not None:
            try:
                import ifcopenshell.api.pset as _Ps

                pset = _Ps.add_pset(f, product=roof, name="Pset_SlabCommon")
                _Ps.edit_pset(
                    f, pset=pset, properties={"ThermalTransmittance": roof_u, "IsExternal": True}
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # thermal property is enrichment, not core validity
        _skylights(roof, sky_placed, storey)

    n_level_slabs = 0
    if multi:
        sky_placed, sky_notes, n_level_slabs = _write_level_slabs(
            f,
            model,
            levels,
            storey_of,
            space_level,
            sky_units,
            roof_u,
            slab_u,
            body,
            placement,
            _skylights,
            _R,
            _Gm,
            _Sp,
        )
        sky_notes = sky_notes + level_notes

    # --- shading devices (roadmap item 5, wave 3) -------------------------
    # One IfcShadingDevice per BEMShade, same absolute quad the gbXML Shade
    # surface uses, as a thin swept plate extruded along the quad normal.
    # IFC4 has no OVERHANG/FIN enum value, so PredefinedType is USERDEFINED
    # with ObjectType carrying the kind. Source id and host wall id go in
    # Pset_ShadingDeviceCommon.Reference and a matchline pset for traceability.
    shades_written = 0
    shade_notes = []
    for sh in getattr(model, "shades", None) or []:
        frame = _shade_frame(sh.vertices)
        if frame is None:
            shade_notes.append(f"shade {sh.id} skipped: degenerate quad")
            continue
        origin, ax_x, _ax_y, normal, dx_len, dy_len = frame
        # Walls here are centred on the ring (#609); the quad sits offset_m
        # off the ring line, so move it out to the exterior face (#611).
        # Never inward: a quad already past the face stays where it is.
        push = max(0.0, wall_thickness_m / 2.0 - float(getattr(sh, "offset_m", 0.0) or 0.0))
        out = getattr(sh, "outward", None)
        if push > 0.0 and out is not None:
            origin = (origin[0] + out[0] * push, origin[1] + out[1] * push, origin[2])
        dev = _R.create_entity(
            f, ifc_class="IfcShadingDevice", name=sh.id, predefined_type="USERDEFINED"
        )
        dev.ObjectType = (sh.kind or "other").upper()
        ax = f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint", Coordinates=origin),
            Axis=f.create_entity("IfcDirection", DirectionRatios=normal),
            RefDirection=f.create_entity("IfcDirection", DirectionRatios=ax_x),
        )
        dev.ObjectPlacement = f.create_entity(
            "IfcLocalPlacement", PlacementRelTo=storey_pl, RelativePlacement=ax
        )
        prof = f.create_entity(
            "IfcRectangleProfileDef",
            ProfileType="AREA",
            XDim=dx_len,
            YDim=dy_len,
            Position=f.create_entity(
                "IfcAxis2Placement2D",
                Location=f.create_entity(
                    "IfcCartesianPoint", Coordinates=(dx_len / 2.0, dy_len / 2.0)
                ),
            ),
        )
        solid = f.create_entity(
            "IfcExtrudedAreaSolid",
            SweptArea=prof,
            Position=f.create_entity(
                "IfcAxis2Placement3D",
                Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
            ),
            ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
            Depth=SHADE_THICKNESS_M,
        )
        dev.Representation = f.create_entity(
            "IfcProductDefinitionShape",
            Representations=[
                f.create_entity(
                    "IfcShapeRepresentation",
                    ContextOfItems=body,
                    RepresentationIdentifier="Body",
                    RepresentationType="SweptSolid",
                    Items=[solid],
                )
            ],
        )
        _Sp.assign_container(f, products=[dev], relating_structure=storey)
        try:
            import ifcopenshell.api.pset as _Ps

            common = _Ps.add_pset(f, product=dev, name="Pset_ShadingDeviceCommon")
            _Ps.edit_pset(f, pset=common, properties={"Reference": sh.id})
            src = _Ps.add_pset(f, product=dev, name="Matchline_ShadingSource")
            _Ps.edit_pset(
                f, pset=src, properties={"Kind": sh.kind or "", "HostWallId": sh.host_wall_id or ""}
            )
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass  # traceability is enrichment, not core validity
        shades_written += 1

    # --- spaces -----------------------------------------------------------
    ifc_space_by_sid = {}  # sid -> IfcSpace entity for zone assignment
    for sp in model.spaces:
        n = len(sp.polygon_m)
        cx = sum(p[0] for p in sp.polygon_m) / n
        cy = sum(p[1] for p in sp.polygon_m) / n
        space = _R.create_entity(f, ifc_class="IfcSpace", name=sp.name)
        sp_storey, sp_pl = storey_for(sp.sid)
        space.ObjectPlacement = placement((cx, cy, 0.0), parent=sp_pl)
        try:
            space.PredefinedType = "SPACE"
        except AttributeError:
            pass
        # spaces decompose the storey spatially (IfcRelAggregates), they are
        # not "contained products" (IfcSpace has no ContainedInStructure)
        _Ag.assign_object(f, products=[space], relating_object=sp_storey)
        _write_identity(f, [space], getattr(sp, "identity", None))
        # gross floor area as a quantity set (best effort)
        try:
            import ifcopenshell.api.pset as _Ps

            qto = _Ps.add_qto(f, product=space, name="Qto_SpaceBaseQuantities")
            _Ps.edit_qto(f, qto=qto, properties={"GrossFloorArea": sp.area_m2})
        except (AttributeError, TypeError, ValueError, RuntimeError):
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
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # footprint is enrichment, not required for validity
        # lighting power as a property (best effort)
        if sp.lighting_w > 0:
            try:
                import ifcopenshell.api.pset as _Ps

                pset = _Ps.add_pset(f, product=space, name="Pset_SpaceLighting")
                _Ps.edit_pset(f, pset=pset, properties={"LightingPower": sp.lighting_w})
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        ifc_space_by_sid[sp.sid] = space

    # --- zones ------------------------------------------------------------
    terminals_by_id = {}  # a diffuser listed under two zones is one IfcAirTerminal
    for zone_id, zone_space_ids in model.zones:
        zone = f.create_entity("IfcZone", Name=zone_id)
        members = [ifc_space_by_sid[sid] for sid in zone_space_ids if sid in ifc_space_by_sid]
        for did, tag, x, y in getattr(model, "zone_terminals", {}).get(zone_id, []):
            term = terminals_by_id.get(did)
            if term is None:
                term = _R.create_entity(f, ifc_class="IfcAirTerminal", name=did)
                term.PredefinedType = "DIFFUSER"
                if tag:
                    term.Tag = tag
                # ceiling-mounted: at wall height, in the storey frame
                term.ObjectPlacement = placement(
                    (float(x), float(y), float(model.wall_height_m)), parent=storey_pl
                )
                _Sp.assign_container(f, products=[term], relating_structure=storey)
                terminals_by_id[did] = term
            members.append(term)
        if members:
            _Grp.assign_group(f, products=members, group=zone)

    # --- Appendix G thermal zones and air walls (#638) --------------------
    for zid, sids in getattr(model, "thermal_zones", None) or []:
        zone = f.create_entity(
            "IfcZone", Name=zid, ObjectType="ASHRAE 90.1 Appendix G thermal block"
        )
        zone.GlobalId = ifcopenshell.guid.new()
        members = [ifc_space_by_sid[sid] for sid in sids if sid in ifc_space_by_sid]
        if members:
            _Grp.assign_group(f, products=members, group=zone)
    air_written = 0
    for aw in getattr(model, "air_walls", None) or []:
        a = ifc_space_by_sid.get(aw.space_ids[0])
        b = ifc_space_by_sid.get(aw.space_ids[1])
        if a is None or b is None:
            continue
        virt = _R.create_entity(f, ifc_class="IfcVirtualElement", name=aw.id)
        aw_storey, aw_pl = storey_for(aw.space_ids[0])
        virt.ObjectPlacement = placement((0.0, 0.0, 0.0), parent=aw_pl)
        line = f.create_entity(
            "IfcPolyline",
            Points=[
                f.create_entity("IfcCartesianPoint", Coordinates=(float(x), float(y)))
                for x, y in (aw.p0, aw.p1)
            ],
        )
        virt.Representation = f.create_entity(
            "IfcProductDefinitionShape",
            Representations=[
                f.create_entity(
                    "IfcShapeRepresentation",
                    ContextOfItems=body,
                    RepresentationIdentifier="Axis",
                    RepresentationType="Curve2D",
                    Items=[line],
                )
            ],
        )
        _Sp.assign_container(f, products=[virt], relating_structure=aw_storey)
        for sp_ in (a, b):
            f.create_entity(
                "IfcRelSpaceBoundary",
                GlobalId=ifcopenshell.guid.new(),
                RelatingSpace=sp_,
                RelatedBuildingElement=virt,
                PhysicalOrVirtualBoundary="VIRTUAL",
                InternalOrExternalBoundary="INTERNAL",
            )
        air_written += 1

    path = _validate_out_path(path)
    f.write(str(path))
    if air_written:
        model.notes.append(f"IFC4: {air_written} air walls as IfcVirtualElement (#638).")
    model.notes.append(
        f"IFC4: {len(walls)} walls, {len(model.spaces)} "
        f"spaces, {len(model.openings) - len(sky_units)} wall openings hosted, "
        f"{len(model.zones)} zones, {len(terminals_by_id)} diffusers (IfcAirTerminal)."
        + (
            f" Roof {'planes' if sloped else 'slab'} with {len(sky_placed)} of "
            f"{len(sky_units)} skylight(s) "
            f"(IfcWindow SKYLIGHT)."
            if sky_units
            else ""
        )
        + (f" Roof U {roof_u:.4g} W/m2K (Pset_SlabCommon)." if roof_u is not None else "")
        + (f" Sloped roof: {len(roofs)} IfcSlab ROOF plane(s) under one IfcRoof." if sloped else "")
        + (
            f" Ground slab U {slab_u:.4g} W/m2K (BASESLAB, Pset_SlabCommon)."
            if slab_u is not None
            else ""
        )
        + ("".join(f" Roof: {n}" for n in sky_notes))
        + (f" {shades_written} shading device(s)." if shades_written else "")
        + (
            f" {len(levels)} storeys, {n_level_slabs} floor/ceiling/roof slab(s) (#639)."
            if multi
            else ""
        )
        + ("".join(f" Shading: {n}" for n in shade_notes))
    )
    return path


# slab per horizontal type on multi-storey exports (#639):
# (PredefinedType, storey side, top or bottom at the surface height, external)
_LEVEL_SLAB = {
    "InteriorFloor": ("FLOOR", "upper", "top", False),
    "RaisedFloor": ("FLOOR", "upper", "top", True),
    "SlabOnGrade": ("BASESLAB", "upper", "top", True),
    "UndergroundSlab": ("BASESLAB", "upper", "top", True),
    "Roof": ("ROOF", "lower", "bottom", True),
    "UndergroundCeiling": ("FLOOR", "lower", "bottom", True),
}


def _write_level_slabs(
    f,
    model,
    levels,
    storey_of,
    space_level,
    sky_units,
    roof_u,
    slab_u,
    body,
    placement,
    skylights,
    _R,
    _Gm,
    _Sp,
):
    """One IfcSlab per floor/ceiling/roof loop of a multi-storey model (#639).

    Each slab sits in the storey of the space it bounds (the space above for
    floors and slabs, the one below for roofs and underground ceilings), its
    top or bottom face on the surface height. Stated roof and slab-on-grade U
    go in Pset_SlabCommon; no other U is written because none is stated.
    Skylights go on the largest roof slab over their own space.
    """
    import ifcopenshell.api.pset as _Ps

    from bem_multistorey import _signed_area

    notes, roofs, n = [], {}, 0
    base = storey_of[levels[0].id]
    for hz in getattr(model, "horizontals", None) or []:
        spec = _LEVEL_SLAB.get(hz.surface_type)
        if spec is None:
            notes.append(f"{hz.id}: {hz.surface_type} not written")
            continue
        ptype, side, face, external = spec
        sid = hz.upper_space_id if side == "upper" else hz.lower_space_id
        lv = space_level.get(sid)
        st, st_pl = storey_of[lv.id] if lv is not None else base
        elev = float(lv.elevation_m) if lv is not None else float(levels[0].elevation_m)
        depth = GROUND_SLAB_THICKNESS_M if ptype == "BASESLAB" else ROOF_SLAB_THICKNESS_M
        z_local = hz.z_m - elev - (depth if face == "top" else 0.0)
        u = (
            roof_u
            if hz.surface_type == "Roof"
            else slab_u
            if hz.surface_type == "SlabOnGrade"
            else None
        )
        for k, loop in enumerate(hz.loops):
            name = hz.id if len(hz.loops) == 1 else f"{hz.id}-p{k + 1}"
            slab = _R.create_entity(f, ifc_class="IfcSlab", name=name, predefined_type=ptype)
            slab.ObjectPlacement = placement((0.0, 0.0, z_local), parent=st_pl)
            rep = _Gm.add_slab_representation(
                f, context=body, depth=depth, polyline=[(float(x), float(y)) for x, y in loop]
            )
            _Gm.assign_representation(f, product=slab, representation=rep)
            _Sp.assign_container(f, products=[slab], relating_structure=st)
            props = {"IsExternal": external}
            if u is not None:
                props["ThermalTransmittance"] = float(u)
            try:
                pset = _Ps.add_pset(f, product=slab, name="Pset_SlabCommon")
                _Ps.edit_pset(f, pset=pset, properties=props)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # enrichment, not core validity
            n += 1
            if hz.surface_type == "Roof":
                roofs.setdefault(sid, []).append((abs(_signed_area(loop)), name, slab, loop, st))
    placed_all = []
    groups = {}
    for u in sky_units:
        groups.setdefault(u.space_sid if u.space_sid in roofs else None, []).append(u)
    for key, units in sorted(groups.items(), key=lambda kv: kv[0] or ""):
        cands = roofs.get(key) if key else [c for v in roofs.values() for c in v]
        if not cands:
            notes.append(f"{len(units)} skylight(s) with no roof to sit on not exported")
            continue
        _a, _name, slab, loop, st = max(cands, key=lambda c: (c[0], c[1]))
        placed, pnotes = _place_skylights_on_roof(units, loop)
        notes.extend(pnotes)
        skylights(slab, placed, st)
        placed_all.extend(placed)
    return placed_all, notes, n


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
            errors.append(f"opening '{o.Name}' voids no host element")
        if o.id() not in filled:
            errors.append(f"opening '{o.Name}' has no filling element")
    for el in list(windows) + list(doors):
        if not el.ContainedInStructure:
            errors.append(f"{el.is_a()} '{el.Name}' not in a spatial container")
    for sd in f.by_type("IfcShadingDevice"):
        reps = sd.Representation.Representations if sd.Representation else []
        if not any(r.RepresentationType == "SweptSolid" for r in reps):
            errors.append(f"shading device '{sd.Name}' has no SweptSolid body")
        if not sd.ContainedInStructure:
            errors.append(f"shading device '{sd.Name}' not in a spatial container")

    return not errors, errors


def _sloped_solid(f, origin, n, u, v, pts2d, depth):
    """Prism under a plane: profile in the plane's (u, v) frame, extruded down -n."""
    pos = f.create_entity(
        "IfcAxis2Placement3D",
        Location=f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(c) for c in origin)),
        Axis=f.create_entity("IfcDirection", DirectionRatios=tuple(float(c) for c in n)),
        RefDirection=f.create_entity("IfcDirection", DirectionRatios=tuple(float(c) for c in u)),
    )
    ring = [
        f.create_entity("IfcCartesianPoint", Coordinates=(float(a), float(b))) for a, b in pts2d
    ]
    prof = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        OuterCurve=f.create_entity("IfcPolyline", Points=ring + [ring[0]]),
    )
    return f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=prof,
        Position=pos,
        ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, -1.0)),
        Depth=float(depth),
    )


def _body_shape(f, body, solid):
    return f.create_entity(
        "IfcProductDefinitionShape",
        Representations=[
            f.create_entity(
                "IfcShapeRepresentation",
                ContextOfItems=body,
                RepresentationIdentifier="Body",
                RepresentationType="SweptSolid",
                Items=[solid],
            )
        ],
    )


def _write_sloped_roof(
    f, model, roofs, sky_units, regions, roof_u, body, storey, storey_pl, _R, _Gm, _Sp
):
    """Sloped roof: one IfcSlab ROOF per plane, aggregated by one IfcRoof (#619).

    Each slab is a prism whose top face is the roof plane (the plane's full
    outline, overhang included) and which runs ROOF_SLAB_THICKNESS_M down
    along the plane normal, so the importer reads back the same plane. The
    roof U goes on every slab (Pset_SlabCommon), matching the flat roof.
    Skylights use the layout the gbXML writer uses
    (bem_roof.place_skylights_on_pieces): a void through the slab under them
    and an IfcWindow SKYLIGHT in the plane's frame.
    """
    import ifcopenshell
    import ifcopenshell.api.aggregate as _Ag

    def local(parent):
        return f.create_entity(
            "IfcLocalPlacement",
            PlacementRelTo=parent,
            RelativePlacement=f.create_entity(
                "IfcAxis2Placement3D",
                Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
            ),
        )

    roof = _R.create_entity(f, ifc_class="IfcRoof", name="Roof")
    roof.ObjectPlacement = local(storey_pl)
    _Sp.assign_container(f, products=[roof], relating_structure=storey)
    slab_of = {}
    slabs = []
    for r in roofs:
        n, u, v = plane_frame(r)
        o = r.vertices[0]
        pts2d = []
        for x, y, z in r.vertices:
            d = (x - o[0], y - o[1], z - o[2])
            pts2d.append((sum(d[i] * u[i] for i in range(3)), sum(d[i] * v[i] for i in range(3))))
        slab = _R.create_entity(
            f, ifc_class="IfcSlab", name=f"Roof plane {r.id}", predefined_type="ROOF"
        )
        slab.ObjectPlacement = local(roof.ObjectPlacement)
        slab.Representation = _body_shape(
            f, body, _sloped_solid(f, o, n, u, v, pts2d, ROOF_SLAB_THICKNESS_M)
        )
        if roof_u is not None:
            try:
                import ifcopenshell.api.pset as _Ps

                pset = _Ps.add_pset(f, product=slab, name="Pset_SlabCommon")
                _Ps.edit_pset(
                    f, pset=pset, properties={"ThermalTransmittance": roof_u, "IsExternal": True}
                )
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass  # thermal property is enrichment, not core validity
        slab_of[r.id] = slab
        slabs.append(slab)
    _Ag.assign_object(f, products=slabs, relating_object=roof)

    pieces, _ = roof_pieces(model.ring_m, roofs)
    placed, notes = place_skylights_on_pieces(sky_units, pieces, regions)
    for pl_ in placed:
        u_ = pl_["unit"]
        n, u, v = pl_["frame"]
        c = pl_["centre"]
        slab = slab_of[pl_["roof"].id]
        frame = f.create_entity(
            "IfcAxis2Placement3D",
            Location=f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(k) for k in c)),
            Axis=f.create_entity("IfcDirection", DirectionRatios=tuple(float(k) for k in n)),
            RefDirection=f.create_entity(
                "IfcDirection", DirectionRatios=tuple(float(k) for k in u)
            ),
        )
        opening = _R.create_entity(f, ifc_class="IfcOpeningElement", name=f"{u_.tag} opening")
        opening.ObjectPlacement = f.create_entity(
            "IfcLocalPlacement", PlacementRelTo=slab.ObjectPlacement, RelativePlacement=frame
        )
        hw, hh = u_.width_m / 2.0, u_.height_m / 2.0
        box = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        opening.Representation = _body_shape(
            f,
            body,
            _sloped_solid(
                f,
                (0.0, 0.0, 0.05),
                (0.0, 0.0, 1.0),
                (1.0, 0.0, 0.0),
                None,
                box,
                ROOF_SLAB_THICKNESS_M + 0.1,
            ),
        )
        f.create_entity(
            "IfcRelVoidsElement",
            GlobalId=ifcopenshell.guid.new(),
            RelatingBuildingElement=slab,
            RelatedOpeningElement=opening,
        )
        fill = _R.create_entity(
            f,
            ifc_class="IfcWindow",
            name=f"{u_.tag} (skylight {u_.width_m:.2f}x{u_.height_m:.2f} m)",
            predefined_type="SKYLIGHT",
        )
        fill.OverallWidth = float(u_.width_m)
        fill.OverallHeight = float(u_.height_m)
        fill.ObjectPlacement = local(opening.ObjectPlacement)
        f.create_entity(
            "IfcRelFillsElement",
            GlobalId=ifcopenshell.guid.new(),
            RelatingOpeningElement=opening,
            RelatedBuildingElement=fill,
        )
        _Sp.assign_container(f, products=[fill], relating_structure=storey)
        _write_identity(f, [opening, fill], getattr(u_, "identity", None))
    return placed, notes
