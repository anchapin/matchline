"""Wall-type legend rows read off a sheet's vector text (#747)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wall_types import legend_rows, read_legend  # noqa: E402


def _sheet(*spans):
    return {"text": [{"text": t, "bbox": list(bb)} for t, bb in spans]}


def _row(x, y, s, w=None):
    return (s, (x, y, x + (w or 6 * len(s)), y + 10))


def test_tag_and_description_on_one_row():
    sh = _sheet(_row(100, 200, "W1"), _row(160, 200, '8" CMU W/ 2"'), _row(250, 201, "RIGID INSUL"))
    assert legend_rows(sh) == [("W1", '8" CMU W/ 2" RIGID INSUL')]


def test_tag_and_description_in_one_span():
    sh = _sheet(_row(100, 200, 'W-2 - 6" METAL STUD FRAMING, R-19 BATT'))
    assert legend_rows(sh) == [("W-2", '6" METAL STUD FRAMING, R-19 BATT')]


def test_rows_naming_no_class_or_two_classes_are_skipped():
    sh = _sheet(
        _row(100, 200, "W1"),
        _row(160, 200, "SEE DETAIL 3/A-501"),
        _row(100, 300, "W2"),
        _row(160, 300, "WOOD STUD FRAMING OVER STEEL STUD FRAMING"),
    )
    assert legend_rows(sh) == []


def test_description_stops_at_the_next_tag_or_a_wide_gap():
    sh = _sheet(
        _row(100, 200, "W1"),
        _row(160, 200, "CMU"),
        _row(200, 200, "W2"),
        _row(260, 200, "METAL STUD FRAMING"),
        _row(900, 200, "BRICK VENEER"),  # far column, not part of W2
    )
    assert legend_rows(sh) == [("W1", "CMU"), ("W2", "METAL STUD FRAMING")]


def test_other_rows_and_far_text_are_not_descriptions():
    sh = _sheet(_row(100, 200, "W1"), _row(160, 260, "CMU"), _row(400, 200, "CMU"))
    assert legend_rows(sh) == []


def test_read_legend_agrees_conflicts_and_excludes():
    a = _sheet(_row(100, 200, 'W1 8" CMU'), _row(100, 300, "W2 METAL STUD FRAMING"))
    b = _sheet(_row(100, 200, "W1 CONCRETE BLOCK"), _row(100, 300, "W2 WOOD STUD FRAMING"))
    c = _sheet(_row(100, 200, "D1 HM DOOR IN CMU WALL"))
    legend, conflicts = read_legend([("A-501", a), ("A-502", b), ("A-601", c)], exclude={"D1"})
    assert set(legend) == {"W1"}
    assert legend["W1"]["sheet_id"] == "A-501" and legend["W1"]["construction_type"] == "Mass"
    assert [r["construction_type"] for r in conflicts["W2"]] == ["SteelFramed", "WoodFramed"]
