"""Validation battery tests: the clean model is green; each injected defect
fires the right check with the right severity."""

import pytest

from tests.model_factory import (
    break_area,
    break_dangling_zone,
    break_dupe_fixture,
    break_fixture_no_schedule,
    break_fixture_no_schedule_flagged,
    break_lpd_absurd,
    break_lpd_unit_slip,
    break_negative_area,
    break_opening_oversize,
    break_provenance,
    break_untagged_opening,
    break_zone_empty,
    make_clean_model,
)
from validate import N_CHECKS, export_gate, run_checks


def _by_id(report, check_id):
    return next(r for r in report.results if r.check_id == check_id)


def test_clean_model_fully_green():
    m = make_clean_model()
    report = run_checks(m)
    assert report.ok, [f"{e.check_id}: {e.message}" for e in report.errors]
    assert export_gate(report)
    assert len(report.results) == N_CHECKS
    # the headline conservation checks pass
    assert _by_id(report, "area_conservation").severity == "pass"
    assert _by_id(report, "volume_conservation").severity == "pass"


def test_battery_size_documented():
    # keep docs/validation.md's check count honest; update the doc if this
    # number changes intentionally.
    assert N_CHECKS == 26


@pytest.mark.parametrize(
    "breaker,check_id,severity",
    [
        (break_area, "area_conservation", "error"),
        (break_provenance, "provenance_complete", "error"),
        (break_zone_empty, "zone_nonempty", "error"),
        (break_lpd_absurd, "lpd_bounds", "warn"),
        (break_dangling_zone, "zone_space_referential", "error"),
        (break_dupe_fixture, "assignment_uniqueness", "error"),
        (break_opening_oversize, "facade_opening_closure", "error"),
        (break_negative_area, "no_negative_areas", "error"),
        (break_untagged_opening, "window_tag_coverage", "error"),
        (break_fixture_no_schedule, "fixture_schedule_join", "error"),
        (break_fixture_no_schedule_flagged, "fixture_schedule_join", "warn"),
        (break_lpd_unit_slip, "lpd_unit_consistency", "error"),
    ],
)
def test_defect_fires_expected_check(breaker, check_id, severity):
    m = make_clean_model()
    breaker(m)
    report = run_checks(m)
    got = _by_id(report, check_id)
    assert got.severity == severity, (
        f"{breaker.__name__}: expected {check_id}={severity}, got {got.severity} ({got.message})"
    )
    if severity == "error":
        assert not export_gate(report), f"{breaker.__name__}: error should close the export gate"


def test_warnings_do_not_close_gate():
    m = make_clean_model()
    break_lpd_absurd(m)
    report = run_checks(m)
    assert report.warnings and export_gate(report)


def test_export_gate_blocks_errors_explicitly():
    """Conservation law: export_gate returns False when errors are present.

    This makes the blocking enforcement auditable as a standalone assertion,
    separate from the parametrized defect battery.
    """
    m = make_clean_model()
    break_area(m)
    report = run_checks(m)
    assert not export_gate(report)


def test_report_serializes_to_json():
    m = make_clean_model()
    report = run_checks(m)
    d = report.to_dict()
    assert d["ok"] is True
    assert d["summary"]["n_checks"] == N_CHECKS
    import json

    json.loads(report.to_json())  # round-trips


def test_check_never_crashes_battery():
    """A pathological model (empty) yields errors, not tracebacks."""
    from building_model import BuildingModel

    report = run_checks(BuildingModel(name="empty"))
    assert isinstance(report.to_json(), str)
    assert not report.ok
