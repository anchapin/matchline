"""Duct skeleton keeps diagonal flex runs (#721)."""

import numpy as np
from PIL import Image, ImageDraw

from hvac_trace import duct_skeleton


def _sheet(width_px):
    img = Image.new("L", (300, 300), 255)
    d = ImageDraw.Draw(img)
    d.line([40, 40, 160, 260], fill=0, width=width_px)  # ~29 deg off vertical
    return np.asarray(img).astype(np.float64)


def test_diagonal_flex_run_survives():
    skel = duct_skeleton(_sheet(10))
    ys, _ = np.nonzero(skel)
    assert skel.sum() > 100
    assert ys.min() < 80 and ys.max() > 220  # spans most of the run


def test_thin_lines_and_text_still_removed():
    img = Image.new("L", (300, 300), 255)
    d = ImageDraw.Draw(img)
    d.line([40, 40, 160, 260], fill=0, width=3)  # wall / outline weight
    d.rectangle([20, 200, 120, 280], outline=0, width=3)
    d.text((150, 60), "SD-1 150", fill=0)
    assert duct_skeleton(np.asarray(img).astype(np.float64)).sum() == 0
