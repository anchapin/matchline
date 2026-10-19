"""Tile-merge dedup for openings straddling a tile boundary (#1125).

sahi_infer.tile_boxes slices a sheet into 1024px tiles with 0.2 overlap.
An opening wider than the overlap band that straddles a tile edge is seen
clipped by one tile and whole by the next: the clipped sliver overlaps the
full view with IoU ~0.25, so a plain IoU NMS kept both and eval counted the
same physical opening twice (one TP plus one FP). These tests pin the
merge rule: near-duplicates across tiles collapse to one prediction, the
full view wins over its own sliver, and genuinely distinct openings are
never suppressed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from detector.eval_robustness import match_predictions  # noqa: E402
from detector.sahi_infer import nms, tile_boxes  # noqa: E402


def _door(cls=0, conf=0.9, x0=924, y0=400, x1=1124, y1=500):
    return {"cls": cls, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_tile_starts_cover_the_sheet_without_gaps():
    tiles = tile_boxes(2048, 1024)
    assert tiles == [(0, 0, 1024, 1024), (819, 0, 1843, 1024), (1024, 0, 2048, 1024)]


def test_straddling_door_and_window_merge_to_one_pred_each():
    # door and window centred on x=1024, the edge of tile 1; tile 1 sees a
    # clipped sliver, tile 2 sees the whole opening
    tile_preds = [
        _door(cls=0, conf=0.9),  # full door, tile 2
        _door(cls=0, conf=0.55, x1=1024),  # door sliver, tile 1
        _door(cls=1, conf=0.85, x0=904, y0=200, x1=1144, y1=300),  # full window
        _door(cls=1, conf=0.6, x0=904, y0=200, x1=1024, y1=300),  # window sliver
    ]
    kept = nms(tile_preds, 0.5)
    assert len(kept) == 2
    assert {(b["cls"], b["x1"]) for b in kept} == {(0, 1124), (1, 1144)}


def test_opening_fully_inside_the_overlap_band_merges():
    # a small opening fits whole in both tiles' views: two near-identical
    # full boxes, IoU ~1.0
    a = _door(conf=0.9)
    b = _door(conf=0.7, x0=920, y0=398, x1=1120, y1=502)
    kept = nms([a, b], 0.5)
    assert len(kept) == 1
    assert kept[0]["conf"] == 0.9


def test_distinct_openings_near_a_straddle_are_not_suppressed():
    straddle_full = _door(conf=0.9)
    straddle_sliver = _door(conf=0.55, x1=1024)
    other = _door(conf=0.8, x0=100, x1=200)  # a different door down the wall
    kept = nms([straddle_full, straddle_sliver, other], 0.5)
    assert len(kept) == 2
    assert {b["x0"] for b in kept} == {924, 100}


def test_high_confidence_sliver_does_not_beat_the_full_view():
    sliver = _door(conf=0.95, x1=1024)  # clipped, but scored high
    full = _door(conf=0.6)  # whole opening, scored lower
    kept = nms([sliver, full], 0.5)
    assert len(kept) == 1
    assert kept[0]["x1"] == 1124  # the full view, not the sliver


def test_containment_suppression_can_be_disabled():
    sliver = _door(conf=0.55, x1=1024)
    full = _door(conf=0.9)
    kept = nms([full, sliver], 0.5, contain_thr=1.01)  # IoU-only behavior
    assert len(kept) == 2


def test_eval_counts_a_straddling_opening_once():
    # the acceptance case: after the merge, eval yields exactly one TP per
    # ground-truth opening and no false positive
    gt = [
        {"cls": 0, "conf": 1.0, "x0": 924, "y0": 400, "x1": 1124, "y1": 500},
        {"cls": 1, "conf": 1.0, "x0": 904, "y0": 200, "x1": 1144, "y1": 300},
    ]
    merged = nms(
        [
            _door(cls=0, conf=0.9),
            _door(cls=0, conf=0.55, x1=1024),
            _door(cls=1, conf=0.85, x0=904, y0=200, x1=1144, y1=300),
            _door(cls=1, conf=0.6, x0=904, y0=200, x1=1024, y1=300),
        ],
        0.5,
    )
    stats = match_predictions(gt, merged)
    assert (stats[0]["tp"], stats[0]["fp"], stats[0]["fn"]) == (1, 0, 0)
    assert (stats[1]["tp"], stats[1]["fp"], stats[1]["fn"]) == (1, 0, 0)
