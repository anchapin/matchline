"""Tests for room_labels.py — room name/number labeling and label parsing."""

from room_labels import (
    LabeledSpace,
    LabeledTakeoff,
    RoomLabel,
    TextBox,
    looks_like_dimension,
    parse_room_label,
    point_in_polygon,
    to_room_label,
)


class TestParseRoomLabel:
    def test_parse_room_with_number(self):
        name, number, conf = parse_room_label("OPEN OFFICE 201")
        assert number == "201"
        assert conf > 0.9

    def test_parse_room_with_rm_prefix(self):
        name, number, conf = parse_room_label("RM 301")
        assert number == "301"
        assert conf == 1.0

    def test_parse_number_only(self):
        name, number, conf = parse_room_label("101")
        assert number == "101"
        assert conf == 1.0

    def test_parse_name_only(self):
        name, number, conf = parse_room_label("LOBBY")
        assert name == "LOBBY"
        assert number == ""

    def test_parse_ocr_confusion_o_as_zero(self):
        """OCR 'O' between digits is treated as zero."""
        name, number, conf = parse_room_label("2O5")
        assert number == "205"

    def test_parse_fallback_returns_original_text(self):
        """Unrecognised text falls back to name=original with low confidence."""
        name, number, conf = parse_room_label("not a room label at all!")
        assert name == "NOT A ROOM LABEL AT ALL!"
        assert conf == 0.5


class TestLooksLikeDimension:
    def test_dimension_feet_notation(self):
        assert looks_like_dimension("17'-6")

    def test_dimension_inches_notation(self):
        assert looks_like_dimension('10"')

    def test_dimension_fraction(self):
        assert looks_like_dimension("1/2")

    def test_dimension_size(self):
        assert looks_like_dimension("5 X 5")

    def test_room_label_not_dimension(self):
        assert not looks_like_dimension("OPEN OFFICE 201")


class TestPointInPolygon:
    def test_point_inside_rectangle(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon((5, 5), poly) is True

    def test_point_outside_rectangle(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon((15, 15), poly) is False

    def test_point_on_edge(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon((0, 5), poly) is True

    def test_degenerate_polygon_returns_false(self):
        assert point_in_polygon((5, 5), [(0, 0), (0, 0)]) is False

    def test_point_inside_triangle(self):
        poly = [(0, 0), (10, 0), (5, 10)]
        assert point_in_polygon((5, 2), poly) is True


class TestToRoomLabel:
    def test_to_room_label(self):
        tb = TextBox(text="LOBBY 100", bbox=(10, 20, 100, 40), confidence=0.95)
        rl = to_room_label(tb)
        assert isinstance(rl, RoomLabel)
        assert rl.raw_text == "LOBBY 100"
        assert rl.centroid == (55.0, 30.0)


class TestLabeledTakeoff:
    def test_unlabeled_spaces_property(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (10, 0), (10, 10), (0, 10)]),
            LabeledSpace(
                polygon_px=[(20, 0), (30, 0), (30, 10), (20, 10)], name="Office", number="101"
            ),
        ]
        lt = LabeledTakeoff(spaces=spaces, labels=[], unmatched_labels=[])
        unlabeled = lt.unlabeled_spaces
        assert len(unlabeled) == 1
        assert unlabeled[0].name == ""
