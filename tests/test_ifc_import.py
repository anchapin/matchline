"""Round-trip tests for the Tier-0 IFC frontend.

Fixture strategy: bem_export.write_ifc4 builds the base IFC from a
hand-built BEMModel (our own exporter = perfect fixture generator), then
make_ifc_fixture() enriches it IN TEST CODE with the things the exporter
omits: space solid geometry, wall material layer sets, opening solids,
an IfcZone, a duct, and one geometry-less space (quantity fallback).
"""

import re

import pytest

from building_model import BuildingModel
from bem_export import BEMModel, BEMSpace, BEMOpeningUnit, write_ifc4
from ifc_import import import_ifc, _length_scale, _ensure_ifc

_ensure_ifc()
import ifcopenshell
import ifcopenshell.guid as _guid
import ifcopenshell.api.aggregate as _Ag
import ifcopenshell.api.spatial as _Sp
import ifcopenshell.api.geometry as _Gm


H = 3.0  # wall height used by the fixture


def _bem_fixture():
    spaces = [
        BEMSpace(sid="sp-001", name="OPEN OFFICE 101", number="101",
                 polygon_m=[(0, 0), (10, 0), (10, 8), (0, 8)],
                 area_m2=80.0, volume_m3=240.0),
        BEMSpace(sid="sp-002", name="CONF 102", number="102",
                 polygon_m=[(10, 0), (20, 0), (20, 8), (10, 8)],
                 area_m2=80.0, volume_m3=240.0),
        BEMSpace(sid="sp-003", name="LOBBY 103", number="103",
                 polygon_m=[(0, 8), (20, 8), (20, 12), (0, 12)],
                 area_m2=80.0, volume_m3=240.0),
    ]
    openings = [BEMOpeningUnit("window", "A", 1.5, 1.2),
                BEMOpeningUnit("window", "A", 1.5, 1.2),
                BEMOpeningUnit("window", "B", 1.2, 1.2),
                BEMOpeningUnit("window", "B", 1.2, 1.2),
                BEMOpeningUnit("door", "D1", 0.9, 2.1),
                BEMOpeningUnit("door", "D2", 0.9, 2.1)]
    ring = [(0, 0), (20, 0), (20, 12), (0, 12)]
    return BEMModel(building_name="Fixture Building", spaces=spaces,
                    openings=openings, ring_m=ring, wall_height_m=H,
                    area_delta_pct=0.0, simplify_tol_pct=2.0)


def _placement(f, xyz, parent):
    pt = f.create_entity("IfcCartesianPoint",
                         Coordinates=tuple(float(v) for v in xyz))
    ax = f.create_entity("IfcAxis2Placement3D", Location=pt)
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent,
                           RelativePlacement=ax)


def _add_solid(f, product, profile, depth, body_ctx):
    pos = f.create_entity(
        "IfcAxis2Placement3D",
        Location=f.create_entity("IfcCartesianPoint",
                                 Coordinates=(0.0, 0.0, 0.0)))
    solid = f.create_entity(
        "IfcExtrudedAreaSolid", SweptArea=profile, Position=pos,
        ExtrudedDirection=f.create_entity("IfcDirection",
                                          DirectionRatios=(0.0, 0.0, 1.0)),
        Depth=float(depth))
    # NB: assign_representation takes the IfcShapeRepresentation (it wraps
    # the IfcProductDefinitionShape itself), mirroring bem_export.
    rep = f.create_entity(
        "IfcShapeRepresentation", ContextOfItems=body_ctx,
        RepresentationIdentifier="Body", RepresentationType="SweptSolid",
        Items=(solid,))
    _Gm.assign_representation(f, product=product, representation=rep)


def _rect_profile(f, xdim, ydim, ox=0.0, oy=0.0):
    p2d = f.create_entity(
        "IfcAxis2Placement2D",
        Location=f.create_entity("IfcCartesianPoint",
                                 Coordinates=(ox, oy)))
    return f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           XDim=float(xdim), YDim=float(ydim), Position=p2d)


def make_ifc_fixture(path, bem=None):
    """Write base IFC via the exporter, then enrich (test code only)."""
    bem = bem or _bem_fixture()
    write_ifc4(bem, path)
    f = ifcopenshell.open(str(path))
    body = [c for c in f.by_type("IfcGeometricRepresentationSubContext")
            if c.ContextIdentifier == "Body"][0]
    storey = f.by_type("IfcBuildingStorey")[0]
    by_name = {s.Name: s for s in f.by_type("IfcSpace")}

    # --- space solids: extruded footprints --------------------------------
    for sp in bem.spaces:
        el = by_name[sp.name]
        n = len(sp.polygon_m)
        cx = sum(p[0] for p in sp.polygon_m) / n
        cy = sum(p[1] for p in sp.polygon_m) / n
        local = [(x - cx, y - cy) for x, y in sp.polygon_m]
        pts = tuple(f.create_entity("IfcCartesianPoint", Coordinates=p)
                    for p in local + [local[0]])
        poly = f.create_entity("IfcPolyline", Points=pts)
        prof = f.create_entity("IfcArbitraryClosedProfileDef",
                               ProfileType="AREA", OuterCurve=poly)
        _add_solid(f, el, prof, H, body)

    # --- wall material layer sets (analytical thickness) -------------------
    for wall in f.by_type("IfcWall"):
        layers = []
        for mname, t in [("Brick", 0.09), ("Insulation", 0.08),
                         ("Gypsum", 0.03)]:
            m = f.create_entity("IfcMaterial", Name=mname)
            layers.append(f.create_entity("IfcMaterialLayer", Material=m,
                                          LayerThickness=t))
        mls = f.create_entity("IfcMaterialLayerSet",
                              MaterialLayers=tuple(layers))
        f.create_entity("IfcRelAssociatesMaterial",
                        GlobalId=_guid.new(), RelatingMaterial=mls,
                        RelatedObjects=(wall,))

    # --- opening + fill solids ---------------------------------------------
    t = 0.2  # write_ifc4 default wall_thickness_m
    for rel in f.by_type("IfcRelVoidsElement"):
        wall, opening = rel.RelatingBuildingElement, rel.RelatedOpeningElement
        sx, _sy, sz = [float(v) for v in opening.ObjectPlacement
                       .RelativePlacement.Location.Coordinates]
        fill = next(r.RelatedBuildingElement
                    for r in f.by_type("IfcRelFillsElement")
                    if r.RelatingOpeningElement == opening)
        m = re.search(r"([\d.]+)x([\d.]+)\s*m\)", fill.Name or "")
        assert m, f"cannot parse dims from fill name {fill.Name!r}"
        w, h = float(m.group(1)), float(m.group(2))
        _add_solid(f, opening, _rect_profile(f, w, t, -w / 2, -t / 2), h,
                   body)
        _add_solid(f, fill, _rect_profile(f, w, 0.04, -w / 2, -0.02), h,
                   body)

    # --- opportunistic zone: 2 spaces --------------------------------------
    zone = f.create_entity("IfcZone", GlobalId=_guid.new(), Name="ZONE-A")
    f.create_entity("IfcRelAssignsToGroup", GlobalId=_guid.new(),
                    RelatingGroup=zone,
                    RelatedObjects=(by_name["OPEN OFFICE 101"],
                                    by_name["CONF 102"]))

    # --- placement-only duct (inventory fallback path) ---------------------
    duct = f.create_entity("IfcDuctSegment", GlobalId=_guid.new(),
                           Name="Duct-1")
    duct.ObjectPlacement = _placement(f, (5.0, 5.0, 2.5),
                                      storey.ObjectPlacement)
    _Sp.assign_container(f, products=[duct], relating_structure=storey)

    # --- geometry-less space (quantity fallback path) ----------------------
    closet = f.create_entity("IfcSpace", GlobalId=_guid.new(), Name="CLOSET")
    closet.ObjectPlacement = _placement(f, (1.0, 10.0, 0.0),
                                        storey.ObjectPlacement)
    _Ag.assign_object(f, products=[closet], relating_object=storey)
    qto = f.create_entity("IfcElementQuantity", GlobalId=_guid.new(),
                          Name="Qto_SpaceBaseQuantities",
                          Quantities=(f.create_entity(
                              "IfcQuantityArea", Name="GrossFloorArea",
                              AreaValue=6.0),))
    f.create_entity("IfcRelDefinesByProperties", GlobalId=_guid.new(),
                    RelatingPropertyDefinition=qto,
                    RelatedObjects=(closet,))

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
        assert [l["material"] for l in w.material_layers] == [
            "Brick", "Insulation", "Gypsum"]
        assert sum(l["thickness_m"] for l in w.material_layers) == \
            pytest.approx(0.2)
        assert w.provenance.confidence >= 0.9
    # envelope mirror
    assert len(model.envelope) == 4
    assert sum(e.area_m2 for e in model.envelope) == pytest.approx(
        2 * (20 * H + 12 * H))
    # canonical frame spot check: wall (20,0)->(20,12) in IFC
    # becomes (20,0)->(20,-12)
    seg = next(e for e in model.envelope
               if e.from_m == pytest.approx([20.0, 0.0]))
    assert seg.to_m == pytest.approx([20.0, -12.0])


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
    ducts = [e for e in model.bim_elements
             if e.ifc_class == "IfcDuctSegment"]
    assert len(ducts) == 1
    d = ducts[0]
    assert d.length_m is None  # placement only
    assert d.placement_m == pytest.approx([5.0, -5.0, 2.5])
    assert d.provenance.confidence == pytest.approx(0.5)


def test_units_millimetre():
    f = ifcopenshell.file(schema="IFC4")
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT",
                             Prefix="MILLI", Name="METRE")
    f.create_entity("IfcUnitAssignment", Units=(length,))
    assert _length_scale(f) == pytest.approx(0.001)


def test_units_absent_defaults_to_metres():
    f = ifcopenshell.file(schema="IFC4")
    assert _length_scale(f) == pytest.approx(1.0)


def test_model_json_roundtrip(model):
    m2 = BuildingModel.from_json(model.to_json())
    assert len(m2.bim_elements) == len(model.bim_elements)
    assert len(m2.spaces) == len(model.spaces)
    w0 = m2.bim_elements[0]
    assert w0.material_layers and w0.openings is not None
    assert m2.spaces["L1-101"].area_m2 == pytest.approx(80.0, rel=1e-6)


def test_tier1_stub():
    from ifc_import import infer_adjacency
    with pytest.raises(NotImplementedError):
        infer_adjacency(None)
