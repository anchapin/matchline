"""CLI entry-point tests: smoke, argument parsing, and error exit codes."""

from __future__ import annotations

import json
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
            "demo",
            "bem-export",
            "elevation-windows",
            "facade-takeoff",
            "room-labels",
            "multidiscipline",
            "mnist",
            "symbols",
            "ifc-import",
            "ifc-export",
            "ingest",
            "scale",
            "export-package",
            "index",
            "walls",
            "schedules",
            "feedback",
            "review",
            "viewer",
            "viewer-apply",
            "diff",
            "takeoff-diff",
            "learn-legend",
            "legend-eval",
        ],
    )
    def test_subcommand_help(self, sub):
        r = _run([sub, "--help"])
        assert r.returncode == 0, r.stderr
        assert sub in r.stdout or "--" in r.stdout


class TestArgumentParsing:
    def test_run_requires_one_input(self):
        r = _run(["run"])
        assert r.returncode == 1
        assert "exactly one of --seed, --aec-bench, --set or --ifc is required" in r.stderr

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

    def test_confidence_threshold_rejects_value_above_1(self):
        r = _run(["run", "--seed", "101", "--confidence-threshold", "1.5"])
        assert r.returncode == 2
        assert "out of range" in r.stderr or "range" in r.stderr.lower()

    def test_confidence_threshold_rejects_value_below_0(self):
        r = _run(["run", "--seed", "101", "--confidence-threshold", "-0.5"])
        assert r.returncode == 2
        assert "out of range" in r.stderr or "range" in r.stderr.lower()

    def test_confidence_threshold_accepts_valid_edge_values(self):
        # Test boundary values 0.0 and 1.0 are accepted
        r = _run(["run", "--seed", "101", "--confidence-threshold", "0.0"])
        # Should not fail due to range (may fail for other reasons like missing deps)
        assert "out of range" not in r.stderr.lower()
        r = _run(["run", "--seed", "101", "--confidence-threshold", "1.0"])
        assert "out of range" not in r.stderr.lower()


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

    def test_review_list_flag_shows_pending_items(self, tmp_path):
        from building_model import BuildingModel, Provenance, ReviewItem

        model = BuildingModel()
        model.review_queue.extend(
            [
                ReviewItem(
                    id="rq-001",
                    kind="window_room_link",
                    description="Window W1 in room 101",
                    confidence=0.75,
                    provenance=Provenance(
                        sheet_id="E101", revision=1, method="geometric_fallback", confidence=0.75
                    ),
                    status="open",
                ),
                ReviewItem(
                    id="rq-002",
                    kind="fixture_assignment",
                    description="Diffuser D1 in zone Z1",
                    confidence=0.72,
                    provenance=Provenance(
                        sheet_id="M101", revision=2, method="ocr_inference", confidence=0.72
                    ),
                    status="open",
                ),
            ]
        )
        model_path = tmp_path / "model.json"
        model_path.write_text(model.to_json())

        r = _run(["review", str(model_path), "--list"])
        assert r.returncode == 0
        assert "rq-001" in r.stdout
        assert "rq-002" in r.stdout
        assert "window_room_link" in r.stdout
        assert "fixture_assignment" in r.stdout

    def test_review_list_flag_with_json_format(self, tmp_path):
        import json

        from building_model import BuildingModel, Provenance, ReviewItem

        model = BuildingModel()
        model.review_queue.append(
            ReviewItem(
                id="rq-json-001",
                kind="space_no_geometry",
                description="Space S1 has no geometry",
                confidence=0.65,
                provenance=Provenance(
                    sheet_id="A101", revision=1, method=" cadastral_import", confidence=0.65
                ),
                status="open",
            )
        )
        model_path = tmp_path / "model.json"
        model_path.write_text(model.to_json())

        r = _run(["review", str(model_path), "--list", "--format", "json"])
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert len(data) == 1
        assert data[0]["id"] == "rq-json-001"
        assert data[0]["kind"] == "space_no_geometry"
        assert data[0]["confidence"] == 0.65

    def test_review_list_flag_empty_queue(self, tmp_path):
        from building_model import BuildingModel

        model = BuildingModel()
        model_path = tmp_path / "model.json"
        model_path.write_text(model.to_json())

        r = _run(["review", str(model_path), "--list"])
        assert r.returncode == 0
        assert "No pending review items" in r.stdout

    def test_review_list_flag_show_all_includes_confirmed(self, tmp_path):
        from building_model import BuildingModel, Provenance, ReviewItem

        model = BuildingModel()
        model.review_queue.extend(
            [
                ReviewItem(
                    id="rq-open",
                    kind="window_room_link",
                    description="Open item",
                    confidence=0.75,
                    provenance=Provenance(
                        sheet_id="E101", revision=1, method="test", confidence=0.75
                    ),
                    status="open",
                ),
                ReviewItem(
                    id="rq-confirmed",
                    kind="fixture_assignment",
                    description="Confirmed item",
                    confidence=0.82,
                    provenance=Provenance(
                        sheet_id="M101", revision=1, method="test", confidence=0.82
                    ),
                    status="confirmed",
                ),
            ]
        )
        model_path = tmp_path / "model.json"
        model_path.write_text(model.to_json())

        r = _run(["review", str(model_path), "--list", "--show-all"])
        assert r.returncode == 0
        assert "rq-open" in r.stdout
        assert "rq-confirmed" in r.stdout

    def test_review_list_flag_defaults_to_open_only(self, tmp_path):
        from building_model import BuildingModel, Provenance, ReviewItem

        model = BuildingModel()
        model.review_queue.extend(
            [
                ReviewItem(
                    id="rq-open",
                    kind="window_room_link",
                    description="Open item",
                    confidence=0.75,
                    provenance=Provenance(
                        sheet_id="E101", revision=1, method="test", confidence=0.75
                    ),
                    status="open",
                ),
                ReviewItem(
                    id="rq-confirmed",
                    kind="fixture_assignment",
                    description="Confirmed item",
                    confidence=0.82,
                    provenance=Provenance(
                        sheet_id="M101", revision=1, method="test", confidence=0.82
                    ),
                    status="confirmed",
                ),
            ]
        )
        model_path = tmp_path / "model.json"
        model_path.write_text(model.to_json())

        r = _run(["review", str(model_path), "--list"])
        assert r.returncode == 0
        assert "rq-open" in r.stdout
        assert "rq-confirmed" not in r.stdout


# --- config wall_height: one height or per level (#821) ---------------------


@pytest.mark.parametrize(
    "v,ok",
    [(None, True), (3.5, True), (4, True), ({"L1": 4.5, "L2": 3.6}, True), (0, False),
     (-1.0, False), ("tall", False), (True, False), ({}, False), ({"L1": "x"}, False)],
)  # fmt: skip
def test_check_wall_height(v, ok):
    from cli import _check_wall_height

    assert (_check_wall_height(v) == "") is ok


def test_set_storey_height_flag_then_config():
    from types import SimpleNamespace

    from run_pipeline import _set_storey_height

    assert _set_storey_height(SimpleNamespace(storey_height=3.3), {"wall_height": 4.0}) == 3.3
    assert _set_storey_height(SimpleNamespace(storey_height=None), {"wall_height": {"L2": 4}}) == {
        "L2": 4
    }
    assert _set_storey_height(SimpleNamespace(), None) is None


class TestContractDispatch:
    """Per-subcommand contract: happy path plus one degenerate input (#1123).

    Covers the dispatches that had no CLI-level tests at all: export-package,
    diff, viewer-apply, feedback, schedules. A degenerate input must exit
    nonzero with a clean error message, never a traceback.
    """

    def test_export_package_missing_dir(self, tmp_path):
        r = _run(["export-package", str(tmp_path / "nope")])
        assert r.returncode == 1
        assert "ERROR:" in r.stderr
        assert "Traceback" not in r.stderr

    def test_export_package_minimal_package(self, tmp_path):
        import hashlib

        pkg = tmp_path / "run" / "package"
        pkg.mkdir(parents=True)
        payload = b'{"hello": "world"}'
        (pkg / "model.json").write_bytes(payload)
        (pkg / "manifest.json").write_text(
            json.dumps({"files": {"model.json": hashlib.sha256(payload).hexdigest()}})
        )
        r = _run(["export-package", str(tmp_path / "run")])
        assert r.returncode == 0, r.stderr
        assert "zip:" in r.stdout
        assert (tmp_path / "run" / "package.zip").exists()

    def test_diff_reports_changes(self, tmp_path):
        from tests.model_factory import break_area, make_clean_model

        (tmp_path / "old.json").write_text(make_clean_model().to_json())
        new = make_clean_model()
        break_area(new)
        (tmp_path / "new.json").write_text(new.to_json())
        r = _run(["diff", "--old", str(tmp_path / "old.json"), "--new", str(tmp_path / "new.json")])
        assert r.returncode == 0, r.stderr
        assert isinstance(json.loads(r.stdout), dict)

    def test_diff_missing_file_is_clean(self, tmp_path):
        r = _run(
            ["diff", "--old", str(tmp_path / "gone.json"), "--new", str(tmp_path / "also.json")]
        )
        assert r.returncode == 1
        assert "Traceback" not in r.stderr
        assert "Error:" in r.stderr

    def test_viewer_apply_missing_model_is_clean(self, tmp_path):
        ops = tmp_path / "ops.json"
        ops.write_text("{}")
        r = _run(["viewer-apply", str(tmp_path / "nope.json"), str(ops)])
        assert r.returncode == 1
        assert "Traceback" not in r.stderr
        assert "Error:" in r.stderr

    def test_viewer_apply_rename_round_trip(self, tmp_path):
        from tests.test_viewer_corrections import _model, _ops

        model_path = tmp_path / "model.json"
        model_path.write_text(_model().to_json())
        ops_path = tmp_path / "ops.json"
        ops_path.write_text(
            json.dumps(
                _ops({"op": "rename_space", "id": "L1-101", "field": "name", "value": "CONFERENCE"})
            )
        )
        out_path = tmp_path / "out.json"
        r = _run(["viewer-apply", str(model_path), str(ops_path), "--out", str(out_path)])
        assert r.returncode == 0, r.stderr
        applied = json.loads(out_path.read_text())
        assert applied["model"]["spaces"]["L1-101"]["name"] == "CONFERENCE"

    def test_feedback_record_then_summarize(self, tmp_path):
        log = tmp_path / "fb.jsonl"
        r = _run(["feedback", "--text", "contract test", "--topic", "cli", "--log", str(log)])
        assert r.returncode == 0, r.stderr
        assert "Feedback recorded" in r.stdout
        assert log.exists()
        r = _run(["feedback", "--summary", "--log", str(log)])
        assert r.returncode == 0, r.stderr
        assert isinstance(json.loads(r.stdout), dict)

    def test_feedback_requires_text_or_summary(self, tmp_path):
        r = _run(["feedback", "--log", str(tmp_path / "fb.jsonl")])
        assert r.returncode == 2
        assert "Traceback" not in r.stderr
        assert "Error:" in r.stderr

    def test_schedules_missing_dir_is_clean(self, tmp_path):
        r = _run(["schedules", str(tmp_path / "nosheets")])
        assert r.returncode == 1
        assert "Traceback" not in r.stderr
        assert "Error:" in r.stderr
