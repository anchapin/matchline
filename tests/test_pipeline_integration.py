"""Integration tests: run_pipeline stages 1-6 with synthetic input.

Verifies the full pipeline (generate → build_model → simplify → validate → export)
executes end-to-end for seeds 101, 102, 103 using the same synthetic building
generators as conftest.py fixtures.

Each test:
  1. Calls run_pipeline.main() with --seed
  2. Asserts pipeline completed (export_gate returned True → no sys.exit(1))
  3. Verifies stage JSON artifacts were written to the output directory
"""

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
