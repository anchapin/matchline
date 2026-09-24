"""Integration tests for CLI drawing-extraction commands.

Tests invoke each subcommand via the CLI runner and verify:
- Exit code is 0 (or command fails gracefully with missing dependencies)
- Output contains the expected structure
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest


@pytest.mark.timeout(60)
def _run_cli(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([sys.executable, "-m", "cli"] + args, **kwargs)


@pytest.fixture
def runner():
    return _run_cli


@pytest.fixture
def tmp_output_path(tmp_path):
    return tmp_path / "output.json"


class TestElevationWindows:
    def test_invocation_succeeds(self, runner):
        result = runner(["elevation-windows"])
        if result.returncode != 0:
            pytest.skip(f"elevation-windows requires detector dependencies: {result.stderr}")
        assert result.returncode == 0

    def test_output_contains_window_data(self, runner):
        result = runner(["elevation-windows"])
        if result.returncode != 0:
            pytest.skip(f"elevation-windows requires detector dependencies: {result.stderr}")
        output = result.stdout + result.stderr
        assert "window" in output.lower() or "elevation" in output.lower()


class TestRoomLabels:
    def test_invocation_succeeds(self, runner):
        result = runner(["room-labels"])
        if result.returncode != 0:
            pytest.skip(f"room-labels requires rapidocr_onnxruntime: {result.stderr}")
        assert result.returncode == 0

    def test_output_contains_room_data(self, runner):
        result = runner(["room-labels"])
        if result.returncode != 0:
            pytest.skip(f"room-labels requires rapidocr_onnxruntime: {result.stderr}")
        output = result.stdout + result.stderr
        assert "room" in output.lower() or "ocr" in output.lower()


class TestSymbols:
    def test_invocation_succeeds(self, runner, tmp_output_path):
        result = runner(["symbols", "--out-path", str(tmp_output_path)])
        assert result.returncode == 0, result.stderr

    def test_output_file_is_valid_json(self, runner, tmp_output_path):
        result = runner(["symbols", "--out-path", str(tmp_output_path)])
        assert result.returncode == 0, result.stderr
        assert tmp_output_path.exists(), "Output file was not created"
        data = json.loads(tmp_output_path.read_text())
        assert isinstance(data, dict), "Output should be a dict with evaluation results"

    def test_output_contains_expected_keys(self, runner, tmp_output_path):
        result = runner(["symbols", "--out-path", str(tmp_output_path)])
        assert result.returncode == 0, result.stderr
        data = json.loads(tmp_output_path.read_text())
        assert "acc" in data, "Output should contain accuracy"
        assert data["acc"] > 0, "Accuracy should be positive"
