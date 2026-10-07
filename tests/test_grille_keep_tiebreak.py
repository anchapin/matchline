"""Grille/diffuser tie-break in detect_components (#730).

A proposal from the grille template at or above GRILLE_KEEP_NCC keeps the
grille label when WiSARD calls the crop a diffuser; below it, WiSARD wins.
"""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


def _run(monkeypatch, ncc):
    tmpl = np.zeros((10, 10))

    def fake_locate(gray, t, thresh=0.0):
        return [(50, 50, ncc)] if t is tmpl else []

    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index("diffuser")] = 5.0  # WiSARD says diffuser
        return s

    monkeypatch.setattr(H, "ncc_locate", fake_locate)
    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {c: (tmpl if c == "grille" else np.ones((10, 10))) for c in H.MECH_CLASSES}
    return H.detect_components(np.zeros((200, 200)), templates, clf=None)


def test_strong_grille_match_keeps_grille_label(monkeypatch):
    out = _run(monkeypatch, H.GRILLE_KEEP_NCC + 0.05)
    assert [d["label"] for d in out] == ["grille"]


@pytest.mark.parametrize("delta", [-0.05])
def test_weak_grille_match_defers_to_wisard(monkeypatch, delta):
    out = _run(monkeypatch, H.GRILLE_KEEP_NCC + delta)
    assert [d["label"] for d in out] == ["diffuser"]
