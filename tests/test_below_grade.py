"""Below-grade storeys and ground/outdoor boundary types (#634)."""

from __future__ import annotations

import pytest

from below_grade import boundary_types
from building_model import BuildingModel, EnvelopeWall, Level, Space
from interstory import match_interstory


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _model(levels, spaces, walls=()):
    m = BuildingModel(name="t", auto_triage=False)
    m.levels = [
        Level(id=lid, elevation_z_m=z, wall_height_m=3.0, above_ground=ag) for lid, z, ag in levels
    ]
    for sid, lid, poly in spaces:
        m.spaces[sid] = Space(id=sid, level_id=lid, polygon_m=poly)
    m.envelope = [
        EnvelopeWall(id=wid, facade="south", from_m=[0, 0], to_m=[10, 0], space_id=sid)
        for wid, sid in walls
    ]
    return m


def _basement(b1=False, l1=True, upper=None):
    return _model(
        [("B1", -3.0, b1), ("L1", 0.0, l1)],
        [
            ("B1-001", "B1", _rect(0, 0, 20, 6)),
        ]
        + (upper if upper is not None else [("L1-101", "L1", _rect(0, 0, 20, 6))]),
        walls=[("W-B", "B1-001"), ("W-1", "L1-101")],
    )


def _types(res, m, kind):
    ist = match_interstory(m, flag=False)
    return {s.id: res.horizontals[s.id] for s in ist.surfaces if s.kind == kind}


def test_unstated_single_storey_matches_todays_export_types():
    m = _model([("L1", 0.0, None)], [("L1-101", "L1", _rect(0, 0, 10, 6))], [("W1", "L1-101")])
    res = boundary_types(m)
    assert res.walls == {"W1": "ExteriorWall"}
    assert sorted(res.horizontals.values()) == ["Roof", "SlabOnGrade"]
    assert res.below_grade_levels == [] and m.review_queue == []


def test_stated_basement_walls_and_slab_are_underground():
    m = _basement()
    res = boundary_types(m)
    assert res.below_grade_levels == ["B1"]
    assert res.walls == {"W-B": "UndergroundWall", "W-1": "ExteriorWall"}
    assert set(_types(res, m, "ground").values()) == {"UndergroundSlab"}
    assert set(_types(res, m, "interior").values()) == {"InteriorFloor"}
    assert set(_types(res, m, "roof").values()) == {"Roof"}
    assert m.review_queue == []


def test_two_basements_ceiling_between_is_interior_and_setback_is_underground_ceiling():
    m = _model(
        [("B2", -6.0, False), ("B1", -3.0, False), ("L1", 0.0, True)],
        [
            ("B2-001", "B2", _rect(0, 0, 20, 6)),
            ("B1-001", "B1", _rect(0, 0, 10, 6)),
            ("L1-101", "L1", _rect(0, 0, 10, 6)),
        ],
    )
    res = boundary_types(m)
    ist = match_interstory(m, flag=False)
    b2_roof = [s for s in ist.surfaces if s.kind == "roof" and s.lower_space_id == "B2-001"]
    assert len(b2_roof) == 1 and b2_roof[0].area_m2 == pytest.approx(60.0)
    assert res.horizontals[b2_roof[0].id] == "UndergroundCeiling"
    assert m.review_queue == []


def test_basement_under_courtyard_is_unresolved_and_flagged():
    m = _basement(upper=[("L1-101", "L1", _rect(0, 0, 10, 6))])
    res = boundary_types(m)
    roofs = _types(res, m, "roof")
    deck = [sid for sid, v in roofs.items() if v is None]
    assert len(deck) == 1
    items = [r for r in m.review_queue if r.kind == "below_grade"]
    assert len(items) == 1 and "B1-001" in items[0].description


def test_cantilever_above_grade_is_raised_floor():
    m = _basement(upper=[("L1-101", "L1", _rect(0, -2, 20, 6))])
    res = boundary_types(m)
    assert set(_types(res, m, "exposed_floor").values()) == {"RaisedFloor"}


def test_lower_basement_floor_beyond_the_one_below_is_underground_slab():
    m = _model(
        [("B2", -6.0, False), ("B1", -3.0, False)],
        [("B2-001", "B2", _rect(0, 0, 10, 6)), ("B1-001", "B1", _rect(0, 0, 20, 6))],
    )
    res = boundary_types(m, flag=False)
    assert set(_types(res, m, "exposed_floor").values()) == {"UndergroundSlab"}


def test_unstated_level_below_zero_is_flagged_not_reclassified():
    m = _basement(b1=None, l1=None)
    res = boundary_types(m)
    assert res.walls["W-B"] == "ExteriorWall"
    assert set(_types(res, m, "ground").values()) == {"SlabOnGrade"}
    items = [r for r in m.review_queue if r.kind == "below_grade"]
    assert len(items) == 1 and "B1" in items[0].description
    assert m.levels[0].above_ground is None


def test_unstated_level_below_a_stated_above_ground_level_is_flagged():
    m = _model(
        [("L0", 0.0, None), ("L1", 3.0, True)],
        [("L0-001", "L0", _rect(0, 0, 10, 6)), ("L1-101", "L1", _rect(0, 0, 10, 6))],
    )
    res = boundary_types(m)
    assert any("L0" in f for f in res.findings)


def test_below_grade_over_above_grade_is_flagged():
    m = _model(
        [("L1", 0.0, True), ("L2", 3.0, False)],
        [("L1-101", "L1", _rect(0, 0, 10, 6)), ("L2-201", "L2", _rect(0, 0, 10, 6))],
    )
    res = boundary_types(m, flag=False)
    assert any("stated below grade but sits above" in f for f in res.findings)
    assert m.review_queue == []


def test_wall_with_no_space_is_unresolved_only_when_a_basement_exists():
    m = _basement()
    m.envelope.append(EnvelopeWall(id="W-X", facade="north"))
    res = boundary_types(m)
    assert res.walls["W-X"] is None
    assert any("W-X" in r.description for r in m.review_queue)
    m2 = _model(
        [("L1", 0.0, True), ("L2", 3.0, True)],
        [("L1-101", "L1", _rect(0, 0, 10, 6)), ("L2-201", "L2", _rect(0, 0, 10, 6))],
    )
    m2.envelope = [EnvelopeWall(id="W-X", facade="north")]
    assert boundary_types(m2).walls["W-X"] == "ExteriorWall"


def test_model_levels_are_not_modified():
    m = _basement(b1=None, l1=None)
    boundary_types(m)
    assert [lv.above_ground for lv in m.levels] == [None, None]


# --- IFC import of Pset_BuildingStoreyCommon.AboveGround ------------------

ifcopenshell = pytest.importorskip("ifcopenshell")


def _fixture_with_above_ground(tmp_path, value):
    import ifcopenshell.guid as guid

    from ifc_import import import_ifc
    from tests.test_ifc_import import make_ifc_fixture

    path = make_ifc_fixture(tmp_path / "base.ifc")
    f = ifcopenshell.open(str(path))
    st = f.by_type("IfcBuildingStorey")[0]
    if value is not None:
        prop = f.create_entity(
            "IfcPropertySingleValue",
            Name="AboveGround",
            NominalValue=f.create_entity("IfcLogical", value),
        )
        ps = f.create_entity(
            "IfcPropertySet",
            GlobalId=guid.new(),
            Name="Pset_BuildingStoreyCommon",
            HasProperties=[prop],
        )
        f.create_entity(
            "IfcRelDefinesByProperties",
            GlobalId=guid.new(),
            RelatedObjects=[st],
            RelatingPropertyDefinition=ps,
        )
    out = tmp_path / "ag.ifc"
    f.write(str(out))
    return import_ifc(out)


@pytest.mark.parametrize("value", [True, False])
def test_ifc_import_reads_above_ground(tmp_path, value):
    m = _fixture_with_above_ground(tmp_path, value)
    assert m.levels[0].above_ground is value
    assert m.levels[0].above_ground_source == "ifc:Pset_BuildingStoreyCommon.AboveGround"


def test_ifc_import_leaves_absent_above_ground_unstated(tmp_path):
    m = _fixture_with_above_ground(tmp_path, None)
    assert m.levels[0].above_ground is None and m.levels[0].above_ground_source == ""


def test_ifc_import_leaves_unknown_above_ground_unstated(tmp_path):
    m = _fixture_with_above_ground(tmp_path, "UNKNOWN")
    assert m.levels[0].above_ground is None
