"""Seeded text-level degradation for schedule evaluation (#1114).

The image side of noise robustness lives in ``detector/degrade.py``
(scan noise, thickened strokes). The schedule text path has no image to
degrade, so this module applies the two text-level stand-ins the
noise-conditioned schedule evaluation reports:

- ``ocr_confusion``: characters a scanner/OCR pass commonly swaps
  (O/0, l/1, I/1, S/5, B/8, Z/2) substituted at a per-character rate.
- ``glyph_dropout``: characters dropped at a per-character rate, the
  way a faint or broken scan loses glyphs.

Both are seeded and deterministic, using the same per-index seed scheme
as ``detector.degrade.variant_seed``. The clean variant returns the text
unchanged.
"""

from __future__ import annotations

import numpy as np

from detector.degrade import variant_seed

VARIANTS = ("clean", "ocr_confusion", "glyph_dropout")

OCR_CONFUSIONS = {"O": "0", "0": "O", "l": "1", "I": "1", "S": "5", "B": "8", "Z": "2"}


def degrade_texts(
    texts: list[str],
    variant: str,
    seed: int,
    index: int = 0,
    rate: float = 0.05,
) -> list[str]:
    """Return ``texts`` degraded by ``variant``; clean returns them unchanged.

    ``rate`` is the per-character probability of a substitution or drop.
    """
    if variant not in VARIANTS:
        raise ValueError(f"unknown schedule-noise variant: {variant}")
    if variant == "clean":
        return list(texts)
    rng = np.random.default_rng(variant_seed(seed, index))
    out = []
    for s in texts:
        chars = []
        for ch in s:
            r = rng.random()
            if variant == "ocr_confusion" and r < rate:
                chars.append(OCR_CONFUSIONS.get(ch, ch))
            elif variant == "glyph_dropout" and r < rate:
                continue
            else:
                chars.append(ch)
        out.append("".join(chars))
    return out
