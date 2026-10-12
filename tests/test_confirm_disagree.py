"""WiSARD and the NCC proposal disagree on grille vs diffuser (#745).

When WiSARD's label fails the tight confirm but the proposal's class scores at
least RELABEL_CONFIRM_NCC, the detection takes the proposal's class. The usual
cause is an outline drawn heavier or lighter than matchline's own glyph.
"""

import numpy as np
import pytest
from PIL import Image, ImageDraw

import hvac_trace as H
import synth.mech as M
from synth.mech import _GLYPH_FN

C = 150  # glyph centre on the 300 px sheet


def _sheet(cls=None, lw=None, lines=False):
    img = Image.new("L", (300, 300), 255)
    d = ImageDraw.Draw(img)
    if cls is not None:
        if lw is None:
            _GLYPH_FN[cls](d, C, C)
        else:
            _GLYPH_FN[cls](d, C, C, lw=lw)
    if lines:  # duct run with no glyph on it
        d.line([(60, C - 12), (240, C - 12)], fill=0, width=3)
        d.line([(60, C + 12), (240, C + 12)], fill=0, width=3)
    return np.asarray(img)


def _run(monkeypatch, gray, proposal_cls, wisard_cls, ncc=0.5):
    tmpl = np.zeros((10, 10))

    def fake_locate(g, t, thresh=0.0):
        return [(C - 5, C - 5, ncc)] if t is tmpl else []

    def fake_scores(clf, X):
        s = np.zeros((len(X), len(H.MECH_CLASSES) + 1))
        s[:, H.MECH_CLASSES.index(wisard_cls)] = 5.0
        return s

    monkeypatch.setattr(H, "ncc_locate", fake_locate)
    monkeypatch.setattr(H, "_logodds_scores", fake_scores)
    monkeypatch.setattr(M, "detection_crop", lambda g, x, y, c: np.zeros((8, 8)))
    templates = {c: (tmpl if c == proposal_cls else np.ones((10, 10))) for c in H.MECH_CLASSES}
    return H.detect_components(gray, templates, clf=None)


def test_relabel_bar_sits_above_the_confirm():
    assert H.RELABEL_CONFIRM_NCC > H.TIGHT_CONFIRM_NCC


@pytest.mark.parametrize("lw", [2, 4])
def test_reweighted_grille_called_diffuser_is_kept_as_grille(monkeypatch, lw):
    gray = _sheet("grille", lw=lw)
    assert H._tight_confirm_score(gray, "diffuser", C, C) < H.TIGHT_CONFIRM_NCC
    assert H._tight_confirm_score(gray, "grille", C, C) >= H.RELABEL_CONFIRM_NCC
    out = _run(monkeypatch, gray, "grille", "diffuser")
    assert [d["label"] for d in out] == ["grille"]


def test_grille_score_between_confirm_and_bar_is_not_relabelled(monkeypatch):
    gray = _sheet("grille", lw=2)
    s = H._tight_confirm_score(gray, "grille", C, C)
    monkeypatch.setattr(H, "RELABEL_CONFIRM_NCC", s + 0.01)
    assert _run(monkeypatch, gray, "grille", "diffuser") == []


def test_confirmed_wisard_label_is_never_changed(monkeypatch):
    out = _run(monkeypatch, _sheet("diffuser"), "grille", "diffuser")
    assert [d["label"] for d in out] == ["diffuser"]


def test_duct_lines_with_no_glyph_still_drop(monkeypatch):
    gray = _sheet(lines=True)
    assert H._tight_confirm_score(gray, "grille", C, C) < H.TIGHT_CONFIRM_NCC
    assert _run(monkeypatch, gray, "grille", "diffuser") == []


def test_no_relabel_without_a_disagreeing_proposal(monkeypatch):
    # The proposal and WiSARD both said diffuser: a failed diffuser confirm
    # drops it even though the grille template would confirm.
    out = _run(monkeypatch, _sheet("grille", lw=2), "diffuser", "diffuser")
    assert out == []
