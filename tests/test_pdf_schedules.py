"""Schedule tables from vector PDF sheets (#746)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.append(str(Path(__file__).resolve().parent))

from pdf_fixtures import PageSpec, line, text, write_pdf  # noqa: E402

from pdf_ingest import ingest_pdf  # noqa: E402
from pdf_schedules import extract_schedules, schedules_for_sheets  # noqa: E402

PAGE_W, PAGE_H = 1224.0, 792.0
INCH_M = 0.0254


def grid(x, ytop, widths, heights, merges=(), cells=None, size=7):
    """A ruled table in PDF space (y up). ``merges`` are (r0, r1, c0, c1)
    inclusive; ``cells`` maps (r, c) -> text or [lines]. Returns content."""
    xs = [x]
    for w in widths:
        xs.append(xs[-1] + w)
    ys = [ytop]
    for h in heights:
        ys.append(ys[-1] - h)
    R, C = len(heights), len(widths)
    owner = {}
    for k, (r0, r1, c0, c1) in enumerate(merges):
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                owner[(r, c)] = k
    own = lambda r, c: owner.get((r, c), (r, c))  # noqa: E731
    out = ""
    for r in range(R + 1):  # horizontal pieces
        for c in range(C):
            if 0 < r < R and own(r - 1, c) == own(r, c):
                continue
            out += line(xs[c], ys[r], xs[c + 1], ys[r], 0.5)
    for c in range(C + 1):
        for r in range(R):
            if 0 < c < C and own(r, c - 1) == own(r, c):
                continue
            out += line(xs[c], ys[r], xs[c], ys[r + 1], 0.5)
    for (r, c), val in (cells or {}).items():
        lines = val if isinstance(val, list) else [val]
        top = ys[r] - 2
        for i, s in enumerate(lines):
            out += text(xs[c] + 2, top - size - i * (size + 1), s, size)
    return out


def door_schedule(x=60, ytop=740, rows=None):
    rows = rows or [
        ("D1", "3'-0\"", "7'-0\"", "HOLLOW METAL"),
        ("D2", "6'-0\"", "7'-0\"", ["PAIR,", "HOLLOW METAL"]),
        ("D3", "3'-0\"", "8'-0\"", "WOOD"),
    ]
    heights = [16, 14] + [20] * len(rows) + [14]
    cells = {(0, 0): "MARK", (0, 1): "SIZE", (1, 1): "WIDTH", (1, 2): "HEIGHT", (0, 3): "TYPE"}
    for i, r in enumerate(rows):
        for c, v in enumerate(r):
            cells[(2 + i, c)] = v
    n = len(heights) - 1
    cells[(n, 0)] = "NOTES: VERIFY ALL OPENINGS IN FIELD"
    content = text(x, ytop + 6, "DOOR SCHEDULE", 10)
    content += grid(
        x, ytop, [50, 60, 60, 110], heights,
        merges=[(0, 1, 0, 0), (0, 0, 1, 2), (0, 1, 3, 3), (n, n, 0, 3)],
        cells=cells,
    )  # fmt: skip
    return content


def lighting_schedule(x=400, ytop=740):
    heights = [16, 14, 14, 14, 14]
    cells = {
        (0, 0): "LIGHTING FIXTURE SCHEDULE",
        (1, 0): "TYPE", (1, 1): "DESCRIPTION", (1, 2): "LAMP", (1, 3): "WATTS",
        (2, 0): "A", (2, 1): "2X4 LED TROFFER", (2, 2): "LED", (2, 3): "45 W",
        (3, 0): "B", (3, 1): "6\" DOWNLIGHT", (3, 2): "LED", (3, 3): "18",
        (4, 0): "C", (4, 1): "EXIT SIGN", (4, 2): "LED", (4, 3): "",
    }  # fmt: skip
    return grid(x, ytop, [30, 110, 40, 40], heights, merges=[(0, 0, 0, 3)], cells=cells)


def vav_schedule(x=700, ytop=740):
    heights = [14, 14, 14, 14]
    cells = {
        (0, 0): "TAG", (0, 1): "MAX CFM", (0, 2): "MIN CFM", (0, 3): "INLET SIZE",
        (1, 0): "VAV-1", (1, 1): "1,200", (1, 2): "360", (1, 3): "10\"",
        (2, 0): "VAV-2", (2, 1): "800", (2, 2): "240", (2, 3): "8\"",
        (3, 0): "VAV-3", (3, 1): "450", (3, 2): "135", (3, 3): "6\"",
    }  # fmt: skip
    return text(x, ytop + 6, "VAV BOX SCHEDULE", 10) + grid(
        x, ytop, [50, 50, 50, 60], heights, cells=cells
    )


def plan_grid(x=60, ytop=400):
    """Room-sized rectangles with labels and no schedule title: a plan."""
    return grid(
        x, ytop, [200, 200], [150, 150],
        cells={(0, 0): "OFFICE", (0, 1): "101", (1, 0): "CORRIDOR"},
    )  # fmt: skip


def _sheet(tmp_path, content, name="s.pdf"):
    pdf = write_pdf(tmp_path / name, [PageSpec(width=PAGE_W, height=PAGE_H, content=content)])
    ingest_pdf(pdf, out_dir=tmp_path / "out")
    return json.loads((tmp_path / "out" / "sheet_001.json").read_text())


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sched")
    content = door_schedule() + lighting_schedule() + vav_schedule() + plan_grid()
    sheet = _sheet(tmp, content)
    return {s.kind: s for s in extract_schedules(sheet, "A-601")}, sheet


def test_finds_titled_tables_only(full):
    found, _ = full
    assert sorted(found) == ["door", "lighting", "mechanical"]  # the plan grid is not a schedule


def test_door_schedule_group_header_multiline_and_note(full):
    s = full[0]["door"]
    assert s.status == "ok", s.reason
    assert s.title == "DOOR SCHEDULE"
    assert s.headers == ["MARK", "SIZE WIDTH", "SIZE HEIGHT", "TYPE"]
    assert [r[0] for r in s.rows] == ["D1", "D2", "D3"]
    assert s.rows[1][3] == "PAIR, HOLLOW METAL"  # two lines in one cell
    assert s.notes == ["NOTES: VERIFY ALL OPENINGS IN FIELD"]
    d2 = s.entries["D2"]
    assert d2["category"] == "door"
    assert d2["width_m"] == pytest.approx(72 * INCH_M)
    assert d2["height_m"] == pytest.approx(84 * INCH_M)
    assert s.entries["D3"]["height_m"] == pytest.approx(96 * INCH_M)


def test_every_cell_has_provenance_with_a_pixel_bbox(full):
    s, sheet = full[0]["door"], full[1]
    k = sheet["px_per_pt"]
    t = s.table
    assert t is not None and t.provenance.method == "pdf_ruled_table"
    for row in t.rows:
        for cell in row.cells:
            p = cell.provenance
            assert p.sheet_id == "A-601" and p.method == "pdf_ruled_table"
            assert len(p.bbox) == 4 and p.bbox[0] < p.bbox[2] and p.bbox[1] < p.bbox[3]
    # D1's mark cell: column 0, first data band (y-down 792-740+30 .. +20 pt)
    b = t.rows[0].cells[0].provenance.bbox
    assert b == pytest.approx(
        [60 * k, (792 - 740 + 30) * k, 110 * k, (792 - 740 + 50) * k], abs=0.2
    )


def test_lighting_title_row_inside_the_frame_and_missing_watts(full):
    s = full[0]["lighting"]
    assert s.status == "ok", s.reason
    assert s.title == "LIGHTING FIXTURE SCHEDULE"
    a, b, c = s.entries["A"], s.entries["B"], s.entries["C"]
    assert (a["watts"], b["watts"]) == (45.0, 18.0)
    assert a["description"] == "2X4 LED TROFFER" and a["lamp_type"] == "LED"
    assert c["watts"] is None and "watts" in c["note"]  # blank stays blank, never guessed


def test_mechanical_vav_records(full):
    s = full[0]["mechanical"]
    assert s.status == "ok", s.reason
    eq = {e["tag"]: e for e in s.equipment}
    assert sorted(eq) == ["VAV-1", "VAV-2", "VAV-3"]
    assert eq["VAV-1"]["kind"] == "vav"
    assert (eq["VAV-1"]["cfm_max"], eq["VAV-1"]["cfm_min"]) == (1200.0, 360.0)
    assert eq["VAV-2"]["neck_size"] == '8"'


def test_tags_are_normalised_like_the_takeoff_join(tmp_path):
    rows = [("d 1", "3'-0\"", "7'-0\"", "HM"), ("d-2", "3'-0\"", "7'-0\"", "HM")]
    s = extract_schedules(_sheet(tmp_path, door_schedule(rows=rows)))[0]
    assert sorted(s.entries) == ["D-2", "D1"]


@pytest.mark.parametrize(
    "rows, why",
    [
        ([("D1", "3'-0\"", "7'-0\"", "HM"), ("D1", "3'-0\"", "8'-0\"", "HM")], "more than once"),
        ([("D1", "3'-0\"", "7'-0\"", "HM"), ("", "3'-0\"", "8'-0\"", "HM")], "no MARK"),
    ],
)
def test_a_bad_table_is_flagged_not_half_read(tmp_path, rows, why):
    s = extract_schedules(_sheet(tmp_path, door_schedule(rows=rows)))[0]
    assert s.status == "unparsed" and why in s.reason
    assert s.rows == [] and s.entries == {} and s.table is None


def test_text_crossing_a_cell_line_is_flagged(tmp_path):
    content = door_schedule() + text(105, 740 - 30 - 8, "OVERHANGING TEXT", 7)  # D1 row, col 0->1
    s = extract_schedules(_sheet(tmp_path, content))[0]
    assert s.status == "unparsed" and "crosses a cell line" in s.reason


def test_schedules_for_sheets_writes_json(tmp_path):
    _sheet(tmp_path, door_schedule() + vav_schedule())
    res = schedules_for_sheets(tmp_path / "out", {"sheet_001.json": "A-601"})
    out = json.loads((tmp_path / "out" / "schedules_001.json").read_text())
    assert out == res["sheet_001.json"]
    assert out["schema"] == "matchline.pdf_schedules/1"
    assert [s["kind"] for s in out["schedules"]] == ["door", "mechanical"]
    assert "table" not in out["schedules"][0]


def test_cli_schedules(tmp_path, capsys):
    _sheet(tmp_path, door_schedule())
    from cli import main

    main(["schedules", str(tmp_path / "out")])
    printed = capsys.readouterr().out
    assert "door" in printed and "DOOR SCHEDULE  rows 3" in printed
