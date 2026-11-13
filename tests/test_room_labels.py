"""Tests for room_labels.py — room name/number labeling and label parsing."""

from room_labels import (
    LabeledSpace,
    LabeledTakeoff,
    RoomLabel,
    TextBox,
    attach_room_labels,
    label_spaces_from_sheet,
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


class TestNameOcrConfusables:
    """#1386: a zero standing in for the letter O inside the *name* part of a
    label is repaired ("RESTRO0M" -> "RESTROOM"); the number part and the
    dimension-string guard are untouched."""

    def test_name_part_zero_is_read_as_letter_o(self):
        name, number, conf = parse_room_label("RESTRO0M 105")
        assert name == "RESTROOM"
        assert number == "105"
        assert conf > 0.9

    def test_leading_zero_name_is_repaired(self):
        name, number, conf = parse_room_label("0FFICE 201")
        assert name == "OFFICE"
        assert number == "201"
        assert conf > 0.9

    def test_name_only_zero_is_repaired(self):
        name, number, conf = parse_room_label("C0RRIDOR")
        assert name == "CORRIDOR"
        assert number == ""

    def test_multiword_name_is_repaired(self):
        name, number, _ = parse_room_label("OPEN 0FFICE 201")
        assert name == "OPEN OFFICE"
        assert number == "201"

    def test_number_part_is_never_rewritten(self):
        # the existing "O next to a digit is a zero" rule still holds
        assert parse_room_label("2O5")[1] == "205"
        # and plain numbers are untouched by the name repair
        assert parse_room_label("201A")[1] == "201A"
        assert parse_room_label("C1")[1] == "C1"
        name, number, _ = parse_room_label("B-102")
        assert (name, number) == ("B", "102")

    def test_alphanumeric_tokens_are_left_alone(self):
        # tokens that keep other digits stay exactly as parsed
        assert parse_room_label("B2")[1] == "B2"
        name, number, _ = parse_room_label("W12X72")
        assert (name, number) == ("W12", "X72")

    def test_corrected_names_classify_by_space_use(self):
        from space_use_defaults import classify_space_type

        assert classify_space_type(parse_room_label("RESTRO0M 105")[0])[0] == "restroom"
        assert classify_space_type(parse_room_label("0FFICE 201")[0])[0] == "closed_office"
        assert classify_space_type(parse_room_label("C0RRIDOR")[0])[0] == "corridor"
        # the corrupted spellings match no rule and fall back
        assert classify_space_type("RESTRO0M")[0] == "office_whole_building"
        assert classify_space_type("0FFICE")[0] == "office_whole_building"

    def test_dimension_detection_is_unaffected(self):
        assert looks_like_dimension("5 X 5")
        assert looks_like_dimension("W12X72")
        assert not looks_like_dimension("RESTRO0M 105")
        assert not looks_like_dimension("OPEN OFFICE 201")

    def test_raw_ocr_text_is_kept_verbatim(self):
        tb = TextBox(text="RESTRO0M 105", bbox=(10, 20, 100, 40), confidence=0.9)
        rl = to_room_label(tb)
        assert rl.raw_text == "RESTRO0M 105"
        assert rl.name == "RESTROOM"
        assert rl.number == "105"


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


class TestLineGrouping:
    """Multi-line room labels: stacked boxes merge into one RoomLabel (#1384)."""

    @staticmethod
    def _room(x, y, w=200, h=200):
        return LabeledSpace(polygon_px=[(x, y), (x + w, y), (x + w, y + h), (x, y + h)])

    def test_stacked_triple_merges_into_one_label(self):
        """'OPEN' / 'OFFICE' / '201' stacked parse to name 'OPEN OFFICE', no. '201'."""
        boxes = [
            TextBox(text="OPEN", bbox=(100, 100, 200, 120), confidence=0.95),
            TextBox(text="OFFICE", bbox=(95, 125, 210, 145), confidence=0.90),
            TextBox(text="201", bbox=(130, 150, 170, 170), confidence=0.88),
        ]
        spaces = [self._room(50, 50)]
        out = label_spaces_from_sheet(None, spaces, pre_detected=boxes)
        assert len(out.labels) == 1
        lab = out.labels[0]
        assert lab.name == "OPEN OFFICE"
        assert lab.number == "201"
        assert lab.raw_text == "OPEN OFFICE 201"
        # merged bbox is the union of the member boxes
        assert lab.bbox == (95, 100, 210, 170)
        # confidence rule: minimum member confidence
        assert lab.ocr_confidence == 0.88
        # assigned as one label to the room
        assert out.spaces[0].name == "OPEN OFFICE"
        assert out.spaces[0].number == "201"
        assert out.n_labeled == 1

    def test_two_stacked_pairs_in_different_locations_stay_separate(self):
        """Stacked pairs in different rooms must not merge across locations."""
        boxes = [
            TextBox(text="OPEN", bbox=(100, 100, 200, 120), confidence=0.95),
            TextBox(text="OFFICE", bbox=(100, 125, 200, 145), confidence=0.95),
            TextBox(text="101", bbox=(130, 150, 170, 170), confidence=0.95),
            TextBox(text="CONF", bbox=(600, 100, 700, 120), confidence=0.95),
            TextBox(text="102", bbox=(620, 125, 680, 145), confidence=0.95),
        ]
        spaces = [self._room(50, 50), self._room(550, 50)]
        out = label_spaces_from_sheet(None, spaces, pre_detected=boxes)
        assert len(out.labels) == 2
        by_text = sorted(lab.raw_text for lab in out.labels)
        assert by_text == ["CONF 102", "OPEN OFFICE 101"]
        assert out.spaces[0].name == "OPEN OFFICE"
        assert out.spaces[0].number == "101"
        assert out.spaces[1].name == "CONF"
        assert out.spaces[1].number == "102"

    def test_name_only_stack_merges_to_name_only_label(self):
        """A stack without a number merges into a name-only label."""
        boxes = [
            TextBox(text="OPEN", bbox=(100, 100, 200, 120), confidence=0.95),
            TextBox(text="OFFICE", bbox=(100, 125, 200, 145), confidence=0.95),
        ]
        spaces = [self._room(50, 50)]
        out = label_spaces_from_sheet(None, spaces, pre_detected=boxes)
        assert len(out.labels) == 1
        assert out.labels[0].name == "OPEN OFFICE"
        assert out.labels[0].number == ""
        assert out.spaces[0].name == "OPEN OFFICE"
        assert out.spaces[0].number == ""

    def test_single_line_label_unchanged(self):
        """A one-line label behaves exactly as before the grouping pass."""
        boxes = [TextBox(text="LOBBY 100", bbox=(100, 100, 220, 125), confidence=0.93)]
        spaces = [self._room(50, 50)]
        out = label_spaces_from_sheet(None, spaces, pre_detected=boxes)
        assert len(out.labels) == 1
        assert out.labels[0].name == "LOBBY"
        assert out.labels[0].number == "100"
        assert out.labels[0].raw_text == "LOBBY 100"
        assert out.labels[0].bbox == (100, 100, 220, 125)
        assert out.labels[0].ocr_confidence == 0.93

    def test_side_by_side_boxes_are_not_merged(self):
        """Text side by side on one line is never treated as a stack."""
        boxes = [
            TextBox(text="OPEN", bbox=(100, 100, 180, 120), confidence=0.95),
            TextBox(text="OFFICE", bbox=(200, 100, 290, 120), confidence=0.95),
        ]
        spaces = [self._room(50, 50)]
        out = label_spaces_from_sheet(None, spaces, pre_detected=boxes)
        assert len(out.labels) == 2
        assert sorted(lab.raw_text for lab in out.labels) == ["OFFICE", "OPEN"]


class TestAttachRoomLabelsProvenance:
    """Verify attach_room_labels integration with run_pipeline sets core_provenance
    and routes low-confidence labels to review queue."""

    def test_attach_room_labels_returns_labeled_spaces_for_room_category(self):
        """attach_room_labels creates LabeledSpace objects for 'room' category regions."""
        from datasets_adapter import DrawingScale, Region, TakeoffResult

        regions = [
            Region(
                category="room",
                polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)],
                source="test",
            ),
        ]
        result = TakeoffResult(
            drawing_type="floor_plan",
            scale=DrawingScale(m_per_px=0.001),
            regions=regions,
        )

        labeled = attach_room_labels(result, None)

        assert len(labeled.spaces) == 1
        assert labeled.spaces[0].polygon_px == [(0, 0), (100, 0), (100, 100), (0, 100)]
        assert labeled.spaces[0].source == "test"
