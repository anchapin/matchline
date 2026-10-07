"""Sheet index (#738): title blocks, drawing index, discipline/type/level,
cross-discipline pairing. Sheets are hand-written fixture PDFs run through
pdf_ingest, so the whole read path is exercised."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

import pdf_ingest as P  # noqa: E402
import sheet_index as X  # noqa: E402

W, H = 1728, 1152


def _tb(number, *title, rev="2", date="03/15/2026", project="MEDICAL CLINIC"):
    """A title block down the right edge: project, title lines, rev, date, number."""
    out = line(1480, 0, 1480, H) + text(1500, 220, project, 14)
    for i, t in enumerate(title):
        out += text(1500, 150 - i * 14, t, 12)
    if rev:
        out += text(1500, 96, f"REV {rev}", 8)
    if date:
        out += text(1500, 84, f"DATE: {date}", 8)
    return out + text(1560, 40, number, 24)


def _index(rows, x=100, top=1050):
    out = text(x, top + 20, "DRAWING INDEX", 12)
    for i, (n, t) in enumerate(rows):
        y = top - i * 14
        out += text(x, y, n, 10) + text(x + 80, y, t, 10)
    return out


def _build(tmp_path, pages):
    pdf = write_pdf(tmp_path / "set.pdf", [PageSpec(width=W, height=H, content=c) for c in pages])
    res = P.ingest_pdf(pdf, out_dir=tmp_path / "o", dpi=18)
    return X.build_index(res.sheets), tmp_path / "o"


INDEX_ROWS = [
    ("G-001", "COVER SHEET"),
    ("A-101", "FIRST FLOOR PLAN"),
    ("A-102", "SECOND FLOOR PLAN"),
    ("A-201", "BUILDING ELEVATIONS"),
    ("A-401", "ENLARGED TOILET PLANS - FIRST FLOOR"),
    ("M-101", "FIRST FLOOR MECHANICAL PLAN"),
    ("M-102", "SECOND FLOOR MECHANICAL PLAN"),
    ("M-601", "MECHANICAL SCHEDULES"),
]


def _set(a102_title="SECOND FLOOR PLAN", with_index=True):
    cover = _tb("G-001", "COVER SHEET") + (_index(INDEX_ROWS) if with_index else "")
    return [
        cover,
        _tb("A-101", "FIRST FLOOR PLAN"),
        _tb("A-102", a102_title),
        _tb("A-201", "BUILDING ELEVATIONS"),
        _tb("A-401", "ENLARGED TOILET PLANS", "FIRST FLOOR"),
        _tb("M-101", "FIRST FLOOR", "MECHANICAL PLAN"),
        _tb("M-102", "MECHANICAL PLAN"),
        _tb("M-601", "MECHANICAL SCHEDULES"),
    ]


# ------------------------------------------------------------------ parsers


@pytest.mark.parametrize(
    "s,num,disc,digit",
    [
        ("A-101", "A-101", "A", "1"),
        ("A101", "A-101", "A", "1"),
        ("M-601", "M-601", "M", "6"),
        ("A1.01", "A-1.01", "A", "1"),
        ("FP-101", "FP-101", "F", "1"),
        ("A-501A", "A-501A", "A", "5"),
        ("E-1", "E-1", "E", None),
    ],
)
def test_parse_sheet_number(s, num, disc, digit):
    p = X.parse_sheet_number(s)
    assert (p["number"], p["discipline"], p["type_digit"]) == (num, disc, digit)


@pytest.mark.parametrize("s", ["101", "OFFICE", "5/A-501", "A-101 FIRST", "K-101", "12'-6\""])
def test_not_a_sheet_number(s):
    assert X.parse_sheet_number(s) is None


@pytest.mark.parametrize(
    "title,kind",
    [
        ("FIRST FLOOR PLAN", "floor_plan"),
        ("FIRST FLOOR REFLECTED CEILING PLAN", "rcp"),
        ("ENLARGED TOILET PLANS", "enlarged_plan"),
        ("PARTIAL FIRST FLOOR PLAN - AREA A", "partial_plan"),
        ("FIRST FLOOR PLAN - AREA B", "partial_plan"),
        ("ROOF PLAN", "roof_plan"),
        ("SITE PLAN", "site_plan"),
        ("BUILDING ELEVATIONS", "elevation"),
        ("WALL SECTIONS", "section"),
        ("MECHANICAL SCHEDULES", "schedule"),
        ("DOOR AND WINDOW SCHEDULE", "schedule"),
        ("TYPICAL DETAILS", "detail"),
        ("PLUMBING RISER DIAGRAM", "diagram"),
        ("SYMBOLS AND ABBREVIATIONS", "general"),
        ("COVER SHEET", "cover"),
        ("MEDICAL CLINIC", None),
    ],
)
def test_classify_title(title, kind):
    assert X.classify_title(title) == kind


@pytest.mark.parametrize(
    "title,level",
    [
        ("FIRST FLOOR PLAN", "L1"),
        ("GROUND FLOOR PLAN", "L1"),
        ("SECOND FLOOR MECHANICAL PLAN", "L2"),
        ("3RD FLOOR PLAN", "L3"),
        ("LEVEL 02 PLAN", "L2"),
        ("LEVEL B1 PLAN", "B1"),
        ("BASEMENT PLAN", "B1"),
        ("MEZZANINE PLAN", "MEZZ"),
        ("ROOF PLAN", "ROOF"),
        ("BUILDING ELEVATIONS", None),
    ],
)
def test_parse_level(title, level):
    assert X.parse_level(title) == level


# ---------------------------------------------------------------- full sets


def test_title_block_fields(tmp_path):
    idx, _ = _build(tmp_path, [_tb("A-101", "FIRST FLOOR PLAN")])
    e = idx.sheets[0]
    assert e.value("number") == "A-101" and e.number["source"] == "title_block"
    assert e.number["bbox"][0] > 0.6 * W  # provenance points at the title block
    assert e.value("title") == "FIRST FLOOR PLAN"
    assert e.value("discipline") == "architectural"
    assert e.value("type") == "floor_plan" and e.value("level") == "L1"
    assert e.value("revision") == "2" and e.value("date") == "03/15/2026"
    assert e.use_for_takeoff and not e.needs_review


def test_project_name_is_not_the_title(tmp_path):
    idx, _ = _build(
        tmp_path, [_tb("A-201", "BUILDING ELEVATIONS", project="SECOND STREET OFFICES")]
    )
    assert idx.sheets[0].value("title") == "BUILDING ELEVATIONS"


def test_multi_line_title(tmp_path):
    idx, _ = _build(tmp_path, [_tb("M-101", "FIRST FLOOR", "MECHANICAL PLAN")])
    e = idx.sheets[0]
    assert e.value("title") == "FIRST FLOOR MECHANICAL PLAN"
    assert e.value("discipline") == "mechanical" and e.value("level") == "L1"


def test_whole_set_with_drawing_index(tmp_path):
    idx, _ = _build(tmp_path, _set())
    by = {e.value("number"): e for e in idx.sheets}
    assert set(by) == {r[0] for r in INDEX_ROWS}
    assert len(idx.drawing_index) == len(INDEX_ROWS)
    assert by["G-001"].value("type") == "cover"
    assert by["A-201"].value("type") == "elevation" and not by["A-201"].use_for_takeoff
    assert by["M-601"].value("type") == "schedule"
    # an abbreviated title block agrees with the index and takes its level from it
    assert by["M-102"].level["source"] == "drawing_index" and by["M-102"].value("level") == "L2"
    assert not by["M-102"].needs_review
    # enlarged plans never feed takeoff
    a401 = by["A-401"]
    assert a401.value("type") == "enlarged_plan" and a401.value("level") == "L1"
    assert not a401.use_for_takeoff and "enlarged" in a401.takeoff_note
    # pairing across disciplines by level
    assert idx.levels["L1"] == {"A": ["A-101", "A-401"], "M": ["M-101"]}
    assert idx.levels["L2"] == {"A": ["A-102"], "M": ["M-102"]}
    assert idx.warnings == []


def test_title_block_and_index_disagree(tmp_path):
    idx, _ = _build(tmp_path, _set(a102_title="THIRD FLOOR PLAN"))
    e = idx.by_number("A-102")
    assert e.needs_review and e.value("level") == "L3"
    assert any("drawing index says 'SECOND FLOOR PLAN'" in w for w in idx.warnings)


def test_level_from_paired_sheet_without_index(tmp_path):
    idx, _ = _build(tmp_path, _set(with_index=False))
    e = idx.by_number("M-102")
    assert e.value("level") == "L2" and e.level["source"] == "paired_sheet_number"
    assert idx.drawing_index == {}


def test_index_lists_a_missing_sheet(tmp_path):
    pages = _set()
    del pages[-1]  # drop M-601
    idx, _ = _build(tmp_path, pages)
    assert any("M-601 is in the drawing index but not in this set" in w for w in idx.warnings)


def test_duplicate_sheet_numbers(tmp_path):
    idx, _ = _build(tmp_path, [_tb("A-101", "FIRST FLOOR PLAN"), _tb("A-101", "SECOND FLOOR PLAN")])
    assert all(e.needs_review for e in idx.sheets)
    assert any("A-101 appears on pages 1, 2" in w for w in idx.warnings)


def test_partial_plan_beside_a_full_plan(tmp_path):
    idx, _ = _build(
        tmp_path,
        [_tb("A-101", "FIRST FLOOR PLAN"), _tb("A-111", "PARTIAL FIRST FLOOR PLAN", "AREA A")],
    )
    e = idx.by_number("A-111")
    assert e.value("type") == "partial_plan" and not e.use_for_takeoff and not e.needs_review


def test_partial_plans_alone_need_review(tmp_path):
    idx, _ = _build(
        tmp_path,
        [_tb("A-111", "FIRST FLOOR PLAN - AREA A"), _tb("A-112", "FIRST FLOOR PLAN - AREA B")],
    )
    for e in idx.sheets:
        assert e.value("type") == "partial_plan" and e.needs_review
        assert "match lines" in e.takeoff_note


def test_ncs_digit_when_title_is_silent(tmp_path):
    idx, _ = _build(tmp_path, [_tb("A-501", "TOILET ROOM", "TYP.")])
    e = idx.sheets[0]
    assert e.value("type") == "detail" and e.type["source"] == "ncs_type_digit"


def test_ncs_large_scale_digit_marks_enlarged(tmp_path):
    idx, _ = _build(tmp_path, [_tb("A-401", "TOILET ROOM PLAN")])
    e = idx.sheets[0]
    assert e.value("type") == "enlarged_plan" and not e.use_for_takeoff


def test_no_sheet_number(tmp_path):
    idx, _ = _build(tmp_path, [text(100, 100, "SKETCH")])
    e = idx.sheets[0]
    assert e.number is None and e.needs_review


def test_revision_word_is_not_a_revision(tmp_path):
    idx, _ = _build(
        tmp_path, [_tb("A-101", "FIRST FLOOR PLAN", rev=None) + text(1500, 70, "REVIEW", 8)]
    )
    assert idx.sheets[0].revision is None


def test_callout_rows_on_a_plan_are_not_a_drawing_index(tmp_path):
    rows = [("A-501", "SIM."), ("A-502", "TYP."), ("A-503", "SIM.")]
    content = _tb("A-101", "FIRST FLOOR PLAN") + "".join(
        text(300, 700 - i * 14, n, 10) + text(380, 700 - i * 14, t, 10)
        for i, (n, t) in enumerate(rows)
    )
    idx, _ = _build(tmp_path, [content])
    assert idx.drawing_index == {} and idx.warnings == []


def test_index_sheets_and_cli(tmp_path, capsys):
    _, out = _build(tmp_path, _set())
    from cli import main

    main(["index", str(out)])
    printed = capsys.readouterr().out
    assert "A-101" in printed and "floor_plan" in printed and "L1" in printed
    data = json.loads((out / "sheet_index.json").read_text())
    assert data["schema"] == "matchline.sheet_index/1"
    assert len(data["sheets"]) == 8 and data["sheets"][1]["file"] == "sheet_002.json"
    assert data["levels"]["L2"]["M"] == ["M-102"]
