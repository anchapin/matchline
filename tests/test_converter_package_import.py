"""The training workflow imports converters as package modules; the tests
suite imports them by path. Guard both modes so `python3 -m detector.convert_synth`
never regresses on the bare `from classes import ...` fallback (#743)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULES = (
    "convert_synth",
    "convert_aec",
    "convert_cubicasa",
    "convert_floorplancad",
    "eval_zero_shot",
)
NAMES = "detector." + ", detector.".join(MODULES)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_converters_import_as_package_modules():
    # the mode detector-train.yml uses: python3 -m detector.convert_synth
    r = _run(f"import {NAMES}")
    assert r.returncode == 0, r.stderr


def test_converters_still_import_by_path_from_detector_dir():
    # the mode tests and manual runs use: sys.path includes detector/
    r = _run("import sys; sys.path.insert(0, 'detector'); import " + ", ".join(MODULES))
    assert r.returncode == 0, r.stderr
