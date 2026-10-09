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


# ---- stated U, SHGC and VT on door/window schedules (#746) -------------------


def window_thermal_schedule(u_head, rows, x=60, ytop=740):
    heights = [14] + [14] * len(rows)
    cells = {(0, 0): "MARK", (0, 1): "WIDTH", (0, 2): "HEIGHT", (0, 3): u_head,
             (0, 4): "SHGC", (0, 5): "VT"}  # fmt: skip
    for i, r in enumerate(rows):
        for c, v in enumerate(r):
            cells[(1 + i, c)] = v
    return text(x, ytop + 6, "WINDOW SCHEDULE", 10) + grid(
        x, ytop, [40, 40, 40, 130, 40, 40], heights, cells=cells
    )


def _win(tmp_path, u_head, rows):
    (s,) = extract_schedules(_sheet(tmp_path, window_thermal_schedule(u_head, rows)), "A-601")
    assert s.status == "ok", s.reason
    return s.entries


FT = ("4'-0\"", "5'-0\"")


def test_window_u_ip_from_the_header(tmp_path):
    e = _win(tmp_path, "U-FACTOR (BTU/HR-SF-F)", [("W1", *FT, "0.36", "0.38", "0.42")])["W1"]
    assert e["u_value_w_m2k"] == pytest.approx(0.36 * 5.678263, abs=1e-4)
    assert (e["shgc"], e["vt"], e["thermal_confidence"]) == (0.38, 0.42, 0.9)
    assert "Btu/h-ft2-F" in e["thermal_note"]


def test_window_u_si_from_the_header(tmp_path):
    e = _win(tmp_path, "U-VALUE (W/M2K)", [("W1", *FT, "2.04", "0.38", "")])["W1"]
    assert e["u_value_w_m2k"] == pytest.approx(2.04)
    assert e["vt"] is None and e["thermal_confidence"] == 0.9


def test_window_u_without_units_is_ip_only_for_feet_and_inches(tmp_path):
    e = _win(tmp_path, "U-VALUE", [("W1", *FT, "0.36", "0.38", "0.42")])["W1"]
    assert e["u_value_w_m2k"] == pytest.approx(2.0442, abs=1e-4)
    assert e["thermal_confidence"] == 0.75 and "states no units" in e["thermal_note"]
    (tmp_path / "mm").mkdir()
    e = _win(tmp_path / "mm", "U-VALUE", [("W1", "1200", "1500", "2.0", "0.38", "")])["W1"]
    assert e["u_value_w_m2k"] is None and e["thermal_confidence"] is None
    assert "not used" in e["thermal_note"]


def test_window_shgc_out_of_range_is_not_read(tmp_path):
    e = _win(tmp_path, "U-FACTOR (BTU/HR-SF-F)", [("W1", *FT, "0.36", "1.5", "0.42")])["W1"]
    assert e["shgc"] is None and e["vt"] == 0.42
    assert "SHGC '1.5' not read" in e["thermal_note"]


def test_door_schedule_without_thermal_columns_is_unchanged(full):
    e = full[0]["door"].entries["D1"]
    assert e["u_value_w_m2k"] is None and e["thermal_note"] == ""


# ---- unruled (whitespace-aligned) schedules (#746) -----------------------


def unruled(x, ytop, title, header, rows, widths, size=7, pitch=11):
    """Text only: title, header words, data rows; a row value may be [lines]."""
    xs = [x]
    for w in widths:
        xs.append(xs[-1] + w)
    out = text(x, ytop + 6, title, 10)
    for c, h in enumerate(header):
        out += text(xs[c], ytop - size, h, size)
    y = ytop - size - pitch - 4
    for r in rows:
        if isinstance(r, str):  # a note across the table
            out += text(x, y, r, size)
            y -= pitch
            continue
        depth = 1
        for c, v in enumerate(r):
            lines = v if isinstance(v, list) else [v]
            depth = max(depth, len(lines))
            for i, s in enumerate(lines):
                out += text(xs[c], y - i * (size + 1), s, size)
        y -= pitch + (depth - 1) * (size + 1)
    return out


DOOR_UNRULED = dict(
    title="DOOR SCHEDULE",
    header=["MARK", "WIDTH", "HEIGHT", "TYPE"],
    widths=[50, 60, 60, 110],
)


def test_unruled_door_schedule_reads_rows_wrapped_cells_and_a_note(tmp_path):
    rows = [
        ("D1", "3'-0\"", "7'-0\"", "HOLLOW METAL"),
        ("D2", "6'-0\"", "7'-0\"", ["PAIR,", "HOLLOW METAL"]),
        ("D3", "3'-0\"", "8'-0\"", "WOOD"),
        "NOTES: VERIFY ALL OPENINGS IN FIELD BEFORE ORDERING",
    ]
    sheet = _sheet(tmp_path, unruled(60, 700, rows=rows, **DOOR_UNRULED))
    (s,) = extract_schedules(sheet, "A-601")
    assert s.status == "ok", s.reason
    assert s.method == "pdf_unruled_table" and s.kind == "door"
    assert s.headers == ["MARK", "WIDTH", "HEIGHT", "TYPE"]
    assert [r[0] for r in s.rows] == ["D1", "D2", "D3"]
    assert s.rows[1][3] == "PAIR, HOLLOW METAL"
    assert s.notes == ["NOTES: VERIFY ALL OPENINGS IN FIELD BEFORE ORDERING"]
    assert s.entries["D2"]["width_m"] == pytest.approx(72 * INCH_M, abs=1e-6)
    assert s.entries["D3"]["height_m"] == pytest.approx(96 * INCH_M, abs=1e-6)
    cell = s.table.rows[0].cells[1]
    assert cell.provenance.method == "pdf_unruled_table"
    assert cell.provenance.confidence == pytest.approx(0.75)
    assert len(cell.provenance.bbox) == 4


def test_unruled_vav_schedule_gives_equipment(tmp_path):
    rows = [("VAV-1", "1,200", "360", '10"'), ("VAV-2", "800", "240", '8"')]
    content = unruled(
        60, 700, "VAV BOX SCHEDULE", ["TAG", "MAX CFM", "MIN CFM", "INLET SIZE"], rows,
        [50, 50, 50, 60],
    )  # fmt: skip
    (s,) = extract_schedules(_sheet(tmp_path, content), "M-601")
    assert s.status == "ok", s.reason
    assert [(e["tag"], e["cfm_max"], e["cfm_min"]) for e in s.equipment] == [
        ("VAV-1", 1200.0, 360.0),
        ("VAV-2", 800.0, 240.0),
    ]


def test_ruled_and_unruled_on_one_sheet_are_each_read_once(tmp_path):
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED")]
    content = vav_schedule() + unruled(
        60, 400, "WINDOW SCHEDULE", ["MARK", "WIDTH", "HEIGHT", "TYPE"], rows, [50, 60, 60, 110]
    )
    found = extract_schedules(_sheet(tmp_path, content), "A-601")
    assert sorted((s.kind, s.method) for s in found) == [
        ("mechanical", "pdf_ruled_table"),
        ("window", "pdf_unruled_table"),
    ]


def test_unruled_value_crossing_a_column_is_flagged(tmp_path):
    rows = [("D1", "3'-0\"", "7'-0\"", "WOOD"), ("D2", "A VERY LONG WIDTH VALUE", "", "WOOD")]
    (s,) = extract_schedules(_sheet(tmp_path, unruled(60, 700, rows=rows, **DOOR_UNRULED)))
    assert s.status == "unparsed" and "crosses a column" in s.reason


def test_unruled_duplicate_tag_is_flagged(tmp_path):
    rows = [("D1", "3'-0\"", "7'-0\"", "WOOD"), ("D1", "3'-0\"", "8'-0\"", "WOOD")]
    (s,) = extract_schedules(_sheet(tmp_path, unruled(60, 700, rows=rows, **DOOR_UNRULED)))
    assert s.status == "unparsed" and "D1" in s.reason


def test_schedule_word_in_plan_notes_is_not_a_table(tmp_path):
    content = (
        text(60, 700, "SEE DOOR SCHEDULE ON A-601", 7)
        + text(60, 690, "PROVIDE BLOCKING AT ALL WALL MOUNTED EQUIPMENT", 7)
        + text(60, 680, "1. FIELD VERIFY", 7)
    )
    assert extract_schedules(_sheet(tmp_path, content)) == []
