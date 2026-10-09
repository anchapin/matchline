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


# ---- #828: descriptions wrapped onto several lines ---------------------------


def _wrapped(*lines, x=160, y=200, step=14):
    return [_row(x, y + k * step, s) for k, s in enumerate(lines)]


def test_class_word_on_a_wrapped_line_resolves_the_tag():
    sh = _sheet(
        _row(100, 200, "W2"),
        *_wrapped("EXTERIOR WALL", '6" METAL STUD FRAMING', "R-19 BATT INSULATION"),
    )
    (row,) = legend_rows(sh)
    assert row == ("W2", 'EXTERIOR WALL 6" METAL STUD FRAMING R-19 BATT INSULATION')


def test_first_line_class_wins_over_a_later_line():
    # MTL STUD FURRING behind a CMU wall must not make it steel-framed
    sh = _sheet(_row(100, 200, "W1"), *_wrapped('8" CMU', '7/8" MTL STUD FURRING'))
    assert legend_rows(sh) == [("W1", '8" CMU')]
    legend, _c = read_legend([("A-501", sh)])
    assert legend["W1"]["construction_type"] == "Mass"
    assert legend["W1"]["full_text"] == '8" CMU 7/8" MTL STUD FURRING'


def test_next_tag_row_is_never_absorbed():
    sh = _sheet(
        _row(100, 200, "W1"),
        *_wrapped("EXTERIOR WALL", "SEE DETAIL"),
        _row(100, 228, "W2"),
        _row(160, 228, "CMU"),
    )
    assert legend_rows(sh) == [("W2", "CMU")]


def test_other_column_or_a_blank_gap_is_not_absorbed():
    sh = _sheet(
        _row(100, 200, "W1"),
        _row(160, 200, "EXTERIOR WALL"),
        _row(600, 214, "CMU"),  # a different column
        _row(160, 260, "METAL STUD FRAMING"),  # after a blank gap
    )
    assert legend_rows(sh) == []


def test_inline_tag_wraps_onto_indented_lines():
    sh = _sheet(_row(100, 200, "W3 - EXTERIOR WALL"), _row(130, 214, "WOOD STUD FRAMING"))
    assert legend_rows(sh) == [("W3", "EXTERIOR WALL WOOD STUD FRAMING")]


def test_r_value_on_a_wrapped_line_is_text_not_a_tag():
    sh = _sheet(
        _row(100, 200, "W4"),
        *_wrapped("EXTERIOR WALL", "R-19 BATT", "WOOD STUD FRAMING"),
    )
    assert legend_rows(sh) == [("W4", "EXTERIOR WALL R-19 BATT WOOD STUD FRAMING")]
