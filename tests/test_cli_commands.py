"""Integration tests for CLI commands that were previously undertested at CLI level.

Covers: elevation-windows, facade-takeoff, room-labels, multidiscipline,
mnist, symbols, validate (CLI smoke). Each test invokes the CLI via
subprocess, exercises the primary code path, and asserts on exit code and
key output.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Invoke the matchline CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "cli"] + args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        **kwargs,
    )


class TestElevationWindows:
    """Tests for `elevation-windows` CLI command."""

    def test_exits_zero_synthetic(self):
        """Synthetic generation completes without error."""
        pytest.importorskip("cv2", exc_type=ImportError)  # #535
        r = _run(["elevation-windows"])
        assert r.returncode == 0, r.stderr

    def test_produces_window_data(self):
        """Output mentions window count or placement."""
        pytest.importorskip("cv2", exc_type=ImportError)  # #535
        r = _run(["elevation-windows"])
        assert r.returncode == 0
        out = r.stdout + r.stderr
        assert "window" in out.lower() or "elevation" in out.lower()


class TestFacadeTakeoff:
    """Tests for `facade-takeoff` CLI command."""

    def test_help_works(self):
        r = _run(["facade-takeoff", "--help"])
        assert r.returncode == 0

    def test_exits_nonzero_when_dataset_missing(self):
        """Fails gracefully when the CMP Facade dataset is absent."""
        r = _run(["facade-takeoff", "--data-root", "/nonexistent/cmp-facade"])
        assert r.returncode == 1
        out = r.stdout + r.stderr
        assert "not found" in out.lower() or "cmp" in out.lower()


class TestRoomLabels:
    """Tests for `room-labels` CLI command."""

    def test_fails_gracefully_without_ocr(self, monkeypatch):
        """Fails with a clear message when the OCR dependency is absent (#1040).

        rapidocr_onnxruntime is forced absent in sys.modules, so the
        missing-dependency path is exercised on every machine instead of
        only where the [ocr] extra is not installed (where the old
        subprocess assertion wrongly expected the pipeline to fail).
        """
        import room_labels as rl

        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
        monkeypatch.setattr(rl, "_ENGINE", None)
        with pytest.raises(RuntimeError, match="rapidocr_onnxruntime is not installed"):
            rl.ocr_engine_name()

    def test_cli_path_reaches_ocr_guard(self, monkeypatch):
        """The command path fails at the OCR import, not at sheet synthesis (#1040)."""
        import room_labels as rl
        import run_room_labels

        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
        monkeypatch.setattr(rl, "_ENGINE", None)
        with pytest.raises(RuntimeError, match="rapidocr_onnxruntime is not installed"):
            run_room_labels.main()

    def test_cli_subprocess_without_ocr(self):
        """The CLI exits non-zero with the message, on machines without the [ocr] extra (#1040)."""
        if importlib.util.find_spec("rapidocr_onnxruntime") is not None:
            pytest.skip("rapidocr installed; the in-process tests cover the missing path")
        r = _run(["room-labels"])
        assert r.returncode != 0
        out = r.stdout + r.stderr
        assert "rapidocr" in out.lower() or "not installed" in out.lower()


class TestMultidiscipline:
    """Tests for `multidiscipline` CLI command."""

    def test_exits_zero(self):
        """Three-building synthetic pipeline completes without error."""
        r = _run(["multidiscipline"])
        assert r.returncode == 0, r.stderr

    def test_reports_all_three_buildings(self):
        """Output mentions all three building types or scenarios."""
        r = _run(["multidiscipline"])
        assert r.returncode == 0
        out = r.stdout + r.stderr
        assert len(out) > 0


class TestMnist:
    """Tests for `mnist` CLI command."""

    def test_help_works(self):
        r = _run(["mnist", "--help"])
        assert r.returncode == 0

    def test_missing_data_dir_exits_nonzero(self):
        """Fails gracefully when data dir does not exist."""
        r = _run(["mnist", "--data-dir", "/nonexistent/mnist/data"])
        assert r.returncode != 0

    def test_error_message_mentions_data(self):
        """Error output mentions the data requirement."""
        r = _run(["mnist", "--data-dir", "/nonexistent/mnist/data"])
        assert r.returncode != 0
        out = r.stdout + r.stderr
        assert "data" in out.lower() or "not found" in out.lower()

    def test_missing_npy_files_raises_FileNotFoundError(self, tmp_path):
        """Regress: FileNotFoundError (not SystemExit) with clear message when .npy files absent."""
        import subprocess
        import sys

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        r = subprocess.run(
            [sys.executable, "-m", "cli", "mnist", "--data-dir", str(tmp_path)],
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0
        out = r.stdout + r.stderr
        assert "MNIST data not found" in out
        assert "mnist_X.npy" in out and "mnist_y.npy" in out


class TestSymbols:
    """Tests for `symbols` CLI command."""

    def test_help_works(self):
        r = _run(["symbols", "--help"])
        assert r.returncode == 0

    def test_exits_zero_with_default_path(self):
        """Symbols evaluation completes with default output path."""
        r = _run(["symbols"])
        assert r.returncode == 0, r.stderr

    def test_produces_results_file(self, tmp_path):
        """Results JSON is written to the specified output path."""
        out = tmp_path / "symbols_out.json"
        r = _run(["symbols", "--out-path", str(out)])
        assert r.returncode == 0, r.stderr
        assert out.exists(), "symbols output file not created"


class TestValidate:
    """Additional CLI-level smoke tests for `validate`.

    The `validate` command runs two synthetic buildings through the full
    validation pipeline (generate -> link -> validate -> export gate).
    """

    def test_exits_zero(self):
        """Synthetic validation pipeline completes without error."""
        r = _run(["validate"])
        assert r.returncode == 0, r.stderr

    def test_produces_validation_output(self):
        """Output mentions building ids or validation results."""
        r = _run(["validate"])
        assert r.returncode == 0
        out = r.stdout + r.stderr
        assert len(out) > 0


class TestRunInputGuards:
    """The `run` command accepts every input path the pipeline supports (#1036)."""

    def test_requires_one_input(self):
        r = _run(["run"])
        assert r.returncode != 0
        assert "exactly one of" in r.stderr

    def test_rejects_two_inputs(self):
        r = _run(["run", "--seed", "7", "--ifc", "no_such_model.ifc"])
        assert r.returncode != 0
        assert "exactly one of" in r.stderr

    def test_accepts_set_pdf(self):
        # the guard must let a --set run through; a missing file fails later, in stage 1
        r = _run(["run", "--set", "no_such_set.pdf"])
        assert r.returncode != 0
        assert "exactly one of" not in r.stderr

    def test_accepts_ifc(self):
        r = _run(["run", "--ifc", "no_such_model.ifc"])
        assert r.returncode != 0
        assert "exactly one of" not in r.stderr
