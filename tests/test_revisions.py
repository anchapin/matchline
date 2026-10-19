"""Revision and addenda tracking on a vector drawing set (#993)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import review.revisions as revisions  # noqa: E402


def _sheet(texts, prims=None):
    return {
        "width_pt": 3000.0,
        "height_pt": 2000.0,
        "text": [{"text": t, "bbox": list(b)} for t, b in texts],
        "primitives": prims or [],
    }


def _cloud_path(x0, y0, size=200.0, arcs=12):
    """One closed scallop ring: arcs short segments around a box."""
    segs = [["M", [[x0, y0]]]]
    n = arcs
    for i in range(1, n):
        x = x0 + size * i / n
        y = y0 + (size / 2 if i % 2 else 0.0)
        segs.append(["L", [[x, y]]])
    segs.append(["L", [[x0, y0]]])
    segs.append(["Z", []])
    return {"kind": "path", "segments": segs, "bbox": [x0, y0, x0 + size, y0 + size / 2]}


def test_table_rows_mark_date_description():
    sh = _sheet(
        [
            ("REVISIONS", (2400, 100, 2600, 115)),
            ("MARK DATE DESCRIPTION", (2400, 130, 2600, 140)),
            ("2 10/12/26 ADDED STORAGE ROOM", (2400, 150, 2600, 160)),
            ("B 11/01/26 REVISED CANOPY", (2400, 170, 2600, 180)),
        ]
    )
    rows = revisions.read_revision_table(sh)
    assert [r["mark"] for r in rows] == ["2", "B"]
    assert rows[0]["date"] == "10/12/26"
    assert rows[0]["description"] == "ADDED STORAGE ROOM"
    assert rows[1]["mark"] == "B"


def test_no_table_returns_empty():
    sh = _sheet([("FLOOR PLAN - LEVEL 1", (100, 100, 500, 120))])
    assert revisions.read_revision_table(sh) == []
    assert revisions.find_revision_clouds(sh) == []


def test_title_variants_recognized():
    for title in ("REVISION SCHEDULE", "REVISIONS", "REV HISTORY"):
        sh = _sheet([(title, (2400, 100, 2600, 115))])
        assert revisions.read_revision_table(sh) == []


def test_cloud_single_closed_path():
    sh = _sheet([("plan", (0, 0, 100, 20))], [_cloud_path(500, 500)])
    clouds = revisions.find_revision_clouds(sh)
    assert len(clouds) == 1
    assert clouds[0]["segments"] >= revisions.MIN_ARCS


def _scallop(cx, cy, half=6.0):
    """One open scallop arc: a short 3-point open path."""
    return {
        "kind": "path",
        "segments": [
            ["M", [[cx - half, cy - half]]],
            ["L", [[cx + half, cy - half]]],
            ["L", [[cx + half, cy + half]]],
        ],
        "bbox": [cx - half, cy - half, cx + half, cy + half],
    }


def test_cloud_chain_of_small_open_scallops():
    import math

    prims = []
    for i in range(8):
        a = 2 * math.pi * i / 8
        cx, cy = 1000 + 20 * math.cos(a), 1000 + 20 * math.sin(a)
        prims.append(_scallop(cx, cy))
    sh = _sheet([("plan", (0, 0, 100, 20))], prims)
    clouds = revisions.find_revision_clouds(sh)
    assert len(clouds) == 1


def test_schedule_cell_grid_is_not_a_cloud():
    # a schedule table drawn as tightly packed closed rectangles is detail
    # geometry, never a revision cloud, however many cells it packs
    prims = []
    for ix in range(5):
        for iy in range(2):
            x0, y0 = 200 + ix * 30.0, 500 + iy * 20.0
            prims.append(
                {
                    "kind": "polygon",
                    "segments": [
                        ["M", [[x0, y0]]],
                        ["L", [[x0 + 30, y0]]],
                        ["L", [[x0 + 30, y0 + 20]]],
                        ["L", [[x0, y0 + 20]]],
                        ["L", [[x0, y0]]],
                        ["Z", []],
                    ],
                    "bbox": [x0, y0, x0 + 30, y0 + 20],
                }
            )
    assert revisions.find_revision_clouds(_sheet([], prims)) == []


def test_straight_line_table_grid_is_not_a_cloud():
    # a schedule drawn as bare short straight rules is the most common vector
    # form; single straight segments never chain into a cloud
    lines = []
    for k in range(6):  # 6 horizontal rules, 40pt long
        y = 150.0 + k * 12.0
        lines.append(
            {
                "kind": "line",
                "segments": [["M", [[100.0, y]]], ["L", [[140.0, y]]]],
                "bbox": [100.0, y, 140.0, y],
            }
        )
    for k in range(4):  # 4 vertical rules, 20pt long
        x = 100.0 + k * 12.0
        lines.append(
            {
                "kind": "line",
                "segments": [["M", [[x, 150.0]]], ["L", [[x, 170.0]]]],
                "bbox": [x, 150.0, x, 170.0],
            }
        )
    assert revisions.find_revision_clouds(_sheet([], lines)) == []


def test_rectangle_is_not_a_cloud():
    rect = {
        "kind": "polygon",
        "segments": [
            ["M", [[0, 0]]],
            ["L", [[100, 0]]],
            ["L", [[100, 50]]],
            ["L", [[0, 50]]],
            ["L", [[0, 0]]],
            ["Z", []],
        ],
        "bbox": [0, 0, 100, 50],
    }
    assert revisions.find_revision_clouds(_sheet([], [rect])) == []


def test_cloud_mark_inside_bbox_matched_to_table():
    sh = _sheet(
        [
            ("REVISIONS", (2400, 100, 2600, 115)),
            ("2 10/12/26 ADDED STORAGE ROOM", (2400, 150, 2600, 160)),
            ("2", (600, 550, 610, 562)),
        ],
        [_cloud_path(500, 500)],
    )
    res = revisions.analyze_sheet(sh, title_revision="2")
    assert res["clouds"][0]["mark"] == "2"
    assert res["ambiguous"] == []


def test_cloud_without_table_entry_is_ambiguous():
    sh = _sheet([("2", (600, 600, 610, 612))], [_cloud_path(500, 500)])
    res = revisions.analyze_sheet(sh)
    kinds = {a["kind"] for a in res["ambiguous"]}
    assert kinds == {"cloud_without_table_entry"}


def test_cloud_mark_missing_from_table_is_ambiguous():
    sh = _sheet(
        [
            ("REVISIONS", (2400, 100, 2600, 115)),
            ("1 09/01/26 ISSUED FOR BID", (2400, 150, 2600, 160)),
            ("2", (600, 550, 610, 562)),
        ],
        [_cloud_path(500, 500)],
    )
    res = revisions.analyze_sheet(sh, title_revision="1")
    kinds = {a["kind"] for a in res["ambiguous"]}
    assert kinds == {"cloud_without_table_entry", "table_entry_without_cloud"}
    clouds = [a for a in res["ambiguous"] if a["kind"] == "cloud_without_table_entry"]
    assert clouds[0]["mark"] == "2"


def test_table_entry_claimed_by_title_block_without_cloud():
    sh = _sheet(
        [
            ("REVISIONS", (2400, 100, 2600, 115)),
            ("2 10/12/26 ADDED STORAGE ROOM", (2400, 150, 2600, 160)),
            ("1 09/01/26 ISSUED FOR BID", (2400, 170, 2600, 180)),
        ]
    )
    # the sheet claims revision 2 but draws no cloud for it: ambiguous
    res = revisions.analyze_sheet(sh, title_revision="2")
    kinds = {a["kind"] for a in res["ambiguous"]}
    assert kinds == {"table_entry_without_cloud"}
    # the same ambiguity fires whichever claimed row goes cloudless
    res = revisions.analyze_sheet(sh, title_revision="1")
    kinds = {a["kind"] for a in res["ambiguous"]}
    assert kinds == {"table_entry_without_cloud"}
    assert res["ambiguous"][0]["mark"] == "1"


def test_index_revisions_groups_sheets_and_addenda():
    out = revisions.index_revisions([("2", False), ("2", True), (None, False), ("1", False)])
    assert out["2"]["sheets"] == ["sheet_001", "sheet_002"]
    assert out["1"]["sheets"] == ["sheet_004"]
    assert "__addenda_sheets__" in out
    assert out["__addenda_sheets__"]["sheets"] == ["sheet_002"]


def test_read_title_block_sets_addenda(tmp_path):
    import ingest.sheet_index as sheet_index

    sh = _sheet(
        [
            ("A-101 FLOOR PLAN", (100, 100, 500, 120)),
            ("ADDENDUM NO. 1", (100, 1900, 400, 1915)),
        ]
    )
    (tmp_path / "sheet_001.json").write_text(json.dumps(sh))
    idx = sheet_index.index_sheets(tmp_path)
    assert idx.sheets[0].addenda is True
    assert idx.revisions.get("__addenda_sheets__", {}).get("sheets") == ["sheet_001"]
