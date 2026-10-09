"""Scale-tolerant diffuser/grille templates (#745).

A terminal drawn 15% smaller or larger than our template scores well below
the proposal and confirm thresholds against the one nominal template; the
scaled templates in ``TEMPLATE_SCALES`` find it.
"""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _resized(tmpl: np.ndarray, sc: float) -> np.ndarray:
    return H.template_bank(tmpl, (sc,))[0]


def _sheet_with(tmpl: np.ndarray, cx: int = 150, cy: int = 150, size: int = 300) -> np.ndarray:
    g = np.full((size, size), 255.0)
    th, tw = tmpl.shape
    g[cy - th // 2 : cy - th // 2 + th, cx - tw // 2 : cx - tw // 2 + tw] = tmpl
    return g


def test_template_bank_shapes_and_nominal_identity():
    t = M.render_template("diffuser", margin_px=0, stubs=False)
    bank = H.template_bank(t, (1.0, 0.87, 1.15))
    assert bank[0] is t
    h, w = t.shape
    assert bank[1].shape == (round(h * 0.87), round(w * 0.87))
    assert bank[2].shape == (round(h * 1.15), round(w * 1.15))


@pytest.mark.parametrize("sc", [0.85, 1.15])
def test_confirm_gate_keeps_rescaled_diffuser(sc):
    t = M.render_template("diffuser", margin_px=0, stubs=False).astype(np.float64)
    g = _sheet_with(_resized(t, sc))
    assert H._tight_confirm_score(g, "diffuser", 150, 150) >= H.TIGHT_CONFIRM_NCC


def test_grille_keeps_one_template_scale():
    # Scaled grille templates added false grilles on clean Clinic sheets (#854).
    assert "grille" not in H.TEMPLATE_SCALES
    assert len(H._tight_templates("grille")) == len(H.TIGHT_TURNS["grille"])


def test_rescaled_grille_confirm_falls_back_to_nominal(monkeypatch):
    # Opting grilles back in still works through the same table.
    monkeypatch.setattr(H, "TEMPLATE_SCALES", {"grille": (1.0, 0.87, 1.15)})
    t = M.render_template("grille", margin_px=0, stubs=False).astype(np.float64)
    g = _sheet_with(_resized(t, 1.15))
    assert H._tight_confirm_score(g, "grille", 150, 150) >= H.TIGHT_CONFIRM_NCC


@pytest.mark.parametrize("sc", [0.85, 1.15])
def test_nominal_template_alone_misses_rescaled_diffuser(monkeypatch, sc):
    # Guard: without the scaled templates the rescaled glyph fails the gate.
    monkeypatch.setattr(H, "TEMPLATE_SCALES", {})
    t = M.render_template("diffuser", margin_px=0, stubs=False).astype(np.float64)
    g = _sheet_with(_resized(t, sc))
    assert H._tight_confirm_score(g, "diffuser", 150, 150) < H.TIGHT_CONFIRM_NCC


@pytest.mark.parametrize("sc", [0.85, 1.15])
def test_rescaled_diffuser_is_proposed(sc):
    t = M.render_template("diffuser").astype(np.float64)
    g = _sheet_with(_resized(t, sc))
    hits = [
        (y + b.shape[0] / 2, x + b.shape[1] / 2)
        for b in H.template_bank(t, H.TEMPLATE_SCALES["diffuser"])
        for y, x, _ in H.ncc_locate(g, b, thresh=H.NCC_PROPOSE["diffuser"])
    ]
    assert any(abs(cy - 150) <= 3 and abs(cx - 150) <= 3 for cy, cx in hits)


def test_rescaled_grille_not_taken_for_diffuser():
    # Scaling must not let a grille pass the diffuser tight template.
    g = _sheet_with(
        _resized(M.render_template("grille", margin_px=0, stubs=False).astype(np.float64), 1.15)
    )
    best = max(
        H._local_ncc(g, d, 150, 150, H.TIGHT_CONFIRM_R_PX) for d in H._tight_templates("diffuser")
    )
    assert best < H.DIFFUSER_TIGHT_NCC


def _detect_diffuser(monkeypatch, gray):
    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index("diffuser")] = 5.0
        return s

    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {"diffuser": M.render_template("diffuser").astype(np.float64)}
    monkeypatch.setattr(H, "NCC_PROPOSE", {**H.NCC_PROPOSE})
    return [d for d in H.detect_components(gray, templates, clf=None) if d["label"] == "diffuser"]


@pytest.mark.parametrize("sc", [0.85, 1.15])
def test_rescaled_diffuser_detected_end_to_end(monkeypatch, sc):
    t = M.render_template("diffuser", margin_px=0, stubs=False).astype(np.float64)
    out = _detect_diffuser(monkeypatch, _sheet_with(_resized(t, sc)))
    assert [(round(d["cx"]), round(d["cy"])) for d in out if abs(d["cx"] - 150) <= 3] != []


@pytest.mark.parametrize("sc", [0.85, 1.15])
def test_rescaled_diffuser_lost_without_scales(monkeypatch, sc):
    monkeypatch.setattr(H, "TEMPLATE_SCALES", {})
    t = M.render_template("diffuser", margin_px=0, stubs=False).astype(np.float64)
    out = _detect_diffuser(monkeypatch, _sheet_with(_resized(t, sc)))
    assert [d for d in out if abs(d["cx"] - 150) <= 3 and abs(d["cy"] - 150) <= 3] == []
