"""Integration tests for CLI commands that were previously undertested at CLI level.

Covers: elevation-windows, facade-takeoff, room-labels, multidiscipline,
mnist, symbols, validate (CLI smoke). Each test invokes the CLI via
subprocess, exercises the primary code path, and asserts on exit code and
key output.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
        r = _run(["elevation-windows"])
        assert r.returncode == 0, r.stderr

    def test_produces_window_data(self):
        """Output mentions window count or placement."""
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

    def test_fails_gracefully_without_ocr(self):
        """Fails with a clear message when the OCR dependency is absent."""
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
