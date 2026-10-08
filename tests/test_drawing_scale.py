"""Drawing scale from the sheet itself (#739). Sheets come from hand-written
fixture PDFs run through pdf_ingest, so the whole read path is exercised."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

import drawing_scale as S  # noqa: E402
import pdf_ingest as P  # noqa: E402

FT = 0.3048
PT = 0.0254 / 72


def _sheet(tmp_path, content, w=1728, h=1152, dpi=72):
    pdf = write_pdf(tmp_path / "s.pdf", [PageSpec(width=w, height=h, content=content)])
    return P.ingest_pdf(pdf, out_dir=tmp_path / "out", dpi=dpi).sheets[0]


def _bar(x0, y, step_pt, values, size=6):
    """Scale-bar labels centred on their ticks (Helvetica digits are 0.556 em)."""
    out = ""
    for i, v in enumerate(values):
        w = len(str(v)) * 0.556 * size
        out += text(x0 + i * step_pt - w / 2, y, str(v), size)
    return out


def _dim(x, y, length_pt, label, size=8):
    """A horizontal dimension: line split around centred text sitting on it."""
    gap = len(label) * size * 0.3
    mid = x + length_pt / 2
    tx = mid - gap
    return (
        line(x, y, mid - gap - 2, y, 0.5)
        + line(mid + gap + 2, y, x + length_pt, y, 0.5)
        + line(x, y - 4, x, y + 4, 0.5)
        + line(x + length_pt, y - 4, x + length_pt, y + 4, 0.5)
        + text(tx, y - size / 3, label, size)
    )


@pytest.mark.parametrize(
    "s,m",
    [
        ("12'-6\"", 12.5 * FT),
        ("12'-6 1/2\"", (150.5 / 12) * FT),
        ("12' 6\"", 12.5 * FT),
        ("3'", 3 * FT),
        ('6"', 0.5 * FT),
        ('1 1/2"', 1.5 * 0.0254),
        ("10\u2019-11 3/4\u201d", (131.75 / 12) * FT),
        ("3600 mm", 3.6),
        ("3.6 m", 3.6),
    ],
)
def test_parse_length(s, m):
    assert S.parse_length_m(s) == pytest.approx(m)


@pytest.mark.parametrize("s", ["101", "A-101", "12'-14\"", "OFFICE", ""])
def test_not_a_length(s):
    assert S.parse_length_m(s) is None


@pytest.mark.parametrize(
    "s,r",
    [
        ('SCALE: 1/8" = 1\'-0"', 96),
        ('1/4"=1\'-0"', 48),
        ('3/32" = 1\u2019-0\u201d', 128),
        ('1 1/2" = 1\'-0"', 8),
        ('SCALE: 1" = 20\'-0"', 240),
        ("1\" = 20'", 240),
        ("SCALE 1:100", 100),
        ("1:50", 50),
        ("N.T.S.", "NTS"),
        ("SCALE: NOT TO SCALE", "NTS"),
        ("SCALE: AS NOTED", None),
        ("ROOM 1:1", None),
        ("12'-6\"", None),
    ],
)
def test_parse_scale_note(s, r):
    got = S.parse_scale_note(s)
    assert got == (pytest.approx(r) if isinstance(r, int) else r)


def test_note_on_a_sheet(tmp_path):
    sh = _sheet(tmp_path, text(100, 60, "FIRST FLOOR PLAN") + text(100, 48, 'SCALE: 1/8" = 1\'-0"'))
    sc = S.sheet_scale(sh)
    assert sc.source == "note" and sc.ratio == pytest.approx(96)
    assert sc.m_per_pt == pytest.approx(96 * PT)
    assert sc.m_per_px == pytest.approx(96 * PT)  # 72 dpi: one px per pt
    assert not sc.needs_review


def test_m_per_px_follows_raster_dpi(tmp_path):
    sh = _sheet(tmp_path, text(100, 48, '1/4" = 1\'-0"'), dpi=144)
    sc = S.sheet_scale(sh)
    assert sc.m_per_px == pytest.approx(48 * PT / 2)


def test_scale_bar_alone(tmp_path):
    # 1/8" = 1'-0": 8 ft is 1 in = 72 pt on paper
    labels = _bar(100, 100, 72, [0, 8, 16, 24])
    sh = _sheet(tmp_path, labels + text(100 + 3 * 72 + 20, 100, "FEET", 6) + line(100, 95, 316, 95))
    sc = S.sheet_scale(sh)
    assert sc.source == "bar" and sc.ratio == pytest.approx(96, rel=0.005)


def test_scale_bar_needs_a_unit(tmp_path):
    labels = _bar(100, 100, 72, [0, 8, 16, 24])
    sc = S.sheet_scale(_sheet(tmp_path, labels))
    assert sc.m_per_pt is None and sc.needs_review


def test_dimensions_alone(tmp_path):
    # at 1/8" = 1'-0", 20'-0" is 2.5 in = 180 pt
    dims = (
        _dim(100, 400, 180, "20'-0\"")
        + _dim(100, 300, 90, "10'-0\"")
        + _dim(400, 300, 135, "15'-0\"")
    )
    sc = S.sheet_scale(_sheet(tmp_path, dims))
    assert sc.source == "dimensions" and sc.ratio == pytest.approx(96, rel=0.01)
    assert len([e for e in sc.evidence if e["source"] == "dimension"]) == 3


def test_two_dimensions_are_not_enough_alone(tmp_path):
    dims = _dim(100, 400, 180, "20'-0\"") + _dim(100, 300, 90, "10'-0\"")
    sc = S.sheet_scale(_sheet(tmp_path, dims))
    assert sc.m_per_pt is None and "2 dimension(s)" in sc.reason


def test_note_confirmed_by_dimensions(tmp_path):
    dims = _dim(100, 400, 180, "20'-0\"") + _dim(100, 300, 90, "10'-0\"")
    sc = S.sheet_scale(_sheet(tmp_path, dims + text(100, 48, 'SCALE: 1/8" = 1\'-0"')))
    assert sc.source == "note" and not sc.needs_review and sc.confidence > 0.9


def test_note_contradicted_by_dimensions_goes_to_review(tmp_path):
    dims = _dim(100, 400, 180, "20'-0\"") + _dim(100, 300, 90, "10'-0\"")
    sc = S.sheet_scale(_sheet(tmp_path, dims + text(100, 48, 'SCALE: 1/4" = 1\'-0"')))
    assert sc.ratio == pytest.approx(48) and sc.needs_review
    assert any("dimensions give 1:96" in w for w in sc.warnings)


def test_not_to_scale_refuses(tmp_path):
    sc = S.sheet_scale(_sheet(tmp_path, text(100, 48, "SCALE: N.T.S.")))
    assert sc.nts and sc.m_per_pt is None and "not to scale" in sc.reason


def test_two_views_two_scales(tmp_path):
    content = (
        text(100, 600, "FIRST FLOOR PLAN")
        + text(100, 588, 'SCALE: 1/8" = 1\'-0"')
        + text(1000, 600, "ENLARGED TOILET PLAN")
        + text(1000, 588, 'SCALE: 1/4" = 1\'-0"')
    )
    sc = S.sheet_scale(_sheet(tmp_path, content))
    assert sc.m_per_pt is None and len(sc.views) == 2 and "per-view" in sc.reason
    # views sit above their titles: a point above each title takes its scale
    assert S.scale_at(sc, 150, 300) == pytest.approx(96 * PT)
    assert S.scale_at(sc, 1050, 300) == pytest.approx(48 * PT)


def test_nothing_found(tmp_path):
    sc = S.sheet_scale(_sheet(tmp_path, text(100, 100, "OFFICE 101")))
    assert sc.m_per_pt is None and sc.needs_review and sc.source is None


def test_scale_sheets_and_cli(tmp_path, capsys):
    pdf = write_pdf(
        tmp_path / "s.pdf",
        [
            PageSpec(width=1728, height=1152, content=text(100, 48, 'SCALE: 1/8" = 1\'-0"')),
            PageSpec(width=1728, height=1152, content=text(100, 48, "NOT TO SCALE")),
        ],
    )
    P.ingest_pdf(pdf, out_dir=tmp_path / "o", dpi=36)
    from cli import main

    main(["scale", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert "sheet_001.json" in out and "1:96" in out and "not to scale" in out
    data = json.loads((tmp_path / "o" / "scale.json").read_text())
    assert data["sheet_001.json"]["ratio"] == pytest.approx(96)
    assert data["sheet_002.json"]["nts"] is True
