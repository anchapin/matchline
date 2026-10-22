"""Tests for detector/degrade_scans.py (issue #1241): the scan-degradation family."""

from __future__ import annotations

import copy

import numpy as np
import pytest
from PIL import Image, ImageDraw

from detector.degrade_scans import (
    EVAL_SEVERITY,
    FAMILY,
    SEVERITY_RANGES,
    apply_family_with_boxes,
    apply_scan,
    apply_scan_family,
    sample_member,
    sample_severity,
    transform_boxes,
    transform_gt,
    transform_points,
)


@pytest.fixture
def plan_img() -> Image.Image:
    """A small white sheet with one dark square and one line."""
    img = Image.new("RGB", (400, 400), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([100, 100, 200, 200], fill="black")
    d.line([300, 50, 350, 350], fill="black", width=3)
    return img


def test_family_registry():
    assert set(FAMILY) == {
        "rotation_skew",
        "low_dpi",
        "jpeg",
        "contrast_loss",
        "bleed_through",
        "blur",
    }


def test_member_deterministic_same_seed(plan_img):
    a = np.asarray(apply_scan(plan_img, "blur", 0.7, seed=42))
    b = np.asarray(apply_scan(plan_img, "blur", 0.7, seed=42))
    assert (a == b).all()


def test_member_seed_sensitive(plan_img):
    a = np.asarray(apply_scan(plan_img, "rotation_skew", 0.7, seed=1))
    b = np.asarray(apply_scan(plan_img, "rotation_skew", 0.7, seed=2))
    assert (a != b).any()


@pytest.mark.parametrize("member", FAMILY)
def test_size_preserved(plan_img, member):
    out = apply_scan(plan_img, member, 0.7, seed=42)
    assert out.size == plan_img.size
    assert out.mode == "RGB"


@pytest.mark.parametrize("member", FAMILY)
def test_severity_zero_near_identity_or_mild(plan_img, member):
    """Severity 0 is either the identity or the member's stated floor."""
    out = apply_scan(plan_img, member, 0.0, seed=42)
    assert out.size == plan_img.size
    diff = np.asarray(out, dtype=np.int16) - np.asarray(plan_img, dtype=np.int16)
    # jpeg q95 and blur 0.3px stay within a small band; nothing is a rewrite.
    assert np.abs(diff).mean() < 12.0


def test_rotation_keeps_boxes_on_features(plan_img):
    boxes = [{"x0": 100.0, "y0": 100.0, "x1": 200.0, "y1": 200.0, "cls": 0}]
    for sev in (0.2, 0.5, 1.0):
        seed = 7
        deg = apply_scan(plan_img, "rotation_skew", sev, seed)
        tb = transform_boxes(boxes, "rotation_skew", sev, seed, plan_img.size)[0]
        region = np.asarray(deg.convert("L"))[
            int(tb["y0"]) : int(tb["y1"]) + 1, int(tb["x0"]) : int(tb["x1"]) + 1
        ]
        assert (region < 128).mean() > 0.9, sev


def test_rotation_transforms_dict_and_list_boxes(plan_img):
    dict_boxes = [{"x0": 100.0, "y0": 100.0, "x1": 200.0, "y1": 200.0}]
    list_boxes = [[100.0, 100.0, 200.0, 200.0]]
    seed, sev = 9, 0.5
    td = transform_boxes(dict_boxes, "rotation_skew", sev, seed, plan_img.size)[0]
    tl = transform_boxes(list_boxes, "rotation_skew", sev, seed, plan_img.size)[0]
    assert abs(td["x0"] - tl[0]) < 1e-9
    assert abs(td["y1"] - tl[3]) < 1e-9


@pytest.mark.parametrize("member", ["low_dpi", "jpeg", "contrast_loss", "bleed_through", "blur"])
def test_transform_boxes_identity_for_non_geometric(plan_img, member):
    boxes = [{"x0": 10.0, "y0": 10.0, "x1": 50.0, "y1": 50.0}]
    assert transform_boxes(boxes, member, 0.7, 7, plan_img.size) == boxes


def test_transform_points_identity_for_non_geometric():
    pts = [[10.0, 20.0], [30.0, 40.0]]
    assert (transform_points(pts, "jpeg", 0.7, 7, (400, 400)) == np.asarray(pts)).all()


def test_rotation_clips_and_drops_degenerate_boxes():
    # A box flush against the edge stays in-bounds after a small tilt; a
    # zero-area clip is dropped rather than passed through broken.
    boxes = [[0.0, 0.0, 100.0, 100.0], [50.0, 50.0, 50.2, 50.2]]
    out = transform_boxes(boxes, "rotation_skew", 1.0, 3, (400, 400))
    assert all(b[2] - b[0] >= 1.0 and b[3] - b[1] >= 1.0 for b in out)
    assert len(out) == 1


def test_severity_sampled_in_range():
    rng = np.random.default_rng(0)
    seen = {}
    for _ in range(400):
        m = sample_member(rng)
        s = sample_severity(m, rng)
        lo, hi = SEVERITY_RANGES[m]
        assert lo <= s <= hi
        seen.setdefault(m, []).append(s)
    for m, vals in seen.items():
        # Real spread, not one fixed level.
        assert max(vals) - min(vals) > 0.2, m


def test_sample_member_deterministic():
    assert sample_member(np.random.default_rng(5)) == sample_member(np.random.default_rng(5))


def test_unknown_member_raises(plan_img):
    with pytest.raises(ValueError):
        apply_scan(plan_img, "phaser", 0.5, 7)
    with pytest.raises(ValueError):
        sample_severity("phaser", np.random.default_rng(0))


def test_apply_scan_family_returns_valid_meta(plan_img):
    deg, member, severity = apply_scan_family(plan_img, seed=123)
    assert member in FAMILY
    assert 0.0 <= severity <= 1.0
    assert deg.size == plan_img.size
    deg2, member2, severity2 = apply_scan_family(plan_img, seed=123)
    assert member == member2 and severity == severity2
    assert (np.asarray(deg) == np.asarray(deg2)).all()


def test_apply_family_with_boxes_alignment(plan_img):
    boxes = [{"x0": 100.0, "y0": 100.0, "x1": 200.0, "y1": 200.0, "cls": 0}]
    deg, aligned, meta = apply_family_with_boxes(plan_img, boxes, seed=3)
    assert set(meta) == {"member", "severity", "boxes_dropped"}
    if meta["member"] != "rotation_skew":
        assert aligned == boxes and meta["boxes_dropped"] == 0
    else:
        tb = aligned[0]
        region = np.asarray(deg.convert("L"))[
            int(tb["y0"]) : int(tb["y1"]) + 1, int(tb["x0"]) : int(tb["x1"]) + 1
        ]
        assert (region < 128).mean() > 0.9


def _gt() -> dict:
    return {
        "symbols": [{"type": "Window", "bbox_px": [100, 100, 140, 140]}],
        "labels": [{"text": "KITCHEN", "bbox_px": [220, 120, 280, 140]}],
        "walls": [{"kind": "exterior", "a_px": [50, 50], "b_px": [350, 50], "t_px": 6.0}],
        "rooms": [
            {
                "name": "r1",
                "polygon_px": [[60, 60], [340, 60], [340, 300], [60, 300]],
                "area_m2": 20.0,
            }
        ],
    }


def test_transform_gt_identity_for_non_geometric():
    gt = _gt()
    out = transform_gt(copy.deepcopy(gt), "low_dpi", 0.7, 9, (400, 400))
    assert out == gt


def test_transform_gt_moves_geometry_together():
    seed, sev = 9, 0.5
    gt = _gt()
    out = transform_gt(copy.deepcopy(gt), "rotation_skew", sev, seed, (400, 400))
    # The wall's two endpoints moved by the same transform; thickness kept.
    a, b = out["walls"][0]["a_px"], out["walls"][0]["b_px"]
    ref = transform_points([[50, 50], [350, 50]], "rotation_skew", sev, seed, (400, 400))
    assert np.allclose(a, ref[0]) and np.allclose(b, ref[1])
    assert out["walls"][0]["t_px"] == 6.0
    # Symbols and labels moved off their original spot (real transform, not a copy).
    assert out["symbols"][0]["bbox_px"] != gt["symbols"][0]["bbox_px"]
    assert out["labels"][0]["bbox_px"] != gt["labels"][0]["bbox_px"]
    # Area rollups stay untouched (rotation preserves area).
    assert out["rooms"][0]["area_m2"] == 20.0
    # The polygon still has four vertices.
    assert len(out["rooms"][0]["polygon_px"]) == 4


def test_eval_severity_pinned():
    assert 0.0 < EVAL_SEVERITY <= 1.0


def test_bleed_through_darkens_where_flipped_ink_sits(plan_img):
    """Bleed-through shows the sheet behind: the plan's own mirrored ink
    faintly darkens otherwise-blank paper, and the effect is seeded."""
    out = apply_scan(plan_img, "bleed_through", 1.0, seed=42)
    arr = np.asarray(out.convert("L"), dtype=np.float32)
    # plan_img has ink at x in [100..200] and [300..350]; mirrored it lands
    # at x in [200..300] and [50..100]. Compare a mirrored-ink zone to a
    # truly blank zone at the same distance from the edges.
    ink_zone = arr[160:240, 210:290]
    blank_zone = arr[160:240, 360:400]
    assert ink_zone.mean() < blank_zone.mean() - 1.0
    # Deterministic.
    out2 = apply_scan(plan_img, "bleed_through", 1.0, seed=42)
    assert (np.asarray(out) == np.asarray(out2)).all()
