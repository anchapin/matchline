"""takeoff_estimate (#1419): the #1418 Phase 1 gates G1-G7 as repo tests,
plus loader refusals, the D1-D6 design-decision tests, and properties.

Ground truth in this file is hand arithmetic written independently of the
module (the same numbers as the Phase 1 report, re-derived from the fixture
geometry). The vendored fixtures under tests/fixtures/takeoff_estimate/ are
the Phase 1 spike fixtures; the spike's own estimates are vendored too, and
the package totals must match them on the money surface.

Fixtures:
- fixture 1: the #1415 mapped two-room model (inner-face polygons): Living
  4.85 x 4.8 = 23.28, Bedroom 2.85 x 4.8 = 13.68, envelope centrelines
  8 x 5 at height 3.0, one interior divider (offset pairing).
- case 2: hand-built three-room tiling model (exact pairing), 7 x 5 outer
  rectangle at height 2.8 (ceiling_height_m; level wall height is 3.2 on
  purpose, to prove the height source hierarchy).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

import takeoff_estimate as te
from building_model import BuildingModel, EnvelopeWall, Level, Space, SpaceOpening
from tests.model_factory import make_clean_model

FX = Path(__file__).parent / "fixtures" / "takeoff_estimate"


def _load_model(name: str) -> BuildingModel:
    return BuildingModel.from_json((FX / name).read_text())


def _fixture1():
    model = _load_model("model_fixture1.json")
    book = te.load_price_book(FX / "price_book_fixture1.json")
    fin = te.load_finishes_schedule(FX / "finishes_fixture1.json")
    return model, book, fin


def _case2():
    model = _load_model("model_case2.json")
    book = te.load_price_book(FX / "price_book_case2.json")
    fin = te.load_finishes_schedule(FX / "finishes_case2.json")
    return model, book, fin


def _approx(a, b, tol=0.01):
    return a is not None and abs(a - b) <= tol * max(1.0, abs(b))


def _line(est, line_id):
    return next(ln for ln in est.lines if ln.line_id == line_id)


# ---------------------------------------------------------------------------
# G1: quantities within 1% of independent hand ground truth
# ---------------------------------------------------------------------------

GT1_SPACES = {
    "ground-101": {"floor": 23.28, "wall_gross": 57.9, "wall_net": 54.57, "trim": 18.4},
    "ground-102": {"floor": 13.68, "wall_gross": 45.9, "wall_net": 44.46, "trim": 15.3},
}
GT2_SPACES = {
    "L1-101": {"floor": 12.0, "wall_gross": 39.2, "wall_net": 35.72, "trim": 13.2},
    "L1-102": {"floor": 9.0, "wall_gross": 33.6, "wall_net": 30.84, "trim": 11.2},
    "L1-103": {"floor": 14.0, "wall_gross": 50.4, "wall_net": 44.94, "trim": 15.4},
}


def _check_spaces(q, gt):
    for sid, exp in gt.items():
        s = q.spaces[sid]
        assert _approx(s.floor_area_recomputed_m2, exp["floor"]), sid
        assert _approx(s.floor_area_stored_m2, exp["floor"]), sid
        assert _approx(s.ceiling_area_m2, exp["floor"]), sid
        assert _approx(s.wall_gross_m2, exp["wall_gross"]), sid
        assert _approx(s.wall_net_m2, exp["wall_net"]), sid
        assert _approx(s.trim_length_m, exp["trim"]), sid
        assert s.floor_area_drift_pct == 0.0, sid


def test_g1_fixture1_quantities():
    model, _, _ = _fixture1()
    q = te.compute_quantities(model)
    _check_spaces(q, GT1_SPACES)
    assert _approx(q.total_floor_area_m2, 36.96)
    assert _approx(q.envelope_gross_total_m2, 78.0)
    assert _approx(q.envelope_net_total_m2, 73.23)
    for facade, (g, n) in {
        "north": (24, 22.56),
        "east": (15, 13.56),
        "south": (24, 22.11),
        "west": (15, 15),
    }.items():
        assert _approx(q.envelope_by_facade[facade]["gross_m2"], g), facade
        assert _approx(q.envelope_by_facade[facade]["net_m2"], n), facade
    assert len(q.partitions) == 1
    p = q.partitions[0]
    assert (p.space_a, p.space_b) == ("ground-101", "ground-102")
    assert _approx(p.length_m, 4.8) and _approx(p.area_m2, 14.4)
    assert p.pairing == ["offset"]  # inner-face islands, not shared edges
    roll = {(r["category"], r["tag"]): r for r in q.opening_rollup}
    assert roll[("window", "")]["count"] == 2 and _approx(roll[("window", "")]["area_m2"], 2.88)
    assert roll[("door", "")]["count"] == 1 and _approx(roll[("door", "")]["area_m2"], 1.89)


def test_g1_case2_quantities():
    model, _, _ = _case2()
    q = te.compute_quantities(model)
    _check_spaces(q, GT2_SPACES)
    assert _approx(q.total_floor_area_m2, 35.0)
    assert _approx(q.envelope_gross_total_m2, 67.2)
    assert _approx(q.envelope_net_total_m2, 62.22)
    parts = {(p.space_a, p.space_b): p for p in q.partitions}
    for pair, (ln, ar) in {
        ("L1-101", "L1-102"): (3.0, 8.4),
        ("L1-101", "L1-103"): (4.0, 11.2),
        ("L1-102", "L1-103"): (3.0, 8.4),
    }.items():
        p = parts[pair]
        assert _approx(p.length_m, ln) and _approx(p.area_m2, ar), pair
        assert p.pairing == ["exact"]  # tiling convention: shared edges
    roll = {(r["category"], r["tag"]): r for r in q.opening_rollup}
    assert roll[("window", "W1")]["count"] == 2
    assert roll[("door", "D1")]["count"] == 1 and _approx(roll[("door", "D1")]["area_m2"], 2.1)
    assert roll[("door", "D2")]["count"] == 2 and _approx(roll[("door", "D2")]["area_m2"], 3.36)
    assert roll[("skylight", "S1")]["count"] == 1


def test_g1_totals_match_vendored_spike_estimates():
    """The package totals must reproduce the Phase 1 spike's verified totals
    on the money surface (the vendored spike estimates are the expectation)."""
    for model_name, book_name, fin_name, spike_name in (
        (
            "model_fixture1.json",
            "price_book_fixture1.json",
            "finishes_fixture1.json",
            "expected_estimate_fixture1_spike.json",
        ),
        (
            "model_case2.json",
            "price_book_case2.json",
            "finishes_case2.json",
            "expected_estimate_case2_spike.json",
        ),
    ):
        model = _load_model(model_name)
        est = te.build_estimate(
            model, te.load_price_book(FX / book_name), te.load_finishes_schedule(FX / fin_name)
        )
        spike = json.loads((FX / spike_name).read_text())
        assert est.total == spike["total"]
        assert est.subtotals_by_category == spike["subtotals_by_category"]
        spike_lines = {ln["line_id"].split(":", 1)[1]: ln for ln in spike["lines"]}
        for ln in est.lines:
            if ln.line_id in spike_lines:
                assert ln.line_total == spike_lines[ln.line_id]["line_total"], ln.line_id
                assert ln.quantity == spike_lines[ln.line_id]["quantity"], ln.line_id


# ---------------------------------------------------------------------------
# G2: opening attribution
# ---------------------------------------------------------------------------


def test_g2_opening_attribution():
    model, _, _ = _fixture1()
    q = te.compute_quantities(model)
    got = {o.opening_id: (o.space_id, o.host_facade, o.category, o.tag) for o in q.openings}
    assert got == {
        "living-window": ("ground-101", "north", "window", ""),
        "front-door": ("ground-101", "south", "door", ""),
        "bedroom-window": ("ground-102", "east", "window", ""),
    }
    model2, _, _ = _case2()
    q2 = te.compute_quantities(model2)
    got2 = {o.opening_id: (o.space_id, o.host_facade, o.category, o.tag) for o in q2.openings}
    assert got2 == {
        "kitchen-window": ("L1-101", "north", "window", "W1"),
        "bath-window": ("L1-102", "east", "window", "W1"),
        "hall-entry-door": ("L1-103", "south", "door", "D1"),
        "kitchen-hall-door": ("L1-101", "", "door", "D2"),
        "bath-hall-door": ("L1-102", "", "door", "D2"),
        "hall-skylight": ("L1-103", "roof", "skylight", "S1"),
    }
    for o in q2.openings:
        if o.opening_id in ("kitchen-hall-door", "bath-hall-door"):
            assert o.adjacent_space_id == "L1-103"


# ---------------------------------------------------------------------------
# G3 determinism, G4 separation, G5 change test, G6 unmatched, G7 provenance
# ---------------------------------------------------------------------------


def test_g3_byte_identical_rerun():
    model, book, fin = _fixture1()
    a = te.build_estimate(model, book, fin).to_json()
    b = te.build_estimate(model, book, fin).to_json()
    assert a == b
    model2, book2, fin2 = _case2()
    assert te.build_estimate(model2, book2, fin2).to_json() == (
        te.build_estimate(model2, book2, fin2).to_json()
    )


def test_g4_price_book_bump_changes_prices_only():
    model, book, fin = _fixture1()
    est = te.build_estimate(model, book, fin)
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    doc["header"]["price_book_version"] = "fixture-1.1"
    for item in doc["items"]:
        if item["item_id"] == "flooring_laminate":
            item["material_unit_price"] = 44.0
    book2 = te.price_book_from_dict(doc)
    est2 = te.build_estimate(model, book2, fin)
    assert est2.quantities.to_dict() == est.quantities.to_dict()
    assert est2.price_book_version == "fixture-1.1"
    assert est.total == 6697.19 and est2.total == 6804.28
    la = _line(est, "flooring:ground-101:floor")
    lb = _line(est2, "flooring:ground-101:floor")
    assert la.quantity == lb.quantity and la.line_total != lb.line_total


def test_g5_change_test_divider_move_and_finish_change():
    model, book, fin = _fixture1()
    base = te.build_estimate(model, book, fin)
    moved = copy.deepcopy(model)
    moved.spaces["ground-101"].polygon_m = [[0.1, 0.1], [4.45, 0.1], [4.45, 4.9], [0.1, 4.9]]
    moved.spaces["ground-101"].area_m2 = 20.88
    moved.spaces["ground-101"].volume_m3 = 62.64
    moved.spaces["ground-102"].polygon_m = [[4.55, 0.1], [7.9, 0.1], [7.9, 4.9], [4.55, 4.9]]
    moved.spaces["ground-102"].area_m2 = 16.08
    moved.spaces["ground-102"].volume_m3 = 48.24
    est_moved = te.build_estimate(moved, book, fin)
    la = {ln.line_id: ln.to_dict() for ln in base.lines}
    lc = {ln.line_id: ln.to_dict() for ln in est_moved.lines}
    changed = {k for k in la if k in lc and la[k] != lc[k]}
    must_change = {
        "paint:ground-101:wall",
        "paint:ground-102:wall",
        "paint:ground-101:ceiling",
        "paint:ground-102:ceiling",
        "flooring:ground-101:floor",
        "tile:ground-102:floor",
        "trim:ground-101",
        "trim:ground-102",
        "spare:ground-101:floor",
    }
    assert must_change <= changed
    for stable in (
        "opening:living-window",
        "opening:bedroom-window",
        "opening:front-door",
        "wall_assembly:none",
        "roof",
    ):
        assert la[stable] == lc[stable], stable
    q = est_moved.quantities
    assert _approx(q.spaces["ground-101"].floor_area_recomputed_m2, 20.88)
    assert _approx(q.spaces["ground-102"].floor_area_recomputed_m2, 16.08)
    assert _approx(q.envelope_net_total_m2, 73.23)
    assert len(q.partitions) == 1 and _approx(q.partitions[0].length_m, 4.8)

    fin_doc = json.loads((FX / "finishes_fixture1.json").read_text())
    for row in fin_doc["rows"]:
        if row["space_id"] == "ground-102" and row["surface"] == "floor":
            row["finish_id"] = "flooring_laminate"
    est_fin = te.build_estimate(model, book, te.finishes_from_dict(fin_doc))
    ld = {ln.line_id: ln.to_dict() for ln in est_fin.lines}
    assert sorted(set(la) - set(ld)) == ["tile:ground-102:floor"]
    assert sorted(set(ld) - set(la)) == ["flooring:ground-102:floor", "spare:ground-102:floor"]
    assert [k for k in la if k in ld and la[k] != ld[k]] == []


def test_g6_unmatched_edges_reported_and_flagged():
    model, _, _ = _fixture1()
    q = te.compute_quantities(model)
    st = q.partition_levels["ground"]
    assert st.unmatched_m == 0.0 and st.unmatched_pct == 0.0
    assert not st.exceeds_unmatched_flag
    model2, _, _ = _case2()
    st2 = te.compute_quantities(model2).partition_levels["L1"]
    assert st2.unmatched_pct == 0.0 and not st2.exceeds_unmatched_flag

    # defect injection: remove the Bedroom, so Living's divider edge has no
    # partner and is not envelope-adjacent -> 100% unmatched, flag fires.
    broken = copy.deepcopy(model)
    del broken.spaces["ground-102"]
    qb = te.compute_quantities(broken)
    stb = qb.partition_levels["ground"]
    assert qb.partitions == []
    assert _approx(stb.interior_edge_total_m, 4.8)
    assert _approx(stb.unmatched_m, 4.8) and stb.unmatched_pct == 100.0
    assert stb.exceeds_unmatched_flag


def test_g7_provenance_completeness():
    for model, book, fin in (_fixture1(), _case2()):
        est = te.build_estimate(model, book, fin)
        for ln in est.lines:
            if ln.status != "priced":
                continue
            p = ln.provenance
            assert p.element_ids, ln.line_id
            assert p.rule and p.price_item_id, ln.line_id
            assert p.price_book_version == est.price_book_version, ln.line_id


def test_unpriced_lines_carry_reasons_in_place():
    model, book, fin = _fixture1()
    est = te.build_estimate(model, book, fin)
    assert est.total_label == "priced_lines_only"
    door = _line(est, "opening:front-door")
    assert door.status == "unpriced" and door.reason == "no_price_item"
    asm = _line(est, "wall_assembly:none")
    assert asm.status == "unpriced" and asm.reason == "no_construction"
    assert _approx(asm.quantity, 73.23)
    assert set(est.unpriced_line_ids) == {"opening:front-door", "roof", "wall_assembly:none"}


def test_height_sources_recorded():
    model, _, _ = _fixture1()
    q = te.compute_quantities(model)
    # fixture 1 spaces state height_m; the level value equals the dataclass
    # default and that ambiguity is flagged, not hidden
    assert q.spaces["ground-101"].height_source == "space.height_m"
    assert "level_wall_height_equals_dataclass_default_3.0" in q.spaces["ground-101"].height_flags
    model2, _, _ = _case2()
    q2 = te.compute_quantities(model2)
    assert q2.spaces["L1-101"].height_source == "space.ceiling_height_m"
    assert q2.spaces["L1-101"].height_m == 2.8  # not the level's 3.2


def test_floor_area_drift_flagged_not_hidden():
    model, book, fin = _fixture1()
    model.spaces["ground-101"].area_m2 = 25.0  # recomputed is 23.28
    q = te.compute_quantities(model)
    s = q.spaces["ground-101"]
    assert s.floor_area_drift_pct > 1.0
    assert "floor_area_drift_over_1pct" in s.flags


# ---------------------------------------------------------------------------
# Loader refusals: no silent defaults, ever
# ---------------------------------------------------------------------------

BOOK = {
    "header": {
        "price_book_version": "t-1",
        "currency": "USD",
        "region": "test",
        "source": "test fixture",
        "source_date": "2026-11-20",
    },
    "items": [
        {
            "item_id": "paint_wall",
            "applies_to": {"finish_id": "paint_standard"},
            "category": "paint",
            "unit": "l",
            "material_unit_price": 12.0,
            "coverage_m2_per_l": 10.0,
            "coats": 2,
            "container_l": 5.0,
            "source": "test",
            "source_date": "2026-11-20",
        }
    ],
}


def _book_with(**item_over):
    doc = copy.deepcopy(BOOK)
    doc["items"][0].update(item_over)
    return doc


def test_loader_refuses_missing_header_source():
    doc = copy.deepcopy(BOOK)
    del doc["header"]["source"]
    with pytest.raises(te.PriceBookError, match="source"):
        te.price_book_from_dict(doc)


def test_loader_refuses_missing_header_source_date():
    doc = copy.deepcopy(BOOK)
    del doc["header"]["source_date"]
    with pytest.raises(te.PriceBookError, match="source"):
        te.price_book_from_dict(doc)


def test_loader_refuses_item_without_source():
    doc = copy.deepcopy(BOOK)
    del doc["items"][0]["source"]
    with pytest.raises(te.PriceBookError, match="source"):
        te.price_book_from_dict(doc)


def test_loader_refuses_unknown_keys_anywhere():
    doc = _book_with(surprise_field=1)
    with pytest.raises(te.PriceBookError, match="unknown"):
        te.price_book_from_dict(doc)
    doc = copy.deepcopy(BOOK)
    doc["header"]["surprise"] = 1
    with pytest.raises(te.PriceBookError, match="unknown"):
        te.price_book_from_dict(doc)
    doc = copy.deepcopy(BOOK)
    doc["items"][0]["applies_to"]["surprise"] = 1
    with pytest.raises(te.PriceBookError, match="unknown"):
        te.price_book_from_dict(doc)


def test_loader_refuses_labour_without_basis():
    doc = _book_with(labour_hours_per_unit=0.3, labour_rate_per_hour=50.0)
    with pytest.raises(te.PriceBookError, match="labour_basis"):
        te.price_book_from_dict(doc)
    doc = _book_with(labour_hours_per_unit=0.3, labour_rate_per_hour=50.0, labour_basis="banana")
    with pytest.raises(te.PriceBookError, match="labour_basis"):
        te.price_book_from_dict(doc)


def test_loader_refuses_duplicate_item_and_bad_unit_and_missing_applies_to():
    doc = copy.deepcopy(BOOK)
    doc["items"].append(copy.deepcopy(doc["items"][0]))
    with pytest.raises(te.PriceBookError, match="duplicate"):
        te.price_book_from_dict(doc)
    with pytest.raises(te.PriceBookError, match="unit"):
        te.price_book_from_dict(_book_with(unit="parsecs"))
    doc = copy.deepcopy(BOOK)
    del doc["items"][0]["applies_to"]
    with pytest.raises(te.PriceBookError, match="applies_to"):
        te.price_book_from_dict(doc)


FIN = {
    "header": {
        "finishes_version": "f-1",
        "source": "test fixture",
        "source_date": "2026-11-20",
    },
    "rows": [{"space_id": "S1", "surface": "wall", "finish_id": "paint_standard"}],
}


def test_finishes_loader_refusals():
    doc = copy.deepcopy(FIN)
    del doc["header"]["source"]
    with pytest.raises(te.FinishesError, match="source"):
        te.finishes_from_dict(doc)
    doc = copy.deepcopy(FIN)
    doc["rows"][0]["surface"] = "skirting"
    with pytest.raises(te.FinishesError, match="surface"):
        te.finishes_from_dict(doc)
    doc = copy.deepcopy(FIN)
    doc["rows"].append({"space_id": "S1", "surface": "wall", "finish_id": "other"})
    with pytest.raises(te.FinishesError, match="duplicate"):
        te.finishes_from_dict(doc)
    doc = copy.deepcopy(FIN)
    doc["rows"][0]["extra"] = 1
    with pytest.raises(te.FinishesError, match="unknown"):
        te.finishes_from_dict(doc)


def test_missing_finish_is_an_unpriced_line_not_a_default():
    model, book, fin = _fixture1()
    doc = json.loads((FX / "finishes_fixture1.json").read_text())
    doc["rows"] = [
        r for r in doc["rows"] if not (r["space_id"] == "ground-101" and r["surface"] == "wall")
    ]
    est = te.build_estimate(model, book, te.finishes_from_dict(doc))
    ln = _line(est, "paint:ground-101:wall")
    assert ln.status == "unpriced" and ln.reason == "no_finish_assigned"
    assert _approx(ln.quantity, 54.57)


# ---------------------------------------------------------------------------
# D2: roof reports union AND gross; pricing uses gross; no silent fallback
# ---------------------------------------------------------------------------


def test_d2_roof_union_and_gross_and_priced_on_gross():
    model, book, fin = _fixture1()
    q = te.compute_quantities(model)
    assert _approx(q.roof.area_union_m2, 36.96)  # inner-face union
    assert _approx(q.roof.area_gross_m2, 40.0)  # 8 x 5 envelope ring
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    doc["items"].append(
        {
            "item_id": "roof_membrane",
            "applies_to": {"kind": "roof"},
            "category": "roof",
            "unit": "m2",
            "material_unit_price": 50.0,
            "source": "test",
            "source_date": "2026-11-20",
        }
    )
    est = te.build_estimate(model, te.price_book_from_dict(doc), fin)
    roof_line = _line(est, "roof")
    assert roof_line.status == "priced"
    assert roof_line.quantity == 40.0  # gross, not the 36.96 union
    assert roof_line.line_total == 2000.0
    assert roof_line.quantity_detail["area_union_m2"] == 36.96


def test_d2_roof_gross_underivable_is_unquantified_with_reason():
    model, book, fin = _case2()
    model.envelope = []  # no envelope ring to close -> gross underivable
    est = te.build_estimate(model, book, fin)
    roof_line = _line(est, "roof")
    assert roof_line.status == "unquantified"
    assert roof_line.reason == "no_envelope_segments"
    assert roof_line.quantity is None
    assert roof_line.quantity_detail["area_union_m2"] == 35.0  # union still reported


# ---------------------------------------------------------------------------
# D3: cumulative interval matching, straddling openings split pro-rata
# ---------------------------------------------------------------------------


def _two_room_model(north_segments, window_interval):
    """Rooms A [0,0]-[4,3] and B [4,0]-[8,3] (y-down plan), height 3.0 via
    ceiling_height_m; a window on A's north wall with the given host
    interval along the facade's canonical axis."""
    a = Space(
        id="L1-A",
        level_id="L1",
        polygon_m=[[0, 0], [4, 0], [4, 3], [0, 3]],
        area_m2=12.0,
        ceiling_height_m=3.0,
        openings=[
            SpaceOpening(
                id="win",
                tag="",
                category="window",
                width_m=2.0,
                height_m=1.44,
                host_facade="north",
                host_interval_m=window_interval,
            )
        ],
    )
    b = Space(
        id="L1-B",
        level_id="L1",
        polygon_m=[[4, 0], [8, 0], [8, 3], [4, 3]],
        area_m2=12.0,
        ceiling_height_m=3.0,
    )
    env = [
        EnvelopeWall(id=f"n{i}", facade="north", from_m=list(fr), to_m=list(to), height_m=3.0)
        for i, (fr, to) in enumerate(north_segments)
    ]
    env += [
        EnvelopeWall(id="e1", facade="east", from_m=[8, 0], to_m=[8, 3], height_m=3.0),
        EnvelopeWall(id="s1", facade="south", from_m=[8, 3], to_m=[0, 3], height_m=3.0),
        EnvelopeWall(id="w1", facade="west", from_m=[0, 3], to_m=[0, 0], height_m=3.0),
    ]
    return BuildingModel(
        name="d3",
        levels=[Level(id="L1", wall_height_m=3.0)],
        spaces={"L1-A": a, "L1-B": b},
        envelope=env,
    )


def test_d3_straddling_opening_splits_pro_rata_and_is_recorded():
    model = _two_room_model([([0, 0], [4, 0]), ([4, 0], [8, 0])], [3.0, 5.0])
    q = te.compute_quantities(model)
    segs = {s.segment_id: s for s in q.envelope_segments if s.facade == "north"}
    # window is 2.0 wide x 1.44 high = 2.88 m2; 1 m of width in each segment
    assert segs["n0"].deductions == [{"opening_id": "win", "overlap_m": 1.0, "area_m2": 1.44}]
    assert segs["n1"].deductions == [{"opening_id": "win", "overlap_m": 1.0, "area_m2": 1.44}]
    assert _approx(segs["n0"].net_m2, 12.0 - 1.44)
    assert _approx(segs["n1"].net_m2, 12.0 - 1.44)
    assert q.envelope_unplaced_openings == []
    assert _approx(q.envelope_net_total_m2, (24 - 2.88) + 9 + 24 + 9)


def test_d3_unplaced_opening_is_reported_not_dropped():
    model = _two_room_model([([0, 0], [4, 0]), ([4, 0], [8, 0])], [9.0, 11.0])
    q = te.compute_quantities(model)
    assert len(q.envelope_unplaced_openings) == 1
    assert q.envelope_unplaced_openings[0]["opening_id"] == "win"
    assert q.envelope_unplaced_openings[0]["reason"] == "interval_outside_segments"


# ---------------------------------------------------------------------------
# D4: general segment pairing, including a diagonal partition
# ---------------------------------------------------------------------------


def test_d4_diagonal_partition_pairs():
    a = Space(
        id="L1-A",
        level_id="L1",
        polygon_m=[[0, 0], [4, 0], [4, 4]],
        area_m2=8.0,
        ceiling_height_m=2.5,
    )
    b = Space(
        id="L1-B",
        level_id="L1",
        polygon_m=[[0, 0], [4, 4], [0, 4]],
        area_m2=8.0,
        ceiling_height_m=2.5,
    )
    env = [
        EnvelopeWall(id="s", facade="south", from_m=[0, 0], to_m=[4, 0]),
        EnvelopeWall(id="e", facade="east", from_m=[4, 0], to_m=[4, 4]),
        EnvelopeWall(id="n", facade="north", from_m=[4, 4], to_m=[0, 4]),
        EnvelopeWall(id="w", facade="west", from_m=[0, 4], to_m=[0, 0]),
    ]
    model = BuildingModel(
        name="diag",
        levels=[Level(id="L1", wall_height_m=2.5)],
        spaces={"L1-A": a, "L1-B": b},
        envelope=env,
    )
    q = te.compute_quantities(model)
    assert len(q.partitions) == 1
    p = q.partitions[0]
    assert _approx(p.length_m, math.sqrt(32.0), tol=1e-6)
    assert _approx(p.area_m2, math.sqrt(32.0) * 2.5, tol=1e-6)
    assert q.partition_levels["L1"].unmatched_pct == 0.0


# ---------------------------------------------------------------------------
# D5: labour_basis is a price-book field; priced lines name the basis used
# ---------------------------------------------------------------------------


def _paint_book(basis: str, hours: float) -> te.PriceBook:
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    for item in doc["items"]:
        if item["item_id"] == "paint_interior":
            item["labour_basis"] = basis
            item["labour_hours_per_unit"] = hours
    return te.price_book_from_dict(doc)


def test_d5_labour_basis_priced_unit_vs_quantity_unit():
    model, _, fin = _fixture1()
    est_p = te.build_estimate(model, _paint_book("priced_unit", 0.35), fin)
    ln_p = _line(est_p, "paint:ground-101:wall")
    assert ln_p.labour_basis == "priced_unit"
    assert ln_p.labour_quantity == 15.0  # purchasable litres
    assert _approx(ln_p.labour_hours, 15.0 * 0.35)
    est_q = te.build_estimate(model, _paint_book("quantity_unit", 0.1), fin)
    ln_q = _line(est_q, "paint:ground-101:wall")
    assert ln_q.labour_basis == "quantity_unit"
    assert _approx(ln_q.labour_quantity, 54.57)  # measured net wall area
    assert _approx(ln_q.labour_hours, 54.57 * 0.1)
    # the same line under the two bases differs in labour cost, and says why
    assert ln_p.line_total != ln_q.line_total


def test_labour_missing_rate_is_flagged_not_assumed():
    model, book, fin = _fixture1()
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    for item in doc["items"]:
        if item["item_id"] == "trim_baseboard":
            item["labour_rate_per_hour"] = None
    est = te.build_estimate(model, te.price_book_from_dict(doc), fin)
    ln = _line(est, "trim:ground-101")
    assert ln.status == "priced"
    assert ln.labour_flag == "no_labour_rate"
    assert ln.labour_cost is None
    assert ln.line_total == ln.material_cost


# ---------------------------------------------------------------------------
# D6: partition_basis option; mixed or unstated basis is a validation error
# ---------------------------------------------------------------------------


def _book_with_partition_item():
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    doc["items"].append(
        {
            "item_id": "partition_stud_gwb",
            "applies_to": {"kind": "partition"},
            "category": "partition_assembly",
            "unit": "m2",
            "material_unit_price": 100.0,
            "source": "test",
            "source_date": "2026-11-20",
        }
    )
    return te.price_book_from_dict(doc)


def test_d6_faces_basis_default_partitions_not_priced():
    model, book, fin = _fixture1()
    est = te.build_estimate(model, book, fin)  # default basis
    assert est.partition_basis == "faces"
    assert not any(ln.kind == "partition_assembly" for ln in est.lines)
    ln = _line(est, "paint:ground-101:wall")
    assert "partition_face_excluded_m2" not in ln.quantity_detail
    assert ln.quantity_detail["net_area_m2"] == 54.57


def test_d6_assembly_basis_prices_partitions_and_excludes_faces():
    model, _, fin = _fixture1()
    book = _book_with_partition_item()
    est = te.build_estimate(model, book, fin, partition_basis="assembly")
    part = _line(est, "partition:ground-101:ground-102")
    assert part.status == "priced" and part.quantity == 14.4
    assert part.line_total == 1440.0
    assert part.partition_basis == "assembly"
    for sid, net in (("ground-101", 54.57), ("ground-102", 44.46)):
        ln = _line(est, f"paint:{sid}:wall")
        assert ln.quantity_detail["partition_face_excluded_m2"] == 14.4
        assert _approx(ln.quantity_detail["net_area_m2"], net - 14.4)
    te.validate_estimate_dict(est.to_dict())  # must not raise


def test_d6_mixed_or_unstated_basis_is_a_validation_error():
    model, book, fin = _fixture1()
    with pytest.raises(te.EstimateValidationError, match="partition_basis"):
        te.build_estimate(model, book, fin, partition_basis="both")
    est = te.build_estimate(model, _book_with_partition_item(), fin, partition_basis="assembly")
    doc = est.to_dict()
    doc["lines"][0]["partition_basis"] = "faces"  # tamper: mixed basis
    with pytest.raises(te.EstimateValidationError, match="mixed or unstated"):
        te.validate_estimate_dict(doc)
    doc2 = est.to_dict()
    del doc2["partition_basis"]  # unstated basis
    with pytest.raises(te.EstimateValidationError):
        te.validate_estimate_dict(doc2)
    doc3 = te.build_estimate(model, book, fin).to_dict()
    doc3["lines"].append(  # assembly line smuggled into a faces estimate
        {"line_id": "partition:x:y", "kind": "partition_assembly", "partition_basis": "faces"}
    )
    with pytest.raises(te.EstimateValidationError, match="faces basis"):
        te.validate_estimate_dict(doc3)


# ---------------------------------------------------------------------------
# D1: factory-built multi-room model (synth substitute, stated in the PR)
# ---------------------------------------------------------------------------


def test_d1_factory_model_quantities():
    """make_clean_model(): 10 x 6 m, two 5 x 6 rooms, height 3.0 from the
    level, two tag-A windows (1.5 x 1.2) on the south wall of L1-101.
    Ground truth is hand arithmetic from those parameters."""
    model = make_clean_model()
    q = te.compute_quantities(model)
    for sid in ("L1-101", "L1-102"):
        s = q.spaces[sid]
        assert _approx(s.floor_area_recomputed_m2, 30.0), sid
        assert _approx(s.wall_gross_m2, 22.0 * 3.0), sid  # perimeter 22
        assert s.height_source == "level.wall_height_m"
    assert _approx(q.spaces["L1-101"].wall_net_m2, 66.0 - 2 * 1.8)
    assert _approx(q.spaces["L1-102"].wall_net_m2, 66.0)
    assert _approx(q.total_floor_area_m2, 60.0)
    assert _approx(q.envelope_gross_total_m2, (10 + 10 + 6 + 6) * 3.0)
    assert _approx(q.envelope_net_total_m2, 96.0 - 3.6)
    assert len(q.partitions) == 1
    p = q.partitions[0]
    assert (p.space_a, p.space_b) == ("L1-101", "L1-102")
    assert _approx(p.length_m, 6.0) and _approx(p.area_m2, 18.0)
    assert p.pairing == ["exact"]
    assert _approx(q.roof.area_union_m2, 60.0)
    assert _approx(q.roof.area_gross_m2, 60.0)


def test_d1_factory_model_estimate_unassigned_finishes():
    model = make_clean_model()
    book = te.load_price_book(FX / "price_book_fixture1.json")
    fin = te.finishes_from_dict(
        {
            "header": {
                "finishes_version": "none-1",
                "source": "test fixture",
                "source_date": "2026-11-20",
            },
            "rows": [],
        }
    )
    est = te.build_estimate(model, book, fin)
    for sid in ("L1-101", "L1-102"):
        ln = _line(est, f"paint:{sid}:wall")
        assert ln.status == "unpriced" and ln.reason == "no_finish_assigned"
        trim = _line(est, f"trim:{sid}")
        assert trim.status == "priced" and _approx(trim.quantity, 22.0)
    # both tag-A windows price against the tag-less window item
    wins = [ln for ln in est.lines if ln.kind == "opening_unit"]
    assert len(wins) == 2 and all(ln.status == "priced" for ln in wins)
    asm = _line(est, "wall_assembly:none")
    assert asm.reason == "no_construction" and _approx(asm.quantity, 92.4)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_property_self_pairing_yields_zero_unmatched():
    """A polygon set paired with itself (a perfect tiling) leaves no
    unmatched interior length; a single room has no interior edges at all."""
    model2, _, _ = _case2()
    q = te.compute_quantities(model2)
    assert all(st.unmatched_m == 0.0 for st in q.partition_levels.values())
    single = _two_room_model([([0, 0], [8, 0])], [1.0, 3.0])
    del single.spaces["L1-B"]
    single.spaces["L1-A"].polygon_m = [[0, 0], [8, 0], [8, 3], [0, 3]]
    single.spaces["L1-A"].area_m2 = 24.0
    q1 = te.compute_quantities(single)
    assert q1.partitions == []
    assert q1.partition_levels["L1"].unmatched_m == 0.0


def test_property_repricing_is_quantity_invariant_under_book_permutation():
    model, book, fin = _fixture1()
    est = te.build_estimate(model, book, fin)
    doc = json.loads((FX / "price_book_fixture1.json").read_text())
    doc["items"] = list(reversed(doc["items"]))
    est2 = te.build_estimate(model, te.price_book_from_dict(doc), fin)
    assert est2.quantities.to_dict() == est.quantities.to_dict()
    assert est2.total == est.total
    assert {ln.line_id: ln.line_total for ln in est2.lines} == {
        ln.line_id: ln.line_total for ln in est.lines
    }
