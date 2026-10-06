"""Tests for link.py: cross-sheet linker and build_model.

Tests cover:
  - Happy path: build_model produces a valid BuildingModel for seeds 101/102
  - Invariant: _dedupe_space_openings leaves no duplicate openings
  - Defect injection: missing space polygon is flagged for review
"""

from __future__ import annotations

import pytest

from building_model import (
    REVIEW_CONFIDENCE,
    BuildingModel,
    Provenance,
    Space,
    SpaceOpening,
    SymbolLinkage,
)
from link import build_model
from link._dedupe import _dedupe_space_openings
from synth.multidiscipline import generate_building


class TestBuildModel:
    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_returns_valid_model(self, seed: int):
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_grid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.levels) > 0
        assert len(model.spaces) > 0

    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_with_open_office(self, seed: int):
        bldg = generate_building(seed, open_office_span=True)
        model, report = build_model(
            bldg, elevation_key="elev_grid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.spaces) > 0

    @pytest.mark.parametrize("seed", [101, 102])
    def test_build_model_nogrid_elevation(self, seed: int):
        bldg = generate_building(seed, open_office_span=False)
        model, report = build_model(
            bldg, elevation_key="elev_nogrid", building_name=bldg["building_id"]
        )
        assert isinstance(model, BuildingModel)
        assert len(model.spaces) > 0

    def test_build_model_spaces_have_valid_polygon(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for sid, space in model.spaces.items():
            if space.polygon_m:
                assert len(space.polygon_m) >= 3, f"space {sid} polygon needs >= 3 pts"
                for pt in space.polygon_m:
                    assert len(pt) == 2

    def test_build_model_all_spaces_have_core_provenance(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for sid, space in model.spaces.items():
            assert space.core_provenance is not None, f"space {sid} missing provenance"

    def test_build_model_envelope_has_provenance(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        for wall in model.envelope:
            assert wall.provenance is not None


class TestDedupeSpaceOpenings:
    def test_different_tags_not_deduplicated(self):
        model = BuildingModel(name="dedup-test")
        level_id = "L1"
        model.levels.append(
            type("Level", (), {"id": level_id, "name": "L1", "elevation_z_m": 0.0})()
        )
        space = Space(
            id="L1-101",
            level_id=level_id,
            name="Room 101",
            number="101",
            polygon_m=[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
            area_m2=100.0,
        )
        model.spaces["L1-101"] = space

        prov = type("Provenance", (), {"confidence": 0.95, "sheet_id": "EL-01"})()
        o1 = SpaceOpening(
            id="OP1",
            tag="WINDOW",
            category="window",
            width_m=1.5,
            height_m=2.0,
            sill_m=0.9,
            provenance=prov,
        )
        o2 = SpaceOpening(
            id="OP2",
            tag="DOOR",
            category="door",
            width_m=0.9,
            height_m=2.1,
            sill_m=0.0,
            provenance=prov,
        )
        space.openings.extend([o1, o2])

        _dedupe_space_openings(model)

        tags = {op.tag for op in space.openings}
        assert "WINDOW" in tags
        assert "DOOR" in tags

    def test_two_storey_window_on_adjacent_sheets_merges(self):
        """#664: one tall window seen as halves on two adjacent sheets -> one opening."""
        from tests.model_factory import _add_l2_space, make_clean_model, two_storey_window_pair

        m = make_clean_model()
        lo, hi = two_storey_window_pair(s=2.6)
        m.spaces["L1-101"].openings.append(lo)
        _add_l2_space(m).openings.append(hi)

        _dedupe_space_openings(m)

        cw = [o for sp in m.spaces.values() for o in sp.openings if o.tag == "CW"]
        assert len(cw) == 1
        kept = cw[0]
        assert kept is hi  # higher-confidence sighting is primary
        assert kept.source_sheet_ids == ["elev_A202", "elev_A201"]
        assert kept.provenance is kept.source_provenance[0]
        assert kept.needs_review  # kept half does not span the full window
        assert kept.history and kept.history[-1].method == "window_dedup"

    def test_stacked_punched_windows_stay_separate(self):
        """#664: identical windows floor over floor are two windows (spandrel gap)."""
        from tests.model_factory import _add_l2_space, make_clean_model

        m = make_clean_model()
        l1 = m.spaces["L1-101"].openings[0]
        l2 = SpaceOpening(
            id="south-W1-L2",
            tag=l1.tag,
            category="window",
            width_m=l1.width_m,
            height_m=l1.height_m,
            sill_m=l1.sill_m,
            head_m=l1.head_m,
            host_facade=l1.host_facade,
            host_interval_m=list(l1.host_interval_m),
            s_center_m=l1.s_center_m,
            area_m2=l1.area_m2,
            provenance=Provenance("elev_A202", 1, "grid_registration", 0.95),
        )
        _add_l2_space(m).openings.append(l2)
        n_before = sum(len(sp.openings) for sp in m.spaces.values())

        _dedupe_space_openings(m)

        assert sum(len(sp.openings) for sp in m.spaces.values()) == n_before

    def test_same_tag_windows_at_different_positions_stay_separate(self):
        """Regression: same-tag same-size windows along one facade are distinct.

        The old (facade, tag, width, height) key collapsed every W-type window on a
        facade to one; the synthetic building lost 3 of its 5 windows.
        """
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        before = [o.id for o in m.spaces["L1-101"].openings]
        assert len(before) >= 2
        _dedupe_space_openings(m)
        assert [o.id for o in m.spaces["L1-101"].openings] == before

    def test_unplaced_same_tag_openings_are_not_merged(self):
        """Without an along-wall position there is no evidence two sightings are one."""
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        for o in m.spaces["L1-101"].openings:
            o.s_center_m = None
            o.host_interval_m = None
        n = len(m.spaces["L1-101"].openings)
        _dedupe_space_openings(m)
        assert len(m.spaces["L1-101"].openings) == n

    def test_same_level_double_link_merges_with_both_sheets(self):
        """Two runs of one facade link the same window twice on one level."""
        from tests.model_factory import make_clean_model

        m = make_clean_model()
        sp = m.spaces["L1-101"]
        o = sp.openings[0]
        dup = SpaceOpening(
            id="south-W1-dup",
            tag=o.tag,
            category="window",
            width_m=o.width_m,
            height_m=o.height_m,
            sill_m=o.sill_m,
            head_m=o.head_m,
            host_facade=o.host_facade,
            host_interval_m=list(o.host_interval_m),
            s_center_m=o.s_center_m + 0.03,
            area_m2=o.area_m2,
            provenance=Provenance("elev_A202", 1, "grid_registration", 0.8),
        )
        n = len(sp.openings)
        sp.openings.append(dup)
        _dedupe_space_openings(m)
        assert len(sp.openings) == n
        assert sp.openings[0].source_sheet_ids == ["elev_A201", "elev_A202"]

    def test_two_storey_facade_end_to_end_passes_validation(self):
        """#664 fixture: dedup then validate; the cross-level check passes."""
        from tests.model_factory import break_cross_level_duplicate, make_clean_model
        from validate import run_checks

        m = make_clean_model()
        break_cross_level_duplicate(m)
        res = {r.check_id: r for r in run_checks(m).results}
        assert res["cross_level_dedup"].severity == "error"

        _dedupe_space_openings(m)
        res = {r.check_id: r for r in run_checks(m).results}
        assert res["cross_level_dedup"].severity == "pass", res["cross_level_dedup"].message


class TestReviewQueueRouting:
    def test_low_confidence_fact_lands_in_review_queue(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        prov = type("Provenance", (), {"confidence": 0.5, "sheet_id": "test"})()
        model.flag_for_review(
            kind="geometry",
            description="synthetic low-confidence item for testing",
            confidence=0.5,
            provenance=prov,
        )
        assert len(model.review_queue) > 0
        assert any(item.confidence < REVIEW_CONFIDENCE for item in model.review_queue)

    def test_review_queue_classified_by_confidence(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="test")
        prov = type("Provenance", (), {"confidence": 0.95, "sheet_id": "test"})()
        model.flag_for_review(
            kind="geometry",
            description="high-confidence item",
            confidence=0.95,
            provenance=prov,
        )
        high_conf_count = sum(
            1 for item in model.review_queue if item.confidence >= REVIEW_CONFIDENCE
        )
        assert high_conf_count == 0


class TestSymbolLinkageGraph:
    def test_symbol_linkages_populated_after_build_model(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        assert hasattr(model, "symbol_linkages")
        assert len(model.symbol_linkages) > 0

    def test_symbol_linkages_have_valid_structure(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for linkage in model.symbol_linkages:
            assert isinstance(linkage, SymbolLinkage)
            assert linkage.symbol_id
            assert linkage.symbol_tag
            assert linkage.category in ("window", "door", "lighting")
            assert 0.0 <= linkage.confidence <= 1.0
            assert linkage.provenance is not None

    def test_linked_windows_have_schedule_entry(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        window_linkages = [lnk for lnk in model.symbol_linkages if lnk.category == "window"]
        if window_linkages:
            linked = [lnk for lnk in window_linkages if lnk.schedule_entry is not None]
            assert len(linked) > 0

    def test_unlinked_instances_are_visible_in_graph(self):
        bldg = generate_building(101, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        window_linkages = [lnk for lnk in model.symbol_linkages if lnk.category == "window"]
        unlinked = [lnk for lnk in window_linkages if lnk.schedule_entry is None]
        if unlinked:
            for lnk in unlinked:
                assert lnk.symbol_id
                assert lnk.symbol_tag
