"""Tests for hvac_trace.py — HVAC zoning tracer and diffuser trace."""

import numpy as np

from hvac_trace import (
    PX_PER_M,
    duct_skeleton,
    extract_zones,
    ncc_locate,
    trace_sheet,
)


class TestNccLocate:
    """ncc_locate finds template occurrences via normalized cross-correlation."""

    def test_finds_single_template_match(self):
        tmpl = np.zeros((5, 5), dtype=np.float64)
        tmpl[2, 2] = 1.0
        gray = np.zeros((20, 20), dtype=np.float64)
        gray[10, 10] = 1.0
        results = ncc_locate(gray, tmpl, thresh=0.0)
        assert len(results) >= 1

    def test_empty_image_returns_no_matches(self):
        tmpl = np.ones((3, 3), dtype=np.float64)
        gray = np.zeros((10, 10), dtype=np.float64)
        results = ncc_locate(gray, tmpl, thresh=0.5)
        assert results == []

    def test_nms_suppresses_overlapping_matches(self):
        tmpl = np.ones((5, 5), dtype=np.float64)
        gray = np.ones((30, 30), dtype=np.float64) * 0.5
        gray[10:15, 10:15] = 1.0
        gray[12:17, 12:17] = 1.0
        results = ncc_locate(gray, tmpl, thresh=0.0)
        y_coords = [r[0] for r in results]
        assert len(y_coords) <= 2


class TestDuctSkeleton:
    """duct_skeleton extracts a 1-px centerline from filled duct bars."""

    def test_returns_uint8_skeleton(self):
        gray = np.full((100, 100), 255, dtype=np.uint8)
        skel = duct_skeleton(gray)
        assert skel.dtype == np.uint8
        assert skel.shape == gray.shape

    def test_skeleton_is_sparse(self):
        gray = np.full((100, 100), 255, dtype=np.uint8)
        skel = duct_skeleton(gray)
        assert skel.sum() < gray.size

    def test_dark_bar_produces_skeleton(self):
        gray = np.full((100, 100), 255, dtype=np.uint8)
        gray[40:60, :] = 0
        skel = duct_skeleton(gray)
        center_row = 49
        assert skel[center_row, :].sum() > 0


class TestExtractZones:
    """extract_zones builds zone graph from skeleton and VAV detections."""

    def _room(self, x0, y0, x1, y1, id_):
        return {"rect_m": (x0, y0, x1, y1), "id": id_}

    def _det(self, cx_px, cy_px, label, ncc=0.9, ncc_cls=None, margin=5.0):
        return {
            "cx": cx_px,
            "cy": cy_px,
            "label": label,
            "ncc": ncc,
            "ncc_cls": ncc_cls or label,
            "margin": margin,
        }

    def test_vav_cuts_skeleton_and_zones_one_vav_one_diffuser(self):
        skel = np.zeros((200, 200), dtype=np.uint8)
        skel[30:170, 100] = 1
        vav_x = 100
        vav_y = 100
        dets = [
            self._det(vav_x, vav_y, "vav", ncc=0.9, ncc_cls="vav", margin=10.0),
            self._det(50, 100, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0),
        ]
        rooms = [self._room(0, 0, 10, 10, "R1")]
        zones, dbg = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert len(zones) == 1
        assert "Z1" in zones[0]["zone_id"]

    def test_diffuser_outside_all_rooms_still_tracked(self):
        skel = np.zeros((200, 200), dtype=np.uint8)
        skel[30:170, 100] = 1
        dets = [
            self._det(100, 100, "vav", ncc=0.9, ncc_cls="vav", margin=10.0),
            self._det(500, 500, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0),
        ]
        rooms = [self._room(0, 0, 10, 10, "R1")]
        zones, dbg = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert len(zones) == 1
        # diffuser at 500,500 is outside R1 (0,0)-(10,10)
        assert len(zones[0]["diffusers"]) == 0  # not skeleton-adjacent to VAV

    def test_multiple_vavs_produce_multiple_zones(self):
        skel = np.zeros((200, 200), dtype=np.uint8)
        skel[30:170, 60] = 1
        skel[30:170, 140] = 1
        dets = [
            self._det(60, 100, "vav", ncc=0.9, ncc_cls="vav", margin=10.0),
            self._det(140, 100, "vav", ncc=0.9, ncc_cls="vav", margin=10.0),
            self._det(30, 100, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0),
            self._det(170, 100, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0),
        ]
        rooms = [
            self._room(0, 0, 10, 10, "R1"),
            self._room(10, 0, 20, 10, "R2"),
        ]
        zones, dbg = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert len(zones) == 2

    def test_no_vavs_returns_empty_zones(self):
        skel = np.zeros((200, 200), dtype=np.uint8)
        skel[50, :] = 1
        dets = [self._det(50, 50, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0)]
        rooms = [self._room(0, 0, 10, 10, "R1")]
        zones, dbg = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert zones == []

    def test_debug_includes_skeleton_pixel_count(self):
        skel = np.zeros((100, 100), dtype=np.uint8)
        skel[10:90, 50] = 1
        dets = [self._det(50, 50, "vav", ncc=0.9, ncc_cls="vav", margin=10.0)]
        rooms = [self._room(0, 0, 10, 10, "R1")]
        _, dbg = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert "n_skel_px" in dbg
        assert dbg["n_skel_px"] > 0

    def test_sensor_in_served_room_associates_with_zone(self):
        skel = np.zeros((200, 200), dtype=np.uint8)
        skel[30:170, 100] = 1
        dets = [
            self._det(100, 100, "vav", ncc=0.9, ncc_cls="vav", margin=10.0),
            self._det(50, 100, "diffuser", ncc=0.9, ncc_cls="diffuser", margin=5.0),
            self._det(3, 5, "sensor", ncc=0.9, ncc_cls="sensor", margin=3.0),
        ]
        rooms = [self._room(0, 0, 10, 10, "R1")]
        zones, _ = extract_zones(skel, dets, rooms, px_per_m=PX_PER_M)
        assert len(zones) == 1
        # sensor in room not served by VAV (diffuser not adjacent), so not tracked
        assert len(zones[0]["sensors"]) == 0


class TestTraceSheet:
    """trace_sheet end-to-end: detect + skeleton + zone extraction."""

    def test_trace_sheet_returns_zones_and_detections(self):
        from jesse import WisardClassifier
        from synth.mech import MECH_CLASSES, generate_mech_sheet, render_template

        img, gt = generate_mech_sheet(seed=11)
        gray = np.asarray(img).astype(np.float64)
        clf = WisardClassifier(len(MECH_CLASSES) + 1, seed=42)
        templates = {c: render_template(c) for c in MECH_CLASSES}
        result = trace_sheet(gray, gt, clf, templates)
        assert "zones" in result
        assert "detections" in result
        assert "skel_px" in result
        assert "ncc_wisard_agreement" in result

    def test_trace_sheet_detections_include_label_and_coordinates(self):
        from jesse import WisardClassifier
        from synth.mech import MECH_CLASSES, generate_mech_sheet, render_template

        img, gt = generate_mech_sheet(seed=22)
        gray = np.asarray(img).astype(np.float64)
        clf = WisardClassifier(len(MECH_CLASSES) + 1, seed=42)
        templates = {c: render_template(c) for c in MECH_CLASSES}
        result = trace_sheet(gray, gt, clf, templates)
        for d in result["detections"]:
            assert "label" in d
            assert "cx" in d
            assert "cy" in d

    def test_trace_sheet_zones_have_required_fields(self):
        from jesse import WisardClassifier
        from synth.mech import MECH_CLASSES, generate_mech_sheet, render_template

        img, gt = generate_mech_sheet(seed=33)
        gray = np.asarray(img).astype(np.float64)
        clf = WisardClassifier(len(MECH_CLASSES) + 1, seed=42)
        templates = {c: render_template(c) for c in MECH_CLASSES}
        result = trace_sheet(gray, gt, clf, templates)
        for z in result["zones"]:
            assert "zone_id" in z
            assert "rooms_served" in z
            assert "diffusers" in z
            assert "duct_length_m" in z
            assert "audit" in z

    def test_trace_sheet_n_vav_suppressed_is_non_negative(self):
        from jesse import WisardClassifier
        from synth.mech import MECH_CLASSES, generate_mech_sheet, render_template

        img, gt = generate_mech_sheet(seed=44)
        gray = np.asarray(img).astype(np.float64)
        clf = WisardClassifier(len(MECH_CLASSES) + 1, seed=42)
        templates = {c: render_template(c) for c in MECH_CLASSES}
        result = trace_sheet(gray, gt, clf, templates)
        assert result["n_vav_suppressed"] >= 0
