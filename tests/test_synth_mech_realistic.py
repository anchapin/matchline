"""Realistic rendering mode for synthetic mechanical sheets (#721)."""

import numpy as np

from synth.mech import generate_mech_sheet, training_crops_from_sheets


def _pos(gt):
    return [(c["id"], c["type"], c["x_m"], c["y_m"]) for c in gt["components"]]


def test_realistic_keeps_layout_and_ground_truth():
    img, gt = generate_mech_sheet(11)
    rimg, rgt = generate_mech_sheet(11, realistic=True)
    assert _pos(gt) == _pos(rgt)
    assert gt["zones"] == rgt["zones"]
    assert img.size == rimg.size
    assert gt["realistic"] is False and rgt["realistic"] is True


def test_realistic_changes_pixels_and_is_deterministic():
    img, _ = generate_mech_sheet(22)
    a, _ = generate_mech_sheet(22, realistic=True)
    b, _ = generate_mech_sheet(22, realistic=True)
    assert np.array_equal(np.asarray(a), np.asarray(b))
    diff = (np.asarray(a) != np.asarray(img)).mean()
    assert 0.0 < diff < 0.05


def test_training_crops_accept_realistic_sheets():
    X0, y0, n0 = training_crops_from_sheets(n_per_class=4, seed=0, bg_per_sheet=1)
    X1, y1, n1 = training_crops_from_sheets(n_per_class=4, seed=0, bg_per_sheet=1, realistic=True)
    assert n0 == n1 and X0.shape == X1.shape
    assert np.array_equal(y0, y1)
