"""Integration test: conservation-violation in model_from_linked_model() causes pipeline to exit 1.

When validate_bem_conservation raises StageError, run_checks() catches it and adds it
as an error result to the ValidationReport. The pipeline then exits with code 1 via
export_gate returning False.

This test verifies that a conservation violation in the BEM model transformation
causes the pipeline to exit with code 1 and that stage_04_validation.json is present
with the error recorded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

import run_pipeline


def test_conservation_violation_exit_code(tmp_path, monkeypatch):
    """Conservation failure in validate_bem_conservation causes pipeline to exit 1."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def broken_validate_bem_conservation(bem, *, tol_area=0.03, tol_volume=0.03):
        raise run_pipeline.StageError(
            stage_name="model_from_linked_model",
            stage_index=3,
            msg="Conservation law violation: volume_conservation",
            hint="Check geometry.",
        )

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
        f"stage_04_validation.json should be present when conservation check "
        f"fails, but not found at {stage_04}"
    )

    report = json.loads(stage_04.read_text())
    errors = [r for r in report.get("results", []) if r.get("severity") == "error"]
    assert any("Conservation law violation" in r.get("msg", "") for r in errors), (
        f"Expected conservation-violation error in report, got: {errors}"
    )
