"""Tests for lighting.py — lighting zone construction and LPD calculation."""

import pytest

from datasets_adapter import Detection, DrawingScale, ScheduleEntry
from lighting import (
    LightingResult,
    assign_fixtures_to_spaces,
    lighting_takeoff,
    summarize_lighting,
)
from room_labels import LabeledSpace


def _detection(tag: str, bbox: tuple[float, float, float, float], score: float = 1.0):
    return Detection(label="troffer", tag=tag, score=score, bbox=bbox, source="test")


class TestAssignFixturesToSpaces:
    """assign_fixtures_to_spaces maps fixture detections to rooms by centroid."""

    def test_single_fixture_inside_one_room(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        dets = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        result = assign_fixtures_to_spaces(dets, spaces)
        assert 0 in result
        assert len(result[0]) == 1
        assert -1 not in result or len(result[-1]) == 0

    def test_fixture_outside_all_rooms_goes_to_unassigned(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        dets = [_detection(tag="T84", bbox=(200, 200, 220, 220))]
        result = assign_fixtures_to_spaces(dets, spaces)
        assert -1 in result
        assert len(result[-1]) == 1

    def test_multiple_fixtures_multiple_rooms(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
            LabeledSpace(polygon_px=[(100, 0), (200, 0), (200, 100), (100, 100)], name="R2", number="102"),
        ]
        dets = [
            _detection(tag="T84", bbox=(40, 40, 60, 60)),
            _detection(tag="T84", bbox=(140, 40, 160, 60)),
        ]
        result = assign_fixtures_to_spaces(dets, spaces)
        assert len(result[0]) == 1
        assert len(result[1]) == 1

    def test_fixture_inside_second_room(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
            LabeledSpace(polygon_px=[(100, 0), (200, 0), (200, 100), (100, 100)], name="R2", number="102"),
        ]
        dets = [_detection(tag="T84", bbox=(130, 40, 150, 60))]
        result = assign_fixtures_to_spaces(dets, spaces)
        assert len(result[1]) == 1


class TestLightingTakeoff:
    """lighting_takeoff joins fixture detections to schedule watts and computes LPD."""

    def _scale(self, m_per_px: float = 0.01):
        return DrawingScale(m_per_px=m_per_px)

    def test_happy_path_total_watts_and_lpd(self):
        spaces = [
            LabeledSpace(
                polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)],
                name="Office",
                number="101",
            ),
        ]
        # 100x100 px at 0.01 m/px = 1x1 m = 1 m²
        detections = [
            _detection(tag="T84", bbox=(40, 40, 60, 60)),
            _detection(tag="T84", bbox=(50, 50, 70, 70)),
        ]
        schedule = {
            "T84": ScheduleEntry(
                tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0, description="2x4 LED"
            )
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(m_per_px=0.01),
        )
        assert result.total_fixtures == 2
        assert result.total_w == pytest.approx(128.0)
        assert len(result.lines) == 1
        assert result.lines[0].tag == "T84"
        assert result.lines[0].count == 2
        assert result.lines[0].total_w == pytest.approx(128.0)
        # 64 W x 2 / 1 m² = 128 W/m²
        assert result.rooms[0].lpd_w_m2 == pytest.approx(128.0)
        assert result.unmatched == []
        assert result.unassigned == []

    def test_unmatched_detections_tag_not_in_schedule(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [
            _detection(tag="T84", bbox=(40, 40, 60, 60)),
            _detection(tag="MISSING", bbox=(50, 50, 70, 70)),
        ]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
        )
        assert len(result.unmatched) == 1
        assert result.unmatched[0].tag == "MISSING"

    def test_no_watts_schedule_entry_lacks_watts(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        schedule = {
            "T84": ScheduleEntry(
                tag="T84", category="lighting", width_m=None, height_m=None, watts=None, description="TBD"
            )
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
        )
        assert len(result.no_watts) == 1
        assert result.no_watts[0].tag == "T84"
        assert result.lines[0].total_w is None
        assert result.total_w == 0.0

    def test_unassigned_fixture_centroid_in_no_room(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [
            _detection(tag="T84", bbox=(40, 40, 60, 60)),
            _detection(tag="T84", bbox=(200, 200, 220, 220)),
        ]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
        )
        assert len(result.unassigned) == 1
        assert result.total_fixtures == 2

    def test_space_with_no_fixtures_has_zero_watts(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
            LabeledSpace(polygon_px=[(100, 0), (200, 0), (200, 100), (100, 100)], name="R2", number="102"),
        ]
        detections = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
        )
        assert result.rooms[0].fixture_count == 1
        assert result.rooms[1].fixture_count == 0
        assert result.rooms[1].watts == 0.0

    def test_space_areas_m2_override_polygon_measurement(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
            space_areas_m2=[10.0],
        )
        assert result.rooms[0].area_m2 == 10.0
        # 64 W / 10 m² = 6.4 W/m²
        assert result.rooms[0].lpd_w_m2 == pytest.approx(6.4)

    def test_no_scale_gives_none_lpd(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=DrawingScale(m_per_px=None),
        )
        assert result.rooms[0].lpd_w_m2 is None
        assert result.rooms[0].lpd_w_ft2 is None
        assert result.rooms[0].area_m2 is None
        assert result.rooms[0].watts == pytest.approx(64.0)

    def test_multiple_tags_rollup_correctly(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [
            _detection(tag="T84", bbox=(40, 40, 60, 60)),
            _detection(tag="T84", bbox=(50, 50, 70, 70)),
            _detection(tag="LED", bbox=(10, 10, 30, 30)),
        ]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0),
            "LED": ScheduleEntry(tag="LED", category="lighting", width_m=None, height_m=None, watts=30.0),
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=self._scale(),
        )
        assert result.total_fixtures == 3
        assert result.total_w == pytest.approx(64 * 2 + 30)
        assert len(result.lines) == 2
        assert {ln.tag for ln in result.lines} == {"T84", "LED"}


class TestSummarizeLighting:
    def test_summarize_includes_total_and_room_lines(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="Office", number="101"),
        ]
        detections = [_detection(tag="T84", bbox=(40, 40, 60, 60))]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=DrawingScale(m_per_px=0.01),
        )
        summary = summarize_lighting(result)
        assert "64.0 W" in summary
        assert "fixture" in summary.lower()

    def test_summarize_reports_unmatched_tags(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [_detection(tag="MISSING", bbox=(40, 40, 60, 60))]
        schedule = {}
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=DrawingScale(m_per_px=0.01),
        )
        summary = summarize_lighting(result)
        assert "UNMATCHED" in summary

    def test_summarize_reports_unassigned_count(self):
        spaces = [
            LabeledSpace(polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="R1", number="101"),
        ]
        detections = [_detection(tag="T84", bbox=(200, 200, 220, 220))]
        schedule = {
            "T84": ScheduleEntry(tag="T84", category="lighting", width_m=None, height_m=None, watts=64.0)
        }
        result = lighting_takeoff(
            detections=detections,
            schedule=schedule,
            spaces=spaces,
            scale=DrawingScale(m_per_px=0.01),
        )
        summary = summarize_lighting(result)
        assert "UNASSIGNED" in summary
