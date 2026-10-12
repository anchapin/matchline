"""Check ``daylight_zones``: zones inside their space, areas add up."""

from __future__ import annotations

import pytest

from tests.model_factory import _add_toplit, make_clean_model
from validate import run_checks


def _res(m):
    return next(r for r in run_checks(m).results if r.check_id == "daylight_zones")


def test_skips_without_zones():
    assert _res(make_clean_model()).severity == "skip"


def test_passes_with_a_computed_toplit_zone():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.5, 3.0)
    r = _res(m)
    assert r.severity == "pass", r.message
    assert f"{m.spaces['L1-101'].daylight.toplit_m2:.2f}" in r.message


def test_overlapping_toplit_zones_pass_because_union_is_recomputed():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.0, 3.0)
    _add_toplit(m, "L1-101", "SK2", 3.0, 3.0)
    dl = m.spaces["L1-101"].daylight
    # compute_skylight_daylight is per call; second call recomputes both
    assert len(dl.toplit) == 2
    assert dl.toplit_m2 < sum(z.area_m2 for z in dl.toplit)
    assert _res(m).severity == "pass"


def test_stored_toplit_total_that_double_counts_is_an_error():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.0, 3.0)
    _add_toplit(m, "L1-101", "SK2", 3.0, 3.0)
    dl = m.spaces["L1-101"].daylight
    dl.toplit_m2 = sum(z.area_m2 for z in dl.toplit)
    r = _res(m)
    assert r.severity == "error" and "union" in r.message


def test_zone_area_disagreeing_with_polygon_is_an_error():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.5, 3.0)
    m.spaces["L1-101"].daylight.toplit[0].area_m2 *= 2
    assert _res(m).severity == "error"


def test_toplit_zone_pointing_at_a_non_skylight_is_an_error():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.5, 3.0)
    m.spaces["L1-101"].daylight.toplit[0].window_id = "W-GHOST"
    r = _res(m)
    assert r.severity == "error" and "W-GHOST" in r.message


def test_toplit_zone_with_sidelight_class_is_an_error():
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.5, 3.0)
    m.spaces["L1-101"].daylight.toplit[0].zone_class = "primary"
    assert _res(m).severity == "error"


@pytest.mark.parametrize("ch", [None, 0.0])
def test_unknown_ceiling_height_warns_and_names_the_skylight(ch):
    m = make_clean_model()
    _add_toplit(m, "L1-101", "SK1", 2.5, 3.0, ch=ch)
    r = _res(m)
    assert r.severity == "warn" and "L1-101:SK1" in r.entities


def test_error_blocks_export_and_warn_does_not():
    from tests.model_factory import break_daylight_unplaced, break_daylight_zone_outside
    from validate import export_gate

    m = make_clean_model()
    break_daylight_zone_outside(m)
    assert not export_gate(run_checks(m))
    m = make_clean_model()
    break_daylight_unplaced(m)
    rep = run_checks(m)
    assert not any(r.severity == "error" for r in rep.results), [
        (r.check_id, r.message) for r in rep.results if r.severity == "error"
    ]
