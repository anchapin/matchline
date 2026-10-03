"""Roadmap item 5: shading surfaces and the shading_host_reference check."""

from building_model import BuildingModel, ShadingSurface
from tests.model_factory import add_shading, make_clean_model
from validate import run_checks


def _check(m, cid="shading_host_reference"):
    return next(r for r in run_checks(m).results if r.check_id == cid)


def test_no_shading_skips():
    assert _check(make_clean_model()).severity == "skip"


def test_hosted_shading_passes():
    m = make_clean_model()
    add_shading(m)
    res = _check(m)
    assert res.severity == "pass", res.message
    assert "1 fin" in res.message and "1 overhang" in res.message


def test_shading_never_enters_the_envelope_budget():
    clean = run_checks(make_clean_model()).results
    m = make_clean_model()
    add_shading(m)
    shaded = run_checks(m).results
    before = {r.check_id: (r.severity, r.message) for r in clean}
    after = {r.check_id: (r.severity, r.message) for r in shaded}
    for cid in ("envelope_area_matches_perimeter", "envelope_closure", "facade_opening_closure"):
        assert before[cid] == after[cid], cid


def test_opening_on_another_facade_is_not_adjacent():
    m = make_clean_model()
    add_shading(m)
    m.shading[1].host_opening_id = "south-W1"  # fin on the east wall
    res = _check(m)
    assert res.severity == "error" and "SH-FIN-1" in res.entities


def test_missing_opening_is_an_error():
    m = make_clean_model()
    add_shading(m)
    m.shading[0].host_opening_id = "south-W42"
    assert _check(m).severity == "error"


def test_non_positive_depth_is_an_error():
    m = make_clean_model()
    add_shading(m)
    m.shading[0].depth_m = 0.0
    assert _check(m).severity == "error"


def test_past_wall_end_and_implausible_depth_warn():
    m = make_clean_model()
    add_shading(m)
    m.shading[0].width_m = 12.0  # south wall is 10 m
    m.shading[1].depth_m = 7.5
    res = _check(m)
    assert res.severity == "warn"
    assert "past the ends" in res.message and "deeper than 5 m" in res.message


def test_shading_round_trips_through_json():
    m = make_clean_model()
    add_shading(m)
    back = BuildingModel.from_json(m.to_json())
    assert [type(s) for s in back.shading] == [ShadingSurface, ShadingSurface]
    assert back.shading[0].depth_m == 0.6 and back.shading[1].kind == "fin"
    assert _check(back).severity == "pass"
