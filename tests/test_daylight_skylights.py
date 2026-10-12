"""Daylight area under skylights (roadmap item 3, toplighting).

ASHRAE 90.1 Sec. 3.2 / Fig. 3.2-2: skylight opening + min(0.7 x CH,
obstruction) in each horizontal direction; combined area is the union.
"""

from __future__ import annotations

import math

import pytest

from building_model import BuildingModel, Provenance, Space, SpaceOpening
from daylight_skylights import METHOD, SOURCE, SPREAD_FACTOR, compute_skylight_daylight

ROOM = [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]]


def _space(*openings, poly=ROOM):
    return Space(
        id="L1-101",
        level_id="L1",
        number="101",
        polygon_m=[list(p) for p in poly],
        area_m2=200.0,
        openings=list(openings),
    )


def _sky(oid, cx, cy, w=1.2, h=0.9, conf=0.9):
    return SpaceOpening(
        id=oid,
        tag=oid,
        category="skylight",
        width_m=w,
        height_m=h,
        host_facade="roof",
        area_m2=w * h,
        plan_center_m=[cx, cy],
        provenance=Provenance(sheet_id="ifc", revision=1, method="t", confidence=conf),
    )


def test_spread_is_seventy_percent_of_ceiling_height():
    sp = _space(_sky("SK1", 10.0, 5.0))
    compute_skylight_daylight(sp, 3.0)
    (z,) = sp.daylight.toplit
    d = SPREAD_FACTOR * 3.0
    assert math.isclose(z.area_m2, (1.2 + 2 * d) * (0.9 + 2 * d), abs_tol=1e-3)
    assert math.isclose(sp.daylight.toplit_m2, z.area_m2, abs_tol=1e-3)
    assert z.zone_class == "under_skylight" and z.window_id == "SK1" and z.head_height_m == 3.0


def test_zone_stops_at_the_space_boundary():
    sp = _space(_sky("SK1", 0.5, 0.5))
    compute_skylight_daylight(sp, 3.0)
    d = SPREAD_FACTOR * 3.0
    # left/bottom spread cut off at x=0, y=0
    assert math.isclose(sp.daylight.toplit_m2, (0.5 + 0.6 + d) * (0.5 + 0.45 + d), abs_tol=1e-3)
    for x, y in sp.daylight.toplit[0].polygon_m:
        assert 0 <= x <= 20 and 0 <= y <= 10


def test_non_convex_space_is_clipped_exactly():
    L = [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]
    sp = _space(_sky("SK1", 2.0, 2.0, w=1.0, h=1.0), poly=L)
    compute_skylight_daylight(sp, 3.0)
    # box is [-0.6, 4.6]^2 -> clipped: 4x4.6 + 0.6x4 = 18.4 + 2.4 ... computed directly
    from shapely.geometry import Polygon, box

    want = box(-0.6, -0.6, 4.6, 4.6).intersection(Polygon(L)).area
    assert math.isclose(sp.daylight.toplit_m2, want, abs_tol=1e-3)


def test_overlapping_zones_are_not_double_counted():
    sp = _space(_sky("SK1", 8.0, 5.0), _sky("SK2", 10.0, 5.0))
    compute_skylight_daylight(sp, 3.0)
    a, b = sp.daylight.toplit
    assert sp.daylight.toplit_m2 < a.area_m2 + b.area_m2 - 1.0
    d = SPREAD_FACTOR * 3.0
    assert math.isclose(sp.daylight.toplit_m2, (2.0 + 1.2 + 2 * d) * (0.9 + 2 * d), abs_tol=1e-3)


def test_skylight_without_position_is_unplaced_not_guessed():
    s = _sky("SK1", 0, 0)
    s.plan_center_m = None
    sp = _space(s, _sky("SK2", 10.0, 5.0))
    compute_skylight_daylight(sp, 3.0)
    assert sp.daylight.unplaced_skylights == ["SK1"]
    assert [z.window_id for z in sp.daylight.toplit] == ["SK2"]


@pytest.mark.parametrize("ch", [None, 0.0])
def test_no_ceiling_height_means_nothing_computed(ch):
    sp = _space(_sky("SK1", 10.0, 5.0))
    compute_skylight_daylight(sp, ch)
    assert sp.daylight.toplit == [] and sp.daylight.toplit_m2 == 0.0
    assert sp.daylight.unplaced_skylights == ["SK1"]


def test_rerun_is_idempotent_and_ignores_windows():
    win = SpaceOpening(id="W1", tag="A", category="window", width_m=1.2, height_m=1.5)
    sp = _space(_sky("SK1", 10.0, 5.0), win)
    compute_skylight_daylight(sp, 3.0)
    first = sp.daylight.toplit_m2
    compute_skylight_daylight(sp, 3.0)
    assert len(sp.daylight.toplit) == 1 and sp.daylight.toplit_m2 == first


def test_provenance_cites_the_rule():
    sp = _space(_sky("SK1", 10.0, 5.0, conf=0.85))
    compute_skylight_daylight(sp, 3.0)
    p = sp.daylight.toplit[0].provenance
    assert p.method == METHOD and p.confidence == 0.85 and SOURCE in p.note and "CH=3 m" in p.note


def test_json_round_trip_keeps_toplit():
    m = BuildingModel(name="t")
    sp = _space(_sky("SK1", 10.0, 5.0))
    compute_skylight_daylight(sp, 3.0)
    m.spaces[sp.id] = sp
    m2 = BuildingModel.from_json(m.to_json())
    dl = m2.spaces[sp.id].daylight
    assert dl.toplit_m2 == sp.daylight.toplit_m2 and dl.toplit[0].zone_class == "under_skylight"
    assert m2.spaces[sp.id].openings[0].plan_center_m == [10.0, 5.0]


def test_ifc_round_trip_computes_daylight_under_skylights(tmp_path, monkeypatch):
    pytest.importorskip("ifcopenshell")
    from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, write_ifc4
    from ifc_import import import_ifc

    west = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    east = [(10.0, 0.0), (20.0, 0.0), (20.0, 10.0), (10.0, 10.0)]
    m = BEMModel(
        building_name="Sky DL",
        spaces=[
            BEMSpace("sp-w", "WEST 101", "101", west, 100.0, 300.0),
            BEMSpace("sp-e", "EAST 102", "102", east, 100.0, 300.0),
        ],
        openings=[BEMOpeningUnit("skylight", "SK-W", 1.2, 0.9, space_sid="sp-w")],
        ring_m=[(0.0, 0.0), (20.0, 0.0), (20.0, 10.0), (0.0, 10.0)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.0,
    )
    monkeypatch.chdir(tmp_path)
    write_ifc4(m, tmp_path / "dl.ifc")
    bm = import_ifc(tmp_path / "dl.ifc")
    by_num = {s.number: s for s in bm.spaces.values()}
    w, e = by_num["101"], by_num["102"]
    assert e.daylight.toplit == [] and e.daylight.toplit_m2 == 0.0
    (z,) = w.daylight.toplit
    sky = next(o for o in w.openings if o.category == "skylight")
    assert sky.plan_center_m is not None
    assert 1.08 < w.daylight.toplit_m2 <= (1.2 + 4.2) * (0.9 + 4.2) + 1e-6
    assert z.head_height_m == pytest.approx(bm.levels[0].wall_height_m)
