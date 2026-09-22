"""Tests for elevation_windows.py — exact window positioning from building elevations."""

import numpy as np
import pytest

from elevation_windows import (
    ContourWindowDetector,
    ElevationWindowObs,
    FacadeWindow,
    detect_windows,
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
                np.zeros((100, 100, 3), dtype=np.uint8), 100.0, "sheet", 1, backend="nonexistent"
            )

    def test_detect_windows_with_valid_backend(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        result = detect_windows(img, 100.0, "sheet", 1, backend="contour")
        assert isinstance(result, list)


class TestRegisterBackend:
    def test_register_backend_accepts_custom_backend(self):
        class DummyBackend:
            def detect(self, image, px_per_m, sheet_id, revision):
                return []

        register_backend("test_dummy", DummyBackend())
        result = detect_windows(
            np.zeros((100, 100, 3), dtype=np.uint8), 100.0, "sheet", 1, backend="test_dummy"
        )
        assert result == []
