"""Validation battery tests: the clean model is green; each injected defect
fires the right check with the right severity.

Conservation law correctness tests (Issue #263):
- floor-area ratio: total floor area matches sum of spaces
- aspect ratio: envelope area matches perimeter-derived area
- volume consistency: total volume matches sum of space volumes
- window-wall-ratio: opening area does not exceed facade area
- infiltration integrity: envelope simplification budget is respected
- zone adjacency: every space references a zone and vice versa
"""

import pytest

from tests.model_factory import (
    break_area,
    break_dangling_zone,
    break_dupe_fixture,
    break_elevation_placement_consistency,
    break_envelope_area_matches_perimeter,
    break_envelope_wall_provenance,
    break_fixture_no_schedule,
    break_fixture_no_schedule_flagged,
    break_gbxml_opening_refs,
    break_gbxml_spaces,
    break_gbxml_wall_areas,
    break_hvac_diffuser_provenance,
    break_hvac_sensor_provenance,
    break_hvac_terminal_unit_provenance,
    break_ifc_counts,
    break_lighting_fixture_provenance,
    break_lpd_absurd,
    break_lpd_unit_slip,
    break_negative_area,
    break_opening_oversize,
    break_opening_provenance,
    break_opening_schedule_join,
    break_provenance,
    break_review_queue_acknowledged,
    break_review_queue_sound,
    break_revision_log_present,
    break_room_number,
    break_sill_head_sanity,
    break_simplify_budget,
    break_simplify_invalid,
    break_space_core_provenance,
    break_space_id_hygiene,
    break_space_volume_matches_area_height,
    break_takeoff_counts_reconcile,
    break_untagged_opening,
    break_volume_conservation,
    break_window_double_link,
    break_zone_empty,
    break_zone_provenance,
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
    assert N_CHECKS == 37


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
        (break_window_double_link, "window_double_link", "error"),
        (break_fixture_no_schedule, "fixture_schedule_join", "error"),
        (break_fixture_no_schedule_flagged, "fixture_schedule_join", "warn"),
        (break_lpd_unit_slip, "lpd_unit_consistency", "error"),
        (break_space_volume_matches_area_height, "space_volume_matches_area_height", "warn"),
        (break_volume_conservation, "volume_conservation", "error"),
        (break_envelope_area_matches_perimeter, "envelope_area_matches_perimeter", "error"),
        (break_simplify_budget, "simplify_budget", "error"),
        (break_simplify_invalid, "simplify_budget", "error"),
        (break_takeoff_counts_reconcile, "takeoff_counts_reconcile", "error"),
        (break_opening_schedule_join, "opening_schedule_join", "error"),
        (break_sill_head_sanity, "sill_head_sanity", "warn"),
        (break_space_id_hygiene, "space_id_hygiene", "error"),
        (break_elevation_placement_consistency, "elevation_placement_consistency", "warn"),
        (break_review_queue_sound, "review_queue_sound", "error"),
        (break_review_queue_acknowledged, "review_queue_acknowledged", "error"),
        (break_revision_log_present, "revision_log_present", "warn"),
        (break_gbxml_spaces, "gbxml_space_areas", "error"),
        (break_gbxml_opening_refs, "gbxml_opening_refs", "error"),
        (break_gbxml_wall_areas, "gbxml_wall_areas", "error"),
        (break_ifc_counts, "ifc_entity_counts", "error"),
        pytest.param(
            break_room_number,
            "area_conservation",
            "error",
            marks=pytest.mark.xfail(
                reason="area_conservation sums ALL spaces' areas on each level "
                "(poly_type is not filtered). break_room_number removes the room "
                "number making poly_type='unassigned', but the space area is still "
                "counted in the level sum. The conservation law never fires for "
                "room-number defects. See issue #201."
            ),
        ),
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


@pytest.mark.parametrize(
    "breaker,entity_desc",
    [
        (break_space_core_provenance, "Space.core_provenance"),
        (break_opening_provenance, "SpaceOpening.provenance"),
        (break_lighting_fixture_provenance, "Lighting fixture.provenance"),
        (break_hvac_diffuser_provenance, "HVAC diffuser.provenance"),
        (break_hvac_sensor_provenance, "HVAC sensor.provenance"),
        (break_hvac_terminal_unit_provenance, "HVAC terminal_unit.provenance"),
        (break_zone_provenance, "Zone.provenance"),
        (break_envelope_wall_provenance, "EnvelopeWall.provenance"),
    ],
)
def test_break_provenance_all_entities(breaker, entity_desc):
    """Each entity type's provenance break fires provenance_complete error.

    Downstream symptom: provenance_complete blocks the export gate.
    """
    m = make_clean_model()
    breaker(m)
    report = run_checks(m)
    result = _by_id(report, "provenance_complete")
    assert result.severity == "error"
    assert not export_gate(report), f"{entity_desc}: error should close the export gate"


def test_warnings_do_not_close_gate():
    m = make_clean_model()
    break_lpd_absurd(m)
    report = run_checks(m)
    assert report.warnings and export_gate(report)


class TestConservationLaws:
    """Correctness tests for each conservation law (Issue #263).

    Each test constructs a model that violates the conservation law,
    calls the validation function, and asserts the correct error code is raised.
    """

    def test_conservation_law_floor_area_ratio(self):
        """floor-area ratio: sum of space areas must match building footprint * floors."""
        m = make_clean_model()
        break_area(m)
        report = run_checks(m)
        result = _by_id(report, "area_conservation")
        assert result.severity == "error"
        assert not export_gate(report)

    def test_conservation_law_aspect_ratio(self):
        """aspect ratio: envelope area must be consistent with perimeter * height."""
        m = make_clean_model()
        break_envelope_area_matches_perimeter(m)
        report = run_checks(m)
        result = _by_id(report, "envelope_area_matches_perimeter")
        assert result.severity == "error"
        assert not export_gate(report)

    def test_conservation_law_volume_consistency(self):
        """volume consistency: sum of space volumes must match building gross volume."""
        m = make_clean_model()
        break_volume_conservation(m)
        report = run_checks(m)
        result = _by_id(report, "volume_conservation")
        assert result.severity == "error"
        assert not export_gate(report)

    def test_conservation_law_window_wall_ratio(self):
        """window-wall-ratio: opening area must not exceed the facade area it occupies."""
        m = make_clean_model()
        break_opening_oversize(m)
        report = run_checks(m)
        result = _by_id(report, "facade_opening_closure")
        assert result.severity == "error"
        assert not export_gate(report)

    def test_conservation_law_infiltration_integrity(self):
        """infiltration integrity: envelope simplification must stay within budget."""
        m = make_clean_model()
        break_simplify_budget(m)
        report = run_checks(m)
        result = _by_id(report, "simplify_budget")
        assert result.severity == "error"
        assert not export_gate(report)

    def test_conservation_law_zone_adjacency(self):
        """zone adjacency: every space must reference a zone, and every zone must be referenced."""
        m = make_clean_model()
        break_dangling_zone(m)
        report = run_checks(m)
        result = _by_id(report, "zone_space_referential")
        assert result.severity == "error"
        assert not export_gate(report)


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


class TestExportGateIntegration:
    """Integration tests: validate-export gate blocks pipeline on error.

    Verifies the hard enforcement point: when any conservation-law check
    returns error severity, the pipeline must not call write_gbxml/write_ifc4
    and must exit with non-zero code.
    """

    def test_pipeline_exits_non_zero_when_validation_fails(self, tmp_path, monkeypatch):
        """Pipeline calls sys.exit(1) before export when validation returns error."""
        import argparse

        import run_pipeline
        from validate import CheckResult, ValidationReport

        out_dir = tmp_path / "run_error"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = argparse.Namespace(
            seed=101,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=out_dir,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
        )

        error_report = ValidationReport(building_name="defective")
        error_report.results.append(
            CheckResult(
                check_id="area_conservation",
                name="Area conservation",
                severity="error",
                message="injected error for test",
                entities=[],
            )
        )

        def mock_run_checks(model, **kwargs):
            return error_report

        monkeypatch.setattr("run_pipeline.run_checks", mock_run_checks)

        exit_called = False
        exit_code = None

        def mock_exit(code=0):
            nonlocal exit_called, exit_code
            exit_called = True
            exit_code = code
            raise SystemExit(code)

        monkeypatch.setattr("sys.exit", mock_exit)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert exit_called, "pipeline should call sys.exit when validation fails"
        assert exit_code == 1, f"expected exit(1), got exit({exit_code})"

        bem_dir = out_dir / "stage_06_bem"
        assert not bem_dir.exists(), "export should not be reached when validation fails"

    def test_export_functions_never_called_when_gate_closed(self, tmp_path, monkeypatch):
        """write_gbxml and write_ifc4 are never called when export_gate blocks."""
        import argparse

        import bem_export
        import run_pipeline
        from validate import CheckResult, ValidationReport

        out_dir = tmp_path / "run_mock_export"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = argparse.Namespace(
            seed=101,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=out_dir,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
        )

        error_report = ValidationReport(building_name="defective")
        error_report.results.append(
            CheckResult(
                check_id="area_conservation",
                name="Area conservation",
                severity="error",
                message="injected error for test",
                entities=[],
            )
        )

        def mock_run_checks(model, **kwargs):
            return error_report

        monkeypatch.setattr("run_pipeline.run_checks", mock_run_checks)

        gbxml_called = False
        ifc_called = False

        original_write_gbxml = bem_export.write_gbxml
        original_write_ifc4 = bem_export.write_ifc4

        def mock_write_gbxml(*args, **kwargs):
            nonlocal gbxml_called
            gbxml_called = True
            return original_write_gbxml(*args, **kwargs)

        def mock_write_ifc4(*args, **kwargs):
            nonlocal ifc_called
            ifc_called = True
            return original_write_ifc4(*args, **kwargs)

        monkeypatch.setattr(bem_export, "write_gbxml", mock_write_gbxml)
        monkeypatch.setattr(bem_export, "write_ifc4", mock_write_ifc4)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert not gbxml_called, "write_gbxml should not be called when export_gate blocks"
        assert not ifc_called, "write_ifc4 should not be called when export_gate blocks"


def test_run_checks_exception_surfaces_to_cli():
    """Regression test for issue #308: run_checks should print exceptions to stderr."""
    import io
    import sys

    import validate
    from tests.model_factory import make_clean_model
    from validate import run_checks

    original_check = validate._check_area_conservation

    def raising_wrapper(model):
        raise ValueError("deliberate test failure")

    validate._check_area_conservation = raising_wrapper

    for i, check in enumerate(validate.BATTERY):
        if check.__name__ == "_check_area_conservation":
            validate.BATTERY[i] = raising_wrapper
            break

    captured_stderr = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured_stderr
    try:
        m = make_clean_model()
        report = run_checks(m)
    finally:
        sys.stderr = old_stderr
        validate._check_area_conservation = original_check
        for i, check in enumerate(validate.BATTERY):
            if check.__name__ == "_check_area_conservation":
                validate.BATTERY[i] = original_check
                break

    stderr_output = captured_stderr.getvalue()
    assert "ERROR in check 'raising_wrapper': ValueError: deliberate test failure" in stderr_output
    error_results = [r for r in report.results if r.severity == "error"]
    assert len(error_results) == 1
    assert "ValueError" in error_results[0].message
    assert "deliberate test failure" in error_results[0].message
