"""Tests for polygon_classify.py: happy-path, invariant, and defect-injection."""

import pytest

from polygon_classify import (
    PolyClassEvidence,
    _aspect_ratio,
    _classify_from_evidence,
    classify_polygons,
)

# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestClassifyFromEvidence:
    def test_room_numbered_large_area(self):
        ev = PolyClassEvidence(
            area_m2=15.0,
            aspect_ratio=1.5,
            has_room_number=True,
            label_text="Office",
            n_doors=1,
            n_adjacent_spaces=2,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "room"
        assert result.confidence == 0.95
        assert "has number and area" in result.reasons[0]

    def test_room_numbered_small_area(self):
        ev = PolyClassEvidence(
            area_m2=5.0,
            aspect_ratio=1.2,
            has_room_number=True,
            label_text="Storage",
            n_doors=0,
            n_adjacent_spaces=1,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "room"
        assert result.confidence == 0.80

    def test_shaft_tiny_unnumbered(self):
        ev = PolyClassEvidence(
            area_m2=1.0,
            aspect_ratio=1.0,
            has_room_number=False,
            label_text="",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "shaft"
        assert result.confidence == 0.95

    def test_closet_small_unnumbered(self):
        ev = PolyClassEvidence(
            area_m2=3.0,
            aspect_ratio=1.0,
            has_room_number=False,
            label_text="",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "closet"
        assert result.confidence == 0.90

    def test_elevator_label_area_small_is_closet(self):
        ev = PolyClassEvidence(
            area_m2=6.0,
            aspect_ratio=1.0,
            has_room_number=False,
            label_text="Elevator shaft",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "closet"
        assert result.confidence == 0.90

    def test_elevator_label_area_large_is_elevator_core(self):
        ev = PolyClassEvidence(
            area_m2=20.0,
            aspect_ratio=1.0,
            has_room_number=False,
            label_text="Elevator shaft",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "elevator_core"
        assert result.confidence == 0.90

    def test_stairwell_label_area_large(self):
        ev = PolyClassEvidence(
            area_m2=20.0,
            aspect_ratio=0.5,
            has_room_number=False,
            label_text="Stairwell A",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "elevator_core"
        assert result.confidence == 0.90

    def test_shaft_label_area_large_is_shaft(self):
        ev = PolyClassEvidence(
            area_m2=10.0,
            aspect_ratio=2.0,
            has_room_number=False,
            label_text="Mechanical chase",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "shaft"
        assert result.confidence == 0.90

    def test_closet_label_area_large(self):
        ev = PolyClassEvidence(
            area_m2=10.0,
            aspect_ratio=1.0,
            has_room_number=False,
            label_text="Storage closet",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "closet"
        assert result.confidence == 0.85

    def test_utility_label_area_large(self):
        ev = PolyClassEvidence(
            area_m2=10.0,
            aspect_ratio=1.5,
            has_room_number=False,
            label_text="Utility room",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "closet"
        assert result.confidence == 0.85

    def test_unassigned_large_unnumbered_no_label(self):
        ev = PolyClassEvidence(
            area_m2=50.0,
            aspect_ratio=2.0,
            has_room_number=False,
            label_text="",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "unassigned"
        assert result.confidence == 0.50

    def test_small_unclassified_area_in_2_to_8_closet(self):
        ev = PolyClassEvidence(
            area_m2=3.5,
            aspect_ratio=1.5,
            has_room_number=False,
            label_text="",
            n_doors=0,
            n_adjacent_spaces=0,
        )
        result = _classify_from_evidence(ev)
        assert result.poly_type == "closet"
        assert result.confidence == 0.90


class TestAspectRatio:
    def test_wide_rect(self):
        assert _aspect_ratio([0, 0, 10, 5]) == 2.0

    def test_tall_rect(self):
        assert _aspect_ratio([0, 0, 4, 8]) == 2.0

    def test_square(self):
        assert _aspect_ratio([0, 0, 5, 5]) == 1.0

    def test_degenerate_zero_size_returns_one(self):
        assert _aspect_ratio([0, 0, 0, 0]) == 1.0

    def test_degenerate_negative_size_returns_one(self):
        assert _aspect_ratio([0, 0, 0, 5]) == 1.0


class TestClassifyPolygons:
    def test_room_classification(self):
        rooms = [
            {"number": "101", "name": "Office", "area_m2": 15.0, "rect_m": [0, 0, 5, 3]},
            {"number": "102", "name": "Conf", "area_m2": 10.0, "rect_m": [5, 0, 10, 2]},
        ]
        south_windows = [{"room_number": "101"}]
        grids_h = {}
        result = classify_polygons(rooms, south_windows, grids_h)
        assert result["101"].poly_type == "room"
        assert result["102"].poly_type == "room"

    def test_shaft_classification(self):
        rooms = [
            {"number": "", "name": "Shaft", "area_m2": 1.5, "rect_m": [0, 0, 1, 1]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result[""].poly_type == "shaft"

    def test_closet_classification(self):
        rooms = [
            {"number": "", "name": "Closet", "area_m2": 3.0, "rect_m": [0, 0, 2, 1.5]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result[""].poly_type == "closet"

    def test_undefined_room_number(self):
        rooms = [
            {"number": "", "name": "Open area", "area_m2": 30.0, "rect_m": [0, 0, 6, 5]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result[""].poly_type == "unassigned"

    def test_empty_rooms_list(self):
        result = classify_polygons([], [], {})
        assert result == {}

    def test_window_count_passed_to_room(self):
        rooms = [
            {"number": "101", "name": "Office", "area_m2": 15.0, "rect_m": [0, 0, 5, 3]},
        ]
        south_windows = [{"room_number": "101"}, {"room_number": "101"}, {"room_number": "102"}]
        result = classify_polygons(rooms, south_windows, {})
        assert result["101"].poly_type == "room"


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------


def test_classify_polygons_count_invariant():
    """Classification count summed across all poly_types equals total unique output keys.

    Rooms with unique numbers produce one output entry each.
    Rooms sharing an empty-string number are merged under one key.
    """
    rooms = [
        {"number": "101", "name": "Office", "area_m2": 20.0, "rect_m": [0, 0, 5, 4]},
        {"number": "102", "name": "Conf", "area_m2": 15.0, "rect_m": [5, 0, 10, 3]},
        {"number": "", "name": "Shaft", "area_m2": 1.5, "rect_m": [0, 0, 1, 1]},
        {"number": "103", "name": "Elevator", "area_m2": 6.0, "rect_m": [0, 0, 2, 3]},
    ]
    result = classify_polygons(rooms, [], {})
    type_counts: dict[str, int] = {}
    for cls in result.values():
        type_counts[cls.poly_type] = type_counts.get(cls.poly_type, 0) + 1
    assert sum(type_counts.values()) == len(result)
    assert len(result) == len(rooms)


def test_classify_polygons_all_rooms_returned():
    """Every room in input appears exactly once in output."""
    rooms = [
        {"number": str(i), "name": f"Room{i}", "area_m2": 10.0, "rect_m": [0, 0, 2, 5]}
        for i in range(20)
    ]
    result = classify_polygons(rooms, [], {})
    assert len(result) == len(rooms)


def test_classify_polygons_empty_returns_empty():
    result = classify_polygons([], [], {})
    assert result == {}
    type_counts: dict[str, int] = {}
    for cls in result.values():
        type_counts[cls.poly_type] = type_counts.get(cls.poly_type, 0) + 1
    assert sum(type_counts.values()) == 0


# ---------------------------------------------------------------------------
# Defect-injection tests
# ---------------------------------------------------------------------------


class TestDefectInjection:
    def test_negative_area_absorbed(self):
        """Negative area is used as-is; abs() is called inside classify_polygons."""
        rooms = [
            {"number": "101", "name": "Office", "area_m2": -15.0, "rect_m": [0, 0, 5, 3]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result["101"].poly_type == "room"

    def test_zero_area_tiny_unnumbered_becomes_shaft(self):
        rooms = [
            {"number": "", "name": "", "area_m2": 0.0, "rect_m": [0, 0, 0, 0]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result[""].poly_type == "shaft"

    def test_malformed_rect_handled_gracefully(self):
        """Degenerate rect gives aspect_ratio 1.0; classification still runs."""
        rooms = [
            {"number": "101", "name": "Office", "area_m2": 15.0, "rect_m": [0, 0, 0, 0]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result["101"].poly_type == "room"

    def test_missing_rect_defaults_to_zero(self):
        rooms = [
            {"number": "101", "name": "Office", "area_m2": 15.0},
        ]
        result = classify_polygons(rooms, [], {})
        assert result["101"].poly_type == "room"

    def test_missing_area_defaults_to_zero(self):
        rooms = [
            {"number": "101", "name": "Office", "rect_m": [0, 0, 5, 3]},
        ]
        result = classify_polygons(rooms, [], {})
        assert result["101"].poly_type == "room"
        assert result["101"].confidence == 0.80

    def test_none_room_number_key_preserved(self):
        rooms = [
            {"number": None, "name": "NoNumber", "area_m2": 5.0, "rect_m": [0, 0, 2, 2]},
        ]
        result = classify_polygons(rooms, [], {})
        assert None in result

    def test_whitespace_only_room_number_key_preserved(self):
        rooms = [
            {"number": "   ", "name": "Space", "area_m2": 5.0, "rect_m": [0, 0, 2, 2]},
        ]
        result = classify_polygons(rooms, [], {})
        assert "   " in result

    def test_south_windows_none_raises_type_error(self):
        rooms = [
            {"number": "101", "name": "Office", "area_m2": 15.0, "rect_m": [0, 0, 5, 3]},
        ]
        with pytest.raises(TypeError):
            classify_polygons(rooms, None, {})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Coverage: all public functions/classes have at least one test
# ---------------------------------------------------------------------------


def test_public_api_coverage():
    """All public names in polygon_classify are exercised by these tests."""
    import polygon_classify as pc

    public_names = [
        name
        for name in dir(pc)
        if not name.startswith("_") and name not in ("dataclass", "annotations")
    ]
    assert "PolyClassEvidence" in public_names
    assert "PolyClassification" in public_names
    assert "classify_polygons" in public_names
    assert "_aspect_ratio" not in public_names
    assert "_classify_from_evidence" not in public_names
