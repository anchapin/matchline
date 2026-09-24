"""Tests for symbols.py — programmatic vector-style symbol rendering."""

import numpy as np
import pytest

from symbols import CANVAS, OUT, draw, jitter, make_dataset, to28


def test_draw_returns_correct_shape():
    """draw(cls) returns a CANVAS×CANVAS float array."""
    for cls in range(10):
        img = draw(cls)
        assert img.shape == (CANVAS, CANVAS)
        assert img.dtype == np.float64


def test_draw_all_classes_valid():
    """All 10 symbol classes (0-9) render without raising."""
    for cls in range(10):
        img = draw(cls)
        assert img.max() > 0, f"class {cls} produced empty image"


def test_draw_class_9_has_content():
    """Class 9 (window symbol) produces non-zero pixels."""
    img = draw(9)
    assert img.max() > 0


def test_draw_invalid_class_raises():
    """Invalid class raises ValueError."""
    with pytest.raises(ValueError):
        draw(10)


def test_to28_reduces_resolution():
    """to28() reduces CANVAS×CANVAS to OUT×OUT."""
    img = draw(0)
    result = to28(img)
    assert result.shape == (OUT, OUT)


def test_to28_output_range():
    """to28() output values are in 0..255 (uint8 range)."""
    result = to28(draw(0))
    assert result.min() >= 0
    assert result.max() <= 255


def test_jitter_changes_pixels():
    """jitter() produces a different pixel array from the input."""
    rng = np.random.default_rng(42)
    img = draw(0)
    jitted = jitter(img, rng)
    assert jitted.shape == img.shape
    assert not np.array_equal(jitted, img)


def test_jitter_is_deterministic_with_same_rng():
    """Same seed produces identical jitter output."""
    rng1 = np.random.default_rng(99)
    rng2 = np.random.default_rng(99)
    img = draw(5)
    assert np.array_equal(jitter(img, rng1), jitter(img, rng2))


def test_make_dataset_returns_arrays():
    """make_dataset returns correctly shaped numpy arrays."""
    Xtr, ytr, Xte, yte = make_dataset(n_train=5, n_test=3, seed=42)
    assert Xtr.shape == (50, OUT, OUT)
    assert ytr.shape == (50,)
    assert Xte.shape == (30, OUT, OUT)
    assert yte.shape == (30,)


def test_make_dataset_labels_match_classes():
    """make_dataset labels 0..9 each appear n_train times."""
    Xtr, ytr, _, _ = make_dataset(n_train=5, n_test=3, seed=42)
    for cls in range(10):
        assert list(ytr).count(cls) == 5
