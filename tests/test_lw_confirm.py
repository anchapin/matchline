"""Stroke-width-invariant grille confirm route (#1252)."""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _sheet_with(cls, cx=100, cy=100, size=200, style=0, lw=None):
    g = np.full((size, size), 255.0)
    t = M.render_template(cls, margin_px=0, stubs=False, style=style, lw=lw).astype(np.float64)
    th, tw = t.shape
    g[cy - th // 2 : cy - th // 2 + th, cx - tw // 2 : cx - tw // 2 + tw] = t
    return g


def test_render_template_lw_ink_weight_ordering():
    inks = [
        int((M.render_template("grille", margin_px=0, stubs=False, lw=lw) < 128).sum())
        for lw in (2, 3, 4)
    ]
    assert inks[0] < inks[1] < inks[2]


def test_render_template_default_matches_explicit_lw():
    # lw=None keeps each class's own default: 3 px for terminal glyphs, 2 px for the sensor
    assert np.array_equal(
        M.render_template("grille", margin_px=0, stubs=False),
        M.render_template("grille", margin_px=0, stubs=False, lw=3),
    )
    assert np.array_equal(
        M.render_template("sensor", margin_px=0, stubs=False),
        M.render_template("sensor", margin_px=0, stubs=False, lw=2),
    )


@pytest.mark.parametrize("style", [0, 1])
@pytest.mark.parametrize("lw", [2, 3, 4])
def test_lw_confirm_score_true_grilles_above_bars(style, lw):
    # true grilles drawn 1 px lighter or heavier than the template confirm
    # through the stroke-normalized route at the stricter relabel bar
    g = _sheet_with("grille", style=style, lw=lw)
    assert H._lw_confirm_score(g, "grille", 100, 100) >= H.LW_RELABEL_NCC


@pytest.mark.parametrize("lw", [2, 3])
def test_lw_confirm_score_survives_drop_mask_first(lw):
    # mask-first skeletonization: the band is blanked in window and template
    # before skeletonizing, so a duct drop cannot reshape one skeleton alone
    g = _sheet_with("grille", lw=lw)
    g[100:140, 93:108] = 0.0
    assert H._lw_confirm_score(g, "grille", 100, 100) >= H.LW_RELABEL_NCC


@pytest.mark.parametrize("lw", [2, 3, 4])
def test_lw_confirm_score_rejects_diffuser_as_grille(lw):
    # the other terminal class must never clear the grille-side lw bar
    g = _sheet_with("diffuser", lw=lw)
    assert H._lw_confirm_score(g, "grille", 100, 100) < H.LW_CONFIRM_NCC


def _duct_cross():
    g = np.full((200, 200), 255.0)
    g[90:110, :] = 0.0
    g[:, 90:110] = 0.0
    return g


def _walls():
    g = np.full((200, 200), 255.0)
    g[40:44, :] = 0.0
    g[:, 160:164] = 0.0
    g[130:170, 30:34] = 0.0
    return g


def _boxes():
    g = np.full((200, 200), 255.0)
    for x0, y0, x1, y1 in [
        (20, 20, 80, 80),
        (100, 30, 150, 70),
        (40, 120, 110, 180),
        (120, 110, 180, 190),
    ]:
        g[y0:y1, x0:x1] = 255.0
        g[y0:y1, x0 : x0 + 3] = 0.0
        g[y0:y1, x1 - 3 : x1] = 0.0
        g[y0 : y0 + 3, x0:x1] = 0.0
        g[y1 - 3 : y1, x0:x1] = 0.0
    return g


def _text_blobs():
    g = np.full((200, 200), 255.0)
    rng = np.random.default_rng(7)
    for _ in range(6):
        x, y = rng.integers(10, 180), rng.integers(10, 180)
        g[y : y + 2, x : x + 14] = 0.0
        g[y : y + 12, x : x + 2] = 0.0
    return g


@pytest.mark.parametrize(
    "builder",
    [lambda: np.full((200, 200), 255.0), _duct_cross, _walls, _boxes, _text_blobs],
    ids=["blank", "duct-cross", "walls", "boxes", "text"],
)
def test_lw_confirm_score_rejects_false_sites(builder):
    assert H._lw_confirm_score(builder(), "grille", 100, 100) < H.LW_CONFIRM_NCC


def test_confirm_label_lw_relabel_recovers_weight_varied_grille():
    # WiSARD called it diffuser, the NCC proposal named grille, the raw relabel
    # bar rejects the lw-2 glyph (0.74 < 0.76), the lw route recovers it
    g = _sheet_with("grille", lw=2)
    g[100:140, 93:108] = 0.0
    d = {"label": "diffuser", "ncc_cls": "grille", "cx": 100.0, "cy": 100.0}
    assert H._confirm_label(g, d) is True
    assert d["label"] == "grille"


def test_confirm_label_lw_route_disabled_reverts_to_baseline(monkeypatch):
    monkeypatch.setattr(H, "LW_RELABEL_NCC", None)
    g = _sheet_with("grille", lw=2)
    g[100:140, 93:108] = 0.0
    d = {"label": "diffuser", "ncc_cls": "grille", "cx": 100.0, "cy": 100.0}
    assert H._confirm_label(g, d) is False
    assert d["label"] == "diffuser"


def test_confirm_label_diffuser_side_never_routed(monkeypatch):
    # the route is grille-only: the measured losses are grille-side (#887)
    # and a diffuser-side route added a false diffuser on Clinic
    calls = []
    real = H._lw_confirm_score

    def spy(gray, cls, cx, cy):
        calls.append(cls)
        return real(gray, cls, cx, cy)

    monkeypatch.setattr(H, "_lw_confirm_score", spy)
    d = {"label": "diffuser", "cx": 100.0, "cy": 100.0}
    assert H._confirm_label(_duct_cross(), d) is False
    assert d["label"] == "diffuser"
    assert calls == []


def test_confirm_label_keep_path_unchanged_where_raw_confirms():
    g = _sheet_with("grille", lw=3)
    d = {"label": "grille", "ncc_cls": "grille", "cx": 100.0, "cy": 100.0}
    assert H._confirm_label(g, d) is True
    assert d["label"] == "grille"
