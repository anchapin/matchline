"""Tests for elevation_windows.py — exact window positioning from building elevations."""

import numpy as np
import pytest

from building_model import BuildingModel, Provenance, Space
from datasets_adapter import ScheduleEntry
from elevation_windows import (
    ContourWindowDetector,
    ElevationWindowObs,
    FacadeWindow,
    MergedWindow,
    attach_merged_windows,
    compute_daylit_zones,
    detect_windows,
    link_elevations,
    register_backend,
)


class TestElevationWindowObs:
    def test_elevation_window_obs_creation(self):
        obs = ElevationWindowObs(
            id="w1",
            u0_px=10.0,
            u1_px=110.0,
            v_head_px=50.0,
            v_sill_px=150.0,
            confidence=0.95,
            method="contour",
        )
        assert obs.id == "w1"
        assert obs.u0_px == 10.0
        assert obs.confidence == 0.95


class TestFacadeWindow:
    def test_facade_window_creation(self):
        fw = FacadeWindow(
            id="sheet:w1",
            sheet_id="sheet",
            facade="south",
            s0_m=0.5,
            s1_m=1.7,
            s_center_m=1.1,
            width_m=1.2,
            sill_m=0.9,
            head_m=2.4,
            height_m=1.5,
            category_hint="window",
            confidence=0.9,
        )
        assert fw.s_center_m == 1.1
        assert fw.category_hint == "window"


class TestContourWindowDetector:
    def test_contour_detector_rejects_invalid_backend(self):
        detector = ContourWindowDetector()
        assert isinstance(detector, ContourWindowDetector)


class TestDetectWindows:
    def test_detect_windows_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown window detector backend"):
            detect_windows(
                np.zeros((100, 100), dtype=np.uint8), 100.0, "sheet", 1, backend="nonexistent"
            )

    def test_detect_windows_with_grayscale_image(self):
        # exc_type=ImportError: a GUI build of cv2 missing libGL raises a plain
        # ImportError, not ModuleNotFoundError, and should still skip (#535).
        pytest.importorskip("cv2", exc_type=ImportError)
        img = np.zeros((100, 100), dtype=np.uint8)
        result = detect_windows(img, 100.0, "sheet", 1, backend="contour")
        assert isinstance(result, list)


class TestRegisterBackend:
    def test_register_backend_accepts_custom_backend(self):
        class DummyBackend:
            def detect(self, image, px_per_m, sheet_id, revision):
                return []

        register_backend("test_dummy", DummyBackend())
        result = detect_windows(
            np.zeros((100, 100), dtype=np.uint8), 100.0, "sheet", 1, backend="test_dummy"
        )
        assert result == []


# ---------------------------------------------------------------------------
# #1373: a grade-level opening is a door, not a window
# ---------------------------------------------------------------------------


def _space():
    return Space(
        id="L1-101",
        level_id="L1",
        number="101",
        polygon_m=[[0.0, 0.0], [8.0, 0.0], [8.0, 6.0], [0.0, 6.0]],
    )


def _bldg():
    return {
        "building_id": "B1",
        "W_m": 8.0,
        "D_m": 6.0,
        "rooms": [{"number": "101", "rect_m": (0.0, 0.0, 8.0, 6.0)}],
    }


def _merged(i, s0, s1, sill, head, category_hint, width=None, height=None):
    return MergedWindow(
        id=f"MW-{i}",
        facade="south",
        s0_m=s0,
        s1_m=s1,
        s_center_m=(s0 + s1) / 2,
        width_m=width if width is not None else round(s1 - s0, 3),
        sill_m=sill,
        head_m=head,
        height_m=height if height is not None else round(head - sill, 3),
        tag=None,
        category_hint=category_hint,
        confidence=0.9,
        provenance=Provenance(
            sheet_id="elev_A201", revision=1, method="elevation_dedup", confidence=0.9
        ),
    )


def _sched(tag, category, width, height):
    return ScheduleEntry(tag=tag, category=category, width_m=width, height_m=height)


def test_door_hint_attaches_as_door_and_window_hint_as_window():
    """A merged observation tagged as a door opens a DOOR, not a window."""
    model = BuildingModel(name="doors")
    sp = _space()
    win_sched = {
        "D1": _sched("D1", "door", 0.9, 2.1),
        "W1": _sched("W1", "window", 1.2, 1.5),
    }
    door = _merged(1, 1.0, 1.9, 0.0, 2.1, "door", width=0.9, height=2.1)
    win = _merged(2, 3.0, 4.2, 0.9, 2.4, "window", width=1.2, height=1.5)

    attached, unlinked = attach_merged_windows(
        model, [sp], _bldg(), [door, win], win_sched, "south"
    )

    assert len(attached) == 2
    assert unlinked == []
    ops = {o.tag: o for o in sp.openings}
    assert ops["D1"].category == "door"
    assert ops["W1"].category == "window"


def test_tag_and_dimension_lookup_stays_inside_one_category():
    """A door draws only on door rows; a window only on window rows."""
    model = BuildingModel(name="cats")
    sp = _space()
    # The window row is the nearer fit for the door's measured size: it must
    # not be able to tag or dimension the door.
    win_sched = {
        "W1": _sched("W1", "window", 0.9, 2.1),
        "D1": _sched("D1", "door", 0.95, 2.15),
    }
    door = _merged(1, 1.0, 1.9, 0.0, 2.1, "door", width=0.9, height=2.1)
    win = _merged(2, 3.0, 3.9, 0.9, 3.0, "window", width=0.9, height=2.1)

    attach_merged_windows(model, [sp], _bldg(), [door, win], win_sched, "south")

    ops = {o.tag: o for o in sp.openings}
    assert door.tag == "D1"
    assert (ops["D1"].width_m, ops["D1"].height_m) == (0.95, 2.15)
    assert win.tag == "W1"
    assert (ops["W1"].width_m, ops["W1"].height_m) == (0.9, 2.1)


def test_sill_vs_schedule_category_mismatch_is_surfaced():
    """A door whose nearest schedule row is a window is flagged, not resolved."""
    model = BuildingModel(name="mismatch")
    sp = _space()
    win_sched = {"W1": _sched("W1", "window", 0.9, 2.1)}
    door = _merged(1, 1.0, 1.9, 0.0, 2.1, "door", width=0.9, height=2.1)

    attach_merged_windows(model, [sp], _bldg(), [door], win_sched, "south")

    op = sp.openings[0]
    assert op.category == "door"
    assert op.tag == ""  # no door row describes it: measured dims only
    assert "CATEGORY MISMATCH" in op.provenance.note
    assert "schedule row 'W1' is a window" in op.provenance.note
    flags = [
        i
        for i in model.review_queue
        if "CATEGORY MISMATCH" in i.description or "is a window" in i.description
    ]
    assert flags, "the category mismatch must reach the review queue"


def test_door_opening_does_not_feed_the_daylight_pass():
    """A door is not fenestration: it produces no sidelighted zone."""
    model = BuildingModel(name="daylight")
    sp = _space()
    win_sched = {
        "D1": _sched("D1", "door", 0.9, 2.1),
        "W1": _sched("W1", "window", 1.2, 1.5),
    }
    door = _merged(1, 1.0, 1.9, 0.0, 2.1, "door", width=0.9, height=2.1)
    win = _merged(2, 3.0, 4.2, 0.9, 2.4, "window", width=1.2, height=1.5)

    attach_merged_windows(model, [sp], _bldg(), [door, win], win_sched, "south")
    compute_daylit_zones(sp, 8.0, 6.0)

    door_op = next(o for o in sp.openings if o.category == "door")
    zones = sp.daylight.primary + sp.daylight.secondary
    assert zones, "the window must still light the room"
    assert all(z.window_id != door_op.id for z in zones)


def test_door_only_room_gets_no_daylit_zone():
    """A room whose only opening is a door is not daylit."""
    model = BuildingModel(name="daylight_door")
    sp = _space()
    win_sched = {"D1": _sched("D1", "door", 0.9, 2.1)}
    door = _merged(1, 1.0, 1.9, 0.0, 2.1, "door", width=0.9, height=2.1)

    attach_merged_windows(model, [sp], _bldg(), [door], win_sched, "south")
    compute_daylit_zones(sp, 8.0, 6.0)

    assert sp.daylight.primary == []
    assert sp.daylight.secondary == []


class _StubDetector:
    """Detector stub returning fixed observations (no cv2 needed)."""

    def __init__(self, obs):
        self._obs = obs

    def detect(self, image, px_per_m, sheet_id, revision):
        return list(self._obs)


def test_link_elevations_counts_doors_apart_from_windows():
    """The report carries door attach/unlinked counts (like link/_elevation.py)."""
    model = BuildingModel(name="report")
    sp = _space()
    bldg = _bldg()
    bldg["south_windows"] = []
    bldg["grids_v"] = {}
    bldg["sheets"] = {
        "arch": {"meta": {"sheet_id": "arch_A101", "revision": 1}, "data": {}},
        "elev": {
            "meta": {"sheet_id": "elev_A201", "revision": 1, "facade": "south"},
            "data": {
                "px_per_m": 100.0,
                "v_ground_px": 500.0,
                "wall_u0_px": 0.0,
                "bubbles": [],
                "windows": [],
            },
            "image": np.zeros((10, 10), dtype=np.uint8),
        },
    }
    # px_per_m = 100, ground at v=500 px: a door at s=[1.0, 1.9] m with sill 0,
    # and a window at s=[3.0, 4.2] m with sill 0.9 m.
    obs = [
        ElevationWindowObs(
            id="D1", u0_px=100.0, u1_px=190.0, v_head_px=290.0, v_sill_px=500.0, confidence=0.9,
            method="stub",
        ),
        ElevationWindowObs(
            id="W1", u0_px=300.0, u1_px=420.0, v_head_px=260.0, v_sill_px=410.0, confidence=0.9,
            method="stub",
        ),
    ]
    register_backend("stub_1373", _StubDetector(obs))
    win_sched = {
        "D1": _sched("D1", "door", 0.9, 2.1),
        "W1": _sched("W1", "window", 1.2, 1.5),
    }

    report = link_elevations(
        model, bldg, [sp], ("elev",), win_sched, detector_backend="stub_1373"
    )

    assert report.n_doors_attached == 1
    assert report.n_windows_attached == 1
    assert report.n_doors_unlinked == 0
    assert report.n_windows_unlinked == 0
    assert report.n_attached == 2
    ops = {o.category for o in sp.openings}
    assert ops == {"door", "window"}
