"""Inter-story surface matching and shaft stack consistency (#632)."""

from __future__ import annotations

import json

import pytest

from building_model import BuildingModel, Level, Space
from interstory import (
    SHAFT_IOU,
    InterstoryError,
    match_interstory,
)


def _rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _model(levels, spaces):
    m = BuildingModel(name="t", auto_triage=False)
    m.levels = [Level(id=lid, elevation_z_m=z, wall_height_m=h) for lid, z, h in levels]
    for sid, lid, poly, *rest in spaces:
        m.spaces[sid] = Space(
            id=sid, level_id=lid, polygon_m=poly, poly_type=rest[0] if rest else "room"
        )
    return m


def _two_storey(upper=None):
    return _model(
        [("L1", 0.0, 3.0), ("L2", 3.0, 3.0)],
        [
            ("L1-101", "L1", _rect(0, 0, 10, 6)),
            ("L1-102", "L1", _rect(10, 0, 20, 6)),
        ]
        + (upper if upper is not None else [("L2-201", "L2", _rect(0, 0, 20, 6))]),
    )


def _kind_area(res, kind):
    return sum(s.area_m2 for s in res.surfaces if s.kind == kind)


def test_single_level_is_ground_floor_and_roof_only():
    m = _model([("L1", 0.0, 3.0)], [("L1-101", "L1", _rect(0, 0, 10, 6))])
    res = match_interstory(m)
    assert sorted(s.kind for s in res.surfaces) == ["ground", "roof"]
    roof = [s for s in res.surfaces if s.kind == "roof"][0]
    assert roof.z_m == pytest.approx(3.0)
    assert roof.lower_space_id == "L1-101" and roof.upper_space_id is None
    assert res.findings == [] and m.review_queue == []


def test_identical_stacked_plates_share_interior_surfaces():
    res = match_interstory(_two_storey())
    inter = [s for s in res.surfaces if s.kind == "interior"]
    assert {(s.lower_space_id, s.upper_space_id) for s in inter} == {
        ("L1-101", "L2-201"),
        ("L1-102", "L2-201"),
    }
    assert all(s.z_m == pytest.approx(3.0) for s in inter)
    assert _kind_area(res, "interior") == pytest.approx(120.0)
    assert _kind_area(res, "exposed_floor") == pytest.approx(0.0)
    # only the top level's roof, no lower roof pieces
    roofs = [s for s in res.surfaces if s.kind == "roof"]
    assert {s.lower_space_id for s in roofs} == {"L2-201"}
    assert roofs[0].z_m == pytest.approx(6.0)


def test_both_sides_carry_one_surface_id():
    res = match_interstory(_two_storey())
    ids_ceiling = {s.id for s in res.ceilings_of("L1-101")}
    ids_floor = {s.id for s in res.floors_of("L2-201")}
    assert ids_ceiling & ids_floor


def test_setback_upper_floor_leaves_lower_roof():
    res = match_interstory(_two_storey([("L2-201", "L2", _rect(0, 0, 10, 6))]))
    lower_roof = [s for s in res.surfaces if s.kind == "roof" and s.lower_space_id == "L1-102"]
    assert len(lower_roof) == 1
    assert lower_roof[0].area_m2 == pytest.approx(60.0)
    assert lower_roof[0].z_m == pytest.approx(3.0)
    assert res.ceilings_of("L1-101")[0].kind == "interior"


def test_cantilever_gives_exposed_floor():
    res = match_interstory(_two_storey([("L2-201", "L2", _rect(0, -2, 20, 6))]))
    xf = [s for s in res.surfaces if s.kind == "exposed_floor"]
    assert len(xf) == 1 and xf[0].upper_space_id == "L2-201"
    assert xf[0].area_m2 == pytest.approx(40.0)
    assert xf[0].z_m == pytest.approx(3.0)


@pytest.mark.parametrize(
    "upper",
    [
        [("L2-201", "L2", _rect(0, 0, 20, 6))],
        [("L2-201", "L2", _rect(5, -3, 15, 9))],
        [("L2-201", "L2", _rect(0, 0, 7, 6)), ("L2-202", "L2", _rect(7, 0, 20, 4))],
    ],
)
def test_every_space_floor_and_ceiling_sum_to_plan_area(upper):
    m = _two_storey(upper)
    res = match_interstory(m)
    for sid, sp in m.spaces.items():
        from shapely.geometry import Polygon

        a = Polygon(sp.polygon_m).area
        assert sum(s.area_m2 for s in res.floors_of(sid)) == pytest.approx(a, rel=1e-9)
        assert sum(s.area_m2 for s in res.ceilings_of(sid)) == pytest.approx(a, rel=1e-9)


def test_overlapping_spaces_on_one_level_raise():
    m = _two_storey([("L2-201", "L2", _rect(0, 0, 12, 6)), ("L2-202", "L2", _rect(10, 0, 20, 6))])
    with pytest.raises(InterstoryError, match="overlap"):
        match_interstory(m)


def test_levels_are_ordered_by_elevation_not_list_order():
    m = _two_storey()
    m.levels.reverse()
    res = match_interstory(m)
    ground = {s.upper_space_id for s in res.surfaces if s.kind == "ground"}
    assert ground == {"L1-101", "L1-102"}


def test_low_floor_to_floor_is_flagged_not_corrected():
    m = _two_storey()
    m.levels[1].elevation_z_m = 2.5
    res = match_interstory(m)
    items = [r for r in m.review_queue if r.kind == "interstory"]
    assert len(items) == 1 and "below the top of level L1" in items[0].description
    assert m.levels[1].elevation_z_m == 2.5
    assert len(res.findings) == 1


def _shaft_model(l2_shaft, l3=True):
    levels = [("L1", 0.0, 3.0), ("L2", 3.0, 3.0)] + ([("L3", 6.0, 3.0)] if l3 else [])
    spaces = [
        ("L1-101", "L1", _rect(0, 0, 18, 6)),
        ("L1-S1", "L1", _rect(18, 0, 20, 2), "shaft"),
        ("L1-102", "L1", _rect(18, 2, 20, 6)),
        ("L2-201", "L2", _rect(0, 0, 18, 6)),
        ("L2-202", "L2", _rect(18, 2, 20, 6)),
    ]
    if l2_shaft is not None:
        spaces.append(("L2-S1", "L2", l2_shaft, "shaft"))
    else:
        spaces.append(("L2-203", "L2", _rect(18, 0, 20, 2)))
    if l3:
        spaces.append(("L3-301", "L3", _rect(0, 0, 20, 6)))
    return _model(levels, spaces)


def test_aligned_shafts_form_one_stack():
    m = _shaft_model(_rect(18, 0, 20, 2), l3=False)
    res = match_interstory(m)
    assert len(res.stacks) == 1
    st = res.stacks[0]
    assert st.id == "SHAFT-L1-S1"
    assert st.space_ids == ("L1-S1", "L2-S1") and st.level_ids == ("L1", "L2")
    assert m.review_queue == []


def test_shaft_capped_by_room_above_is_flagged():
    m = _shaft_model(_rect(18, 0, 20, 2), l3=True)
    res = match_interstory(m)
    items = [r for r in m.review_queue if r.kind == "interstory"]
    assert len(items) == 1
    assert "SHAFT-L1-S1" in items[0].description and "L3-301" in items[0].description
    assert res.stacks[0].level_ids == ("L1", "L2")


def test_shaft_ending_under_room_on_level_above_is_flagged():
    m = _shaft_model(None, l3=False)
    match_interstory(m)
    items = [r for r in m.review_queue if r.kind == "interstory"]
    assert len(items) == 1 and "L2-203" in items[0].description


def test_misaligned_shafts_are_flagged_and_not_stacked():
    m = _shaft_model(_rect(18.0, 0.0, 20.0, 1.2), l3=False)
    m.spaces["L2-202"].polygon_m = _rect(18, 1.2, 20, 6)
    res = match_interstory(m)
    assert len(res.stacks) == 2
    assert any("IoU" in f for f in res.findings)
    assert 0.0 < 0.6 < SHAFT_IOU


def test_flag_false_leaves_review_queue_alone():
    m = _shaft_model(None, l3=False)
    res = match_interstory(m, flag=False)
    assert res.findings and m.review_queue == []


def test_model_geometry_is_not_modified():
    m = _shaft_model(_rect(18, 0, 20, 2), l3=True)
    before = json.dumps({k: v.polygon_m for k, v in m.spaces.items()}, sort_keys=True)
    match_interstory(m, flag=False)
    after = json.dumps({k: v.polygon_m for k, v in m.spaces.items()}, sort_keys=True)
    assert before == after


def test_result_is_deterministic_under_space_insertion_order():
    a = _two_storey([("L2-201", "L2", _rect(0, 0, 7, 6)), ("L2-202", "L2", _rect(7, 0, 20, 4))])
    b = _two_storey([("L2-202", "L2", _rect(7, 0, 20, 4)), ("L2-201", "L2", _rect(0, 0, 7, 6))])
    b.spaces = dict(reversed(list(b.spaces.items())))
    ra, rb = match_interstory(a), match_interstory(b)
    key = lambda r: [(s.id, s.kind, round(s.area_m2, 9)) for s in r.surfaces]  # noqa: E731
    assert key(ra) == key(rb)
    assert len({s.id for s in ra.surfaces}) == len(ra.surfaces)
