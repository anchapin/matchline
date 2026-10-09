"""Unit tests for the Clinic detection harness (#707). No IFC needed."""

import numpy as np
import pytest

from scripts.validate_clinic_hvac_detection import (
    VARY_SHIFT_PX,
    SheetFrame,
    match,
    render_storey,
    score_storey,
    varied_glyph,
)


def _storey():
    room_a = {
        "id": "A",
        "name": "101",
        "long_name": "OFFICE",
        "polygon_m": [[0, 0], [5, 0], [5, 4], [0, 4]],
    }
    room_b = {
        "id": "B",
        "name": "102",
        "long_name": "EXAM",
        "polygon_m": [[5, 0], [10, 0], [10, 4], [5, 4]],
    }
    return {
        "rooms": [room_a, room_b],
        "terminals": [
            {"id": "t1", "cls": "diffuser", "x": 2.5, "y": 2.0, "gt_space": "A"},
            {"id": "t2", "cls": "grille", "x": 7.5, "y": 2.0, "gt_space": "B"},
            {"id": "t3", "cls": "diffuser", "x": 8.0, "y": 3.0, "gt_space": "B"},
        ],
        "ducts": [[[1.0, 1.0], [4.0, 1.0], [4.0, 1.2], [1.0, 1.2]]],
    }


def test_sheet_frame_round_trip_flips_y():
    fr = SheetFrame(-10.0, 0.0, 5.0, 20.0, 50.0, 2.0)
    assert fr.to_px(-10.0, 20.0) == pytest.approx((100.0, 100.0))  # top-left corner
    px, py = fr.to_px(-3.2, 7.5)
    assert fr.to_m(px, py) == pytest.approx((-3.2, 7.5))
    assert fr.to_px(-3.2, 6.0)[1] > fr.to_px(-3.2, 7.5)[1]  # plan y up = image y down


def test_render_puts_glyphs_on_terminals_and_ducts_as_fill():
    img, fr = render_storey(_storey())
    g = np.asarray(img)
    cx, cy = (int(round(v)) for v in fr.to_px(2.5, 2.0))
    half = int(0.6 * 50 / 2)  # DIF_S square edge
    assert g[cy, cx - half - 1 : cx - half + 2].min() == 0  # left edge of the diffuser box
    assert g[cy - 5, cx - 10] == 255  # inside the box, off the diagonals: white
    dx, dy = (int(round(v)) for v in fr.to_px(2.5, 1.1))
    assert g[dy, dx] == 0  # duct footprint is filled


def test_match_is_per_class_and_within_tolerance():
    terms = _storey()["terminals"]
    dets = [
        {"label": "diffuser", "x": 2.6, "y": 2.1},  # t1
        {"label": "diffuser", "x": 7.5, "y": 2.0},  # on the grille: wrong class
        {"label": "diffuser", "x": 8.0, "y": 3.8},  # 0.8 m from t3: too far
    ]
    assert list(match(dets, terms, "diffuser")) == [0]


def test_score_storey_counts_detection_and_room_outcomes():
    dets = [
        {"label": "diffuser", "x": 2.6, "y": 2.1},  # t1, room A: correct
        {"label": "grille", "x": 7.4, "y": 2.0},  # t2, room B: correct
        {"label": "vav", "x": 4.0, "y": 3.0},  # false positive, other class
    ]
    r = score_storey(_storey(), dets)
    assert r["det"]["diffuser"] == {"tp": 1, "fp": 0, "fn": 1}
    assert r["det"]["grille"] == {"tp": 1, "fp": 0, "fn": 0}
    assert r["other_labels"] == {"vav": 1}
    assert r["end_to_end"] == {"correct": 2, "missed": 1}


def _ink_bbox(img):
    g = np.asarray(img)
    ys, xs = np.nonzero(g < 128)
    return xs.min(), ys.min(), xs.max(), ys.max()


def test_clean_render_is_the_default():
    a, _ = render_storey(_storey())
    b, _ = render_storey(_storey(), vary=None)
    assert a.tobytes() == b.tobytes()
    assert not hasattr(_, "shift_px")  # no misregistration on a clean sheet


def test_varied_render_is_seeded_and_deterministic():
    a, fa = render_storey(_storey(), vary=7)
    b, fb = render_storey(_storey(), vary=7)
    c, _ = render_storey(_storey(), vary=8)
    assert a.tobytes() == b.tobytes() and fa.shift_px == fb.shift_px
    assert a.tobytes() != c.tobytes()
    assert all(abs(v) <= VARY_SHIFT_PX for v in fa.shift_px)
    assert len(np.unique(np.asarray(a))) > 2  # scan noise / JPEG grey levels


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_varied_terminals_still_sit_on_their_positions(seed):
    img, fv = render_storey(_storey(), vary=seed)
    g = np.asarray(img).astype(float)
    for t in _storey()["terminals"]:
        cx, cy = (int(round(v)) for v in fv.to_px(t["x"], t["y"]))
        box = g[cy - 20 : cy + 21, cx - 20 : cx + 21]  # grille half side is 10 px
        assert (box < 128).sum() > 60, t["id"]  # a symbol's worth of ink there


def test_alternative_glyph_style_differs_but_keeps_the_footprint():
    for cls in ("diffuser", "grille"):
        a, b = varied_glyph(cls, 0), varied_glyph(cls, 1)
        assert a.size == b.size
        assert np.abs(np.asarray(a, float) - np.asarray(b, float)).mean() > 2
        ax, bx = _ink_bbox(a), _ink_bbox(b)
        assert all(abs(p - q) <= 2 for p, q in zip(ax, bx)), (cls, ax, bx)


def test_glyph_scale_and_rotation():
    w0 = _ink_bbox(varied_glyph("diffuser", 0, scale=1.0))
    w1 = _ink_bbox(varied_glyph("diffuser", 0, scale=1.15))
    assert (w1[2] - w1[0]) / (w0[2] - w0[0]) == pytest.approx(1.15, abs=0.05)
    # the hatched grille's middle blade runs across the centre; a quarter turn
    # stands it up along the centre column
    flat = np.asarray(varied_glyph("grille", 1)) < 128
    turned = np.asarray(varied_glyph("grille", 1, angle_deg=90)) < 128
    c = flat.shape[0] // 2
    assert turned[c - 6 : c + 6, c].sum() > flat[c - 6 : c + 6, c].sum()
    assert flat[c, c - 6 : c + 6].sum() > turned[c, c - 6 : c + 6].sum()
