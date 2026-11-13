"""#1397: the lighting joins compare schedule tags through ``normalize_tag``.

Two defects are pinned here:

  * the lighting schedule was keyed by the RAW tag (``row["tag"].strip()``),
    while the window/door schedule has been keyed by the normalised tag
    since #1385 -- so a schedule row ``A-1`` was invisible to a detection
    read ``a-1``;
  * the two lighting joins (``lighting.py`` and ``link/_lighting.py``)
    compared the detection tag with the schedule tag RAW, so the same
    variant never joined.

Both sides of both lighting joins now speak ``normalize_tag``
(``datasets_adapter``), the same normalisation the takeoff rollup and the
window/door elevation join use. The schedule row keeps the tag as the
schedule states it; only the lookup key is normalised.
"""

from __future__ import annotations

import io

import pytest

from datasets_adapter import Detection, DrawingScale, ScheduleEntry, parse_lighting_schedule_csv
from lighting import lighting_takeoff
from link import build_model
from link._schedules import _schedules
from room_labels import LabeledSpace

WATTS = 40.0


def _csv(tag: str) -> io.StringIO:
    buf = io.StringIO()
    buf.write("tag,description,lamp_type,watts\n")
    buf.write(f"{tag},2x4 LED troffer,LED,{WATTS}\n")
    buf.seek(0)
    return buf


def _detection(tag: str) -> Detection:
    return Detection(label="troffer", tag=tag, score=1.0, bbox=(40, 40, 60, 60), source="test")


def _spaces() -> list[LabeledSpace]:
    return [
        LabeledSpace(
            polygon_px=[(0, 0), (100, 0), (100, 100), (0, 100)], name="Office", number="101"
        ),
    ]


class TestLightingScheduleParser:
    """The lighting schedule is keyed by the normalised tag."""

    def test_a_lowercase_row_is_keyed_by_the_normalised_tag(self):
        sched = parse_lighting_schedule_csv(_csv("a-1"))
        assert set(sched) == {"A-1"}
        assert sched["A-1"].watts == pytest.approx(WATTS)

    def test_a_spaced_row_is_keyed_by_the_normalised_tag(self):
        sched = parse_lighting_schedule_csv(_csv("A 1"))
        assert set(sched) == {"A1"}

    def test_the_entry_keeps_the_tag_as_the_schedule_states_it(self):
        sched = parse_lighting_schedule_csv(_csv("a-1"))
        assert sched["A-1"].tag == "a-1"


class TestLightingTakeoffJoin:
    """The takeoff joins a normalised detection tag to the schedule row."""

    @pytest.mark.parametrize(
        ("schedule_tag", "detection_tag"),
        [
            ("A-1", "a-1"),
            ("A 1", "a1"),
            ("A-1", "A-1"),
        ],
    )
    def test_a_variant_tag_joins_the_schedule_row(self, schedule_tag, detection_tag):
        sched = parse_lighting_schedule_csv(_csv(schedule_tag))
        result = lighting_takeoff(
            detections=[_detection(detection_tag)],
            schedule=sched,
            spaces=_spaces(),
            scale=DrawingScale(m_per_px=0.01),
        )
        assert len(result.lines) == 1
        assert result.lines[0].count == 1
        assert result.lines[0].watts_each == pytest.approx(WATTS)
        assert result.total_w == pytest.approx(WATTS)
        assert result.unmatched == []
        assert result.unassigned == []

    def test_the_per_space_rollup_sees_the_joined_watts(self):
        sched = parse_lighting_schedule_csv(_csv("A-1"))
        result = lighting_takeoff(
            detections=[_detection("a-1")],
            schedule=sched,
            spaces=_spaces(),
            scale=DrawingScale(m_per_px=0.01),
        )
        assert result.rooms[0].fixture_count == 1
        assert result.rooms[0].watts == pytest.approx(WATTS)
        # 100x100 px at 0.01 m/px = 1 m²
        assert result.rooms[0].lpd_w_m2 == pytest.approx(WATTS)


def _sheet_meta(sheet_id: str, discipline: str) -> dict:
    return {
        "sheet_id": sheet_id,
        "discipline": discipline,
        "revision": 1,
        "px_per_m": 40.0,
        "origin_px": [0, 0],
    }


def _bldg(fixture_tag: str = "a-1", rows: list | None = None) -> dict:
    """A one-room building with one lighting fixture."""
    rooms = [
        {
            "number": "101",
            "name": "Room 101",
            "rect_m": [0.0, 0.0, 6.0, 8.0],
            "polygon_m": [[0.0, 0.0], [6.0, 0.0], [6.0, 8.0], [0.0, 8.0]],
            "area_m2": 48.0,
            "zone": "Z1",
        }
    ]
    if rows is None:
        rows = [
            {
                "tag": "A-1",
                "description": "2x4 LED troffer",
                "lamp_type": "LED",
                "watts": WATTS,
            }
        ]
    return {
        "building_id": "bldg_light_join",
        "level_id": "L1",
        "wall_height_m": 3.0,
        "W_m": 12.0,
        "D_m": 8.0,
        "rooms": rooms,
        "grids_v": {},
        "grids_h": {},
        "fixtures": [
            {
                "id": "F1",
                "class": "Troffer 2x4",
                "tag": fixture_tag,
                "x_m": 1.0,
                "y_m": 1.0,
                "x_px": 40.0,
                "y_px": 40.0,
                "room_number": "101",
            }
        ],
        "components": [],
        "zones": [],
        "window_schedule": [],
        "lighting_schedule": rows,
        "sheets": {
            "arch": {"meta": _sheet_meta("arch_A101", "arch_plan")},
            "lighting": {"meta": _sheet_meta("light_E101", "lighting_plan")},
            "mech": {"meta": _sheet_meta("mech_M101", "mech_plan")},
        },
    }


class TestLinkScheduleJoin:
    """The link joins a normalised fixture tag to the schedule row."""

    def test_a_variant_fixture_tag_gets_its_watts(self):
        model, _ = build_model(_bldg("a-1"), elevation_key=None, building_name="light-join")
        space = list(model.spaces.values())[0]
        assert len(space.lighting.fixtures) == 1
        assert space.lighting.fixtures[0].watts == pytest.approx(WATTS)
        assert space.lighting.total_w == pytest.approx(WATTS)
        assert not [r for r in model.review_queue if r.kind == "fixture_schedule"]

    def test_the_lighting_schedule_is_keyed_by_the_normalised_tag(self):
        rows = [
            {"tag": "A-1", "description": "d", "lamp_type": "LED", "watts": WATTS},
            {"tag": "a 1", "description": "d", "lamp_type": "LED", "watts": WATTS},
        ]
        _, light_sched = _schedules(_bldg(rows=rows))
        assert set(light_sched) == {"A-1", "A1"}

    def test_two_rows_with_the_same_normalised_tag_keep_the_last_one(self):
        # The takeoff rollup lets the last row of a normalised key win; the
        # same convention is used here, and the collision is traced.
        rows = [
            {"tag": "A-1", "description": "d", "lamp_type": "LED", "watts": WATTS},
            {"tag": "a-1", "description": "d", "lamp_type": "LED", "watts": 55.0},
        ]
        _, light_sched = _schedules(_bldg(rows=rows))
        assert set(light_sched) == {"A-1"}
        entry = light_sched["A-1"]
        assert entry.watts == pytest.approx(55.0)
        assert "A-1" in entry.note and "a-1" in entry.note
