"""Stub-less diffuser pass in detect_components (#730).

Diffusers drawn over a filled duct band can be proposed by the grille template
and labeled grille. The tight (margin-free, stub-less) diffuser template scores
1.00 on diffusers and well under 0.5 on grilles, so a grille detection sitting
on a tight diffuser match is relabeled diffuser.
"""

import numpy as np

import hvac_trace as H
import synth.mech as M

TIGHT_D = M.render_template("diffuser", margin_px=0, stubs=False)
TIGHT_G = M.render_template("grille", margin_px=0, stubs=False)
STD = np.zeros((11, 11))  # stand-in for the stubbed grille template


def _run(monkeypatch, grille_center, tight_center):
    assert STD.shape not in (TIGHT_D.shape, TIGHT_G.shape)
    th, tw = TIGHT_D.shape

    def fake_locate(gray, t, thresh=0.0):
        if t.shape == TIGHT_D.shape:
            cx, cy = tight_center
            return [(cy - th / 2, cx - tw / 2, 0.99)]
        if t is STD:
            cx, cy = grille_center
            return [(cy - 5.5, cx - 5.5, 0.6)]
        return []

    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index("grille")] = 5.0
        return s

    monkeypatch.setattr(H, "ncc_locate", fake_locate)
    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {c: (STD if c == "grille" else np.ones((9, 9))) for c in H.MECH_CLASSES}
    return H.detect_components(np.zeros((300, 300)), templates, clf=None)


def test_tight_diffuser_hit_on_grille_detection_relabels_it(monkeypatch):
    out = _run(monkeypatch, grille_center=(100, 100), tight_center=(100, 101))
    assert [d["label"] for d in out] == ["diffuser"]


def test_tight_diffuser_hit_away_from_detections_adds_diffuser(monkeypatch):
    out = _run(monkeypatch, grille_center=(60, 60), tight_center=(200, 200))
    got = sorted((d["label"], round(d["cx"]), round(d["cy"])) for d in out)
    assert got == [("diffuser", 200, 200), ("grille", 60, 60)]


def test_diffuser_tight_pass_disabled(monkeypatch):
    monkeypatch.setattr(H, "DIFFUSER_TIGHT_NCC", None)
    out = _run(monkeypatch, grille_center=(100, 100), tight_center=(100, 101))
    assert [d["label"] for d in out] == ["grille"]
