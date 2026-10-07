"""Tight-template confirmation gate for diffuser/grille detections (#735)."""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _sheet_with(cls: str, cx: int, cy: int, size: int = 200) -> np.ndarray:
    g = np.full((size, size), 255.0)
    t = M.render_template(cls, margin_px=0, stubs=False).astype(np.float64)
    th, tw = t.shape
    g[cy - th // 2 : cy - th // 2 + th, cx - tw // 2 : cx - tw // 2 + tw] = t
    return g


@pytest.mark.parametrize("cls", ["grille", "diffuser"])
def test_local_ncc_high_on_drawn_glyph(cls):
    g = _sheet_with(cls, 100, 100)
    t = M.render_template(cls, margin_px=0, stubs=False)
    assert H._local_ncc(g, t, 100, 100, H.TIGHT_CONFIRM_R_PX) > 0.95


def test_local_ncc_low_on_duct_crossing():
    g = np.full((200, 200), 255.0)
    g[90:110, :] = 0.0
    g[:, 90:110] = 0.0
    t = M.render_template("grille", margin_px=0, stubs=False)
    assert H._local_ncc(g, t, 100, 100, H.TIGHT_CONFIRM_R_PX) < H.TIGHT_CONFIRM_NCC
    assert H._local_ncc(g, t, 3, 3, H.TIGHT_CONFIRM_R_PX) == 0.0  # off-sheet window


def test_confirm_score_survives_drop_over_glyph():
    # Synthetic sheets run the duct drop into the glyph; the masked centre band keeps it.
    g = _sheet_with("diffuser", 100, 100)
    g[100:140, 93:108] = 0.0
    assert H._tight_confirm_score(g, "diffuser", 100, 100) >= H.TIGHT_CONFIRM_NCC


def test_confirm_score_zero_inside_solid_duct():
    g = np.full((200, 200), 255.0)
    g[60:140, :] = 0.0
    assert H._tight_confirm_score(g, "grille", 100, 100) == 0.0
