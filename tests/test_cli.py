"""CLI entry-point tests: smoke, argument parsing, and error exit codes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).parent.parent


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([sys.executable, "-m", "cli"] + args, **kwargs)


class TestHelp:
    @pytest.mark.parametrize(
        "sub",
        [
            "run",
            "validate",
            "bem-export",
            "elevation-windows",
            "facade-takeoff",
            "room-labels",
            "multidiscipline",
            "mnist",
            "symbols",
            "ifc-import",
            "ifc-export",
            "review",
        ],
    )
    def test_subcommand_help(self, sub):
        r = _run([sub, "--help"])
        assert r.returncode == 0, r.stderr
        assert sub in r.stdout or "--" in r.stdout


class TestArgumentParsing:
    def test_run_requires_seed_or_aec_bench(self):
        r = _run(["run"])
        assert r.returncode == 1
        assert "exactly one of --seed or --aec-bench is required" in r.stderr

    @pytest.mark.skipif(yaml is None, reason="pyyaml not installed")
    def test_run_rejects_missing_config(self):
        r = _run(["run", "--seed", "101", "--config", "/nonexistent/config.yaml"])
        assert r.returncode == 1
        assert "config file not found" in r.stderr

    def test_run_accepts_valid_seed(self):
        r = _run(["run", "--seed", "101", "--help"])
        assert r.returncode == 0
        assert "--seed" in r.stderr or "--help" in r.stdout

    def test_bem_export_accepts_sheet_id(self):
        r = _run(["bem-export", "--sheet-id", "sheet_999", "--help"])
        assert r.returncode == 0

    def test_ifc_import_requires_path(self):
        r = _run(["ifc-import"])
        assert r.returncode == 2  # argparse error

    def test_ifc_export_requires_model_and_out(self):
        r = _run(["ifc-export"])
        assert r.returncode == 2

    def test_review_requires_model(self):
        r = _run(["review"])
        assert r.returncode == 2


class TestValidateExitCode:
    def test_validate_exits_zero_on_clean_run(self, tmp_path, monkeypatch):
        r = _run(["validate"])
        assert r.returncode == 0, r.stderr

    def test_run_pipeline_exits_nonzero_on_validation_failure(self, tmp_path, monkeypatch):
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
            weights=Path("detector/best.pt"),
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

        exit_code = None

        def mock_exit(code=0):
            nonlocal exit_code
            exit_code = code
            raise SystemExit(code)

        monkeypatch.setattr("sys.exit", mock_exit)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert exit_code == 1, f"expected exit(1), got exit({exit_code})"


class TestSmokeSubcommands:
    def test_run_help_works(self):
        r = _run(["run", "--help"])
        assert r.returncode == 0

    def test_validate_help_works(self):
        r = _run(["validate", "--help"])
        assert r.returncode == 0

    def test_review_help_works(self):
        r = _run(["review", "--help"])
        assert r.returncode == 0
