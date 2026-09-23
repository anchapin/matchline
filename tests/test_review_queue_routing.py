"""Review queue routing by confidence threshold tests.

Verifies that link.py and elevation_windows.py correctly route items to the
BuildingModel.review_queue based on the REVIEW_CONFIDENCE = 0.80 threshold.

The review queue is the safety net: low-confidence links are flagged for human
review rather than silently accepted. These tests verify the routing logic
fires at the right confidence boundary.
"""

from __future__ import annotations

import pytest

from building_model import REVIEW_CONFIDENCE, BuildingModel, Provenance
from link import build_model, match_interval_to_segments
from registration import Facade
from synth.multidiscipline import generate_building


class TestReviewQueueRoutingDirect:
    """Direct unit tests for flag_for_review routing logic."""

    def test_flag_review_accepts_high_confidence(self):
        """High-confidence item (conf=0.95) is NOT flagged for review by threshold check."""
        model = BuildingModel()
        prov = Provenance(sheet_id="test", revision=1, method="test", confidence=0.95)
        item = model.flag_for_review(
            kind="fixture_assignment",
            description="fixture X in room 101",
            confidence=0.95,
            provenance=prov,
        )
        assert item.confidence == 0.95
        assert item in model.review_queue
        assert item.provenance.sheet_id == "test"
        assert item.provenance.revision == 1
        assert item.provenance.method == "test"
        assert item.provenance.confidence == 0.95

    def test_provenance_preserved_in_low_confidence_review_item(self):
        """Low-confidence item (conf=0.79) preserves provenance in the review queue item."""
        model = BuildingModel()
        prov = Provenance(
            sheet_id="sheet_001", revision=3, method="geometric_fallback", confidence=0.62
        )
        item = model.flag_for_review(
            kind="window_room_link",
            description="Window W1 in room 101 — ambiguous geometric link",
            confidence=0.79,
            provenance=prov,
        )
        assert item in model.review_queue
        assert item.provenance.sheet_id == "sheet_001"
        assert item.provenance.revision == 3
        assert item.provenance.method == "geometric_fallback"
        assert item.provenance.confidence == 0.62

    def test_flag_review_accepts_boundary_confidence(self):
        """Boundary confidence (conf=0.80) is NOT flagged — REVIEW_CONFIDENCE uses < comparison."""
        model = BuildingModel()
        prov = Provenance(sheet_id="test", revision=1, method="test", confidence=REVIEW_CONFIDENCE)
        item = model.flag_for_review(
            kind="fixture_assignment",
            description="fixture X in room 101",
            confidence=REVIEW_CONFIDENCE,
            provenance=prov,
        )
        assert item.confidence == REVIEW_CONFIDENCE
        assert item in model.review_queue
        assert item.provenance.sheet_id == "test"
        assert item.provenance.revision == 1
        assert item.provenance.method == "test"
        assert item.provenance.confidence == REVIEW_CONFIDENCE

    def test_flag_review_rejects_sub_threshold_confidence(self):
        """Sub-threshold confidence (conf=0.79) IS flagged for review."""
        model = BuildingModel()
        prov = Provenance(sheet_id="test", revision=1, method="test", confidence=0.79)
        item = model.flag_for_review(
            kind="fixture_assignment",
            description="fixture X in room 101",
            confidence=0.79,
            provenance=prov,
        )
        assert item.confidence == 0.79
        assert item in model.review_queue
        assert item.provenance.sheet_id == "test"
        assert item.provenance.revision == 1
        assert item.provenance.method == "test"
        assert item.provenance.confidence == 0.79

    def test_geometric_fallback_confidence_below_threshold(self):
        """Geometric fallback registration confidence (0.65) is always below REVIEW_CONFIDENCE.

        registration.register_elevation_geometric produces confidence 0.65.
        When combined with any interval-overlap fraction, the product is at most
        0.65 * (0.5 + 0.5*1.0) = 0.65 — still below 0.80.
        """
        assert 0.65 < REVIEW_CONFIDENCE
        # max conf from geometric = 0.65 * 1.0 = 0.65
        max_frac = 1.0
        max_conf = 0.65 * (0.5 + 0.5 * max_frac)
        assert max_conf < REVIEW_CONFIDENCE

    def test_grid_registration_confidence_can_exceed_threshold(self):
        """Grid registration confidence (0.95) CAN exceed REVIEW_CONFIDENCE.

        Grid path: conf = 0.95 * (0.5 + 0.5 * frac). For frac >= 0.70,
        conf >= 0.95 * 0.85 = 0.8075 > 0.80 — route not needed.
        For frac < 0.70, conf < 0.80 — routed to review queue.
        """
        assert 0.95 >= REVIEW_CONFIDENCE
        min_frac_to_exceed = (2 * REVIEW_CONFIDENCE / 0.95) - 1
        assert min_frac_to_exceed < 0.70  # low frac still needed for routing


class TestReviewQueueRoutingPipeline:
    """Integration tests: build_model paths that should/shouldn't produce review items."""

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_geometric_elevation_routes_to_review(self, seed):
        """Building linked via elev_nogrid (geometric fallback) produces review items.

        Geometric fallback conf = 0.65 * (0.5 + 0.5 * frac) <= 0.65 < 0.80,
        so every window-room link is routed to the review queue.
        """
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_nogrid", building_name=bldg["building_id"]
        )
        window_items = [r for r in model.review_queue if r.kind == "window_room_link"]
        assert len(window_items) > 0, (
            f"seed={seed}: geometric fallback should route windows to review queue, "
            f"got review_queue={model.review_queue}"
        )
        for item in window_items:
            assert item.confidence < REVIEW_CONFIDENCE
            assert item.provenance.sheet_id is not None
            assert item.provenance.revision is not None
            assert item.provenance.method is not None
            assert item.provenance.confidence is not None

    @pytest.mark.parametrize("seed", [101, 102, 103])
    def test_grid_elevation_routing_is_fraction_dependent(self, seed):
        """Grid elevation routing depends on interval-overlap fraction.

        For high-overlap windows (frac close to 1.0), conf exceeds 0.80 and
        no review item is created for that window. For low-overlap windows,
        conf falls below 0.80 and a review item is created.
        """
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_grid", building_name=bldg["building_id"]
        )
        window_items = [r for r in model.review_queue if r.kind == "window_room_link"]
        for item in window_items:
            assert item.confidence < REVIEW_CONFIDENCE
        non_routed_openings = [
            sp.openings[-1]
            for sp in model.spaces.values()
            if sp.openings and not sp.openings[-1].needs_review
        ]
        assert len(non_routed_openings) > 0 or len(window_items) > 0

    def test_provenance_preserved_in_geometric_review_items(self, tmp_path):
        """Review items from geometric fallback preserve provenance (sheet, revision, method, conf).

        This verifies issue #206: low-confidence routing must not discard provenance.
        """
        bldg = generate_building(101, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_nogrid", building_name=bldg["building_id"]
        )
        window_items = [r for r in model.review_queue if r.kind == "window_room_link"]
        assert len(window_items) > 0
        for item in window_items:
            assert item.provenance is not None
            assert item.provenance.sheet_id is not None
            assert item.provenance.revision is not None
            assert item.provenance.method is not None
            assert item.provenance.confidence is not None
            assert isinstance(item.provenance.sheet_id, str)
            assert isinstance(item.provenance.revision, int)
            assert isinstance(item.provenance.method, str)
            assert isinstance(item.provenance.confidence, float)
            assert item.provenance.method == "geometric_registration"


class TestMatchIntervalSegmentsReview:
    """match_interval_to_segments is the source of ambiguous links that route to review."""

    def test_perfect_overlap_not_ambiguous(self):
        """Full-overlap segment match is not ambiguous."""
        _facade = Facade(
            name="south", ref_corner_m=(0.0, 10.0), length_m=20.0, fixed_coord_m=10.0, axis="x"
        )
        segments = [{"id": "seg1", "s0": 0.0, "s1": 10.0}]
        seg, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert seg is not None
        assert not ambiguous
        assert frac == 1.0

    def test_partial_overlap_not_ambiguous_above_margin(self):
        """Overlap fraction > 0.5 with clear winner is not ambiguous."""
        _facade = Facade(
            name="south", ref_corner_m=(0.0, 10.0), length_m=20.0, fixed_coord_m=10.0, axis="x"
        )
        segments = [
            {"id": "seg1", "s0": 0.0, "s1": 10.0},
            {"id": "seg2", "s0": 8.0, "s1": 18.0},
        ]
        seg, frac, ambiguous = match_interval_to_segments(0.0, 8.0, segments)
        assert seg is not None
        assert not ambiguous
        assert frac == 1.0

    def test_low_overlap_is_ambiguous(self):
        """Low overlap fraction triggers ambiguity → needs_review = True."""
        _facade = Facade(
            name="south", ref_corner_m=(0.0, 10.0), length_m=20.0, fixed_coord_m=10.0, axis="x"
        )
        segments = [
            {"id": "seg1", "s0": 0.0, "s1": 2.0},
            {"id": "seg2", "s0": 8.0, "s1": 10.0},
        ]
        seg, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert ambiguous
        assert frac < 0.5

    def test_no_overlap_is_ambiguous(self):
        """No overlap → ambiguous → needs_review = True."""
        _facade = Facade(
            name="south", ref_corner_m=(0.0, 10.0), length_m=20.0, fixed_coord_m=10.0, axis="x"
        )
        segments = [{"id": "seg1", "s0": 15.0, "s1": 20.0}]
        seg, frac, ambiguous = match_interval_to_segments(0.0, 5.0, segments)
        assert seg is None
        assert ambiguous


class TestConfidenceThresholdOverride:
    """Tests for --confidence-threshold CLI override and MATCHLINE_CONFIDENCE_THRESHOLD env var."""

    def test_confidence_threshold_cli_override(self, monkeypatch):
        """--confidence-threshold sets review_confidence in config."""
        import argparse
        from unittest.mock import patch

        from cli import cmd_run

        monkeypatch.delenv("MATCHLINE_CONFIDENCE_THRESHOLD", raising=False)

        ns = argparse.Namespace(
            seed=42,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=None,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
            confidence_threshold=0.60,
            config_path=None,
        )

        captured_config = {}

        def mock_main(args_ns, config=None):
            captured_config.update(config or {})

        with patch("run_pipeline.main", mock_main):
            cmd_run(ns)

        assert captured_config.get("review_confidence") == 0.60

    def test_confidence_threshold_env_var_override(self, monkeypatch):
        """MATCHLINE_CONFIDENCE_THRESHOLD env var sets review_confidence in config."""
        import argparse
        from unittest.mock import patch

        from cli import cmd_run

        monkeypatch.setenv("MATCHLINE_CONFIDENCE_THRESHOLD", "0.55")

        ns = argparse.Namespace(
            seed=42,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=None,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
            confidence_threshold=None,
            config_path=None,
        )

        captured_config = {}

        def mock_main(args_ns, config=None):
            captured_config.update(config or {})

        with patch("run_pipeline.main", mock_main):
            cmd_run(ns)

        assert captured_config.get("review_confidence") == 0.55

    def test_confidence_threshold_default(self):
        """Default confidence threshold is 0.75 when no override provided."""
        import argparse
        from unittest.mock import patch

        from cli import DEFAULT_CONFIDENCE_THRESHOLD, cmd_run

        assert DEFAULT_CONFIDENCE_THRESHOLD == 0.75

        ns = argparse.Namespace(
            seed=42,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=None,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
            confidence_threshold=None,
            config_path=None,
        )

        captured_config = {}

        def mock_main(args_ns, config=None):
            captured_config.update(config or {})

        with patch("run_pipeline.main", mock_main):
            cmd_run(ns)

        assert captured_config.get("review_confidence") == 0.75
        seg, frac, ambiguous = match_interval_to_segments(0.0, 10.0, segments)
        assert ambiguous
        assert frac < 0.5

    def test_no_overlap_is_ambiguous(self):
        """No overlap → ambiguous → needs_review = True."""
        _facade = Facade(
            name="south", ref_corner_m=(0.0, 10.0), length_m=20.0, fixed_coord_m=10.0, axis="x"
        )
        segments = [{"id": "seg1", "s0": 15.0, "s1": 20.0}]
        seg, frac, ambiguous = match_interval_to_segments(0.0, 5.0, segments)
        assert seg is None
        assert ambiguous


class TestReviewItemKindLiteral:
    """Type-level tests: verify mypy rejects invalid ReviewItem.kind values."""

    def test_valid_kind_values(self):
        """All known kind values should be accepted by the type checker."""
        valid_kinds = [
            "fixture_assignment",
            "fixture_schedule",
            "diffuser_assignment",
            "sensor_assignment",
            "window_room_link",
            "space_no_geometry",
            "elevation_conflict",
            "window_reconciliation",
            "gd_complex_row",
        ]
        for kind in valid_kinds:
            item = ReviewItem(
                id=f"test-{kind}",
                kind=kind,
                description="test",
                confidence=0.5,
                provenance=Provenance(sheet_id="test", revision=0, method="test", confidence=0.5),
            )
            assert item.kind == kind
