"""#1385: the elevation -> room link joins schedule rows through ``normalize_tag``.

Two defects are pinned here:

  * the schedule row fields the #746 thermal columns carry (``u_value_w_m2k``,
    ``shgc``, ``vt``, ``thermal_confidence``, ``thermal_note``) were dropped
    on the way into the link, so a linked opening could never state the
    scheduled thermal values;
  * the join compared the detection tag with the schedule tag RAW, so an OCR
    read of ``w-1`` found no row ``W-1`` and the opening fell back to its
    measured dimensions.

Both sides of the join now speak ``normalize_tag`` (``datasets_adapter``),
the same normalisation the takeoff rollup uses.
"""

from __future__ import annotations

import pytest

from link import build_model
from link._schedules import _schedules

ELEV_PX_PER_M = 40.0
ELEV_U0_PX = 60.0
ELEV_V_GROUND_PX = 400.0
BLDG_W = 12.0
BLDG_D = 8.0
BLDG_H = 3.0

# The scheduled size differs from the measured size of the detection, so a
# link that really used the schedule row is visible in the numbers.
SCHED_WIDTH_M = 1.5
SCHED_HEIGHT_M = 1.2
MEASURED_WIDTH_M = 1.2
MEASURED_HEIGHT_M = 1.5
SILL_M = 0.9

# The #746 thermal values of the schedule row.
THERMAL = {
    "u_value_w_m2k": 1.42,
    "shgc": 0.31,
    "vt": 0.42,
    "thermal_confidence": 0.9,
    "thermal_note": "U stated as 0.25 Btu/h-ft2-F",
}


def _detection(det_id: str, tag: str, s0: float, s1: float, sill_m: float, h_m: float) -> dict:
    """One elevation detection in sheet pixels (south facade, not mirrored)."""
    return {
        "id": det_id,
        "tag": tag,
        "u0_px": ELEV_U0_PX + s0 * ELEV_PX_PER_M,
        "u1_px": ELEV_U0_PX + s1 * ELEV_PX_PER_M,
        "v_sill_px": ELEV_V_GROUND_PX - sill_m * ELEV_PX_PER_M,
        "v_head_px": ELEV_V_GROUND_PX - (sill_m + h_m) * ELEV_PX_PER_M,
    }


def _sheet_meta(sheet_id: str, discipline: str) -> dict:
    return {
        "sheet_id": sheet_id,
        "discipline": discipline,
        "revision": 1,
        "px_per_m": ELEV_PX_PER_M,
        "origin_px": [0, 0],
    }


def _bldg(window_rows: list, detection_tag: str = "w-1") -> dict:
    """A one-room building with one window detection on the south elevation."""
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
    det = _detection("W1", detection_tag, 1.0, 1.0 + MEASURED_WIDTH_M, SILL_M, MEASURED_HEIGHT_M)
    return {
        "building_id": "bldg_join",
        "level_id": "L1",
        "wall_height_m": BLDG_H,
        "W_m": BLDG_W,
        "D_m": BLDG_D,
        "rooms": rooms,
        "grids_v": {},
        "grids_h": {},
        "fixtures": [],
        "components": [],
        "zones": [],
        "window_schedule": window_rows,
        "lighting_schedule": [],
        "sheets": {
            "arch": {"meta": _sheet_meta("arch_A101", "arch_plan")},
            "lighting": {"meta": _sheet_meta("light_E101", "lighting_plan")},
            "mech": {"meta": _sheet_meta("mech_M101", "mech_plan")},
            "elev_nogrid": {
                "meta": {
                    **_sheet_meta("elev_A202", "elevation"),
                    "facade": "south",
                    "facade_ref_corner_m": [0.0, 8.0],
                    "facade_length_m": BLDG_W,
                    "elevation_mirrored": False,
                },
                "data": {
                    "windows": [det],
                    "bubbles": [],
                    "wall_u0_px": ELEV_U0_PX,
                    "v_ground_px": ELEV_V_GROUND_PX,
                    "px_per_m": ELEV_PX_PER_M,
                },
            },
        },
    }


def _openings(model, category="window"):
    return [op for sp in model.spaces.values() for op in sp.openings if op.category == category]


class TestScheduleRows:
    """The schedule row fields survive the link."""

    def test_thermal_fields_of_the_row_are_kept(self):
        row = {
            "tag": "W-1",
            "category": "window",
            "width_m": SCHED_WIDTH_M,
            "height_m": SCHED_HEIGHT_M,
            **THERMAL,
        }
        win_sched, _ = _schedules({"window_schedule": [row], "lighting_schedule": []})
        entry = win_sched["W-1"]
        assert entry.u_value_w_m2k == pytest.approx(THERMAL["u_value_w_m2k"])
        assert entry.shgc == pytest.approx(THERMAL["shgc"])
        assert entry.vt == pytest.approx(THERMAL["vt"])
        assert entry.thermal_confidence == pytest.approx(THERMAL["thermal_confidence"])
        assert entry.thermal_note == THERMAL["thermal_note"]

    def test_a_row_without_thermal_data_is_unchanged(self):
        row = {"tag": "W-1", "category": "window", "width_m": 1.5, "height_m": 1.2}
        win_sched, _ = _schedules({"window_schedule": [row], "lighting_schedule": []})
        entry = win_sched["W-1"]
        assert (entry.width_m, entry.height_m) == (1.5, 1.2)
        assert entry.u_value_w_m2k is None
        assert entry.shgc is None
        assert entry.vt is None
        assert entry.thermal_confidence is None
        assert entry.thermal_note == ""

    def test_the_rows_are_keyed_by_the_normalised_tag(self):
        rows = [
            {"tag": "W-1", "category": "window", "width_m": 1.5, "height_m": 1.2},
            {"tag": "w 2", "category": "window", "width_m": 0.9, "height_m": 2.1},
        ]
        win_sched, _ = _schedules({"window_schedule": rows, "lighting_schedule": []})
        assert set(win_sched) == {"W-1", "W2"}

    def test_two_rows_with_the_same_normalised_tag_keep_the_last_one(self):
        # The takeoff rollup lets the last row of a normalised key win; the
        # same convention is used here, and the collision is traced.
        rows = [
            {"tag": "W-1", "category": "window", "width_m": 1.5, "height_m": 1.2},
            {"tag": "w-1", "category": "window", "width_m": 0.9, "height_m": 2.1},
        ]
        win_sched, _ = _schedules({"window_schedule": rows, "lighting_schedule": []})
        assert set(win_sched) == {"W-1"}
        entry = win_sched["W-1"]
        assert (entry.width_m, entry.height_m) == (0.9, 2.1)
        assert "W-1" in entry.note and "w-1" in entry.note


class TestElevationJoin:
    """The link joins a normalised detection tag to the schedule row."""

    @pytest.mark.parametrize(
        ("schedule_tag", "detection_tag"),
        [
            ("W-1", "w-1"),
            ("W 1", "w 1"),
            ("W1", "w1"),
        ],
    )
    def test_a_variant_tag_links_to_the_schedule_row(self, schedule_tag, detection_tag):
        rows = [
            {
                "tag": schedule_tag,
                "category": "window",
                "width_m": SCHED_WIDTH_M,
                "height_m": SCHED_HEIGHT_M,
                **THERMAL,
            }
        ]
        model, report = build_model(
            _bldg(rows, detection_tag), elevation_key="elev_nogrid", building_name="join-test"
        )
        windows = _openings(model)
        assert len(windows) == 1
        win = windows[0]
        # the SCHEDULED size, not the measured one
        assert win.width_m == pytest.approx(SCHED_WIDTH_M)
        assert win.height_m == pytest.approx(SCHED_HEIGHT_M)
        assert report.windows_linked == 1

        links = [lnk for lnk in model.symbol_linkages if lnk.category == "window"]
        assert len(links) == 1
        entry = links[0].schedule_entry
        assert entry is not None
        assert entry.tag == schedule_tag
        assert (entry.width_m, entry.height_m) == (SCHED_WIDTH_M, SCHED_HEIGHT_M)
        # the thermal fields of the row are intact on the linked entry
        assert entry.u_value_w_m2k == pytest.approx(THERMAL["u_value_w_m2k"])
        assert entry.shgc == pytest.approx(THERMAL["shgc"])
        assert entry.vt == pytest.approx(THERMAL["vt"])
        assert entry.thermal_confidence == pytest.approx(THERMAL["thermal_confidence"])
        assert entry.thermal_note == THERMAL["thermal_note"]

    def test_an_exactly_matching_tag_links_as_before(self):
        rows = [
            {
                "tag": "W-1",
                "category": "window",
                "width_m": SCHED_WIDTH_M,
                "height_m": SCHED_HEIGHT_M,
                **THERMAL,
            }
        ]
        model, report = build_model(
            _bldg(rows, "W-1"), elevation_key="elev_nogrid", building_name="join-test"
        )
        windows = _openings(model)
        assert len(windows) == 1
        assert windows[0].width_m == pytest.approx(SCHED_WIDTH_M)
        assert windows[0].height_m == pytest.approx(SCHED_HEIGHT_M)
        assert report.windows_linked == 1
        entry = [lnk for lnk in model.symbol_linkages if lnk.category == "window"][0].schedule_entry
        assert entry is not None
        assert entry.u_value_w_m2k == pytest.approx(THERMAL["u_value_w_m2k"])

    def test_an_unknown_tag_falls_back_to_the_measured_size(self):
        rows = [
            {
                "tag": "W-1",
                "category": "window",
                "width_m": SCHED_WIDTH_M,
                "height_m": SCHED_HEIGHT_M,
                **THERMAL,
            }
        ]
        model, _ = build_model(
            _bldg(rows, "W 9"), elevation_key="elev_nogrid", building_name="join-test"
        )
        windows = _openings(model)
        assert len(windows) == 1
        # no schedule row matches the detection: the measured width is used
        # and the height stays unknown, exactly as before the fix
        assert windows[0].width_m == pytest.approx(MEASURED_WIDTH_M, abs=0.01)
        assert windows[0].height_m is None
        entry = [lnk for lnk in model.symbol_linkages if lnk.category == "window"][0].schedule_entry
        assert entry is None
