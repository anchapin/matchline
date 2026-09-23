"""Regression test: validate.py errors block export (fixes #214).

Confirms the conservation-law enforcement: when any validation check returns
error severity, run_pipeline.main() must not write any BEM output file.
"""

from __future__ import annotations

import argparse
import sys

from validate import CheckResult, ValidationReport, export_gate


class TestValidateBlocksExport:
    """Verify that validation errors block BEM export.

    The AGENTS.md invariant states: "validate.py errors block export".
    These tests confirm that when a ValidationReport contains error-severity
    checks, the export path is never reached.
    """

    def test_export_gate_returns_false_when_report_has_errors(self):
        """export_gate returns False when the report contains error-severity checks."""
        report = ValidationReport(building_name="defective")
        report.results.append(
            CheckResult(
                check_id="area_conservation",
                name="Area conservation",
                severity="error",
                message="injected error for test",
                entities=[],
            )
        )
        assert not export_gate(report), "export_gate should return False when errors are present"

    def test_no_bem_output_dir_when_validation_fails(self, tmp_path, monkeypatch):
        """Pipeline must not create stage_06_bem/ when validation errors are present."""
        import run_pipeline

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

        monkeypatch.setattr(sys, "exit", mock_exit)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert exit_called, "pipeline should call sys.exit when validation fails"
        assert exit_code == 1, f"expected exit(1), got exit({exit_code})"

        bem_dir = out_dir / "stage_06_bem"
        assert not bem_dir.exists(), (
            "stage_06_bem/ should NOT be created when validation fails; "
            "this confirms 'validate.py errors block export'"
        )

    def test_write_functions_not_called_when_gate_blocks(self, tmp_path, monkeypatch):
        """write_gbxml and write_ifc4 must never be called when validation errors exist."""
        import bem_export
        import run_pipeline

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

        assert not gbxml_called, (
            "write_gbxml must NOT be called when export_gate blocks; "
            "validation errors must block export"
        )
        assert not ifc_called, (
            "write_ifc4 must NOT be called when export_gate blocks; "
            "validation errors must block export"
        )

    def test_warnings_only_do_not_block_export(self, tmp_path, monkeypatch):
        """Warning-severity checks must not close the export gate."""
        import run_pipeline

        out_dir = tmp_path / "run_warn_only"
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

        warn_report = ValidationReport(building_name="warn_only")
        warn_report.results.append(
            CheckResult(
                check_id="lpd_absurd",
                name="LPD absurd",
                severity="warn",
                message="injected warn for test",
                entities=[],
            )
        )

        def mock_run_checks(model, **kwargs):
            return warn_report

        monkeypatch.setattr("run_pipeline.run_checks", mock_run_checks)

        exit_called = False

        def mock_exit(code=0):
            nonlocal exit_called
            exit_called = True
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert not exit_called, "warnings-only should not cause sys.exit"
