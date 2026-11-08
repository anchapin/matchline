"""TempFit template-fit baseline script (#1328 defect-fix slice).

Covers the four defects fixed in the slice: portable data paths, a manifest
that matches the template bank on disk, pose_boxes alignment, and the licence
entry for the template bank. No eval run lives here — that is the follow-up
slice.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tempfit_baseline.py"
TEMPLATE_DIR = ROOT / "assets" / "templates" / "floorplancad"

_spec = importlib.util.spec_from_file_location("tempfit_baseline_under_test", SCRIPT)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_no_hardcoded_machine_paths():
    src = SCRIPT.read_text()
    assert "/home/hatch" not in src
    assert "MATCHLINE_DATASETS" in src


def test_datasets_root_honors_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCHLINE_DATASETS", str(tmp_path))
    assert M.datasets_root() == tmp_path
    assert M.default_parquet() == tmp_path / "floorplancad" / "train-00000-of-00001.parquet"
    assert M.default_dataset() == tmp_path / "detector_yolo" / "floorplancad"
    assert M.default_weights() == tmp_path / "yolo11n.pt"


def test_resolve_path_missing_is_clear_error(tmp_path):
    missing = tmp_path / "no-such.parquet"
    with pytest.raises(SystemExit, match="MATCHLINE_DATASETS"):
        M._resolve_path(None, missing, "parquet", "--parquet")
    with pytest.raises(SystemExit, match="--parquet"):
        M._resolve_path(None, missing, "parquet", "--parquet")


def test_manifest_matches_disk():
    manifest = json.loads((TEMPLATE_DIR / "manifest.json").read_text())
    assert manifest["selection_rule"] == M.SELECTION_RULE
    assert len(manifest["templates"]) == 6
    for entry in manifest["templates"]:
        data = (TEMPLATE_DIR / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(data) > 0
        assert entry["size_px"][0] > 0 and entry["size_px"][1] > 0


def test_manifest_ledger_entry_exists():
    manifest = json.loads((TEMPLATE_DIR / "manifest.json").read_text())
    assert "floorplancad_templates" in manifest["ledger_entry"]
    ledger = json.loads((ROOT / "assets" / "license_ledger.json").read_text())
    entry = next(d for d in ledger["datasets"] if d["id"] == "floorplancad_templates")
    assert entry["license"] == "CC BY-NC 4.0"
    assert entry["class"] == "noncommercial"


def test_pose_boxes_identity():
    a_mat = np.eye(2)
    t = np.zeros(2)
    boxes = [(10.0, 20.0, 30.0, 40.0), (0.0, 0.0, 100.0, 100.0)]
    assert M.pose_boxes(boxes, a_mat, t, (100, 100)) == boxes


def test_pose_boxes_rotation_keeps_alignment():
    # 90-degree rotation about the centre of a 100x100 frame: the box follows
    # its symbol exactly, so the AABB is (60, 10, 80, 30).
    th = np.pi / 2
    a_mat = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    centre = np.array([50.0, 50.0])
    t = centre - a_mat @ centre
    got = M.pose_boxes([(10.0, 20.0, 30.0, 40.0)], a_mat, t, (100, 100))
    assert len(got) == 1
    assert got[0] is not None
    assert got[0] == pytest.approx((60.0, 10.0, 80.0, 30.0))


def test_pose_boxes_degenerate_yields_none_in_place():
    a_mat = np.eye(2)
    t = np.zeros(2)
    boxes = [(10.0, 10.0, 20.0, 20.0), (500.0, 500.0, 510.0, 510.0)]
    got = M.pose_boxes(boxes, a_mat, t, (100, 100))
    assert len(got) == 2
    assert got[0] == (10.0, 10.0, 20.0, 20.0)
    assert got[1] is None  # fully outside the frame: slot kept, not dropped


def test_pose_boxes_zero_width_yields_none():
    a_mat = np.eye(2)
    t = np.zeros(2)
    assert M.pose_boxes([(10.0, 10.0, 10.0, 20.0)], a_mat, t, (100, 100)) == [None]
