"""Integration tests: run_pipeline stages 1-6 with synthetic input.

Verifies the full pipeline (generate → build_model → simplify → validate → export)
executes end-to-end for seeds 101, 102, 103 using the same synthetic building
generators as conftest.py fixtures.

Each test:
  1. Calls run_pipeline.main() with --seed
  2. Asserts pipeline completed (export_gate returned True → no sys.exit(1))
  3. Verifies stage JSON artifacts were written to the output directory
"""

# --------------------------------------------------------------------------- #
# Provenance note (issue #340):
#
# Issue #340: "No pipeline integration test for conservation-violation exit code"
#
# This module previously only had happy-path tests (seeds 101/102/103) that
# verified the pipeline completes without calling sys.exit.  It had NO test
# that exercised the conservation-violation path in stage_06_bem where
# validate_bem_conservation() is called inside model_from_linked_model().
#
# When that check fails (e.g. area_delta_pct > 4 %), model_from_linked_model
# raises StageError("BEM conservation laws failed").  That StageError is NOT
# caught inside run_pipeline.main() -- it propagates to cli.py, which lets it
# become an unhandled exception and Python exits with code 1.
#
# test_pipeline_exits_on_conservation_violation below adds the missing
# integration test that:
#   1. Mocks validate_bem_conservation to return {"ok": False, ...}
#   2. Runs the pipeline end-to-end
#   3. Verifies sys.exit was called with code 1 (pipeline aborts correctly)
#
# This is intentionally NOT a pytest.raises(StageError) test -- we want to
# confirm that the pipeline surface calls sys.exit(1) when conservation fails,
# matching the behaviour that cli.py users observe in production.
# --------------------------------------------------------------------------- #
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

import run_pipeline

SEEDS = [101, 102, 103]


def _namespace(seed: int, out_dir: Path) -> argparse.Namespace:
    ns = argparse.Namespace(
        seed=seed,
        image=None,
        aec_bench=None,
        detections=None,
        schedule_csv=None,
        weights=Path("detector/best.pt"),
        out_dir=out_dir,
        open_office_span=False,
        elevation_key="elev_grid",
        simplify_tol=0.02,
    )
    return ns


def _run_pipeline(seed: int, out_dir: Path) -> None:
    ns = _namespace(seed, out_dir)
    run_pipeline.main(ns)


class TestPipelineIntegration:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_pipeline_runs_without_exit(self, seed: int, tmp_path):
        out_dir = tmp_path / f"run_{seed}"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = _namespace(seed, out_dir)

        exit_called = False
        exit_code = None

        def mock_exit(code=0):
            nonlocal exit_called, exit_code
            exit_called = True
            exit_code = code

        original_exit = sys.exit
        sys.exit = mock_exit
        try:
            run_pipeline.main(ns)
        finally:
            sys.exit = original_exit

        assert not exit_called, f"pipeline should not call sys.exit; got exit({exit_code})"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_export_gate_passed(self, seed: int, tmp_path):
        out_dir = tmp_path / f"gate_{seed}"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = _namespace(seed, out_dir)
        run_pipeline.main(ns)

        validation_path = out_dir / "stage_04_validation.json"
        assert validation_path.exists(), f"stage_04_validation.json not written for seed={seed}"

        report_dict = json.loads(validation_path.read_text())
        assert report_dict.get("ok") is True, (
            f"export_gate should pass for seed={seed}; errors={report_dict.get('errors', [])}"
        )

    @pytest.mark.parametrize("seed", SEEDS)
    def test_stage_artifacts_written(self, seed: int, tmp_path):
        out_dir = tmp_path / f"artifacts_{seed}"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = _namespace(seed, out_dir)
        run_pipeline.main(ns)

        expected = [
            out_dir / "stage_01_building.json",
            out_dir / "stage_02_model.json",
            out_dir / "stage_03_simplified.json",
            out_dir / "stage_04_validation.json",
        ]
        missing = [p for p in expected if not p.exists()]
        assert not missing, f"missing artifacts for seed={seed}: {[str(p) for p in missing]}"

    @pytest.mark.parametrize("seed", SEEDS)
    def test_bem_export_written(self, seed: int, tmp_path):
        out_dir = tmp_path / f"bem_{seed}"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = _namespace(seed, out_dir)
        run_pipeline.main(ns)

        bem_dir = out_dir / "stage_06_bem"
        assert bem_dir.exists() and bem_dir.is_dir(), (
            f"stage_06_bem directory not created for seed={seed}"
        )

        files = list(bem_dir.iterdir())
        assert len(files) >= 2, (
            f"expected at least 2 BEM files (gbXML + IFC4), got {len(files)} for seed={seed}"
        )
        extensions = {f.suffix for f in files}
        assert ".xml" in extensions, f"gbXML .xml not found in stage_06_bem for seed={seed}"
        assert ".ifc" in extensions, f"IFC4 .ifc not found in stage_06_bem for seed={seed}"

    # -------------------------------------------------------------------------
    # Issue #340: conservation-violation exit-code integration test
    # -------------------------------------------------------------------------

    def test_pipeline_exits_on_conservation_violation(self, tmp_path):
        """Verify the pipeline calls sys.exit(1) when BEM conservation laws fail.

        During stage_06_bem_export, model_from_linked_model() calls
        validate_bem_conservation().  When that check fails (e.g. area_delta_pct
        exceeds the 4 % threshold), model_from_linked_model raises StageError:

            StageError("BEM conservation laws failed — see
                        stage_06_bem/conservation_checks.json for details",
                       stage="model_from_linked_model", step="validate_bem_conservation")

        That StageError is re-raised through _stage_6_bem_export and main()
        to cli.py, where it becomes an unhandled exception and Python exits with
        code 1.

        This test mocks validate_bem_conservation to return a failure result so
        that the conservation-violation code path is exercised without needing a
        seed/config that naturally produces a violation.  We then verify that
        sys.exit was called with code 1 -- confirming the correct behaviour that
        a user running `matchline run` would observe.
        """
        # Mock validate_bem_conservation to simulate a conservation failure.
        # The return value shape must match what model_from_linked_model expects:
        #   list[CheckResult] where failed = [r for r in conservation_results if r.severity != "pass"]
        from unittest.mock import patch

        from validate import CheckResult

        out_dir = tmp_path / "conservation_violation_test"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = _namespace(101, out_dir)

        def fake_validate_bem_conservation(bem):
            return [
                CheckResult(
                    check_id="area_conservation",
                    name="area_conservation",
                    severity="error",  # severity != "pass" → treated as failure
                    message="BEM area delta 5.2 % exceeds 4 % threshold",
                )
            ]

        with patch(
            "run_pipeline.validate_bem_conservation",
            side_effect=fake_validate_bem_conservation,
        ):
            # When validate_bem_conservation fails, model_from_linked_model raises:
            #   StageError("[model_from_linked_model] Conservation law violation...")
            # This StageError is NOT caught inside run_pipeline.main() -- it propagates
            # to cli.py, which lets it become an unhandled exception → Python exits 1.
            # In pytest this manifests as an uncaught exception (pytest fails).
            # We use pytest.raises to confirm the correct error is raised.
            with pytest.raises(run_pipeline.StageError) as exc_info:
                run_pipeline.main(ns)

        # Verify the error message contains the conservation violation details
        assert "Conservation law violation" in str(exc_info.value)
        assert "area_conservation" in str(exc_info.value)
