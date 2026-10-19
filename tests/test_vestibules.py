"""Alex's vestibule rule (#747, 2026-10-11): exterior door, small floor area,
connects to a lobby or main entrance."""

from building_model import BuildingModel, Level, Space, SpaceOpening
from geometry.vestibules import detect_vestibules

VES_POLY = [[0, 0], [3, 0], [3, 4], [0, 4]]
LOB_POLY = [[3, 0], [10, 0], [10, 4], [3, 4]]
OFF_POLY = [[10, 0], [20, 0], [20, 4], [10, 4]]


def _space(id_, name, area, polygon, openings=None):
    return Space(
        id=id_, level_id="L1", name=name, polygon_m=polygon, area_m2=area, openings=openings or []
    )


def _door(facade="south", far=None):
    return SpaceOpening(
        id="D1",
        tag="D1",
        category="door",
        width_m=1.0,
        height_m=2.1,
        host_facade=facade,
        adjacent_space_id=far,
    )


def _model(spaces):
    return BuildingModel(
        name="t",
        levels=[Level(id="L1")],
        spaces=spaces,
        zones={},
        envelope=[],
        bim_elements=[],
        schedules={},
        review_queue=[],
        constructions={},
    )


def test_rule_holds():
    ves = _space("L1-101", "VESTIBULE", 12.0, VES_POLY, [_door()])
    lobby = _space("L1-102", "LOBBY", 60.0, LOB_POLY)
    recs = detect_vestibules(_model({"L1-101": ves, "L1-102": lobby}))
    assert recs == [
        {"space_id": "L1-101", "area_m2": 12.0, "door_tag": "D1", "lobby_neighbor": "L1-102"}
    ]


def test_far_side_counts_without_touching():
    ves = _space("L1-101", "ENTRY VESTIBULE", 9.0, VES_POLY, [_door(far="L1-102")])
    lobby = _space("L1-102", "LOBBY", 60.0, OFF_POLY)
    recs = detect_vestibules(_model({"L1-101": ves, "L1-102": lobby}))
    assert len(recs) == 1 and recs[0]["lobby_neighbor"] == "L1-102"


def test_large_area_not_vestibule():
    ves = _space("L1-101", "VESTIBULE", 40.0, VES_POLY, [_door()])
    lobby = _space("L1-102", "LOBBY", 60.0, LOB_POLY)
    assert detect_vestibules(_model({"L1-101": ves, "L1-102": lobby})) == []


def test_lobby_officer_neighbor_not_vestibule():
    ves = _space("L1-101", "VESTIBULE", 12.0, OFF_POLY, [_door()])
    office = _space("L1-102", "OPEN OFFICE", 80.0, LOB_POLY)
    assert detect_vestibules(_model({"L1-101": ves, "L1-102": office})) == []


def test_interior_door_not_vestibule():
    ves = _space("L1-101", "VESTIBULE", 12.0, VES_POLY, [_door(facade="")])
    lobby = _space("L1-102", "LOBBY", 60.0, LOB_POLY)
    assert detect_vestibules(_model({"L1-101": ves, "L1-102": lobby})) == []


def test_adjacent_vestibules_do_not_mark_each_other():
    a = _space("L1-101", "VESTIBULE", 8.0, VES_POLY, [_door()])
    b = _space("L1-102", "VESTIBULE", 8.0, LOB_POLY, [_door("north")])
    assert detect_vestibules(_model({"L1-101": a, "L1-102": b})) == []


def test_serialization_roundtrip_and_old_payload():
    import json

    ves = _space("L1-101", "VESTIBULE", 12.0, VES_POLY, [_door()])
    lobby = _space("L1-102", "LOBBY", 60.0, LOB_POLY)
    model = _model({"L1-101": ves, "L1-102": lobby})
    recs = detect_vestibules(model)
    for r in recs:
        model.spaces[r["space_id"]].is_vestibule = True
    loaded = BuildingModel.from_json(model.to_json())
    assert loaded.spaces["L1-101"].is_vestibule is True
    assert loaded.spaces["L1-102"].is_vestibule is False
    old = json.loads(model.to_json())
    for sp in old["model"]["spaces"].values():
        sp.pop("is_vestibule", None)
    loaded_old = BuildingModel.from_dict(old)
    assert loaded_old.spaces["L1-101"].is_vestibule is False
