"""Quarter-turned grilles in the tight passes and confirm gate (#745).

A quarter turn sends the grille's one diagonal the other way. The 0-turn
tight template then misses it, so a turned grille that WiSARD calls a
diffuser was never relabeled and could fail the confirm gate.
"""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _sheet_with(tmpl: np.ndarray, cx: int, cy: int, size: int = 300) -> np.ndarray:
    g = np.full((size, size), 255.0)
    th, tw = tmpl.shape
    g[cy - th // 2 : cy - th // 2 + th, cx - tw // 2 : cx - tw // 2 + tw] = tmpl
    return g


TIGHT = M.render_template("grille", margin_px=0, stubs=False).astype(np.float64)
TURNED = np.rot90(TIGHT).copy()


def test_turned_grille_differs_from_template():
    # Guard: the test is meaningless if the glyph were turn-symmetric.
    g = _sheet_with(TURNED, 150, 150)
    assert H._local_ncc(g, TIGHT, 150, 150, H.TIGHT_CONFIRM_R_PX) < H.GRILLE_TIGHT_NCC


def test_tight_templates_cover_quarter_turn():
    ts = H._tight_templates("grille")
    assert len(ts) == 2
    assert any(
        np.array_equal(t, np.rot90(M.render_template("grille", margin_px=0, stubs=False)))
        for t in ts
    )
    assert len(H._tight_templates("diffuser")) == 1


def test_confirm_gate_keeps_turned_grille():
    g = _sheet_with(TURNED, 150, 150)
    assert H._tight_confirm_score(g, "grille", 150, 150) >= H.TIGHT_CONFIRM_NCC


def test_turned_grille_does_not_pass_as_diffuser_tight():
    # The relabel back to diffuser must not fire on a turned grille.
    g = _sheet_with(TURNED, 150, 150)
    d = M.render_template("diffuser", margin_px=0, stubs=False)
    assert H._local_ncc(g, d, 150, 150, H.TIGHT_CONFIRM_R_PX) < H.DIFFUSER_TIGHT_NCC


def test_diffuser_not_taken_for_turned_grille():
    d = M.render_template("diffuser", margin_px=0, stubs=False).astype(np.float64)
    g = _sheet_with(d, 150, 150)
    assert H._local_ncc(g, TURNED, 150, 150, H.TIGHT_CONFIRM_R_PX) < H.GRILLE_TIGHT_NCC


def _detect(monkeypatch, gray):
    std = np.zeros((11, 11))
    real_locate = H.ncc_locate

    def fake_locate(g, t, thresh=0.0):
        if t is std:  # WiSARD-side proposal at the glyph, labeled diffuser below
            return [(150 - 5.5, 150 - 5.5, 0.9)]
        if t.shape == TIGHT.shape:
            return real_locate(g, t, thresh)
        return []

    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index("diffuser")] = 5.0
        return s

    monkeypatch.setattr(H, "ncc_locate", fake_locate)
    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {c: (std if c == "diffuser" else np.ones((9, 9))) for c in H.MECH_CLASSES}
    return H.detect_components(gray, templates, clf=None)


def test_turned_grille_called_diffuser_is_relabeled(monkeypatch):
    out = _detect(monkeypatch, _sheet_with(TURNED, 150, 150))
    assert [(d["label"], round(d["cx"]), round(d["cy"])) for d in out] == [("grille", 150, 150)]


@pytest.mark.parametrize("glyph", ["upright", "turned"])
def test_without_turns_only_upright_grille_survives(monkeypatch, glyph):
    monkeypatch.setattr(H, "TIGHT_TURNS", {})
    out = _detect(monkeypatch, _sheet_with(TIGHT if glyph == "upright" else TURNED, 150, 150))
    labels = [d["label"] for d in out]
    assert labels == (["grille"] if glyph == "upright" else [])
