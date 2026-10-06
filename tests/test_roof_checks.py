"""Roof validation checks: roof_plan_coverage and roof_solar_aperture (#617)."""

from __future__ import annotations

import copy
import math

import pytest

from roof_geometry import roof_plane
from roof_simplify import apply_roof_simplification
from tests.model_factory import make_clean_model
from validate import N_CHECKS, run_checks
from validate.conservation import _check_roof_plan_coverage, _check_roof_solar_aperture

T30 = math.tan(math.radians(30))


class _Ctx:
    def __init__(self, model):
        self.model = model


def _top(m):
    elev = {lv.id: lv.elevation_z_m for lv in m.levels}
    return max({sp.level_id for sp in m.spaces.values()}, key=lambda lid: (elev.get(lid, 0.0), lid))


def _bbox(m, lid):
    pts = [p for sp in m.spaces.values() if sp.level_id == lid for p in sp.polygon_m]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _gable(m, eave=0.5):
    """South and north planes over the top level's bounding box, with eaves.
    Canonical frame is y-down, so the south half is the larger-y half."""
    lid = _top(m)
    x0, y0, x1, y1 = _bbox(m, lid)
    x0, y0, x1, y1 = x0 - eave, y0 - eave, x1 + eave, y1 + eave
    ym = (y0 + y1) / 2
    run = (y1 - y0) / 2
    zr = 3 + run * T30
    south = roof_plane("RF-S", [(x0, y1, 3), (x1, y1, 3), (x1, ym, zr), (x0, ym, zr)], level_id=lid)
    north = roof_plane("RF-N", [(x0, ym, zr), (x1, ym, zr), (x1, y0, 3), (x0, y0, 3)], level_id=lid)
    return [south, north]


def _hip_with_dormer(lid):
    from tests.test_roof_simplify import _dormer, _hip

    planes = _hip(notch=True) + [_dormer()]
    for p in planes:
        p.level_id = lid
    return planes


def test_battery_counts_both_checks():
    assert N_CHECKS == 46
    ids = {r.check_id for r in run_checks(make_clean_model()).results}
    assert {"roof_plan_coverage", "roof_solar_aperture"} <= ids


def test_both_skip_without_roof_planes():
    m = make_clean_model()
    assert _check_roof_plan_coverage(_Ctx(m)).severity == "skip"
    assert _check_roof_solar_aperture(_Ctx(m)).severity == "skip"


def test_coverage_passes_on_a_full_gable_with_eaves():
    m = make_clean_model()
    m.roof_planes = _gable(m)
    r = _check_roof_plan_coverage(_Ctx(m))
    assert r.severity == "pass", r.message
    assert r.actual["uncovered_m2"] == pytest.approx(0, abs=1e-6)
    assert r.actual["overlap_m2"] == pytest.approx(0, abs=1e-6)


def test_missing_facet_is_an_error_naming_the_spaces_under_it():
    m = make_clean_model()
    m.roof_planes = [p for p in _gable(m) if p.id == "RF-S"]
    r = _check_roof_plan_coverage(_Ctx(m))
    assert r.severity == "error"
    assert "no roof over it" in r.message
    lid = _top(m)
    _, y0, _, y1 = _bbox(m, lid)
    north_side = [
        sid
        for sid, sp in m.spaces.items()
        if sp.level_id == lid and min(p[1] for p in sp.polygon_m) < (y0 + y1) / 2
    ]
    assert r.entities and set(r.entities) <= set(m.spaces)
    assert set(north_side) <= set(r.entities)


def test_doubled_facet_is_an_error_naming_both_planes():
    m = make_clean_model()
    planes = _gable(m)
    twin = copy.deepcopy(planes[0])
    twin.id = "RF-S2"
    m.roof_planes = planes + [twin]
    r = _check_roof_plan_coverage(_Ctx(m))
    assert r.severity == "error"
    assert "roofed twice" in r.message
    assert set(r.entities) == {"RF-S", "RF-S2"}
    assert "RF-S+RF-S2" in r.actual["overlaps"]


def test_roof_planes_on_the_wrong_level_are_an_error():
    m = make_clean_model()
    planes = _gable(m)
    for p in planes:
        p.level_id = "L-nowhere"
    m.roof_planes = planes
    r = _check_roof_plan_coverage(_Ctx(m))
    assert r.severity == "error" and "has no roof planes" in r.message
    assert r.entities == [_top(m)]


def test_aperture_skips_until_the_roof_is_simplified():
    m = make_clean_model()
    m.roof_planes = _gable(m)
    m.site_latitude_deg = 40.0
    r = _check_roof_solar_aperture(_Ctx(m))
    assert r.severity == "skip" and "not simplified" in r.message


def test_aperture_skips_without_latitude():
    m = make_clean_model()
    m.roof_planes = _hip_with_dormer(_top(m))
    m.source_roof_planes = copy.deepcopy(m.roof_planes)
    r = _check_roof_solar_aperture(_Ctx(m))
    assert r.severity == "skip" and "latitude" in r.message


def test_aperture_passes_after_in_budget_simplification():
    m = make_clean_model()
    m.roof_planes = _hip_with_dormer(_top(m))
    m.site_latitude_deg = 40.0
    res = apply_roof_simplification(m)
    assert len(m.source_roof_planes) == 5 and len(m.roof_planes) == 4 == res.n_after
    r = _check_roof_solar_aperture(_Ctx(m))
    assert r.severity == "pass", r.message
    assert r.actual["aperture_delta_pct"] == pytest.approx(res.aperture_delta_pct, abs=1e-4)


def test_aperture_warns_and_names_the_side_that_lost_a_facet():
    m = make_clean_model()
    m.site_latitude_deg = 40.0
    m.source_roof_planes = _hip_with_dormer(_top(m))
    m.roof_planes = [copy.deepcopy(p) for p in m.source_roof_planes if p.id not in ("S", "D")]
    r = _check_roof_solar_aperture(_Ctx(m))
    assert r.severity == "warn"
    assert "S" in r.entities and "N" not in r.entities
    assert r.actual["planes_before"] == 5 and r.actual["planes_after"] == 3


def test_aperture_check_is_warn_only_so_export_is_not_blocked():
    m = make_clean_model()
    m.site_latitude_deg = 40.0
    m.source_roof_planes = _hip_with_dormer(_top(m))
    m.roof_planes = [copy.deepcopy(p) for p in m.source_roof_planes if p.id != "S"]
    rep = run_checks(m)
    r = next(x for x in rep.results if x.check_id == "roof_solar_aperture")
    assert r.severity == "warn"


def test_repeat_simplification_keeps_the_true_source():
    m = make_clean_model()
    m.roof_planes = _hip_with_dormer(_top(m))
    m.site_latitude_deg = 40.0
    apply_roof_simplification(m)
    apply_roof_simplification(m)
    assert len(m.source_roof_planes) == 5


def test_apply_without_latitude_raises():
    m = make_clean_model()
    m.roof_planes = _gable(m)
    with pytest.raises(ValueError):
        apply_roof_simplification(m)
