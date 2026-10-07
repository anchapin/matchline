"""Unit tests for the Clinic detection harness (#707). No IFC needed."""

import numpy as np
import pytest

from scripts.validate_clinic_hvac_detection import (
    SheetFrame,
    match,
    render_storey,
    score_storey,
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
