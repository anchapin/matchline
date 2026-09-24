"""Integration test: conservation-violation in model_from_linked_model() causes pipeline to exit 1.

When validate_bem_conservation raises StageError, run_checks() catches it and adds it
as an error result to the ValidationReport. The pipeline then exits with code 1 via
export_gate returning False.

This test verifies that a conservation violation in the BEM model transformation
causes the pipeline to exit with code 1 and that stage_04_validation.json is present
with the error recorded.
"""

import argparse

import pytest

import run_pipeline


def test_conservation_violation_exit_code(tmp_path, monkeypatch):
    """Conservation failure in validate_bem_conservation causes pipeline to exit 1."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def broken_validate_bem_conservation(bem, *, tol_area=0.03, tol_volume=0.03):
        from validate import CheckResult

        return [
            CheckResult(
                check_id="validate_bem_conservation",
                name="validate_bem_conservation",
                severity="error",
                message="Conservation law violation: volume_conservation",
            )
        ]

    monkeypatch.setattr("run_pipeline.validate_bem_conservation", broken_validate_bem_conservation)

    ns = argparse.Namespace(
        seed=101,
        out_dir=str(out_dir),
        validate=True,
        min_review_confidence=0.5,
        aec_bench=None,
        ifc=None,
        image=None,
        detections=None,
        schedule_csv=None,
        elevation_key="elev_grid",
        simplify_tol=0.02,
        open_office_span=False,
        export_gate=True,
    )

    with pytest.raises(SystemExit) as exc_info:
        run_pipeline.main(ns)

    assert exc_info.value.code == 1, "Pipeline should exit with code 1 on conservation violation"

    stage_04 = out_dir / "stage_04_validation.json"
    assert stage_04.exists(), (
        f"stage_04_validation.json should exist (written in Stage 4, "
        f"before conservation check in Stage 6), but not found at {stage_04}"
    )
