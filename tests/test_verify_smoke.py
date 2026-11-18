"""Verify-smoke wrapper tests (#1411): generate-if-missing + gated export.

The wrapper's own logic is tested with fake generate/export callables
so the suite stays fast; the real chain (dataset generation + the
#1404/#1405 export gate) is exercised by running the wrapper itself,
as recorded in the #1411 PR.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import run_verify_smoke as vs

ROOT = Path(__file__).parent.parent


def _make_gt(data_out: Path, sheet_id: str = "sheet_007") -> Path:
    gt = vs.gt_path(data_out, sheet_id)
    gt.parent.mkdir(parents=True, exist_ok=True)
    gt.write_text("{}", encoding="utf-8")
    return gt


class TestSheetSeed:
    def test_numeric_suffix_is_the_seed(self):
        assert vs.sheet_seed("sheet_007") == 7
        assert vs.sheet_seed("sheet_101") == 101

    def test_no_numeric_suffix_rejected(self):
        with pytest.raises(ValueError, match="no numeric suffix"):
            vs.sheet_seed("sheet_alpha")


class TestGenerateIfMissing:
    def test_present_dataset_skips_generation(self, tmp_path):
        _make_gt(tmp_path)
        calls: list = []

        def fake_generate(data_out, sheet_id):
            calls.append((data_out, sheet_id))
            return True

        ok = vs.verify_smoke(
            data_out=tmp_path,
            generate=fake_generate,
            export=lambda sheet_id, gt_path, out_dir: True,
        )
        assert ok is True
        assert calls == []

    def test_missing_dataset_generates_then_exports(self, tmp_path):
        seen: dict = {}

        def fake_generate(data_out, sheet_id):
            _make_gt(data_out, sheet_id)
            return True

        def fake_export(sheet_id, gt_path, out_dir):
            seen["gt"] = gt_path
            return True

        ok = vs.verify_smoke(data_out=tmp_path, generate=fake_generate, export=fake_export)
        assert ok is True
        assert seen["gt"] == vs.gt_path(tmp_path, "sheet_007")

    def test_generation_failure_short_circuits_export(self, tmp_path):
        exported: list = []
        ok = vs.verify_smoke(
            data_out=tmp_path,
            generate=lambda data_out, sheet_id: False,
            export=lambda **kw: exported.append(kw) or True,
        )
        assert ok is False
        assert exported == []


class TestExportOutcome:
    def test_failed_export_propagates_false(self, tmp_path):
        _make_gt(tmp_path)
        ok = vs.verify_smoke(
            data_out=tmp_path,
            generate=lambda data_out, sheet_id: True,
            export=lambda sheet_id, gt_path, out_dir: False,
        )
        assert ok is False

    def test_main_bad_sheet_id_fails_cleanly(self, tmp_path, monkeypatch):
        # No GT on disk and no numeric suffix to seed generation from:
        # main() must report failure (exit 1), not raise.
        monkeypatch.setattr(vs, "DATA_OUT", tmp_path)
        assert vs.main(["--sheet-id", "sheet_alpha"]) == 1


class TestGenerateDataset:
    def test_argv_and_success(self, tmp_path):
        argv_seen: dict = {}

        class Result:
            returncode = 0

        def fake_runner(argv, cwd=None):
            argv_seen["argv"] = argv
            _make_gt(tmp_path)
            return Result()

        assert vs.generate_dataset(tmp_path, "sheet_007", runner=fake_runner) is True
        argv = argv_seen["argv"]
        assert argv[1:3] == ["-m", "synth.make_dataset"]
        assert argv[argv.index("--seed") + 1] == "7"
        assert argv[argv.index("--sheets") + 1] == "1"

    def test_nonzero_runner_is_failure(self, tmp_path):
        class Result:
            returncode = 1

        runner = lambda a, cwd=None: Result()  # noqa: E731
        assert vs.generate_dataset(tmp_path, "sheet_007", runner=runner) is False


class TestCli:
    def test_verify_smoke_help(self):
        r = subprocess.run(
            [sys.executable, "-m", "cli", "verify-smoke", "--help"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        assert r.returncode == 0, r.stderr
        assert "--sheet-id" in r.stdout
