"""Context augmentation for the WiSARD terminal classifier (#719)."""

import numpy as np

from synth.mech import CONTEXT_CLASSES, MECH_CLASSES, context_crops, training_crops_from_sheets

BG = len(MECH_CLASSES)


def test_context_crops_shapes_and_labels():
    X, y = context_crops(4, seed=0)
    assert X.shape == (4 * len(CONTEXT_CLASSES) * 2, 28, 28)
    for cls in CONTEXT_CLASSES:
        assert (y == MECH_CLASSES.index(cls)).sum() == 4
    assert (y == BG).sum() == 4 * len(CONTEXT_CLASSES)
    assert X.min() >= 0 and X.max() <= 255


def test_context_crops_background_count_override():
    _, y = context_crops(3, seed=0, bg_per_class=5)
    assert (y == BG).sum() == 5


def test_context_crops_deterministic():
    a, ya = context_crops(3, seed=7)
    b, yb = context_crops(3, seed=7)
    np.testing.assert_array_equal(a, b)
    np.testing.assert_array_equal(ya, yb)
    c, _ = context_crops(3, seed=8)
    assert not np.array_equal(a, c)


def test_training_crops_default_unchanged_and_context_appends():
    X0, y0, names = training_crops_from_sheets(n_per_class=2, seed=0, bg_per_sheet=1)
    X1, y1, _ = training_crops_from_sheets(
        n_per_class=2, seed=0, bg_per_sheet=1, context_per_class=0
    )
    np.testing.assert_array_equal(X0, X1)
    np.testing.assert_array_equal(y0, y1)
    X2, y2, _ = training_crops_from_sheets(
        n_per_class=2, seed=0, bg_per_sheet=1, context_per_class=3, context_bg=2
    )
    assert len(y2) == len(y0) + 3 * len(CONTEXT_CLASSES) + 2
    assert names[-1] == "background"
