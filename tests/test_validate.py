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

import re
from pathlib import Path

import pytest

from tests.model_factory import (
    H,
    _ensure_bim_wall,
    break_area,
    break_convention_bias_implausible_thickness,
    break_convention_bias_large,
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
    break_skylight_below_top,
    break_skylight_oversize,
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


_REPO = Path(__file__).resolve().parents[1]
_VALIDATION_DOC = _REPO / "docs" / "validation.md"

# Live orientation docs that state the battery size in prose. Each is asserted
# against N_CHECKS, so the count cannot drift again in silence.
_COUNT_DOCS = (
    "README.md",
    "ARCHITECTURE.md",
    "QUALITY-SCORE.md",
    "docs/README.md",
    "docs/pipeline.md",
)

# Docs that must name the `validate/` package rather than the removed
# `validate.py` file (split into a package by d48781c, #259).
_PACKAGE_DOCS = (
    "README.md",
    "ARCHITECTURE.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "QUALITY-SCORE.md",
    "ROADMAP.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
    "docs/CODE-REVIEW.md",
    "docs/pipeline.md",
    "docs/run_review.md",
    "docs/validation.md",
    "docs/ifc_import.md",
    "docs/geometry_simplify.md",
    "docs/design-docs/core-beliefs.md",
)

# Backticked tokens that appear in a catalog bullet but are not check ids.
# A new one has to be reviewed and added here, not silently tolerated.
_NON_CHECK_TOKENS = frozenset({"area_m2", "volume_m3", "lpd_w_ft2", "lpd_w_m2"})


def _emitted_check_ids() -> set[str]:
    """Check ids the battery actually produces, from a real run.

    Read off `run_checks` output rather than off `BATTERY` so that a check
    listed in the battery but never wired in still registers as drift.
    """
    return {r.check_id for r in run_checks(make_clean_model()).results}


def _documented_check_ids() -> set[str]:
    """Backticked id-like tokens from the catalog bullets in validation.md."""
    text = _VALIDATION_DOC.read_text(encoding="utf-8")
    start = text.index("## The battery")
    end = text.index("## How to add a check", start)
    bullets = "\n".join(line for line in text[start:end].splitlines() if line.startswith("- "))
    return set(re.findall(r"`([a-z][a-z_0-9]+)`", bullets))


def test_battery_size_documented():
    # Reads docs/validation.md. The previous version of this test asserted a
    # bare literal while its name and comment promised a doc check it never
    # performed, so the count in prose could drift freely and it stayed green.
    text = _VALIDATION_DOC.read_text(encoding="utf-8")
    stated = re.search(r"^## The battery \((\d+) checks\)", text, re.M)
    assert stated, "docs/validation.md lost its '## The battery (N checks)' heading"
    assert int(stated.group(1)) == N_CHECKS, (
        f"docs/validation.md says {stated.group(1)} checks, battery has {N_CHECKS}"
    )


def test_every_check_is_documented():
    # Forward: nothing the battery runs may lack a catalog bullet.
    missing = sorted(_emitted_check_ids() - _documented_check_ids())
    assert not missing, f"checks with no bullet in docs/validation.md: {missing}"


def test_no_phantom_checks_documented():
    # Reverse: the catalog may not promise a check that does not exist.
    phantom = sorted(_documented_check_ids() - _emitted_check_ids() - _NON_CHECK_TOKENS)
    assert not phantom, (
        f"docs/validation.md names non-checks {phantom}; delete the bullet, or "
        f"add the token to _NON_CHECK_TOKENS if it is a model field name"
    )


@pytest.mark.parametrize("relpath", _COUNT_DOCS)
def test_check_count_matches_orientation_docs(relpath):
    path = _REPO / relpath
    stated = {
        int(n)
        for n in re.findall(r"(\d+)-check invariant battery", path.read_text(encoding="utf-8"))
    }
    assert stated, f"{relpath} no longer states a check count; confirm the prose still agrees"
    assert stated == {N_CHECKS}, f"{relpath} states {sorted(stated)}, battery has {N_CHECKS}"


def test_no_docs_reference_removed_validate_module():
    # `validate.py` became the `validate/` package in d48781c (#259).
    # `tests/test_validate.py` is a real filename and is not matched: the
    # lookbehind excludes any preceding word character.
    pattern = re.compile(r"(?<![A-Za-z0-9_])validate\.py")
    stale = [
        f"{rel}:{lineno}"
        for rel in _PACKAGE_DOCS
        for lineno, line in enumerate((_REPO / rel).read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not stale, f"stale `validate.py` references, use `validate/`: {stale}"


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
        (break_convention_bias_implausible_thickness, "convention_bias", "warn"),
        (break_convention_bias_large, "convention_bias", "warn"),
        (break_skylight_oversize, "skylight_within_roof", "error"),
        (break_skylight_below_top, "skylight_within_roof", "warn"),
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

    _bat_index = None
    for i, check in enumerate(validate.BATTERY):
        if check.__name__ == "_check_area_conservation":
            validate.BATTERY[i] = raising_wrapper
            _bat_index = i
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
        if _bat_index is not None:
            validate.BATTERY[_bat_index] = original_check

    stderr_output = captured_stderr.getvalue()
    assert "ERROR in check 'raising_wrapper': ValueError: deliberate test failure" in stderr_output
    error_results = [r for r in report.results if r.severity == "error"]
    assert len(error_results) == 1
    assert "ValueError" in error_results[0].message
    assert "deliberate test failure" in error_results[0].message


class TestConfidenceThresholdValidation:
    """Regression tests for issue #480: --confidence-threshold accepts out-of-range floats."""

    def test_run_checks_rejects_min_review_confidence_above_1(self):
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        with pytest.raises(ValueError, match=r"min_review_confidence must be in \[0.0, 1.0\]"):
            run_checks(m, min_review_confidence=1.5)

    def test_run_checks_rejects_min_review_confidence_below_0(self):
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        with pytest.raises(ValueError, match=r"min_review_confidence must be in \[0.0, 1.0\]"):
            run_checks(m, min_review_confidence=-0.1)

    def test_run_checks_accepts_valid_min_review_confidence(self):
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        # Should not raise - valid range [0.0, 1.0]
        report = run_checks(m, min_review_confidence=0.0)
        assert report is not None
        report = run_checks(m, min_review_confidence=0.5)
        assert report is not None
        report = run_checks(m, min_review_confidence=1.0)
        assert report is not None

    def test_export_gate_rejects_min_review_confidence_above_1(self):
        from tests.model_factory import make_clean_model
        from validate import ValidationReport

        m = make_clean_model()
        report = ValidationReport(building_name="test")
        with pytest.raises(ValueError, match=r"min_review_confidence must be in \[0.0, 1.0\]"):
            export_gate(report, model=m, min_review_confidence=1.5)

    def test_export_gate_rejects_min_review_confidence_below_0(self):
        from tests.model_factory import make_clean_model
        from validate import ValidationReport

        m = make_clean_model()
        report = ValidationReport(building_name="test")
        with pytest.raises(ValueError, match=r"min_review_confidence must be in \[0.0, 1.0\]"):
            export_gate(report, model=m, min_review_confidence=-0.5)

    def test_export_gate_accepts_valid_min_review_confidence(self):
        from tests.model_factory import make_clean_model
        from validate import ValidationReport

        m = make_clean_model()
        report = ValidationReport(building_name="test")
        # Should not raise - valid range [0.0, 1.0]
        result = export_gate(report, model=m, min_review_confidence=0.0)
        assert result is not None
        result = export_gate(report, model=m, min_review_confidence=0.5)
        assert result is not None
        result = export_gate(report, model=m, min_review_confidence=1.0)
        assert result is not None


# ---------------------------------------------------------------------------
# convention_bias (roadmap item 1). A measurement, not a conservation law:
# there is no "correct" value to fail against, so it never blocks export. The
# tests below pin that intent, because a future refactor that quietly promotes
# it to an error would start rejecting honest exports.
# ---------------------------------------------------------------------------


def _bias(m):
    return _by_id(run_checks(m), "convention_bias")


def test_convention_bias_skips_without_thickness():
    # The clean model has no BIM walls, so no thickness exists. The check must
    # say the bias is UNMEASURABLE, not report it as zero: a fabricated
    # thickness would produce a fabricated bias.
    r = _bias(make_clean_model())
    assert r.severity == "skip"
    assert "not zero" in r.message


def test_convention_bias_measured_from_material_layers():
    # Analytical path (roadmap item 1): IfcMaterialLayerSet sums to a true
    # thickness, so the source must be named as analytical and preferred over
    # a bare thickness_m on the same wall.
    m = make_clean_model()
    _ensure_bim_wall(
        m,
        thickness_m=0.9,  # decoy: layers must win
        layers=[
            {"material": "brick", "thickness_m": 0.1},
            {"material": "insulation", "thickness_m": 0.05},
            {"material": "gypsum", "thickness_m": 0.015},
        ],
    )
    r = _bias(m)
    assert r.severity == "pass"
    assert "material_layers" in r.message and "analytical" in r.message
    assert r.actual["thickness_m"] == pytest.approx(0.165)


def test_convention_bias_falls_back_to_thickness_m():
    m = make_clean_model()
    _ensure_bim_wall(m, thickness_m=0.15)
    r = _bias(m)
    assert r.actual["thickness_source"].startswith("thickness_m")
    assert r.actual["thickness_m"] == pytest.approx(0.15)


def test_convention_bias_exterior_face_is_always_larger():
    # Directional by construction: spaces tile the interior, so offsetting the
    # footprint outward can only grow the volume. A negative delta would mean
    # the derivation is wrong, not that the building is unusual.
    m = make_clean_model()
    _ensure_bim_wall(m, thickness_m=0.1)
    per_level = _bias(m).actual["levels"]
    assert per_level
    for lid, v in per_level.items():
        assert v["exterior_volume_m3"] > v["interior_volume_m3"], lid
        assert v["delta_pct"] > 0


def test_convention_bias_grows_with_thickness():
    def frac(t):
        m = make_clean_model()
        _ensure_bim_wall(m, thickness_m=t)
        lv = _bias(m).actual["levels"]["L1"]
        return lv["delta_pct"]

    assert frac(0.05) < frac(0.1) < frac(0.3)


def test_convention_bias_never_blocks_export():
    # Both warn paths, and the pass path, must leave the gate open.
    for breaker in (
        break_convention_bias_implausible_thickness,
        break_convention_bias_large,
    ):
        m = make_clean_model()
        breaker(m)
        report = run_checks(m)
        assert _by_id(report, "convention_bias").severity == "warn"
        assert export_gate(report), f"{breaker.__name__} must not close the export gate"


def test_convention_bias_names_the_units_slip():
    # 12 metres is an inch value in a metre field. The message has to blame the
    # number rather than the building, or the reader will chase the geometry.
    m = make_clean_model()
    break_convention_bias_implausible_thickness(m)
    r = _bias(m)
    assert "outside the plausible band" in r.message
    assert "units slip" in r.message
    assert "W-BIAS-1" in r.entities


def test_convention_bias_reports_per_level_and_worst():
    m = make_clean_model()
    _ensure_bim_wall(m, thickness_m=0.2)
    r = _bias(m)
    assert set(r.actual["levels"]["L1"]) == {
        "interior_volume_m3",
        "exterior_volume_m3",
        "delta_m3",
        "delta_pct",
        "perimeter_m",
        "wall_height_m",
    }
    assert "worst level L1" in r.message
    assert r.actual["convention"].startswith("interior_face")


def test_convention_bias_ignores_non_wall_bim_elements():
    from building_model import BimElement

    m = make_clean_model()
    m.bim_elements.append(
        BimElement(global_id="S-1", ifc_class="IfcSlab", level_id="L1", thickness_m=0.3)
    )
    assert _bias(m).severity == "skip"


# ---------------------------------------------------------------------------
# skylight_within_roof (roadmap item 3). The model has no roof entity; the
# check states the flat-roof convention the gbXML exporter already uses (roof
# over a top-level space = its floor area) and holds skylights to it.
# ---------------------------------------------------------------------------


def _sky(m):
    return _by_id(run_checks(m), "skylight_within_roof")


def _skylight(m, sid="L1-101", oid="roof-SK-1", w=1.2, h=1.2, **kw):
    from tests.model_factory import _add_skylight

    _add_skylight(m, sid, oid, w, h)
    o = m.spaces[sid].openings[-1]
    for k, v in kw.items():
        setattr(o, k, v)
    return o


def test_skylight_check_skips_without_skylights():
    r = _sky(make_clean_model())
    assert r.severity == "skip"


def test_skylight_within_flat_roof_passes():
    m = make_clean_model()
    _skylight(m)
    _skylight(m, oid="roof-SK-2")
    r = _sky(m)
    assert r.severity == "pass", r.message
    assert "2 skylight(s)" in r.message and "2.88 m^2" in r.message


def test_skylight_area_sums_per_space_not_per_opening():
    # Two skylights, each smaller than the 30 m^2 roof, together larger.
    m = make_clean_model()
    _skylight(m, w=4.0, h=4.0)
    _skylight(m, oid="roof-SK-2", w=4.0, h=4.0)
    r = _sky(m)
    assert r.severity == "error"
    assert r.entities == ["L1-101"]
    assert r.expected == 30.0 and abs(r.actual - 32.0) < 1e-9


def test_skylight_area_falls_back_to_dims_then_schedule():
    m = make_clean_model()
    _skylight(m, w=8.0, h=5.0, area_m2=None)  # 40 m^2 from own dims
    assert _sky(m).severity == "error"
    m = make_clean_model()
    _skylight(m, area_m2=None, width_m=None, height_m=None)
    m.schedules["SK-1"] = {"width_m": 8.0, "height_m": 5.0}
    assert _sky(m).severity == "error"


def test_skylight_without_dims_is_named_not_zeroed():
    m = make_clean_model()
    _skylight(m, area_m2=None, width_m=None, height_m=None)
    r = _sky(m)
    assert r.severity == "pass"
    assert "1 skylight(s) without dimensions left out" in r.message


def test_skylight_below_top_level_warns_and_names_it():
    m = make_clean_model()
    break_skylight_below_top(m)
    r = _sky(m)
    assert r.severity == "warn"
    assert r.entities == ["roof-SK-LOW"]
    assert "atrium" in r.message


def test_skylight_on_top_level_of_multi_level_model_passes():
    from building_model import Level

    m = make_clean_model()
    m.levels.insert(0, Level(id="L0", name="Basement", elevation_z_m=-H, wall_height_m=H))
    _skylight(m)
    assert _sky(m).severity == "pass"


def test_skylight_does_not_count_against_facade_closure():
    # Roof glazing must not land on a phantom "roof" facade with zero wall
    # area, which would close the export gate on an honest skylight.
    m = make_clean_model()
    _skylight(m, w=5.0, h=5.0)
    r = _by_id(run_checks(m), "facade_opening_closure")
    assert r.severity == "pass", r.message


def test_skylight_round_trips_through_model_json():
    from building_model import BuildingModel

    m = make_clean_model()
    _skylight(m, tilt_deg=0.0, azimuth_deg=180.0)
    back = BuildingModel.from_dict(m.to_dict())
    o = back.spaces["L1-101"].openings[-1]
    assert (o.category, o.host_facade, o.tilt_deg, o.azimuth_deg) == (
        "skylight",
        "roof",
        0.0,
        180.0,
    )
