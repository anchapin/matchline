"""Stub-less grille pass in detect_components (#730).

Grilles drawn over a filled duct band score low against the stubbed grille
template; the tight (margin-free, stub-less) template finds them. A tight hit
with no detection nearby becomes a grille, and a diffuser detection sitting on
one is relabeled grille.
"""

import numpy as np
import pytest

import hvac_trace as H
import synth.mech as M


@pytest.fixture(autouse=True)
def _no_tight_confirm(monkeypatch):
    # These stubs place fake detections on a blank sheet; the #735 confirm gate
    # would drop them, so it is off here and covered in test_tight_confirm_gate.py.
    monkeypatch.setattr(H, "TIGHT_CONFIRM_NCC", None)


TIGHT = M.render_template("grille", margin_px=0, stubs=False)
STD = np.zeros((11, 11))  # stand-in for the stubbed templates; never TIGHT's shape


def _run(monkeypatch, diff_center, tight_center):
    assert STD.shape != TIGHT.shape
    th, tw = TIGHT.shape

    def fake_locate(gray, t, thresh=0.0):
        if t.shape == TIGHT.shape:
            cx, cy = tight_center
            return [(cy - th / 2, cx - tw / 2, 0.99)]
        if t is STD:
            cx, cy = diff_center
            return [(cy - 5.5, cx - 5.5, 0.9)]
        return []

    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index("diffuser")] = 5.0
        return s

    monkeypatch.setattr(H, "ncc_locate", fake_locate)
    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {c: (STD if c == "diffuser" else np.ones((9, 9))) for c in H.MECH_CLASSES}
    return H.detect_components(np.zeros((300, 300)), templates, clf=None)


def test_tight_hit_away_from_detections_adds_grille(monkeypatch):
    out = _run(monkeypatch, diff_center=(60, 60), tight_center=(200, 200))
    got = sorted((d["label"], round(d["cx"]), round(d["cy"])) for d in out)
    assert got == [("diffuser", 60, 60), ("grille", 200, 200)]


def test_tight_hit_on_diffuser_detection_relabels_it(monkeypatch):
    out = _run(monkeypatch, diff_center=(100, 100), tight_center=(101, 100))
    assert [d["label"] for d in out] == ["grille"]


def test_tight_pass_disabled(monkeypatch):
    monkeypatch.setattr(H, "GRILLE_TIGHT_NCC", None)
    out = _run(monkeypatch, diff_center=(60, 60), tight_center=(200, 200))
    assert [d["label"] for d in out] == ["diffuser"]
