"""End-to-end #744 acceptance, first slice: a synthetic mech sheet drawn in
the second drafting style (``style=1``) carries its own symbol legend, the
legend reader maps its rows, the template cutter cuts each mapped symbol at
its own size, and the detector's hits on that sheet are all true terminals
of the class the legend named. The FIRE DAMPER row (no HVAC class) is left
unmapped: nothing guesses a class from shape alone, and no hit lands inside
any legend symbol, the legend's own site included."""

import numpy as np

from hvac_legend import legend_templates, read_legend
from hvac_trace import detect_legend_symbols
from synth.mech import LEGEND_BAND_PX, MARGIN_M, PX_PER_M, generate_mech_sheet


def _sheet(seed: int):
    img, gt = generate_mech_sheet(seed, realistic=True, style=1, legend=True)
    gray = np.asarray(img, dtype=np.float64)
    return gray, gt


def test_legend_drawn_on_the_sheet_reads_back_mapped():
    gray, gt = _sheet(3)
    legs = read_legend(gt["legend"])
    assert len(legs) == 1
    rows = legs[0].rows
    assert [r.cls for r in rows] == ["diffuser", "grille", "vav", "ahu", "sensor", None]
    assert legs[0].unmapped[0].description == "FIRE DAMPER"


def test_templates_are_cut_at_the_drawn_symbol_size():
    gray, gt = _sheet(3)
    tpl = legend_templates(gray, read_legend(gt["legend"]), 1.0)
    shapes = {t.cls: t.image.shape for t in tpl}
    assert shapes["vav"] == (34, 64)  # VAV box 60x30 px + 2 pt pad
    assert shapes["ahu"] == (64, 104)  # AHU box 60x100 px + pad
    assert shapes["diffuser"] == (34, 34)
    assert shapes["grille"] == (24, 24)
    assert shapes["sensor"] == (20, 20)


def test_legend_hits_are_true_terminals_only():
    for seed in (3, 7, 11):
        gray, gt = _sheet(seed)
        legs = read_legend(gt["legend"])
        tpl = legend_templates(gray, legs, 1.0)
        assert len(tpl) == 5
        dets = detect_legend_symbols(gray, tpl, exclude_px=[])
        assert dets, f"seed {seed}: no legend-template hits at all"
        comp_px = [
            ((c["x_m"] + MARGIN_M) * PX_PER_M, (c["y_m"] + MARGIN_M) * PX_PER_M, c["type"])
            for c in gt["components"]
        ]
        for d in dets:
            hit = any(
                d["label"] == t and abs(d["cx"] - x) <= 30 and abs(d["cy"] - y) <= 30
                for x, y, t in comp_px
            )
            where = f"seed {seed}: legend hit {d['label']} @{d['cx']:.0f},{d['cy']:.0f}"
            assert hit, f"{where} matches no terminal"
            assert d["template"] == "legend"
        # no hit lands in the legend band, the legend's own symbols included
        band_top = gray.shape[0] - LEGEND_BAND_PX
        assert all(d["cy"] < band_top for d in dets)


def test_ducted_terminals_are_the_open_gap():
    """Known limitation (#744, measured): NCC at 0.80 on a clean-cut legend
    glyph finds isolated terminals but most ducted ones stay under the bar.
    This pins the honest state so the gap cannot silently close."""
    gray, gt = _sheet(3)
    tpl = legend_templates(gray, read_legend(gt["legend"]), 1.0)
    dets = detect_legend_symbols(gray, tpl, exclude_px=[])
    labels = {d["label"] for d in dets}
    assert "sensor" in labels  # wall-mounted, duct-free: found
    assert "vav" not in labels and "ahu" not in labels  # spine/trunk through the glyph
