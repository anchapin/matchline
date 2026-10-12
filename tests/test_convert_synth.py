"""Tests for detector/convert_synth.py: synth gt.json -> YOLO conversion (#743).

Covers:
  - Symbol type mapping through the unified taxonomy (door/window, skip fixtures)
  - Box normalization and clipping to the sheet
  - Degenerate-box skipping
  - Deterministic seeded train/val split
  - data.yaml output
  - Missing-PNG and empty-sheet handling
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

DETECTOR = Path(__file__).resolve().parents[1] / "detector"
sys.path.insert(0, str(DETECTOR))

from convert_synth import _rows_from_symbols, convert  # noqa: E402

W, H = 1000, 800


def _write_sheet(dir_path, stem, symbols, size=(W, H), with_png=True):
    """Write one fake synth sheet pair (gt.json + png)."""
    import json

    gt = {
        "sheet_id": stem,
        "seed": 0,
        "scale_px_per_m": 50.0,
        "sheet_size_px": list(size),
        "rooms": [],
        "symbols": symbols,
        "labels": [],
        "schedule": [],
        "expected": {},
    }
    (dir_path / f"{stem}.gt.json").write_text(json.dumps(gt))
    if with_png:
        Image.new("L", size, 255).save(dir_path / f"{stem}.png")


def _sym(kind, bbox):
    return {"type": kind, "tag": "T", "bbox_px": bbox, "drawing": "floor_plan"}


def test_rows_map_doors_windows_and_skip_fixtures():
    symbols = [
        _sym("Single Swing Door", [100, 100, 200, 180]),
        _sym("Double Swing Door", [200, 100, 300, 180]),
        _sym("Window", [500, 400, 700, 560]),
        _sym("Sink", [10, 10, 50, 50]),
    ]
    rows, n_unmapped, n_degenerate = _rows_from_symbols(symbols, W, H)
    assert len(rows) == 3
    assert rows[0] == "0 0.150000 0.175000 0.100000 0.100000"
    assert rows[1] == "0 0.250000 0.175000 0.100000 0.100000"
    assert rows[2] == "1 0.600000 0.600000 0.200000 0.200000"
    assert n_unmapped == 1
    assert n_degenerate == 0


def test_rows_clip_to_sheet_and_skip_degenerate():
    symbols = [
        _sym("Window", [990, 700, 1100, 760]),  # overhangs right/bottom
        _sym("Window", [2000, 2000, 2100, 2100]),  # fully outside
        _sym("Single Swing Door", [100, 100, 100, 180]),  # zero width
    ]
    rows, n_unmapped, n_degenerate = _rows_from_symbols(symbols, W, H)
    assert len(rows) == 1
    cx, cy, w, h = (float(v) for v in rows[0].split()[1:])
    assert w == pytest.approx(10 / W)
    assert h == pytest.approx(60 / H)
    assert cx == pytest.approx(995 / W)
    assert cy == pytest.approx(730 / H)
    assert n_degenerate == 2


def test_split_deterministic_and_sizes(tmp_path):
    src = tmp_path / "sheets"
    src.mkdir()
    for i in range(10):
        _write_sheet(src, f"sheet_{i:03d}", [_sym("Window", [0, 0, 100, 100])])
    out1 = tmp_path / "ds1"
    s1 = convert(src, out1, val_fraction=0.2, seed=0)
    assert s1["sheets_total"] == 10
    assert s1["sheets_train"] == 8
    assert s1["sheets_val"] == 2
    train1 = sorted(p.name for p in (out1 / "labels" / "train").glob("*.txt"))
    out2 = tmp_path / "ds2"
    convert(src, out2, val_fraction=0.2, seed=0)
    train2 = sorted(p.name for p in (out2 / "labels" / "train").glob("*.txt"))
    assert train1 == train2
    # A different seed may (and with 10 sheets almost surely does) differ.
    out3 = tmp_path / "ds3"
    convert(src, out3, val_fraction=0.2, seed=1)
    train3 = sorted(p.name for p in (out3 / "labels" / "train").glob("*.txt"))
    assert train3 != train1


def test_data_yaml_written(tmp_path):
    src = tmp_path / "sheets"
    src.mkdir()
    _write_sheet(src, "sheet_000", [_sym("Window", [0, 0, 100, 100])])
    out = tmp_path / "ds"
    s = convert(src, out, val_fraction=0.2, seed=0)
    text = (out / "data.yaml").read_text()
    assert "0: door" in text
    assert "1: window" in text
    assert "train: images/train" in text
    assert str(out.resolve()) in text
    assert s["data_yaml"].endswith("data.yaml")


def test_missing_png_and_empty_sheet_handling(tmp_path):
    src = tmp_path / "sheets"
    src.mkdir()
    _write_sheet(src, "sheet_000", [_sym("Window", [0, 0, 100, 100])])
    _write_sheet(src, "sheet_001", [_sym("Window", [0, 0, 100, 100])], with_png=False)
    _write_sheet(src, "sheet_002", [_sym("Sink", [0, 0, 100, 100])])  # empty after map
    out = tmp_path / "ds"
    s = convert(src, out, val_fraction=0.0, seed=0)
    assert s["sheets_skipped_missing_png"] == 1
    assert s["sheets_train"] == 1
    assert s["boxes_skipped_unmapped"] == 1
    # include_empty keeps the fixture-only sheet as a background image.
    out2 = tmp_path / "ds2"
    s2 = convert(src, out2, val_fraction=0.0, seed=0, include_empty=True)
    assert s2["sheets_train"] == 2
    assert (out2 / "labels" / "train" / "sheet_002.txt").read_text() == ""


def test_no_sheets_raises(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(FileNotFoundError):
        convert(src, tmp_path / "ds")
