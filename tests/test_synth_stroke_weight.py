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


def _top_wall_median(img, gt):
    poly = gt["rooms"][0]["polygon_px"]
    y_line = int(poly[0][1])
    xs = [p[0] for p in poly]
    return _probe_runs(img, y_line, int(min(xs)) + 5, int(max(xs)) - 5)


def test_sheet_styles_span_filled_and_hairline():
    medians = []
    for seed in range(20):
        img, gt = generate_sheet(seed)
        assert gt["symbols"], "every sheet carries symbols"
        medians.append(_top_wall_median(img, gt))
    assert any(m > 15 for m in medians), "no filled-wall sheets drawn"
    assert any(m <= 5 for m in medians), "no hairline sheets drawn"


def test_hairline_sheet_walls_are_thin():
    for seed in range(20):
        img, gt = generate_sheet(seed)
        if _top_wall_median(img, gt) <= 5:
            # hairline confirmed: probe a second room's wall too
            poly = gt["rooms"][-1]["polygon_px"]
            xs = [p[0] for p in poly]
            m = _probe_runs(img, int(poly[0][1]), int(min(xs)) + 5, int(max(xs)) - 5)
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
