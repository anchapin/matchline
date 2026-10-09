"""Mechanical tags on plans and sheet registration (#746)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from grid_detect import GridLine, GridSet
from mech_tags import find_tags, register, room_of, to_canonical


def _t(s, x=0, y=0):
    return {"text": s, "bbox": [x, y, x + 20, y + 8]}


def _grid(v, h):
    lines = [GridLine(k, "v", c, (0, 1), True, [], 0.9) for k, c in v.items()]
    lines += [GridLine(k, "h", c, (0, 1), True, [], 0.9) for k, c in h.items()]
    return GridSet("s", lines)


SHEET = {"width_pt": 1728, "height_pt": 1152}


def test_find_tags_matches_whole_tag_or_first_word():
    sh = {"text": [_t("VAV-1"), _t("vav 2"), _t("VAV-3 450 CFM"), _t("VAV-10"), _t("ROOM")]}
    got = [h["tag"] for h in find_tags(sh, {"VAV-1", "VAV2", "VAV-3"})]
    assert got == ["VAV-1", "VAV2", "VAV-3"]


def test_grid_registration_gives_the_offset():
    a = _grid({"1": 100, "2": 300}, {"A": 200})
    m = _grid({"1": 150, "2": 350}, {"A": 180})
    dx, dy, how, conf, why = register(SHEET, SHEET, 0.1, 0.1, m, a)
    assert (how, conf) == ("grid", 0.85)
    assert dx == pytest.approx(-5.0) and dy == pytest.approx(2.0)
    assert why == "3 shared grid lines"


def test_grid_registration_handles_a_different_scale():
    a = _grid({"1": 100}, {"A": 200})
    m = _grid({"1": 50}, {"A": 100})  # half the scale: 0.2 m per pt
    dx, dy, how, _c, _w = register(SHEET, SHEET, 0.2, 0.1, m, a)
    assert how == "grid" and dx == pytest.approx(0) and dy == pytest.approx(0)


def test_disagreeing_grid_does_not_register():
    a = _grid({"1": 100, "2": 300}, {"A": 200})
    m = _grid({"1": 100, "2": 320}, {"A": 200})
    assert register(SHEET, SHEET, 0.1, 0.1, m, a)[2] is None


def test_frame_fallback_needs_same_page_and_scale():
    assert register(SHEET, SHEET, 0.1, 0.1)[2:4] == ("frame", 0.6)
    assert register(SHEET, SHEET, 0.05, 0.1)[2] is None
    assert register({"width_pt": 1224, "height_pt": 792}, SHEET, 0.1, 0.1)[2] is None


def test_room_of_needs_exactly_one_room():
    sq = SimpleNamespace(id="A", polygon_m=[[0, 0], [2, 0], [2, 2], [0, 2]])
    sq2 = SimpleNamespace(id="B", polygon_m=[[1, 1], [3, 1], [3, 3], [1, 3]])
    assert room_of((0.5, 0.5), [sq, sq2]) == "A"
    assert room_of((1.5, 1.5), [sq, sq2]) is None
    assert room_of((5, 5), [sq, sq2]) is None
    assert to_canonical([0, 100, 0, 100], 0.1, 1.0, 0.0, 200, 0.1) == (1.0, -10.0)
