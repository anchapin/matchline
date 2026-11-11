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


NORTH_SEED = 4


@pytest.fixture(scope="module")
def bldg_n():
    """Building with north-elevation sheets (#1310)."""
    return generate_building(NORTH_SEED, north_elevation=True)


class TestNorthFacadeElevation:
    """North facade end to end (#1310): windows link to the right rooms,
    with correct sill/head and schedule marks; south behaviour unchanged."""

    @staticmethod
    def _north_openings(model):
        out = {}
        for sp in model.spaces.values():
            for op in sp.openings:
                if op.category == "window" and op.host_facade == "north":
                    out[op.id.removeprefix("north-")] = op
        return out

    @pytest.mark.parametrize("key", ["elev_north", "elev_north_nogrid"])
    def test_north_windows_link_to_correct_rooms(self, bldg_n, key):
        model, report = build_model(bldg_n, elevation_key=key, building_name=bldg_n["building_id"])
        gt = bldg_n["gt_links"]["window_room_north"]
        got = {
            wid: sp.number
            for sp in model.spaces.values()
            for op in sp.openings
            if op.category == "window" and op.host_facade == "north"
            for wid in [op.id.removeprefix("north-")]
        }
        assert got == gt
        assert report.windows_unlinked == 0
        assert report.windows_linked == len(gt)

    @pytest.mark.parametrize("key", ["elev_north", "elev_north_nogrid"])
    def test_north_openings_have_sill_head_and_schedule_marks(self, bldg_n, key):
        model, _ = build_model(bldg_n, elevation_key=key, building_name=bldg_n["building_id"])
        openings = self._north_openings(model)
        assert openings
        schedule = {row["tag"]: row for row in bldg_n["window_schedule"]}
        for op in openings.values():
            assert op.host_facade == "north"
            assert op.sill_m == pytest.approx(0.9, abs=0.01)  # synth sill convention
            assert op.tag in schedule
            row = schedule[op.tag]
            assert op.width_m == pytest.approx(row["width_m"], abs=0.01)
            assert op.height_m == pytest.approx(row["height_m"], abs=0.01)
            assert op.head_m == pytest.approx(op.sill_m + row["height_m"], abs=0.01)
            assert op.host_interval_m[0] < op.host_interval_m[1]

    def test_north_grid_sheet_uses_the_grid_path(self, bldg_n):
        # The path follows the SHEET (grid bubbles present), not the key name.
        _, report = build_model(
            bldg_n, elevation_key="elev_north", building_name=bldg_n["building_id"]
        )
        assert report.elevation_path == "grid"

    def test_north_nogrid_sheet_uses_the_geometric_path(self, bldg_n):
        _, report = build_model(
            bldg_n, elevation_key="elev_north_nogrid", building_name=bldg_n["building_id"]
        )
        assert report.elevation_path == "geometric"

    def test_south_and_north_link_in_one_run(self, bldg_n):
        model, report = build_model(
            bldg_n,
            elevation_key=("elev_grid", "elev_north"),
            building_name=bldg_n["building_id"],
        )
        facades = {
            op.host_facade
            for sp in model.spaces.values()
            for op in sp.openings
            if op.category == "window"
        }
        assert facades == {"south", "north"}
        assert report.windows_unlinked == 0

    def test_south_behaviour_unchanged(self, bldg_n):
        # The south elevation links exactly as before: same rooms, same marks.
        model, report = build_model(
            bldg_n, elevation_key="elev_grid", building_name=bldg_n["building_id"]
        )
        gt = bldg_n["gt_links"]["window_room"]
        got = {
            wid: sp.number
            for sp in model.spaces.values()
            for op in sp.openings
            if op.category == "window" and op.host_facade == "south"
            for wid in [op.id.removeprefix("south-")]
        }
        assert got == gt
        assert report.elevation_path == "grid"
        assert report.windows_unlinked == 0


# ---------------------------------------------------------------------------
# #1324: doors on elevations
# ---------------------------------------------------------------------------

DOOR_TAG = "D1"
WINDOW_TAG = "A"

# The building used by the door fixtures: 12 x 8 m, two rooms per facade.
DOOR_BLDG_W = 12.0
DOOR_BLDG_D = 8.0
DOOR_BLDG_H = 3.0
ELEV_PX_PER_M = 40.0
ELEV_U0_PX = 60.0
ELEV_V_GROUND_PX = 400.0

# Per facade: the two rooms (x0, y0, x1, y1), the plan grid along the
# facade, whether the sheet is drawn mirrored (seen from outside) and the
# facade metres the door and the window sit at.
DOOR_FACADES = {
    "south": {
        "rooms": [(0.0, 4.0, 6.0, 8.0), (6.0, 4.0, 12.0, 8.0)],
        "ref_corner": (0.0, 8.0),
        "grid_axis": "grids_v",
        "grid": {"1": 0.0, "2": 6.0, "3": 12.0},
        "mirrored": False,
        "door_s": (1.0, 1.9),
        "window_s": (7.0, 8.2),
    },
    "north": {
        "rooms": [(0.0, 0.0, 6.0, 4.0), (6.0, 0.0, 12.0, 4.0)],
        "ref_corner": (0.0, 0.0),
        "grid_axis": "grids_v",
        "grid": {"1": 0.0, "2": 6.0, "3": 12.0},
        "mirrored": True,
        "door_s": (1.0, 1.9),
        "window_s": (7.0, 8.2),
    },
    "east": {
        "rooms": [(8.0, 0.0, 12.0, 4.0), (8.0, 4.0, 12.0, 8.0)],
        "ref_corner": (12.0, 0.0),
        "grid_axis": "grids_h",
        "grid": {"A": 0.0, "B": 4.0, "C": 8.0},
        "mirrored": True,
        "door_s": (1.0, 1.9),
        "window_s": (5.0, 6.2),
    },
    "west": {
        "rooms": [(0.0, 0.0, 4.0, 4.0), (0.0, 4.0, 4.0, 8.0)],
        "ref_corner": (0.0, 0.0),
        "grid_axis": "grids_h",
        "grid": {"A": 0.0, "B": 4.0, "C": 8.0},
        "mirrored": False,
        "door_s": (1.0, 1.9),
        "window_s": (5.0, 6.2),
    },
}


def _elevation_u(facade: str, s_m: float) -> float:
    """Sheet pixels for a facade distance, following the drawing convention.

    A sheet seen from outside (north, east) runs right to left, so the
    reference corner sits at the right end of the drawn wall.
    """
    spec = DOOR_FACADES[facade]
    length = DOOR_BLDG_W if spec["grid_axis"] == "grids_v" else DOOR_BLDG_D
    if spec["mirrored"]:
        return ELEV_U0_PX + (length - s_m) * ELEV_PX_PER_M
    return ELEV_U0_PX + s_m * ELEV_PX_PER_M


def _detection(det_id: str, tag: str, facade: str, s0: float, s1: float, sill_m: float, h_m: float):
    """One elevation detection in sheet pixels."""
    return {
        "id": det_id,
        "tag": tag,
        "u0_px": _elevation_u(facade, s0),
        "u1_px": _elevation_u(facade, s1),
        "v_sill_px": ELEV_V_GROUND_PX - sill_m * ELEV_PX_PER_M,
        "v_head_px": ELEV_V_GROUND_PX - (sill_m + h_m) * ELEV_PX_PER_M,
    }


def _door_building(facade: str = "south", door_tag: str = DOOR_TAG, extra_door: bool = False):
    """A hand-built building with one elevation sheet carrying a door.

    The schedule holds a door row (category "door") next to the window row,
    and the sheet's window list carries a ground-level detection tagged with
    the door tag plus an ordinary window above the sill.
    """
    spec = DOOR_FACADES[facade]
    rooms = []
    for i, (x0, y0, x1, y1) in enumerate(spec["rooms"]):
        number = f"10{i + 1}"
        rooms.append(
            {
                "number": number,
                "name": f"Room {number}",
                "rect_m": [x0, y0, x1, y1],
                "polygon_m": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                "area_m2": round((x1 - x0) * (y1 - y0), 3),
                "zone": "Z1",
            }
        )
    d0, d1 = spec["door_s"]
    w0, w1 = spec["window_s"]
    length = DOOR_BLDG_W if spec["grid_axis"] == "grids_v" else DOOR_BLDG_D
    dets = [
        _detection("D1", door_tag, facade, d0, d1, 0.0, 2.1),
        _detection("W1", WINDOW_TAG, facade, w0, w1, 0.9, 1.5),
    ]
    if extra_door:
        # beyond the facade: no wall segment can match it
        dets.append(_detection("D2", door_tag, facade, length + 1.0, length + 1.9, 0.0, 2.1))
    bubbles = [
        {"label": label, "u_px": _elevation_u(facade, s)} for label, s in spec["grid"].items()
    ]

    def sheet_meta(sheet_id, discipline):
        return {
            "sheet_id": sheet_id,
            "discipline": discipline,
            "revision": 1,
            "px_per_m": ELEV_PX_PER_M,
            "origin_px": [0, 0],
        }

    grids_v = spec["grid"] if spec["grid_axis"] == "grids_v" else {}
    grids_h = spec["grid"] if spec["grid_axis"] == "grids_h" else {}
    return {
        "building_id": f"bldg_door_{facade}",
        "level_id": "L1",
        "wall_height_m": DOOR_BLDG_H,
        "W_m": DOOR_BLDG_W,
        "D_m": DOOR_BLDG_D,
        "rooms": rooms,
        "grids_v": grids_v,
        "grids_h": grids_h,
        "fixtures": [],
        "components": [],
        "zones": [],
        "window_schedule": [
            {"tag": WINDOW_TAG, "category": "window", "width_m": 1.2, "height_m": 1.5},
            {"tag": DOOR_TAG, "category": "door", "width_m": 0.9, "height_m": 2.1},
        ],
        "lighting_schedule": [],
        "sheets": {
            "arch": {"meta": sheet_meta("arch_A101", "arch_plan")},
            "lighting": {"meta": sheet_meta("light_E101", "lighting_plan")},
            "mech": {"meta": sheet_meta("mech_M101", "mech_plan")},
            "elev_grid": {
                "meta": {
                    **sheet_meta("elev_A201", "elevation"),
                    "facade": facade,
                    "facade_ref_corner_m": list(spec["ref_corner"]),
                    "facade_length_m": length,
                    "elevation_mirrored": spec["mirrored"],
                },
                "data": {
                    "windows": dets,
                    "bubbles": bubbles,
                    "wall_u0_px": ELEV_U0_PX,
                    "v_ground_px": ELEV_V_GROUND_PX,
                    "px_per_m": ELEV_PX_PER_M,
                },
            },
            "elev_nogrid": {
                "meta": {
                    **sheet_meta("elev_A202", "elevation"),
                    "facade": facade,
                    "facade_ref_corner_m": list(spec["ref_corner"]),
                    "facade_length_m": length,
                    "elevation_mirrored": spec["mirrored"],
                },
                "data": {
                    "windows": dets,
                    "bubbles": [],
                    "wall_u0_px": ELEV_U0_PX,
                    "v_ground_px": ELEV_V_GROUND_PX,
                    "px_per_m": ELEV_PX_PER_M,
                },
            },
        },
    }


def _openings(model, category):
    return [op for sp in model.spaces.values() for op in sp.openings if op.category == category]


class TestElevationDoors:
    """#1324: a sill-at-grade elevation detection links as a door opening."""

    @pytest.mark.parametrize("facade", ["south", "north", "east", "west"])
    @pytest.mark.parametrize("key", ["elev_grid", "elev_nogrid"])
    def test_door_links_to_room_with_schedule_mark(self, facade, key):
        bldg = _door_building(facade)
        model, report = build_model(bldg, elevation_key=key, building_name="door-test")
        doors = _openings(model, "door")
        assert len(doors) == 1, f"expected one door opening, got {doors}"
        door = doors[0]
        assert door.tag == DOOR_TAG
        assert door.sill_m == pytest.approx(0.0, abs=0.01)
        assert door.head_m == pytest.approx(2.1, abs=0.01)
        assert door.width_m == pytest.approx(0.9, abs=0.01)
        assert door.height_m == pytest.approx(2.1, abs=0.01)
        assert door.host_facade == facade
        assert door.provenance is not None
        assert door.provenance.sheet_id == ("elev_A201" if key == "elev_grid" else "elev_A202")
        # linked to the room whose wall it sits on (room 101)
        host = next(sp for sp in model.spaces.values() if door in sp.openings)
        assert host.number == "101"
        assert report.doors_linked == 1
        assert report.doors_unlinked == 0
        assert report.windows_linked == 1

    @pytest.mark.parametrize("facade", ["south", "north", "east", "west"])
    def test_door_symbol_linkage_is_a_door(self, facade):
        bldg = _door_building(facade)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        door_links = [lnk for lnk in model.symbol_linkages if lnk.category == "door"]
        assert len(door_links) == 1
        assert door_links[0].symbol_tag == DOOR_TAG
        assert door_links[0].schedule_entry is not None
        assert door_links[0].schedule_entry.category == "door"

    @pytest.mark.parametrize("facade", ["south", "north", "east", "west"])
    def test_window_behaviour_is_unchanged(self, facade):
        bldg = _door_building(facade)
        model, report = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        windows = _openings(model, "window")
        assert len(windows) == 1
        win = windows[0]
        assert win.tag == WINDOW_TAG
        assert win.sill_m == pytest.approx(0.9, abs=0.01)
        assert win.head_m == pytest.approx(2.4, abs=0.01)
        assert win.width_m == pytest.approx(1.2, abs=0.01)
        assert win.height_m == pytest.approx(1.5, abs=0.01)
        host = next(sp for sp in model.spaces.values() if win in sp.openings)
        assert host.number == "102"
        assert report.windows_linked == 1
        assert report.windows_unlinked == 0

    def test_door_joined_to_a_window_row_is_flagged(self):
        # The detection is at grade (a door) but its tag names a window row:
        # the link stands, the mismatch is recorded for a human.
        bldg = _door_building("south", door_tag=WINDOW_TAG)
        model, report = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        doors = _openings(model, "door")
        assert len(doors) == 1
        assert doors[0].tag == WINDOW_TAG
        assert "MISMATCH" in doors[0].provenance.note
        assert doors[0].needs_review
        mismatches = [item for item in model.review_queue if "schedule row" in item.description]
        assert mismatches, "a category mismatch must reach the review queue"
        assert report.doors_linked == 1

    def test_unlinked_door_is_counted_and_visible(self):
        bldg = _door_building("south", extra_door=True)
        model, report = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        assert report.doors_unlinked == 1
        assert report.doors_linked == 1
        door_links = [lnk for lnk in model.symbol_linkages if lnk.category == "door"]
        assert len(door_links) == 2
        assert {lnk.symbol_id for lnk in door_links} == {"D1", "D2"}
        assert any(
            "D2" in item.description or "D2" in str(item.target) for item in model.review_queue
        )

    def test_doors_count_in_the_facade_opening_closure(self):
        from validate import run_checks

        bldg = _door_building("south")
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name="door-test")
        res = {r.check_id: r for r in run_checks(model).results}
        closure = res["facade_opening_closure"]
        assert closure.severity == "pass", closure.message
        door_area = sum(op.area_m2 or 0.0 for op in _openings(model, "door"))
        assert door_area == pytest.approx(0.9 * 2.1, abs=1e-6)
