"""Alternative drafting styles in the tight passes and confirm gate (#745).

A return grille is often drawn as a box of parallel blades with no diagonal.
The diagonal-grille tight template misses it, so the tight pass never added
or relabeled it and the confirm gate could drop it.
"""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _sheet_with(tmpl: np.ndarray, cx: int = 150, cy: int = 150, size: int = 300) -> np.ndarray:
    g = np.full((size, size), 255.0)
    th, tw = tmpl.shape
    g[cy - th // 2 : cy - th // 2 + th, cx - tw // 2 : cx - tw // 2 + tw] = tmpl
    return g


HATCHED = M.render_template("grille", margin_px=0, stubs=False, style=1)
DIAG = M.render_template("grille", margin_px=0, stubs=False)


def test_render_template_style_draws_other_glyph():
    assert HATCHED.shape == DIAG.shape
    assert not np.array_equal(HATCHED, DIAG)
    # A class with no alternative falls back to its own glyph.
    assert np.array_equal(
        M.render_template("sensor", style=1), M.render_template("sensor", style=0)
    )


def test_tight_templates_include_hatched_grille():
    assert any(np.array_equal(t, HATCHED) for t in H._tight_templates("grille"))
    # Diffusers keep one style.
    assert H.TEMPLATE_STYLES.get("diffuser", (0,)) == (0,)


def test_confirm_gate_keeps_hatched_grille():
    g = _sheet_with(HATCHED)
    assert H._tight_confirm_score(g, "grille", 150, 150) >= H.TIGHT_CONFIRM_NCC


def test_hatched_grille_fails_gate_without_style(monkeypatch):
    # Guard: with matchline's own glyph only, the hatched grille is lost.
    monkeypatch.setattr(H, "TEMPLATE_STYLES", {})
    g = _sheet_with(HATCHED)
    assert H._tight_confirm_score(g, "grille", 150, 150) < H.TIGHT_CONFIRM_NCC


@pytest.mark.parametrize("k", [0, 1])
def test_tight_pass_finds_hatched_grille(k):
    g = _sheet_with(np.rot90(HATCHED, k).copy())
    hits = [
        (y + t.shape[0] / 2, x + t.shape[1] / 2)
        for t in H._tight_templates("grille")
        for y, x, _ in H.ncc_locate(g, t, thresh=H.GRILLE_TIGHT_NCC)
    ]
    assert any(abs(cy - 150) <= 3 and abs(cx - 150) <= 3 for cy, cx in hits)


def test_diffuser_x_not_taken_for_hatched_grille():
    dif = M.render_template("diffuser", margin_px=0, stubs=False)
    g = _sheet_with(dif)
    best = max(
        H._local_ncc(g, t, 150, 150, H.TIGHT_CONFIRM_R_PX)
        for k in (0, 1)
        for t in [np.rot90(HATCHED, k).copy()]
    )
    assert best < H.GRILLE_TIGHT_NCC


def test_validation_script_alt_glyph_is_synth_glyph():
    from scripts.validate_clinic_hvac_detection import varied_glyph

    a = np.asarray(varied_glyph("grille", 1)) < 128
    b = np.asarray(varied_glyph("grille", 0)) < 128
    assert a.sum() > 0 and not np.array_equal(a, b)
    assert M._ALT_GLYPH_FN["grille"] is M._draw_grille_hatched


def _stair(n_treads: int = 12, tread_px: int = 14, width_px: int = 60) -> np.ndarray:
    """A plan stair run: a box with evenly spaced tread lines (no grille)."""
    from PIL import Image, ImageDraw

    h = n_treads * tread_px
    im = Image.new("L", (width_px + 8, h + 8), 255)
    d = ImageDraw.Draw(im)
    d.rectangle([4, 4, 4 + width_px, 4 + h], outline=0, width=3)
    for i in range(1, n_treads):
        d.line([4, 4 + i * tread_px, 4 + width_px, 4 + i * tread_px], fill=0, width=1)
    return np.asarray(im, dtype=np.float64)


@pytest.mark.parametrize("tread_px", [8, 11, 14])
def test_stair_treads_not_taken_for_hatched_grille(tread_px):
    # Parallel tread lines in a box are the closest real look-alike.
    s = _stair(tread_px=tread_px)
    g = np.full((400, 400), 255.0)
    g[20 : 20 + s.shape[0], 150 : 150 + s.shape[1]] = s
    hits = [
        h
        for t in H._tight_templates("grille")
        for h in H.ncc_locate(g, t, thresh=H.GRILLE_TIGHT_NCC)
    ]
    assert hits == []
