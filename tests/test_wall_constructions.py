"""Roadmap item 6: per-segment wall constructions and per-space U rollup."""

import pytest

from building_model import BuildingModel, Construction
from constructions import apply_wall_u_rollup, wall_u_rollup
from tests.model_factory import add_wall_constructions, make_clean_model
from validate import run_checks


def _check(m, cid="wall_construction_coverage"):
    return next(r for r in run_checks(m).results if r.check_id == cid)


def test_clean_model_without_constructions_skips():
    assert _check(make_clean_model()).severity == "skip"


def test_area_weighted_u_matches_hand_calculation():
    m = make_clean_model()
    add_wall_constructions(m)
    h = m.envelope[0].height_m
    # L1-101: south 5 m + north 5 m + west 6 m, all W-1 at 0.35
    assert m.spaces["L1-101"].wall_u_value_w_m2k == pytest.approx(0.35)
    # L1-102: same shape, all W-2 at 0.60
    assert m.spaces["L1-102"].wall_u_value_w_m2k == pytest.approx(0.60)
    # mix: make L1-102's east wall W-1 -> (0.6*10 + 0.35*6) * h / (16 * h)
    east = next(w for w in m.envelope if w.facade == "east")
    east.construction_id = "W-1"
    apply_wall_u_rollup(m)
    assert m.spaces["L1-102"].wall_u_value_w_m2k == pytest.approx((0.6 * 10 + 0.35 * 6) / 16)
    assert wall_u_rollup(m)["L1-102"].area_m2 == pytest.approx(16 * h)
    assert _check(m).severity == "pass"


def test_segments_without_u_or_area_are_left_out_not_zeroed():
    m = make_clean_model()
    add_wall_constructions(m)
    m.constructions["W-3"] = Construction(id="W-3")  # no U-value
    east = next(w for w in m.envelope if w.facade == "east")
    east.construction_id = "W-3"
    r = apply_wall_u_rollup(m)["L1-102"]
    assert east.id in r.left_out and "no U-value" in r.left_out[east.id]
    assert m.spaces["L1-102"].wall_u_value_w_m2k == pytest.approx(0.60)
    res = _check(m)
    assert res.severity == "warn" and "W-3" in res.message


def test_rollup_clears_stale_value():
    m = make_clean_model()
    add_wall_constructions(m)
    for w in m.envelope:
        if w.space_id == "L1-101":
            w.construction_id = ""
    apply_wall_u_rollup(m)
    assert m.spaces["L1-101"].wall_u_value_w_m2k is None


def test_stored_u_without_any_contributor_is_an_error():
    m = make_clean_model()
    m.constructions["W-1"] = Construction(id="W-1", u_value_w_m2k=0.35)
    m.spaces["L1-101"].wall_u_value_w_m2k = 0.35
    assert _check(m).severity == "error"


def test_unknown_space_on_segment_is_an_error():
    m = make_clean_model()
    add_wall_constructions(m)
    m.envelope[0].space_id = "L1-999"
    assert _check(m).severity == "error"


def test_rollup_not_run_warns():
    m = make_clean_model()
    add_wall_constructions(m)
    m.spaces["L1-101"].wall_u_value_w_m2k = None
    res = _check(m)
    assert res.severity == "warn" and "rollup not run" in res.message


def test_constructions_round_trip_through_json():
    m = make_clean_model()
    add_wall_constructions(m)
    back = BuildingModel.from_json(m.to_json())
    assert isinstance(back.constructions["W-2"], Construction)
    assert back.constructions["W-2"].u_value_w_m2k == pytest.approx(0.60)
    assert {w.construction_id for w in back.envelope} == {"W-1", "W-2"}
    assert back.spaces["L1-102"].wall_u_value_w_m2k == pytest.approx(0.60)
    assert _check(back).severity == "pass"
