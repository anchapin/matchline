"""Wall-less borders between spaces (#582).

Idea from Pascal's room-first.ts addSpaceSeparators (MIT, Copyright (c) 2026
Pascal Group Inc., commit 67f8041).
"""

from __future__ import annotations

import pytest

from building_model import BuildingModel, Space
from ifc_space_borders import find_virtual_borders

pytest.importorskip("shapely")


def _model(*spaces):
    m = BuildingModel(name="t")
    for sid, lvl, poly in spaces:
        m.spaces[sid] = Space(id=sid, level_id=lvl, polygon_m=[list(p) for p in poly])
    return m


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _pair(m, a, b):
    return [x for x in m.space_adjacencies if {x.space_a, x.space_b} == {a, b}]


def test_open_office_split_in_two_is_one_virtual_border():
    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 0, 20, 8)))
    find_virtual_borders(m, {})
    (adj,) = m.space_adjacencies
    assert adj.boundary == "virtual" and adj.length_m == pytest.approx(8.0)
    assert adj.provenance.method == "ifc_import:tier1:space_border"
    assert adj.provenance.confidence == pytest.approx(0.7)


def test_gap_up_to_0_35_is_bridged_on_the_midline():
    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10.3, 0, 20, 8)))
    find_virtual_borders(m, {})
    (adj,) = m.space_adjacencies
    assert adj.from_m[0] == pytest.approx(10.15) and adj.to_m[0] == pytest.approx(10.15)


def test_gap_over_0_35_is_not_a_border():
    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10.5, 0, 20, 8)))
    assert find_virtual_borders(m, {}) == []


def test_partial_overlap_gives_the_shared_length():
    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 5, 20, 12)))
    find_virtual_borders(m, {})
    (adj,) = m.space_adjacencies
    assert adj.length_m == pytest.approx(3.0)


def test_wall_on_the_border_removes_it_and_a_gap_in_the_wall_stays_virtual():
    from shapely.geometry import box

    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 0, 20, 8)))
    find_virtual_borders(m, {"L1": [box(9.9, 0, 10.1, 5)]})
    (adj,) = m.space_adjacencies
    assert adj.length_m == pytest.approx(3.0, abs=0.01)
    m2 = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 0, 20, 8)))
    assert find_virtual_borders(m2, {"L1": [box(9.9, -0.1, 10.1, 8.1)]}) == []


def test_other_level_and_corner_touch_are_not_borders():
    m = _model(
        ("A", "L1", _rect(0, 0, 10, 8)),
        ("B", "L2", _rect(10, 0, 20, 8)),
        ("C", "L1", _rect(10, 8, 20, 16)),
    )
    assert find_virtual_borders(m, {}) == []


def test_virtual_element_along_the_border_is_named():
    from shapely.geometry import box

    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 0, 20, 8)))
    find_virtual_borders(m, {}, {"L1": [("VE1", box(9.995, 0, 10.005, 8))]})
    (adj,) = m.space_adjacencies
    assert adj.virtual_element_id == "VE1"
    assert adj.provenance.method == "ifc_import:tier0:virtual_element"
    assert adj.provenance.confidence == pytest.approx(0.9)


def test_borders_round_trip_through_json():
    m = _model(("A", "L1", _rect(0, 0, 10, 8)), ("B", "L1", _rect(10, 0, 20, 8)))
    find_virtual_borders(m, {})
    back = BuildingModel.from_json(m.to_json())
    assert back.space_adjacencies == m.space_adjacencies
