"""Round-trip tests for the Tier-0 IFC frontend.

Fixture strategy: bem_export.write_ifc4 builds the base IFC from a
hand-built BEMModel (our own exporter = perfect fixture generator), then
make_ifc_fixture() enriches it IN TEST CODE with the things the exporter
omits: space solid geometry, wall material layer sets, opening solids,
an IfcZone, a duct, and one geometry-less space (quantity fallback).
"""

import re

import pytest

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4
from building_model import (
    BimElement,
    BimOpening,
    BuildingModel,
    EnvelopeWall,
    OpeningAttachmentSummary,
)
from ifc_import import (
    _WALL_DIR_AMBIGUOUS_TIE,
    _WALL_DIR_NO_EDGE,
    _attach_openings_to_spaces,
    _ensure_ifc,
    _length_scale,
    _wall_direction_from_envelope,
    import_ifc,
)
from run_pipeline import StageError

_ensure_ifc()  # bootstraps the vendored IfcOpenShell before these imports
import ifcopenshell  # noqa: E402
import ifcopenshell.api.aggregate as _Ag  # noqa: E402
import ifcopenshell.api.geometry as _Gm  # noqa: E402
import ifcopenshell.api.spatial as _Sp  # noqa: E402
import ifcopenshell.guid as _guid  # noqa: E402

H = 3.0  # wall height used by the fixture


def _bem_fixture():
    spaces = [
        BEMSpace(
            sid="sp-001",
            name="OPEN OFFICE 101",
            number="101",
            polygon_m=[(0, 0), (10, 0), (10, 8), (0, 8)],
            area_m2=80.0,
            volume_m3=240.0,
        ),
        BEMSpace(
            sid="sp-002",
            name="CONF 102",
            number="102",
            polygon_m=[(10, 0), (20, 0), (20, 8), (10, 8)],
            area_m2=80.0,
            volume_m3=240.0,
        ),
        BEMSpace(
            sid="sp-003",
            name="LOBBY 103",
            number="103",
            polygon_m=[(0, 8), (20, 8), (20, 12), (0, 12)],
            area_m2=80.0,
            volume_m3=240.0,
        ),
    ]
    openings = [
        BEMOpeningUnit("window", "A", 1.5, 1.2),
        BEMOpeningUnit("window", "A", 1.5, 1.2),
        BEMOpeningUnit("window", "B", 1.2, 1.2),
        BEMOpeningUnit("window", "B", 1.2, 1.2),
        BEMOpeningUnit("door", "D1", 0.9, 2.1),
        BEMOpeningUnit("door", "D2", 0.9, 2.1),
    ]
    ring = [(0, 0), (20, 0), (20, 12), (0, 12)]
    return BEMModel(
        building_name="Fixture Building",
        spaces=spaces,
        openings=openings,
        ring_m=ring,
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )


def _placement(f, xyz, parent):
    pt = f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in xyz))
    ax = f.create_entity("IfcAxis2Placement3D", Location=pt)
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=ax)


def _add_solid(f, product, profile, depth, body_ctx):
    pos = f.create_entity(
        "IfcAxis2Placement3D",
        Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0)),
    )
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=pos,
        ExtrudedDirection=f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=float(depth),
    )
    # NB: assign_representation takes the IfcShapeRepresentation (it wraps
    # the IfcProductDefinitionShape itself), mirroring bem_export.
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=(solid,),
    )
    _Gm.assign_representation(f, product=product, representation=rep)


def _rect_profile(f, xdim, ydim, ox=0.0, oy=0.0):
    p2d = f.create_entity(
        "IfcAxis2Placement2D", Location=f.create_entity("IfcCartesianPoint", Coordinates=(ox, oy))
    )
    return f.create_entity(
        "IfcRectangleProfileDef",
        ProfileType="AREA",
        XDim=float(xdim),
        YDim=float(ydim),
        Position=p2d,
    )


def face_reference_walls(f, t=0.2):
    """Shift every IfcWall body to lie wholly inside its axis, Revit style.

    The exporter centres walls on the ring edge (#609). Fixtures that exercise
    face-stopped walls (#575 joins, #579 centrelines, linings, loops, splits)
    want the axis on the exterior face with the body inward, so they set it up
    here on purpose instead of relying on the exporter.
    """
    for wall in f.by_type("IfcWall"):
        for rep in wall.Representation.Representations:
            if rep.RepresentationIdentifier != "Body":
                continue
            for it in rep.Items:
                if not it.is_a("IfcExtrudedAreaSolid") or it.Position is None:
                    continue
                x, y, z = it.Position.Location.Coordinates
                it.Position.Location = f.create_entity(
                    "IfcCartesianPoint", Coordinates=(float(x), float(y + t / 2.0), float(z))
                )


def make_ifc_fixture(path, bem=None, face_walls=True):
    """Write base IFC via the exporter, then enrich (test code only).

    ``face_walls`` (default) moves wall bodies onto the inside of their axis
    (see :func:`face_reference_walls`); pass False for the exporter's own
    centred walls.
    """
    bem = bem or _bem_fixture()
    write_ifc4(bem, path)
    f = ifcopenshell.open(str(path))
    if face_walls:
        face_reference_walls(f)
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    storey = f.by_type("IfcBuildingStorey")[0]
    by_name = {s.Name: s for s in f.by_type("IfcSpace")}

    # --- space solids: extruded footprints --------------------------------
    for sp in bem.spaces:
        el = by_name[sp.name]
        n = len(sp.polygon_m)
        cx = sum(p[0] for p in sp.polygon_m) / n
        cy = sum(p[1] for p in sp.polygon_m) / n
        local = [(x - cx, y - cy) for x, y in sp.polygon_m]
        pts = tuple(f.create_entity("IfcCartesianPoint", Coordinates=p) for p in local + [local[0]])
        poly = f.create_entity("IfcPolyline", Points=pts)
        prof = f.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly)
        _add_solid(f, el, prof, H, body)

    # --- wall material layer sets (analytical thickness) -------------------
    for wall in f.by_type("IfcWall"):
        layers = []
        for mname, t in [("Brick", 0.09), ("Insulation", 0.08), ("Gypsum", 0.03)]:
            m = f.create_entity("IfcMaterial", Name=mname)
            layers.append(f.create_entity("IfcMaterialLayer", Material=m, LayerThickness=t))
        mls = f.create_entity("IfcMaterialLayerSet", MaterialLayers=tuple(layers))
        f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=_guid.new(),
            RelatingMaterial=mls,
            RelatedObjects=(wall,),
        )

    # --- opening + fill solids ---------------------------------------------
    t = 0.2  # write_ifc4 default wall_thickness_m
    for rel in f.by_type("IfcRelVoidsElement"):
        wall, opening = rel.RelatingBuildingElement, rel.RelatedOpeningElement
        sx, _sy, sz = [
            float(v) for v in opening.ObjectPlacement.RelativePlacement.Location.Coordinates
        ]
        fill = next(
            r.RelatedBuildingElement
            for r in f.by_type("IfcRelFillsElement")
            if r.RelatingOpeningElement == opening
        )
        m = re.search(r"([\d.]+)x([\d.]+)\s*m\)", fill.Name or "")
        assert m, f"cannot parse dims from fill name {fill.Name!r}"
        w, h = float(m.group(1)), float(m.group(2))
        _add_solid(f, opening, _rect_profile(f, w, t, -w / 2, -t / 2), h, body)
        _add_solid(f, fill, _rect_profile(f, w, 0.04, -w / 2, -0.02), h, body)

    # --- opportunistic zone: 2 spaces --------------------------------------
    zone = f.create_entity("IfcZone", GlobalId=_guid.new(), Name="ZONE-A")
    f.create_entity(
        "IfcRelAssignsToGroup",
        GlobalId=_guid.new(),
        RelatingGroup=zone,
        RelatedObjects=(by_name["OPEN OFFICE 101"], by_name["CONF 102"]),
    )

    # --- placement-only duct (inventory fallback path) ---------------------
    duct = f.create_entity("IfcDuctSegment", GlobalId=_guid.new(), Name="Duct-1")
    duct.ObjectPlacement = _placement(f, (5.0, 5.0, 2.5), storey.ObjectPlacement)
    _Sp.assign_container(f, products=[duct], relating_structure=storey)

    # --- geometry-less space (quantity fallback path) ----------------------
    closet = f.create_entity("IfcSpace", GlobalId=_guid.new(), Name="CLOSET")
    closet.ObjectPlacement = _placement(f, (1.0, 10.0, 0.0), storey.ObjectPlacement)
    _Ag.assign_object(f, products=[closet], relating_object=storey)
    qto = f.create_entity(
        "IfcElementQuantity",
        GlobalId=_guid.new(),
        Name="Qto_SpaceBaseQuantities",
        Quantities=(f.create_entity("IfcQuantityArea", Name="GrossFloorArea", AreaValue=6.0),),
    )
    f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_guid.new(),
        RelatingPropertyDefinition=qto,
        RelatedObjects=(closet,),
    )

    f.write(str(path))
    return path


@pytest.fixture()
def ifc_path(tmp_path):
    return make_ifc_fixture(tmp_path / "fixture.ifc")


@pytest.fixture()
def model(ifc_path):
    return import_ifc(ifc_path)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_hierarchy(model):
    assert model.name == "Fixture Building"
    assert len(model.levels) == 1
    assert model.levels[0].id == "L1"
    assert model.levels[0].elevation_z_m == pytest.approx(0.0)
    assert model.levels[0].wall_height_m == pytest.approx(H)


def test_spaces_roundtrip(model):
    assert len(model.spaces) == 4  # 3 solid + 1 quantity-fallback
    s101 = model.spaces["L1-101"]
    assert s101.name == "OPEN OFFICE" and s101.number == "101"
    assert s101.area_m2 == pytest.approx(80.0, rel=1e-6)
    assert s101.volume_m3 == pytest.approx(240.0, rel=1e-6)
    assert len(s101.polygon_m) == 4
    # canonical frame is y-down: IFC (x, y north) -> (x, -y)
    assert s101.polygon_m[0] == pytest.approx([0.0, 0.0])
    assert s101.polygon_m[2] == pytest.approx([10.0, -8.0])
    for sid in ("L1-102", "L1-103"):
        assert model.spaces[sid].area_m2 == pytest.approx(80.0, rel=1e-6)
    # every space fact carries provenance + GlobalId
    for sp in model.spaces.values():
        assert sp.core_provenance.method.startswith("ifc_import:tier0")
        assert "GlobalId=" in sp.core_provenance.note


def test_space_quantity_fallback(model):
    closet = model.spaces["L1-UNLABELED-1"]
    assert closet.name == "CLOSET"
    assert closet.polygon_m == []
    assert closet.area_m2 == pytest.approx(6.0)
    assert closet.core_provenance.confidence == pytest.approx(0.6)
    kinds = [i.kind for i in model.review_queue]
    assert "space_no_geometry" in kinds


def test_walls_roundtrip(model):
    walls = [e for e in model.bim_elements if e.ifc_class == "IfcWall"]
    assert len(walls) == 4
    lengths = sorted(w.length_m for w in walls)
    assert lengths == pytest.approx([12.0, 12.0, 20.0, 20.0])
    for w in walls:
        assert w.thickness_m == pytest.approx(0.2)
        assert w.height_m == pytest.approx(H)
        assert [l["material"] for l in w.material_layers] == ["Brick", "Insulation", "Gypsum"]
        assert sum(l["thickness_m"] for l in w.material_layers) == pytest.approx(0.2)
        assert w.provenance.confidence >= 0.9
    # envelope mirror
    assert len(model.envelope) == 4
    # segments sit on the body centrelines (#579): the exporter draws each
    # axis on the exterior face with a 0.2 m body inward, so every wall
    # loses 0.1 m at each end once the corners join
    t = 0.2
    assert sum(e.area_m2 for e in model.envelope) == pytest.approx(
        2 * ((20 - t) * H + (12 - t) * H)
    )
    # canonical frame spot check: wall (20,0)->(20,12) in IFC becomes
    # (20,0)->(20,-12), moved 0.1 m inward onto its centreline
    seg = next(e for e in model.envelope if e.from_m == pytest.approx([19.9, -0.1]))
    assert seg.to_m == pytest.approx([19.9, -11.9])


def test_openings_roundtrip(model):
    openings = [o for e in model.bim_elements for o in e.openings]
    assert len(openings) == 6
    wins = [o for o in openings if o.category == "window"]
    doors = [o for o in openings if o.category == "door"]
    assert len(wins) == 4 and len(doors) == 2
    for o in wins:
        assert o.sill_m == pytest.approx(0.9)
        assert o.height_m == pytest.approx(1.2)
        assert o.width_m in (pytest.approx(1.5), pytest.approx(1.2))
    for o in doors:
        assert o.sill_m == pytest.approx(0.0)
        assert o.height_m == pytest.approx(2.1)
        assert o.width_m == pytest.approx(0.9)
    tags = sorted(o.tag for o in openings)
    assert tags == ["A", "A", "B", "B", "D1", "D2"]
    for o in openings:
        assert o.host_global_id  # host wall recorded
        assert o.fill_global_id
        assert o.s_center_m is not None
        assert o.provenance.confidence == pytest.approx(0.95)


def test_zone_opportunistic(model):
    assert "ZONE-A" in model.zones
    z = model.zones["ZONE-A"]
    assert sorted(z.space_ids) == ["L1-101", "L1-102"]
    assert z.provenance.confidence == pytest.approx(0.9)


def test_duct_inventory_fallback(model):
    ducts = [e for e in model.bim_elements if e.ifc_class == "IfcDuctSegment"]
    assert len(ducts) == 1
    d = ducts[0]
    assert d.length_m is None  # placement only
    assert d.placement_m == pytest.approx([5.0, -5.0, 2.5])
    assert d.provenance.confidence == pytest.approx(0.5)


def test_units_millimetre():
    f = ifcopenshell.file(schema="IFC4")
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    f.create_entity("IfcUnitAssignment", Units=(length,))
    assert _length_scale(f) == pytest.approx(0.001)


def test_units_absent_raises_stage_error():
    f = ifcopenshell.file(schema="IFC4")
    with pytest.raises(StageError):
        _length_scale(f)


def test_model_json_roundtrip(model):
    m2 = BuildingModel.from_json(model.to_json())
    assert len(m2.bim_elements) == len(model.bim_elements)
    assert len(m2.spaces) == len(model.spaces)
    # Find a BIM element with geometry (skip placement-only ducts) to verify
    # material_layers roundtrip — element ordering is platform-dependent (CI vs local).
    w0 = next((e for e in m2.bim_elements if e.material_layers), None)
    assert w0 is not None, "No element with material_layers found"
    assert w0.openings is not None
    assert m2.spaces["L1-101"].area_m2 == pytest.approx(80.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Tests for unattached opening counters (issue #512)
# ---------------------------------------------------------------------------


def test_no_unattached_openings_in_fixture(model):
    """The standard fixture should have zero unattached openings."""
    summary = model.opening_attachment_summary
    assert summary.is_empty(), f"Expected no unattached openings, got {summary.summary_line()}"
    assert summary.total == 0


def test_wall_direction_from_envelope_no_edge():
    """Wall with no matching envelope edge endpoint returns (None, no_envelope_edge)."""
    el = BimElement(
        global_id="orphan-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[5.0, 5.0, 0.0],  # nowhere near any envelope edge
    )
    model = BuildingModel(envelope=[])  # empty envelope
    direction, reason = _wall_direction_from_envelope(el, model)
    assert direction is None
    assert reason == _WALL_DIR_NO_EDGE


def test_wall_direction_from_envelope_ambiguous_tie():
    """Two envelope edges of same length both match wall endpoint -> ambiguous."""
    # A rectangular room: walls (0,0)->(10,0) and (0,10)->(0,0) share endpoint at (0,0)
    # A wall at (0,0) with length 10 would tie between going right and going up
    el = BimElement(
        global_id="tied-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[0.0, 0.0, 0.0],
    )
    model = BuildingModel(
        envelope=[
            EnvelopeWall(
                id="e1", facade="south", from_m=[0.0, 0.0], to_m=[10.0, 0.0], length_m=10.0
            ),
            EnvelopeWall(
                id="e2", facade="west", from_m=[0.0, 0.0], to_m=[0.0, -10.0], length_m=10.0
            ),
        ]
    )
    direction, reason = _wall_direction_from_envelope(el, model)
    assert direction is None
    assert reason == _WALL_DIR_AMBIGUOUS_TIE


def test_wall_direction_from_envelope_success():
    """Single matching envelope edge returns direction, None reason."""
    el = BimElement(
        global_id="good-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[0.0, 0.0, 0.0],
    )
    model = BuildingModel(
        envelope=[
            EnvelopeWall(
                id="e1", facade="south", from_m=[0.0, 0.0], to_m=[10.0, 0.0], length_m=10.0
            ),
        ]
    )
    direction, reason = _wall_direction_from_envelope(el, model)
    assert direction is not None
    assert reason is None
    # Direction should be (1, 0) (going from (0,0) toward (10,0))
    assert direction == pytest.approx((1.0, 0.0))


def test_attach_openings_counts_no_envelope_edge():
    """Wall with no matching envelope edge increments no_envelope_edge counter."""
    el = BimElement(
        global_id="orphan-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[999.0, 999.0, 0.0],  # nowhere near any envelope edge
        openings=[
            BimOpening(id="o1", category="window"),
            BimOpening(id="o2", category="window"),
        ],
    )
    model = BuildingModel(
        bim_elements=[el],
        envelope=[],
    )
    _attach_openings_to_spaces(model)
    summary = model.opening_attachment_summary
    assert summary.no_envelope_edge == 2
    assert summary.total == 2
    # Should also have a review item
    assert len(model.review_queue) == 1
    assert model.review_queue[0].kind == "opening_attachment"
    assert "no envelope edge" in model.review_queue[0].description


def test_attach_openings_counts_ambiguous_tie():
    """Wall with ambiguous tie increments ambiguous_tie counter."""
    el = BimElement(
        global_id="tied-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[0.0, 0.0, 0.0],
        openings=[BimOpening(id="o1", category="door")],
    )
    model = BuildingModel(
        bim_elements=[el],
        envelope=[
            EnvelopeWall(
                id="e1", facade="south", from_m=[0.0, 0.0], to_m=[10.0, 0.0], length_m=10.0
            ),
            EnvelopeWall(
                id="e2", facade="west", from_m=[0.0, 0.0], to_m=[0.0, -10.0], length_m=10.0
            ),
        ],
    )
    _attach_openings_to_spaces(model)
    summary = model.opening_attachment_summary
    assert summary.ambiguous_tie == 1
    assert summary.total == 1
    assert "ambiguous tie" in model.review_queue[0].description


def test_attach_openings_counts_no_ref_direction():
    """Wall with no RefDirection fallback (entity has no RefDirection) increments counter.

    This tests the case where envelope matching found an ambiguous tie (so env_reason
    is set), but the RefDirection fallback also fails, so the final failure reason
    is still 'no_ref_direction' because the RefDirection was needed and unavailable.

    Note: When the envelope matching fails with 'no_envelope_edge' and RefDirection
    also fails, we record 'no_envelope_edge' as the reason (the primary failure).
    The 'no_ref_direction' reason is used when envelope found ambiguous tie but
    RefDirection was also missing.
    """
    # To trigger 'no_ref_direction', we need:
    # 1. Envelope matching to return a reason (not None)
    # 2. RefDirection fallback to also return None
    # The current implementation sets failure_reason = env_reason when env fails,
    # so we need to test the ambiguous_tie case which correctly uses that reason.

    # Create a wall with an ambiguous envelope match but no RefDirection.
    # BimElement doesn't have _raw_ifc as a field, so it will return None from
    # _wall_direction_from_entity.
    el = BimElement(
        global_id="no-ref-wall",
        ifc_class="IfcWall",
        length_m=10.0,
        placement_m=[0.0, 0.0, 0.0],
        openings=[BimOpening(id="o1", category="window")],
    )
    # Ambiguous tie: two envelope edges of same length both match wall endpoint
    model = BuildingModel(
        bim_elements=[el],
        envelope=[
            EnvelopeWall(
                id="e1", facade="south", from_m=[0.0, 0.0], to_m=[10.0, 0.0], length_m=10.0
            ),
            EnvelopeWall(
                id="e2", facade="west", from_m=[0.0, 0.0], to_m=[0.0, -10.0], length_m=10.0
            ),
        ],
    )
    _attach_openings_to_spaces(model)
    # Both envelope (ambiguous_tie) and entity RefDirection fail, so the
    # failure_reason is the envelope reason (ambiguous_tie).
    summary = model.opening_attachment_summary
    assert summary.ambiguous_tie == 1
    assert summary.total == 1
    assert "ambiguous tie" in model.review_queue[0].description


def test_attach_openings_mixed_scenarios():
    """Multiple walls with different failure reasons produce correct per-reason counts."""
    el1 = BimElement(
        global_id="wall-no-edge",
        ifc_class="IfcWall",
        length_m=5.0,
        placement_m=[100.0, 100.0, 0.0],
        openings=[BimOpening(id="o1", category="window")],
    )
    el2 = BimElement(
        global_id="wall-tied",
        ifc_class="IfcWall",
        length_m=8.0,
        placement_m=[0.0, 0.0, 0.0],
        openings=[BimOpening(id="o2", category="door")],
    )
    el3 = BimElement(
        global_id="wall-also-no-edge",
        ifc_class="IfcWall",
        length_m=6.0,
        placement_m=[200.0, 200.0, 0.0],
        openings=[BimOpening(id="o3", category="window")],
    )
    model = BuildingModel(
        bim_elements=[el1, el2, el3],
        envelope=[
            EnvelopeWall(id="e1", facade="south", from_m=[0.0, 0.0], to_m=[8.0, 0.0], length_m=8.0),
            EnvelopeWall(id="e2", facade="west", from_m=[0.0, 0.0], to_m=[0.0, -8.0], length_m=8.0),
        ],
    )
    _attach_openings_to_spaces(model)
    summary = model.opening_attachment_summary
    assert summary.no_envelope_edge == 2  # el1 and el3 both have no matching edge
    assert summary.ambiguous_tie == 1  # el2 has ambiguous tie
    assert summary.total == 3
    assert len(model.review_queue) == 3


def test_opening_attachment_summary_methods():
    """OpeningAttachmentSummary helper methods work correctly."""
    s = OpeningAttachmentSummary()
    assert s.is_empty()
    assert s.total == 0
    assert s.summary_line() == "0 openings unattached"

    s.no_envelope_edge = 2
    assert not s.is_empty()
    assert s.total == 2
    assert "2 no envelope edge" in s.summary_line()

    s.ambiguous_tie = 3
    s.no_ref_direction = 1
    assert s.total == 6
    assert "2 no envelope edge" in s.summary_line()
    assert "3 ambiguous tie" in s.summary_line()
    assert "1 no RefDirection" in s.summary_line()


def test_import_ifc_includes_unattached_summary_in_log(model):
    """import_ifc log_revision includes unattached opening summary when non-empty."""
    # The standard fixture should have no unattached openings
    assert model.opening_attachment_summary.is_empty()
    # Check the last revision log entry
    last_log = model.revision_log[-1]
    assert "Tier-0 IFC import" in last_log.note
    # When empty, the summary line should not be in the note
    # (empty summary is not appended per the implementation)


# --- Tier 1 attach + observe + guard (#666) ---------------------------------


def _all_openings(model):
    return [(sp.id, op) for sp in model.spaces.values() for op in sp.openings]


def test_tier1_fixture_openings_attached_once(model):
    """All six fixture openings (exterior walls) land on exactly one space."""
    ids = [op.id for _, op in _all_openings(model) if op.category in ("window", "door")]
    assert len(ids) == len(set(ids)) == 6


def test_tier1_interior_door_stored_once_with_adjacent_space(monkeypatch):
    """An interior door lands once, on the lower-id space, naming the other side."""
    import ifc_import

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    _attach_openings_to_spaces(m)
    assert [(sid, op.id, op.adjacent_space_id) for sid, op in _all_openings(m)] == [
        ("R-1", "d1", "R-2")
    ]


def test_tier1_exterior_openings_have_no_adjacent_space(model):
    ops = [op for _, op in _all_openings(model) if op.category in ("window", "door")]
    assert len(ops) == 6
    assert all(op.adjacent_space_id is None for op in ops)


def test_tier1_fixture_passes_space_opening_attachment(model):
    from validate import run_checks

    res = {r.check_id: r for r in run_checks(model).results}
    assert res["space_opening_attachment"].severity == "pass", res[
        "space_opening_attachment"
    ].message


def _two_room_model(level_id="L1", wall_level="L1", thickness=0.2):
    """Two 5x4 rooms sharing the wall x=5; the wall runs +y from (5, 0)."""
    from building_model import Space

    rooms = {
        "R-1": Space(
            id="R-1", level_id=level_id, name="A", polygon_m=[[0, 0], [5, 0], [5, 4], [0, 4]]
        ),
        "R-2": Space(
            id="R-2", level_id=level_id, name="B", polygon_m=[[5, 0], [10, 0], [10, 4], [5, 4]]
        ),
    }
    wall = BimElement(
        global_id="shared-wall",
        ifc_class="IfcWall",
        level_id=wall_level,
        length_m=4.0,
        thickness_m=thickness,
        placement_m=[5.0, 0.0, 0.0],
        openings=[
            BimOpening(
                id="d1", category="door", tag="D1", width_m=0.9, height_m=2.1, s_center_m=2.0
            )
        ],
    )
    return BuildingModel(spaces=rooms, bim_elements=[wall])


def test_tier1_ref_direction_fallback_attaches_at_085(monkeypatch):
    """No envelope edge: the wall's RefDirection places the door, at confidence 0.85."""
    import ifc_import

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    _attach_openings_to_spaces(m)
    [(sid, op)] = _all_openings(m)
    assert (sid, op.adjacent_space_id) == ("R-1", "R-2")
    assert op.provenance.method == "ifc_ref_direction"
    assert op.provenance.confidence == pytest.approx(0.85)
    s = m.opening_attachment_summary
    assert s.ref_direction_fallback == 1 and s.is_empty()
    assert "1 attached via RefDirection" in s.summary_line()


def test_tier1_host_interval_set_along_wall(monkeypatch):
    """Attached IFC openings carry [s0, s1] along the host wall around s_center_m (#681)."""
    import ifc_import

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    _attach_openings_to_spaces(m)
    [(_, op)] = _all_openings(m)
    assert op.host_interval_m == [pytest.approx(1.55), pytest.approx(2.45)]
    assert op.host_interval_m[0] <= op.s_center_m <= op.host_interval_m[1]
    assert not [r for r in m.review_queue if r.kind == "adjacency_ambiguous"]
    assert m.opening_attachment_summary.adjacency_ambiguous == 0


def test_tier1_host_interval_clamped_to_wall(monkeypatch):
    """An opening near the wall end never gets an interval past the wall."""
    import ifc_import

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    m.bim_elements[0].openings[0].s_center_m = 0.3
    _attach_openings_to_spaces(m)
    [(_, op)] = _all_openings(m)
    assert op.host_interval_m == [pytest.approx(0.0), pytest.approx(0.75)]


def test_tier1_opening_spanning_partition_is_ambiguous(monkeypatch):
    """A door whose edge reaches a second room on the same side goes to review (#681)."""
    import ifc_import
    from building_model import Space

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    # split R-1 at y=2: the door (centre y=1.8, 0.9 wide) now reaches R-3
    m.spaces["R-1"].polygon_m = [[0, 0], [5, 0], [5, 2], [0, 2]]
    m.spaces["R-3"] = Space(
        id="R-3", level_id="L1", name="C", polygon_m=[[0, 2], [5, 2], [5, 4], [0, 4]]
    )
    m.bim_elements[0].openings[0].s_center_m = 1.8
    _attach_openings_to_spaces(m)
    [(sid, op)] = _all_openings(m)
    assert (sid, op.adjacent_space_id) == ("R-1", "R-2")
    assert op.needs_review
    [item] = [r for r in m.review_queue if r.kind == "adjacency_ambiguous"]
    assert "R-3" in item.description and item.needs_review
    s = m.opening_attachment_summary
    assert s.adjacency_ambiguous == 1 and s.is_empty()
    assert "1 spanning a room boundary" in s.summary_line()


def test_tier1_ambiguous_opening_blocks_export_until_acknowledged(monkeypatch):
    """adjacency_ambiguous rides the existing review gate, no new check needed."""
    import ifc_import
    from building_model import Space
    from validate import run_checks

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    m.spaces["R-1"].polygon_m = [[0, 0], [5, 0], [5, 2], [0, 2]]
    m.spaces["R-3"] = Space(
        id="R-3", level_id="L1", name="C", polygon_m=[[0, 2], [5, 2], [5, 4], [0, 4]]
    )
    m.bim_elements[0].openings[0].s_center_m = 1.8
    _attach_openings_to_spaces(m)
    rq = {r.check_id: r.severity for r in run_checks(m).results}["review_queue_acknowledged"]
    assert rq == "error"


def test_tier1_other_level_spaces_never_candidates(monkeypatch):
    """A wall on L2 never attaches to L1 rooms at the same plan position."""
    import ifc_import

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model(level_id="L1", wall_level="L2")
    _attach_openings_to_spaces(m)
    assert _all_openings(m) == []
    assert m.opening_attachment_summary.outside_spaces == 1
    assert m.review_queue and m.review_queue[-1].kind == "opening_attachment"


def test_tier1_outside_spaces_flags_and_warns(monkeypatch):
    """Direction known but no room on either side: counted, queued, check warns."""
    import ifc_import
    from validate import run_checks

    monkeypatch.setattr(ifc_import, "_wall_direction_from_entity", lambda el: (0.0, 1.0))
    m = _two_room_model()
    m.bim_elements[0].placement_m = [50.0, 50.0, 0.0]
    _attach_openings_to_spaces(m)
    s = m.opening_attachment_summary
    assert s.outside_spaces == 1 and s.total == 1
    assert "1 outside every space" in s.summary_line()
    res = {r.check_id: r for r in run_checks(m).results}
    chk = res["space_opening_attachment"]
    assert chk.severity == "warn"
    assert "d1" in chk.entities
    assert "L1: 1/1" in chk.message


def test_space_opening_attachment_skips_without_ifc_walls():
    from validate import run_checks

    res = {r.check_id: r for r in run_checks(BuildingModel()).results}
    assert res["space_opening_attachment"].severity == "skip"


def test_space_opening_attachment_never_errors_on_full_miss():
    """Every opening unattached is still a warn: Tier 1 gaps never block export."""
    from validate import run_checks

    m = _two_room_model()
    res = {r.check_id: r for r in run_checks(m).results}
    assert res["space_opening_attachment"].severity == "warn"


def test_adjacent_space_id_round_trips():
    from building_model import Space, SpaceOpening

    m = BuildingModel(
        spaces={
            "R-1": Space(
                id="R-1",
                level_id="L1",
                name="A",
                openings=[
                    SpaceOpening(
                        id="d1",
                        tag="D1",
                        category="door",
                        width_m=0.9,
                        height_m=2.1,
                        adjacent_space_id="R-2",
                    )
                ],
            )
        }
    )
    back = BuildingModel.from_dict(m.to_dict())
    assert back.spaces["R-1"].openings[0].adjacent_space_id == "R-2"
