"""Mechanical symbol legend read off a vector sheet (#744)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.append(str(Path(__file__).resolve().parent))

from pdf_fixtures import PageSpec, line, rect, text, write_pdf  # noqa: E402

from hvac_legend import classify, read_legend  # noqa: E402
from pdf_ingest import ingest_pdf  # noqa: E402

PAGE_W, PAGE_H = 1224.0, 792.0
PITCH = 25

ROWS = [
    "SUPPLY AIR DIFFUSER",
    "RETURN AIR GRILLE",
    "VAV BOX",
    "THERMOSTAT",
    "MANUAL VOLUME DAMPER",
]


def legend(x=800, ytop=720, rows=ROWS, wrap=None, title="HVAC SYMBOL LEGEND"):
    """A legend: title, a SYMBOL / DESCRIPTION header, then one drawn symbol
    (a 20 x 12 box with a diagonal) left of each description. ``wrap``
    (index, text) adds a symbol-less continuation line under that row."""
    out = text(x, ytop, title, 12) + text(x, ytop - PITCH, "SYMBOL", 8)
    out += text(x + 40, ytop - PITCH, "DESCRIPTION", 8)
    y = ytop - 2 * PITCH
    for i, desc in enumerate(rows):
        out += rect(x, y - 2, 20, 10) + line(x, y - 2, x + 20, y + 8)
        out += text(x + 40, y, desc, 8)
        y -= PITCH
        if wrap and wrap[0] == i:
            out += text(x + 40, y, wrap[1], 8)
            y -= PITCH
    return out


def _sheet(tmp_path, content):
    pdf = write_pdf(tmp_path / "s.pdf", [PageSpec(width=PAGE_W, height=PAGE_H, content=content)])
    ingest_pdf(pdf, out_dir=tmp_path / "out")
    return json.loads((tmp_path / "out" / "sheet_001.json").read_text())


@pytest.mark.parametrize(
    "desc, cls",
    [
        ("SUPPLY AIR DIFFUSER", "diffuser"),
        ("LINEAR SLOT DIFFUSER", "diffuser"),
        ("RETURN AIR GRILLE", "grille"),
        ("EXHAUST REGISTER", "grille"),
        ("RETURN AIR DIFFUSER", "grille"),
        ("GRILLE, TRANSFER", "grille"),
        ("VAV BOX", "vav"),
        ("VARIABLE AIR VOLUME TERMINAL UNIT", "vav"),
        ("AIR TERMINAL UNIT", "vav"),
        ("AIR TERMINAL", None),
        ("AIR HANDLING UNIT", "ahu"),
        ("THERMOSTAT", "sensor"),
        ("SPACE TEMPERATURE SENSOR", "sensor"),
        ("SUPPLY GRILLE", None),
        ("MANUAL VOLUME DAMPER", None),
        ("DUCT SMOKE DETECTOR", None),
    ],
)
def test_classify_reads_the_description_only(desc, cls):
    assert classify(desc) == cls


def test_legend_rows_and_symbol_boxes(tmp_path):
    (leg,) = read_legend(_sheet(tmp_path, legend()))
    assert leg.title == "HVAC SYMBOL LEGEND"
    assert [(r.description, r.cls) for r in leg.rows] == [
        ("SUPPLY AIR DIFFUSER", "diffuser"),
        ("RETURN AIR GRILLE", "grille"),
        ("VAV BOX", "vav"),
        ("THERMOSTAT", "sensor"),
        ("MANUAL VOLUME DAMPER", None),
    ]
    x0, y0, x1, y1 = leg.rows[0].symbol_bbox_pt
    # the 20 x 12 symbol left of the first description, sheet y down
    assert (x0, x1) == pytest.approx((800, 820), abs=1.5)
    assert y1 - y0 == pytest.approx(10, abs=1.5)
    assert PAGE_H - y1 == pytest.approx(720 - 2 * PITCH - 2, abs=1.5)
    assert [r.description for r in leg.unmapped] == ["MANUAL VOLUME DAMPER"]


def test_wrapped_description_joins_its_row(tmp_path):
    rows = ["SUPPLY", "THERMOSTAT"]
    (leg,) = read_legend(_sheet(tmp_path, legend(rows=rows, wrap=(0, "DIFFUSER, 4-WAY"))))
    assert [(r.description, r.cls) for r in leg.rows] == [
        ("SUPPLY DIFFUSER, 4-WAY", "diffuser"),
        ("THERMOSTAT", "sensor"),
    ]


def test_text_far_below_or_beside_is_not_a_row(tmp_path):
    content = legend() + text(840, 300, "GENERAL NOTES", 8) + rect(800, 298, 20, 10)
    content += text(100, 670, "RETURN AIR GRILLE", 8) + rect(60, 668, 20, 10)
    (leg,) = read_legend(_sheet(tmp_path, content))
    assert len(leg.rows) == len(ROWS)


def test_ruled_legend_keeps_the_divider_out_of_the_symbol_box(tmp_path):
    # a table: border and a column divider at x+30, one short vertical per row
    content = legend()
    y = 720 - 2 * PITCH
    for _ in ROWS:
        top, bot = y + PITCH / 2 + 3, y - PITCH / 2 + 3
        content += line(795, bot, 795, top) + line(830, bot, 830, top)
        y -= PITCH
    (leg,) = read_legend(_sheet(tmp_path, content))
    assert [r.cls for r in leg.rows] == ["diffuser", "grille", "vav", "sensor", None]
    for r in leg.rows:
        assert (r.symbol_bbox_pt[0], r.symbol_bbox_pt[2]) == pytest.approx((800, 820), abs=1.5)


def test_no_legend_title_no_legend(tmp_path):
    assert read_legend(_sheet(tmp_path, legend(title="DUCT NOTES"))) == []
