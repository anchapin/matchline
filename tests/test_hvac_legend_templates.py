"""One-shot templates cut from the sheet's own symbol legend (#744)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.append(str(Path(__file__).resolve().parent))

from pdf_fixtures import PageSpec, line, rect, text, write_pdf  # noqa: E402

from hvac_legend import Legend, LegendRow, legend_templates, read_legend  # noqa: E402
from hvac_trace import (  # noqa: E402
    ASSOC_PX,
    NCC_PROPOSE,
    detect_legend_symbols,
    merge_legend_detections,
    ncc_locate,
)
from pdf_ingest import ingest_pdf  # noqa: E402
from synth.mech import render_template  # noqa: E402

PAGE_W, PAGE_H = 1224.0, 792.0
PITCH = 25


def xbox(x, y, s=14):
    """Second-style diffuser: a square with a smaller square inside (a
    four-way diffuser face; matchline's built-in glyph is a square with an X).
    pdf y up."""
    return rect(x, y, s, s) + rect(x + 4, y + 4, s - 8, s - 8)


def circle(cx, cy, r=7):
    """Second-style thermostat: a triangle (matchline's built-in is a circle
    with a T). pdf y up; centred on (cx, cy)."""
    a, b, c = (cx - r, cy - r), (cx + r, cy - r), (cx, cy + r)
    return line(*a, *b) + line(*b, *c) + line(*c, *a)


def damper(x, y):
    """A legend row the detector has no class for: a line with a bar."""
    return line(x, y + 5, x + 16, y + 5) + line(x + 8, y, x + 8, y + 10)


DIFFUSERS = [(120, 600), (300, 420), (520, 250)]  # pdf pt, lower-left
SENSORS = [(200, 300), (450, 520)]  # pdf pt, centre


def _content(legend_rows=True, diffusers=DIFFUSERS, sensors=SENSORS):
    out = ""
    if legend_rows:
        x, y = 900, 720
        out += text(x, y, "HVAC SYMBOL LEGEND", 12)
        y -= 2 * PITCH
        out += xbox(x, y - 4) + text(x + 40, y, "SUPPLY AIR DIFFUSER", 8)
        y -= PITCH
        out += circle(x + 7, y + 3) + text(x + 40, y, "THERMOSTAT", 8)
        y -= PITCH
        out += damper(x, y - 2) + text(x + 40, y, "MANUAL VOLUME DAMPER", 8)
    for x, y in diffusers:
        out += xbox(x, y)
    for cx, cy in sensors:
        out += circle(cx, cy)
    # room outlines so the sheet is not just symbols
    out += rect(80, 200, 300, 450, lw=2) + rect(380, 200, 300, 450, lw=2)
    return out


def _sheet(tmp_path, content):
    pdf = write_pdf(tmp_path / "s.pdf", [PageSpec(width=PAGE_W, height=PAGE_H, content=content)])
    ingest_pdf(pdf, out_dir=tmp_path / "out")
    sheet = json.loads((tmp_path / "out" / "sheet_001.json").read_text())
    gray = np.asarray(Image.open(tmp_path / "out" / sheet["raster_file"]), dtype=np.float64)
    return sheet, gray


def _px(sheet, x_pt, y_pt_up):
    """pdf pt (y up) -> raster px (y down)."""
    k = sheet["px_per_pt"]
    return x_pt * k, (PAGE_H - y_pt_up) * k


def _expected(sheet):
    diff = [_px(sheet, x + 7, y + 7) for x, y in DIFFUSERS]
    sens = [_px(sheet, cx, cy) for cx, cy in SENSORS]
    return diff, sens


def _matched(dets, label, truth, tol):
    pts = [(d["cx"], d["cy"]) for d in dets if d["label"] == label]
    return sum(1 for tx, ty in truth if any(np.hypot(px - tx, py - ty) <= tol for px, py in pts))


def test_templates_are_cut_for_mapped_rows_only(tmp_path):
    sheet, gray = _sheet(tmp_path, _content())
    legends = read_legend(sheet)
    assert [r.cls for r in legends[0].rows] == ["diffuser", "sensor", None]
    tmpls = legend_templates(gray, legends, sheet["px_per_pt"])
    assert [t.cls for t in tmpls] == ["diffuser", "sensor"]
    for t in tmpls:
        assert t.image.shape[0] >= 10 and t.image.shape[1] >= 10
        assert (t.image < 128).sum() >= 12


def test_legend_templates_find_a_second_symbol_style(tmp_path):
    sheet, gray = _sheet(tmp_path, _content())
    tmpls = legend_templates(gray, read_legend(sheet), sheet["px_per_pt"])
    dets = detect_legend_symbols(gray, tmpls)
    diff, sens = _expected(sheet)
    tol = 4 * sheet["px_per_pt"]
    assert _matched(dets, "diffuser", diff, tol) == 3
    assert _matched(dets, "sensor", sens, tol) == 2
    assert len(dets) == 5  # no hit on the legend's own symbols or the damper
    assert all(d["template"] == "legend" for d in dets)
    assert {d["legend_row"] for d in dets} == {"SUPPLY AIR DIFFUSER", "THERMOSTAT"}


def test_builtin_templates_miss_the_second_style(tmp_path):
    """The measured gap: matchline's own glyphs do not propose the firm's
    diffusers or thermostats at their proposal thresholds."""
    sheet, gray = _sheet(tmp_path, _content())
    diff, sens = _expected(sheet)
    tol = 4 * sheet["px_per_pt"]
    for cls, truth in (("diffuser", diff), ("sensor", sens)):
        tmpl = render_template(cls, margin_px=0, stubs=False)
        hits = ncc_locate(gray, tmpl, thresh=NCC_PROPOSE[cls])
        th, tw = tmpl.shape
        pts = [(x + tw / 2, y + th / 2) for y, x, _ in hits]
        found = sum(
            1 for tx, ty in truth if any(np.hypot(px - tx, py - ty) <= tol for px, py in pts)
        )
        assert found == 0, cls


def test_no_legend_no_templates(tmp_path):
    sheet, gray = _sheet(tmp_path, _content(legend_rows=False))
    assert read_legend(sheet) == []
    assert legend_templates(gray, [], sheet["px_per_pt"]) == []
    assert detect_legend_symbols(gray, []) == []


def test_speck_and_off_sheet_rows_are_skipped():
    gray = np.full((100, 100), 255.0)
    gray[10:12, 10:12] = 0  # 4 ink px
    legs = [
        Legend(
            "LEGEND",
            [0, 0, 1, 1],
            [
                LegendRow("SUPPLY AIR DIFFUSER", "diffuser", [9, 9, 13, 13]),
                LegendRow("THERMOSTAT", "sensor", [500, 500, 510, 510]),
                LegendRow("VAV BOX", "vav", []),
            ],
        )
    ]
    assert legend_templates(gray, legs, 1.0) == []


def test_merge_keeps_builtin_only_for_classes_the_legend_does_not_draw():
    legend = [{"label": "diffuser", "cx": 100.0, "cy": 100.0, "template": "legend"}]
    builtin = [
        {"label": "diffuser", "cx": 400.0, "cy": 400.0},  # class the legend draws
        {"label": "grille", "cx": 100.0 + ASSOC_PX / 2, "cy": 100.0},  # on a legend hit
        {"label": "vav", "cx": 700.0, "cy": 300.0},  # fallback class
    ]
    out = merge_legend_detections(builtin, legend, {"diffuser", "sensor"})
    assert [(d["label"], d["template"]) for d in out] == [
        ("diffuser", "legend"),
        ("vav", "builtin"),
    ]
