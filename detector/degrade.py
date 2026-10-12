#!/usr/bin/env python3
"""Deterministic synthetic degradations for detector robustness evaluation.

Implements the "matched baselines under synthetic degradation" methodology from
arXiv:2609.24565 ("What Survives on Real Drawings"): scan noise and thickened
strokes applied to clean validation images, so the detector's metric delta vs
clean is measurable. See issue #499.

Every degradation is:
  - deterministic: seeded via ``numpy.random.Generator``; the same
    (image bytes, variant, seed) always yields the same pixels;
  - geometry-preserving: the output has the same size and mode as the input,
    so ground-truth boxes stay valid and no re-labeling is needed;
  - CPU-friendly: pure PIL/numpy, no scipy/torch/ultralytics dependency.

Usage:
    from degrade import VARIANTS, degrade, variant_seed
    degraded = degrade(image, "scan_noise", seed=variant_seed(7, idx))
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

VARIANTS = ("clean", "scan_noise", "thickened_strokes")

# Gaussian noise stddev (0-255 scale) for the scan-noise variant.
SCAN_NOISE_SIGMA = 12.0
# Post-noise softening radius (px), mimicking scanner optics.
SCAN_NOISE_BLUR_RADIUS = 0.6
# Dilation kernel size (px) for the thickened-strokes variant.
STROKE_DILATE_SIZE = 3


def variant_seed(base_seed: int, index: int) -> int:
    """Derive a per-image seed so degradation is order-independent.

    Mixing the image index into the seed (via a large prime) keeps each
    image's degradation stable regardless of sampling order or ``--n``.
    """
    return (base_seed * 1_000_003 + index * 9_176_439) % (2**31 - 1)


def degrade(img: Image.Image, variant: str, seed: int) -> Image.Image:
    """Return a degraded copy of ``img``.

    ``variant`` is one of VARIANTS; ``"clean"`` returns an unmodified copy.
    Raises ValueError on an unknown variant.
    """
    if variant == "clean":
        return img.copy()
    if variant == "scan_noise":
        return _scan_noise(img, np.random.default_rng(seed))
    if variant == "thickened_strokes":
        return _thickened_strokes(img)
    raise ValueError(f"unknown degradation variant: {variant!r} (expected one of {VARIANTS})")


def _scan_noise(img: Image.Image, rng: np.random.Generator) -> Image.Image:
    """Additive Gaussian noise plus slight blur: a cheap scan-noise proxy."""
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    noise = rng.normal(0.0, SCAN_NOISE_SIGMA, arr.shape).astype(np.float32)
    noisy = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    return noisy.filter(ImageFilter.GaussianBlur(SCAN_NOISE_BLUR_RADIUS))


def _thickened_strokes(img: Image.Image) -> Image.Image:
    """Morphological dilation of dark pixels: a thickened-stroke proxy.

    Floor plans are dark linework on a light background. Dilating the dark
    channel expands strokes by ~1 px, mimicking heavy plotter pens or
    bleed-through on scanned sheets. Implemented with PIL's MaxFilter so no
    scipy dependency is needed.
    """
    rgb = img.convert("RGB")
    dark = ImageOps.invert(rgb.convert("L"))
    dilated = dark.filter(ImageFilter.MaxFilter(STROKE_DILATE_SIZE))
    thick = ImageOps.invert(dilated).convert("RGB")
    # Per-channel minimum: dark regions grow, light regions are untouched.
    return Image.fromarray(np.minimum(np.asarray(rgb), np.asarray(thick)))
