"""Integration test: conservation-violation in model_from_linked_model() raises StageError and skips stage_04_validation.json."""

import argparse
from pathlib import Path

import pytest

import run_pipeline


def test_conservation_violation_in_model_from_linked_model_raises_stage_error(tmp_path, monkeypatch):
    """Conservation failure inside model_from_linked_model() raises StageError before run_checks() is called.

    When conservation check fails inside model_from_linked_model(), StageError is raised before
    run_checks() is called, so stage_04_validation.json is never written.

    This test verifies that a conservation violation in the BEM model transformation
    (inside model_from_linked_model) raises StageError and stage_04_validation.json is absent.
    """
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

    with pytest.raises(run_pipeline.StageError) as exc_info:
        run_pipeline.main(ns)

    assert "Conservation law violation" in str(exc_info.value)
    stage_04 = out_dir / "stage_04_validation.json"
    assert not stage_04.exists(), (
        f"stage_04_validation.json should be absent when conservation fails in model_from_linked_model, "
        f"but found at {stage_04}"
    )
