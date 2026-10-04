"""Matching logic of scripts/eval_door_swings.py (no dataset needed)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "eval_door_swings", Path(__file__).resolve().parents[1] / "scripts" / "eval_door_swings.py"
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def _d(x, y, s=1.0):
    return {"x_px": x, "y_px": y, "score": s}


def test_each_box_matches_once_and_edges_count():
    boxes = [(100, 100, 200, 200), (400, 100, 500, 200)]
    # centre on the box edge (opening on the wall line), one duplicate, one stray
    tp, fp, fn = ev.match([_d(100, 150), _d(105, 150, 0.9), _d(800, 800)], boxes)
    assert (tp, fp, fn) == (1, 2, 1)


def test_growth_margin():
    boxes = [(0, 0, 100, 100)]
    assert ev.match([_d(114, 50)], boxes)[0] == 1
    assert ev.match([_d(116, 50)], boxes)[0] == 0
