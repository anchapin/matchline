"""#744: duct tracing assigns a legend symbol to the terminal whose ducts it
sits on (``hvac_trace.trace_symbols_to_terminals``), and never guesses."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from hvac_trace import trace_symbols_to_terminals

W, H = 400, 240
LINE_W = 16  # wide enough to survive duct_skeleton's opening kernel


def _img(draw=lambda d: None):
    img = Image.new("L", (W, H), 255)
    draw(ImageDraw.Draw(img))
    return np.asarray(img.convert("L"), dtype=np.float64)


def _det(x, y):
    return {"cx": x, "cy": y}


def test_a_symbol_on_one_terminal_branch_goes_to_that_terminal():
    def draw(d):
        d.rectangle([20, 60, 60, 100], fill=0)  # terminal A tag box
        d.rectangle([20, 140, 60, 180], fill=0)  # terminal B tag box
        d.line([60, 80, 300, 80], fill=0, width=LINE_W)  # A's duct east
        d.line([300, 80, 300, 20], fill=0, width=LINE_W)  # branch to diffuser 1
        d.line([60, 160, 200, 160], fill=0, width=LINE_W)  # B's duct
        d.line([200, 160, 200, 220], fill=0, width=LINE_W)  # branch to diffuser 2

    gray = _img(draw)
    rects = [(20, 60, 60, 100), (20, 140, 60, 180)]
    out = trace_symbols_to_terminals(gray, [_det(300, 20), _det(200, 220)], rects)
    assert out == [(0, "duct"), (1, "duct")]


def test_a_symbol_between_two_terminals_on_one_run_is_contested():
    def draw(d):
        d.rectangle([20, 80, 60, 120], fill=0)
        d.rectangle([320, 80, 360, 120], fill=0)
        d.line([60, 100, 320, 100], fill=0, width=LINE_W)

    gray = _img(draw)
    rects = [(20, 80, 60, 120), (320, 80, 360, 120)]
    # near A the nearest sources is A; midway the two duct distances tie
    out = trace_symbols_to_terminals(gray, [_det(100, 100), _det(190, 100)], rects)
    assert out == [(0, "duct"), (None, "contested")]


def test_a_symbol_off_the_ducts_is_never_assigned():
    def draw(d):
        d.rectangle([20, 80, 60, 120], fill=0)
        d.line([60, 100, 200, 100], fill=0, width=LINE_W)

    gray = _img(draw)
    out = trace_symbols_to_terminals(gray, [_det(330, 40)], [(20, 80, 60, 120)])
    assert out == [(None, "no_duct")]


def test_no_terminals_or_no_ducts_assign_nothing():
    gray = _img(lambda d: d.line([60, 100, 200, 100], fill=0, width=LINE_W))
    assert trace_symbols_to_terminals(gray, [_det(100, 100)], []) == [(None, "no_terminals")]

    blank = _img()
    assert trace_symbols_to_terminals(blank, [_det(200, 100)], [(20, 80, 60, 120)]) == [
        (None, "no_ducts")
    ]
    # a terminal tag alone leaves its own ink in the mask; a symbol with no
    # skeleton pixel within reach is still not assigned
    lone = _img(lambda d: d.rectangle([20, 80, 60, 120], fill=0))
    assert trace_symbols_to_terminals(lone, [_det(200, 100)], [(20, 80, 60, 120)]) == [
        (None, "no_duct")
    ]
