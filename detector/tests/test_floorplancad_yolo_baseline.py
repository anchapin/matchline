#!/usr/bin/env python3
"""Tests for the FloorPlanCAD YOLO baseline driver (#1329).

These cover the pure-python parts only (transforms, subset rule, table
shaping); no model weights and no dataset access are required.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from detector.floorplancad_yolo_baseline import (
        SUBSETS,
        VARIANTS,
        apply_variant,
        rotate_boxes,
        rotate_point,
        scale_boxes,
    )
except ImportError:  # direct execution from inside detector/
    from floorplancad_yolo_baseline import (
        SUBSETS,
        VARIANTS,
        apply_variant,
        rotate_boxes,
        rotate_point,
        scale_boxes,
    )


def _box(cls=0, x0=100.0, y0=200.0, x1=300.0, y1=400.0):
    return {"cls": cls, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_scale_boxes_multiplies_edges():
    b = scale_boxes([_box()], 0.8)
    assert b[0]["x0"] == pytest.approx(80.0)
    assert b[0]["y1"] == pytest.approx(320.0)
    assert b[0]["cls"] == 0


def test_rotate_point_is_counter_clockwise_in_image_coords():
    # a point right of the centre must move up (smaller y) for a CCW rotation
    x, y = rotate_point(110.0, 100.0, 100.0, 100.0, 90.0)
    assert x == pytest.approx(100.0, abs=1e-6)
    assert y == pytest.approx(90.0, abs=1e-6)


def test_rotate_point_roundtrip():
    for deg in (15.0, -15.0, 90.0):
        x, y = rotate_point(123.0, 456.0, 500.0, 500.0, deg)
        bx, by = rotate_point(x, y, 500.0, 500.0, -deg)
        assert bx == pytest.approx(123.0, abs=1e-6)
        assert by == pytest.approx(456.0, abs=1e-6)


def test_rotate_boxes_keeps_centre_and_grows_bounds():
    boxes = [_box(x0=400.0, y0=400.0, x1=600.0, y1=600.0)]
    out = rotate_boxes(boxes, 15.0, (1000, 1000))[0]
    cx = (out["x0"] + out["x1"]) / 2
    cy = (out["y0"] + out["y1"]) / 2
    assert cx == pytest.approx(500.0, abs=1e-6)
    assert cy == pytest.approx(500.0, abs=1e-6)
    # a rotated square's AABB is strictly bigger than the original
    assert (out["x1"] - out["x0"]) > 200.0
    assert (out["y1"] - out["y0"]) > 200.0


def test_apply_variant_scale_resizes_image_and_boxes():
    from PIL import Image

    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    boxes = [_box(x0=100.0, y0=100.0, x1=200.0, y1=300.0)]
    out_img, out_boxes = apply_variant(img, boxes, "scale_1.25")
    assert out_img.size == (1250, 1250)
    assert out_boxes[0]["x1"] == pytest.approx(250.0)


def test_apply_variant_rotation_expands_canvas():
    from PIL import Image

    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    out_img, _ = apply_variant(img, [_box()], "rot_p15")
    assert out_img.size[0] > 1000 and out_img.size[1] > 1000


def test_rotation_label_follows_the_painted_geometry():
    """Regression test: the rotated GT box must land on the rotated pixels.

    A red patch is painted into the source image at the GT box, the image is
    rotated, and the red pixels of the rotated image are compared with the
    rotated GT box. Without the canvas-expansion offset the label is shifted
    by half the canvas growth and the IoU collapses.
    """
    import numpy as np
    from PIL import Image

    img = Image.new("RGB", (400, 400), (255, 255, 255))
    arr = np.array(img)
    arr[100:160, 220:300] = (255, 0, 0)
    painted = Image.fromarray(arr)
    box = {"cls": 0, "x0": 220.0, "y0": 100.0, "x1": 300.0, "y1": 160.0}

    for variant in ("rot_p15", "rot_m15"):
        out_img, out_boxes = apply_variant(painted, [box], variant)
        out_arr = np.array(out_img)
        mask = (
            (out_arr[:, :, 0] > 200) & (out_arr[:, :, 1] < 60) & (out_arr[:, :, 2] < 60)
        )
        ys, xs = np.nonzero(mask)
        assert len(xs) > 0
        red = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
        tb = out_boxes[0]
        gt = (tb["x0"], tb["y0"], tb["x1"], tb["y1"])
        inter_x0, inter_y0 = max(red[0], gt[0]), max(red[1], gt[1])
        inter_x1, inter_y1 = min(red[2], gt[2]), min(red[3], gt[3])
        inter = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
        a1 = (red[2] - red[0]) * (red[3] - red[1])
        a2 = (gt[2] - gt[0]) * (gt[3] - gt[1])
        assert inter / max(1e-9, a1 + a2 - inter) > 0.95


def test_apply_variant_clean_is_identity():
    from PIL import Image

    img = Image.new("RGB", (1000, 1000), (255, 255, 255))
    boxes = [_box()]
    out_img, out_boxes = apply_variant(img, boxes, "clean")
    assert out_img.size == img.size
    assert out_boxes == boxes


def test_variants_and_subsets_are_stable():
    assert VARIANTS == ("clean", "scale_0.8", "scale_1.25", "rot_p15", "rot_m15")
    assert SUBSETS["clean"] == ("clean",)
    assert SUBSETS["scale_rot"] == ("scale_0.8", "scale_1.25", "rot_p15", "rot_m15")


def test_matching_is_the_shared_zero_shot_algorithm():
    # the driver must reuse eval_robustness.match_predictions, which is the
    # greedy IoU matching of eval_zero_shot.py
    from detector.eval_robustness import match_predictions as m1
    from detector.eval_zero_shot import iou as zero_shot_iou

    gt = [{"cls": 0, "x0": 0, "y0": 0, "x1": 10, "y1": 10}]
    preds = [{"cls": 0, "conf": 0.9, "x0": 0, "y0": 0, "x1": 10, "y1": 10}]
    assert zero_shot_iou(gt[0], preds[0]) == pytest.approx(1.0)
    stats = m1(gt, preds, 0.5)
    assert stats[0]["tp"] == 1
    assert stats[0]["fp"] == 0


# --- committed results artifacts (detector/results/tempfit_yolo_baseline/) ---


RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "results",
    "tempfit_yolo_baseline",
)


def test_image_list_is_the_deterministic_subset():
    stems = [
        line.strip()
        for line in open(os.path.join(RESULTS_DIR, "images.txt"))
        if line.strip()
    ]
    assert len(stems) == 100
    assert len(set(stems)) == 100
    assert stems == sorted(stems)


def test_committed_per_image_summaries_reconcile_with_the_image_list():
    import csv

    stems = [
        line.strip()
        for line in open(os.path.join(RESULTS_DIR, "images.txt"))
        if line.strip()
    ]
    for run in ("coco", "doorwindow"):
        with open(
            os.path.join(RESULTS_DIR, f"per_image_yolo11n_{run}.csv"), newline=""
        ) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(stems) * len(VARIANTS)
        assert {(r["stem"], r["variant"]) for r in rows} == {
            (stem, v) for stem in stems for v in VARIANTS
        }


def test_committed_reports_use_the_dataset_root_convention():
    import json

    for name in ("report_yolo11n_coco.json", "report_yolo11n_doorwindow.json"):
        with open(os.path.join(RESULTS_DIR, name)) as f:
            report = json.load(f)
        cfg = report["config"]
        assert cfg["dataset_root"].startswith("$MATCHLINE_DATASETS/")
        assert cfg["weights"].startswith("$MATCHLINE_DATASETS/")
        assert cfg["predictions_source"] == "regenerated"
        assert cfg["n_images"] == 100
        assert cfg["iou"] == 0.5
        assert cfg["variants"] == list(VARIANTS)
        assert cfg["subsets"] == {k: list(v) for k, v in SUBSETS.items()}
        assert report["stems"] == sorted(report["stems"])
        assert len(report["stems"]) == 100


def test_results_note_states_the_premise_correction():
    with open(os.path.join(RESULTS_DIR, "analysis.md")) as f:
        note = f.read()
    # the stock COCO checkpoint must be labelled as a control, and the
    # fine-tuned door/window checkpoint as the baseline
    assert "negative control" in note
    assert "cubi_yolo11n_e10/weights/best.pt" in note
    assert "images.txt" in note
