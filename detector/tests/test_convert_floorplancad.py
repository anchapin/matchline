"""Regression tests for detector/convert_floorplancad.py (issue #1327).

FloorPlanCAD parquet bboxes are COCO-style ``[x, y, w, h]`` (top-left corner
plus size) in 0-1000 coordinates. The converter used to read them as corner
pairs ``[x0, y0, x1, y1]`` and sort the four numbers, which produced wrong
centres and wrong sizes whenever ``w < x`` (or ``h < y``) -- e.g. the issue's
box ``[789.75, 259.57, 140.0, 88.25]`` became a 650px-wide box centred at
464.9 instead of a 140x88 box at (789.75, 259.57).

The module-level helper ``coco_bbox_to_yolo`` is pure, so the math is tested
without pyarrow; ``main()`` is additionally exercised end-to-end with a fake
``pyarrow.parquet`` module (no parquet files, no network, tmp_path only).

Run with the detector venv (pytest is not a training dependency):

    detector/.venv-det/bin/python -m pytest detector/tests/test_convert_floorplancad.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DETECTOR_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DETECTOR_DIR))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "convert_floorplancad", DETECTOR_DIR / "convert_floorplancad.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfp = _load_module()


# --- the issue's concrete example ------------------------------------------
ISSUE_BBOX = [789.75, 259.57, 140.0, 88.25]


def test_issue_box_is_read_as_coco():
    """[789.75, 259.57, 140.0, 88.25] -> cx=0.85975, cy=0.303695, 140x88 px."""
    assert cfp.coco_bbox_to_yolo(ISSUE_BBOX) == pytest.approx(
        (0.859750, 0.303695, 0.140000, 0.088250), abs=1e-9
    )


def test_issue_box_matches_reported_values():
    """Same box, asserted against the values quoted in issue #1327."""
    cx, cy, w, h = cfp.coco_bbox_to_yolo(ISSUE_BBOX)
    assert cx == pytest.approx(0.859750, abs=1e-6)
    assert cy == pytest.approx(0.303700, abs=1e-4)  # 0.303695 rounded
    assert w == pytest.approx(0.140000, abs=1e-6)
    assert h == pytest.approx(0.088250, abs=1e-6)


def test_box_size_is_not_corner_span():
    """The old corner reading made this box 650px wide; it must be 140px."""
    _, _, w, h = cfp.coco_bbox_to_yolo(ISSUE_BBOX)
    assert (w, h) == pytest.approx((0.140, 0.08825), abs=1e-9)
    assert w != pytest.approx(0.650, abs=1e-3)  # old (x1-x0)=650px width


# --- the case that exposed the old sorting bug -----------------------------
def test_width_smaller_than_x_is_not_sorted():
    """w < x: sorting (x, w) produced a wrong centre and a wrong width."""
    assert cfp.coco_bbox_to_yolo([700.0, 100.0, 100.0, 50.0]) == pytest.approx(
        (0.750, 0.125, 0.100, 0.050), abs=1e-9
    )


def test_height_smaller_than_y_is_not_sorted():
    assert cfp.coco_bbox_to_yolo([100.0, 700.0, 50.0, 100.0]) == pytest.approx(
        (0.125, 0.750, 0.050, 0.100), abs=1e-9
    )


# --- degenerate boxes -------------------------------------------------------
def test_zero_size_box_is_skipped():
    assert cfp.coco_bbox_to_yolo([100.0, 100.0, 0.0, 50.0]) is None


def test_negative_size_box_is_skipped():
    assert cfp.coco_bbox_to_yolo([100.0, 100.0, 50.0, -10.0]) is None


# --- class ids --------------------------------------------------------------
def test_class_mapping_is_door_zero():
    assert cfp.FLOORPLANCAD_MAP["single_door"] == "door"
    assert cfp.CLASS_IDS["door"] == 0
    assert cfp.CLASS_IDS["window"] == 1


# --- end-to-end through main() with a fake parquet --------------------------
class _Value:
    def __init__(self, value):
        self._value = value

    def as_py(self):
        return self._value


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.num_rows = len(rows)

    def column(self, name):
        return [_Value(self.rows[i][name]) for i in range(self.num_rows)]


def _fake_pyarrow(rows, monkeypatch):
    """Install a fake ``pyarrow.parquet`` module returning ``rows``."""
    import types

    parquet = types.ModuleType("pyarrow.parquet")
    parquet.read_table = lambda path, columns=None: _Table(rows)
    pyarrow = types.ModuleType("pyarrow")
    pyarrow.parquet = parquet
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)


def test_main_writes_coco_derived_labels(tmp_path, monkeypatch):
    rows = [
        {
            "image_id": "690a547c0420c654cb79d525",
            "image": {"bytes": b"not-a-real-png", "path": "x.png"},
            "objects": {
                "category": ["single_door", "window", "unknown_thing", "single_door"],
                "bbox": [
                    ISSUE_BBOX,
                    [100.0, 700.0, 50.0, 100.0],
                    [10.0, 10.0, 20.0, 20.0],
                    [500.0, 500.0, 0.0, 30.0],
                ],
            },
        }
    ]
    _fake_pyarrow(rows, monkeypatch)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["convert_floorplancad.py", "--out", str(out)])

    cfp.main()

    label = (out / "labels" / "eval" / "690a547c0420c654cb79d525.txt").read_text()
    lines = [ln for ln in label.splitlines() if ln.strip()]
    # unknown category skipped, zero-size box skipped -> 2 lines
    assert len(lines) == 2
    assert lines[0] == f"0 {0.859750:.6f} {0.303695:.6f} {0.140000:.6f} {0.088250:.6f}"
    assert lines[1] == f"1 {0.125:.6f} {0.750:.6f} {0.050000:.6f} {0.100000:.6f}"
    assert (out / "images" / "eval" / "690a547c0420c654cb79d525.png").exists()
