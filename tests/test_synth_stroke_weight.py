"""Stroke-weight regimes in the synth sheet renderer (#743).

Real plan sets span thick poché walls and hairline outline walls; the
synth sheets now draw both (per-sheet rng choice) so the detector trains
across the rendering gap that zeroed clinic window recall on the
fixed-labeling retrain.
"""

import numpy as np
from PIL import Image, ImageDraw

from synth import symbols as sym
from synth.sheets import generate_sheet


def _probe_runs(img, y_line, x0, x1, step=7, half=20):
    """Median over columns of the longest dark run crossing a horizontal wall."""
    arr = np.asarray(img if isinstance(img, np.ndarray) else img.convert("L"))
    runs = []
    for x in range(x0, x1, step):
        col = arr[max(0, y_line - half) : y_line + half, x] < 128
        best = run = 0
        for v in col:
            run = run + 1 if v else 0
            best = max(best, run)
        runs.append(best)
    return float(np.median(runs))


def _edge_wall_median(img, gt, top: bool):
    """Median dark run through the footprint's top/bottom wall.

    Interior walls draw thin since #743 (their strokes may merge), so the
    stroke-regime probes key on exterior edges, which keep full thickness.
    """
    rooms = gt["rooms"]
    if top:
        i = int(np.argmin([min(p[1] for p in r["polygon_px"]) for r in rooms]))
        y_line = int(rooms[i]["polygon_px"][0][1])
    else:
        i = int(np.argmax([max(p[1] for p in r["polygon_px"]) for r in rooms]))
        y_line = int(rooms[i]["polygon_px"][2][1])
    xs = [p[0] for p in rooms[i]["polygon_px"]]
    return _probe_runs(img, y_line, int(min(xs)) + 5, int(max(xs)) - 5)


def test_sheet_styles_span_filled_and_hairline():
    medians = []
    for seed in range(20):
        img, gt = generate_sheet(seed)
        assert gt["symbols"], "every sheet carries symbols"
        medians.append(_edge_wall_median(img, gt, top=True))
    assert any(m > 15 for m in medians), "no filled-wall sheets drawn"
    assert any(m <= 5 for m in medians), "no hairline sheets drawn"


def test_hairline_sheet_walls_are_thin():
    for seed in range(20):
        img, gt = generate_sheet(seed)
        if _edge_wall_median(img, gt, top=True) <= 5:
            # hairline confirmed: probe the footprint's bottom edge too. It is
            # exterior, so it keeps full wall thickness and measures the pure
            # stroke width; interior walls now draw thin (#743) and their
            # strokes may merge into short solid runs by design.
            m = _edge_wall_median(img, gt, top=False)
            assert m <= 5, f"wall stroke too thick on hairline sheet {seed}: {m}"
            return
    raise AssertionError("no hairline sheet among seeds 0..19")


def test_window_glyph_stroke_widths_follow_lw():
    old_canvas = sym.CANVAS
    sym.CANVAS = 128
    try:
        for lw, want_max in ((1, 2), (4, 4)):
            img = Image.new("L", (128, 128), 255)
            d = ImageDraw.Draw(img)
            sym.glyph_window_plan(d, 64, 64, 84, lw, rng=None)
            col = np.asarray(img)[:, 64] < 128  # through both wall lines + glass
            best = run = 0
            for v in col:
                run = run + 1 if v else 0
                best = max(best, run)
            assert best <= want_max, f"lw={lw}: stroke run {best} > {want_max}"
    finally:
        sym.CANVAS = old_canvas


def test_door_glyph_hairline_wall_is_outline_band():
    old_canvas = sym.CANVAS
    sym.CANVAS = 128
    try:
        runs = {}
        for lw in (1, 4):
            img = Image.new("L", (128, 128), 255)
            d = ImageDraw.Draw(img)
            sym.glyph_single_door(d, 64, 64, 84, lw, rng=None)
            col = np.asarray(img)[:, 8] < 128  # left of the opening: wall only
            best = run = 0
            for v in col:
                run = run + 1 if v else 0
                best = max(best, run)
            runs[lw] = best
        assert runs[1] <= 2, f"hairline wall band drawn filled: run {runs[1]}"
        assert runs[4] >= 10, f"bold wall band lost its thickness: run {runs[4]}"
    finally:
        sym.CANVAS = old_canvas
