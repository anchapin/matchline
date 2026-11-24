"""Takeoff -> priced estimate over the canonical BuildingModel (matchline #1419).

The Phase 1 scratch gates G1-G7 are re-registered here as automated tests
against the vendored fixtures under ``tests/fixtures/takeoff_estimate/``
(D1). The gate ground truth is hand arithmetic written independently of the
module, exactly as in the Phase 1 runner.

The rest of the file covers the hardening the issue asks for:

* the loader refusal tests (no silent defaults: an incomplete price book or
  finishes schedule is refused, never guessed),
* design decisions D2 (explicit roof basis), D3 (cumulative facade interval
  matching), D4 (diagonal edges reported), D5 (labour basis in the schema)
  and D6 (partitions reported, not priced),
* the "no silent defaults" invariant end to end.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import takeoff_estimate as te

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "takeoff_estimate"


# --------------------------------------------------------------------- helpers
def _path(name: str) -> Path:
    return FIXTURES / name


def _model(name: str):
    return te.load_model(_path(name))


def _book(name: str) -> dict:
    return te.load_price_book(_path(name))


def _finishes(name: str, book: dict | None = None) -> dict:
    return te.load_finishes(_path(name), book=book)


def _fixture1():
    model = _model("model_fixture1.json")
    book = _book("price_book_fixture.json")
    finishes = _finishes("finishes_fixture.json", book=book)
    return model, book, finishes


def _case2():
    model = _model("model_case2.json")
    book = _book("price_book_case2.json")
    finishes = _finishes("finishes_case2.json", book=book)
    return model, book, finishes


def _estimate(case_id: str, model, book, finishes, **kwargs) -> dict:
    quantities = te.compute_quantities(model, **kwargs)
    return te.build_estimate(case_id, model, quantities, book, finishes)


def _approx(a, b, tol: float = 0.01) -> bool:
    return a is not None and abs(a - b) <= tol * max(1.0, abs(b))


# ------------------------------------------------------------------- gate G1
# Hand ground truth (independent arithmetic), fixture 1: two rooms, living
# 4.85 x 4.80 m, bedroom 2.85 x 4.80 m, 3.0 m high.
GT1_SPACES = {
    "ground-101": {"floor": 23.28, "wall_gross": 57.9, "wall_net": 54.57, "trim": 18.4},
    "ground-102": {"floor": 13.68, "wall_gross": 45.9, "wall_net": 44.46, "trim": 15.3},
}
# Hand ground truth, case 2: three rectangles 4x3, 3x3, 7x2 at 2.8 m high.
GT2_SPACES = {
    "L1-101": {"floor": 12.0, "wall_gross": 39.2, "wall_net": 35.72, "trim": 13.2},
    "L1-102": {"floor": 9.0, "wall_gross": 33.6, "wall_net": 30.84, "trim": 11.2},
    "L1-103": {"floor": 14.0, "wall_gross": 50.4, "wall_net": 44.94, "trim": 15.4},
}


def test_g1_fixture1_quantities_within_one_percent_of_hand_truth():
    model, book, finishes = _fixture1()
    q = te.compute_quantities(model)
    checks = []
    for sid, gt in GT1_SPACES.items():
        s = q["spaces"][sid]
        checks += [
            _approx(s["floor_area_recomputed_m2"], gt["floor"]),
            _approx(s["floor_area_stored_m2"], gt["floor"]),
            _approx(s["ceiling_area_m2"], gt["floor"]),
            _approx(s["wall_gross_m2"], gt["wall_gross"]),
            _approx(s["wall_net_m2"], gt["wall_net"]),
            _approx(s["trim_length_m"], gt["trim"]),
        ]
    checks += [
        _approx(q["total_floor_area_m2"], 36.96),
        _approx(q["envelope_gross_total_m2"], 78.0),
        _approx(q["envelope_net_total_m2"], 73.23),
    ]
    for facade, (gross, net) in {
        "north": (24, 22.56),
        "east": (15, 13.56),
        "south": (24, 22.11),
        "west": (15, 15),
    }.items():
        checks.append(_approx(q["envelope_by_facade"][facade]["gross_m2"], gross))
        checks.append(_approx(q["envelope_by_facade"][facade]["net_m2"], net))
    parts = q["partitions"]
    checks.append(
        len(parts) == 1
        and _approx(parts[0]["length_m"], 4.8)
        and _approx(parts[0]["area_m2"], 14.4)
        and parts[0]["pairing"] == ["offset"]
    )
    roll = {(r["category"], r["tag"]): r for r in q["opening_rollup"]}
    checks.append(
        roll[("window", "")]["count"] == 2 and _approx(roll[("window", "")]["area_m2"], 2.88)
    )
    checks.append(roll[("door", "")]["count"] == 1 and _approx(roll[("door", "")]["area_m2"], 1.89))
    assert all(checks), f"{sum(checks)}/{len(checks)} sub-checks passed"


def test_g1_case2_quantities_within_one_percent_of_hand_truth():
    model, book, finishes = _case2()
    q = te.compute_quantities(model)
    checks = []
    for sid, gt in GT2_SPACES.items():
        s = q["spaces"][sid]
        checks += [
            _approx(s["floor_area_recomputed_m2"], gt["floor"]),
            _approx(s["floor_area_stored_m2"], gt["floor"]),
            _approx(s["wall_gross_m2"], gt["wall_gross"]),
            _approx(s["wall_net_m2"], gt["wall_net"]),
            _approx(s["trim_length_m"], gt["trim"]),
        ]
    checks += [
        _approx(q["total_floor_area_m2"], 35.0),
        _approx(q["envelope_gross_total_m2"], 67.2),
        _approx(q["envelope_net_total_m2"], 62.22),
    ]
    parts = {(p["space_a"], p["space_b"]): p for p in q["partitions"]}
    for pair, (length, area) in {
        ("L1-101", "L1-102"): (3.0, 8.4),
        ("L1-101", "L1-103"): (4.0, 11.2),
        ("L1-102", "L1-103"): (3.0, 8.4),
    }.items():
        p = parts.get(pair)
        checks.append(
            p is not None
            and _approx(p["length_m"], length)
            and _approx(p["area_m2"], area)
            and p["pairing"] == ["exact"]
        )
    roll = {(r["category"], r["tag"]): r for r in q["opening_rollup"]}
    checks.append(
        roll[("window", "W1")]["count"] == 2
        and _approx(roll[("window", "W1")]["area_m2"], 2.88)
    )
    checks.append(
        roll[("door", "D1")]["count"] == 1 and _approx(roll[("door", "D1")]["area_m2"], 2.1)
    )
    checks.append(
        roll[("door", "D2")]["count"] == 2 and _approx(roll[("door", "D2")]["area_m2"], 3.36)
    )
    checks.append(
        roll[("skylight", "S1")]["count"] == 1
        and _approx(roll[("skylight", "S1")]["area_m2"], 0.36)
    )
    assert all(checks), f"{sum(checks)}/{len(checks)} sub-checks passed"


# ------------------------------------------------------------------- gate G2
def test_g2_no_opening_counted_against_the_wrong_space_facade_or_tag():
    model1, book1, fin1 = _fixture1()
    model2, book2, fin2 = _case2()
    q1 = te.compute_quantities(model1)
    q2 = te.compute_quantities(model2)
    exp1 = {
        "living-window": ("ground-101", "north", "window", ""),
        "front-door": ("ground-101", "south", "door", ""),
        "bedroom-window": ("ground-102", "east", "window", ""),
    }
    exp2 = {
        "kitchen-window": ("L1-101", "north", "window", "W1"),
        "bath-window": ("L1-102", "east", "window", "W1"),
        "hall-entry-door": ("L1-103", "south", "door", "D1"),
        "kitchen-hall-door": ("L1-101", "", "door", "D2"),
        "bath-hall-door": ("L1-102", "", "door", "D2"),
        "hall-skylight": ("L1-103", "roof", "skylight", "S1"),
    }
    got1 = {
        o["opening_id"]: (o["space_id"], o["host_facade"], o["category"], o["tag"])
        for o in q1["openings"]
    }
    got2 = {
        o["opening_id"]: (o["space_id"], o["host_facade"], o["category"], o["tag"])
        for o in q2["openings"]
    }
    assert got1 == exp1
    assert got2 == exp2
    assert all(
        o["adjacent_space_id"] == "L1-103"
        for o in q2["openings"]
        if o["opening_id"] in ("kitchen-hall-door", "bath-hall-door")
    )


# ------------------------------------------------------------------- gate G3
def test_g3_rebuild_is_byte_identical():
    model, book, finishes = _fixture1()
    first = te.serialize(_estimate("fixture1", model, book, finishes))
    second = te.serialize(_estimate("fixture1", model, book, finishes))
    assert hashlib.sha256(first.encode()).hexdigest() == hashlib.sha256(second.encode()).hexdigest()
    assert first == second


def test_g3_case2_rebuild_is_byte_identical():
    model, book, finishes = _case2()
    first = te.serialize(_estimate("case2", model, book, finishes))
    second = te.serialize(_estimate("case2", model, book, finishes))
    assert first == second


# ------------------------------------------------------------------- gate G4
def test_g4_price_only_book_bump_changes_prices_and_never_quantities():
    model, book, finishes = _fixture1()
    est = _estimate("fixture1", model, book, finishes)
    bumped = copy.deepcopy(book)
    bumped["header"]["price_book_version"] = "fixture-1.1"
    for item in bumped["items"]:
        if item["item_id"] == "flooring_laminate":
            item["material_unit_price"] = 44.0
    est_bumped = _estimate("fixture1", model, bumped, finishes)
    assert est_bumped["quantities"] == est["quantities"]
    assert est_bumped["price_book_version"] == "fixture-1.1"
    assert est_bumped["total"] != est["total"]
    line_a = next(l for l in est["lines"] if l["line_id"] == "fixture1:flooring:ground-101:floor")
    line_b = next(
        l for l in est_bumped["lines"] if l["line_id"] == "fixture1:flooring:ground-101:floor"
    )
    assert line_a["quantity"] == line_b["quantity"]
    assert line_a["line_total"] != line_b["line_total"]


# ------------------------------------------------------------------- gate G5
def _divider_moved_model():
    model, book, finishes = _fixture1()
    moved = copy.deepcopy(model.to_dict()["model"])
    moved["spaces"]["ground-101"]["polygon_m"] = [
        [0.1, 0.1],
        [4.45, 0.1],
        [4.45, 4.9],
        [0.1, 4.9],
    ]
    moved["spaces"]["ground-101"]["area_m2"] = 20.88
    moved["spaces"]["ground-101"]["volume_m3"] = 62.64
    moved["spaces"]["ground-102"]["polygon_m"] = [
        [4.55, 0.1],
        [7.9, 0.1],
        [7.9, 4.9],
        [4.55, 4.9],
    ]
    moved["spaces"]["ground-102"]["area_m2"] = 16.08
    moved["spaces"]["ground-102"]["volume_m3"] = 48.24
    return moved, book, finishes


def test_g5_divider_move_changes_exactly_the_expected_lines():
    model, book, finishes = _fixture1()
    moved, book, finishes = _divider_moved_model()
    base = {l["line_id"]: l for l in _estimate("fixture1", model, book, finishes)["lines"]}
    after = {l["line_id"]: l for l in _estimate("fixture1", moved, book, finishes)["lines"]}
    changed = sorted(k for k in base if k in after and base[k] != after[k])
    must_change = {
        "fixture1:paint:ground-101:wall",
        "fixture1:paint:ground-102:wall",
        "fixture1:paint:ground-101:ceiling",
        "fixture1:paint:ground-102:ceiling",
        "fixture1:flooring:ground-101:floor",
        "fixture1:tile:ground-102:floor",
        "fixture1:trim:ground-101",
        "fixture1:trim:ground-102",
        "fixture1:spare:ground-101:floor",
    }
    must_not_change = {
        "fixture1:opening:living-window",
        "fixture1:opening:bedroom-window",
        "fixture1:opening:front-door",
        "fixture1:wall_assembly:none",
    }
    assert must_change.issubset(set(changed))
    assert all(base[k] == after[k] for k in must_not_change)
    quantities = te.compute_quantities(moved)
    assert _approx(quantities["spaces"]["ground-101"]["floor_area_recomputed_m2"], 20.88)
    assert _approx(quantities["spaces"]["ground-102"]["floor_area_recomputed_m2"], 16.08)
    assert _approx(quantities["envelope_net_total_m2"], 73.23)
    assert len(quantities["partitions"]) == 1
    assert _approx(quantities["partitions"][0]["length_m"], 4.8)


def test_g5_finish_change_replaces_exactly_the_expected_lines():
    model, book, finishes = _fixture1()
    base = {l["line_id"]: l for l in _estimate("fixture1", model, book, finishes)["lines"]}
    changed = copy.deepcopy(finishes)
    for row in changed["rows"]:
        if row["space_id"] == "ground-102" and row["surface"] == "floor":
            row["finish_id"] = "flooring_laminate"
    after = {l["line_id"]: l for l in _estimate("fixture1", model, book, changed)["lines"]}
    assert sorted(set(base) - set(after)) == ["fixture1:tile:ground-102:floor"]
    assert sorted(set(after) - set(base)) == [
        "fixture1:flooring:ground-102:floor",
        "fixture1:spare:ground-102:floor",
    ]
    assert [k for k in base if k in after and base[k] != after[k]] == []


# ------------------------------------------------------------------- gate G6
def test_g6_unmatched_interior_edge_length_is_reported_within_five_percent():
    model1, _, _ = _fixture1()
    model2, _, _ = _case2()
    for model, level in ((model1, "ground"), (model2, "L1")):
        stats = te.compute_quantities(model)["partition_levels"][level]
        assert stats["unmatched_m"] <= 0.05 * stats["interior_edge_total_m"]
        assert stats["unmatched_pct"] <= 5.0


# ------------------------------------------------------------------- gate G7
@pytest.mark.parametrize("case", ["fixture1", "case2"])
def test_g7_every_priced_line_traces_to_element_rule_price_item_and_version(case):
    if case == "fixture1":
        model, book, finishes = _fixture1()
    else:
        model, book, finishes = _case2()
    est = _estimate(case, model, book, finishes)
    priced = [l for l in est["lines"] if l["status"] == "priced"]
    assert priced
    for line in priced:
        prov = line["provenance"]
        assert prov["element_ids"]
        assert prov["rule"]
        assert prov["price_item_id"]
        assert prov["price_book_version"] == est["price_book_version"]


# ------------------------------------------------------- Phase 1 parity
def _phase1(name: str) -> dict:
    return json.loads(_path(name).read_text())


@pytest.mark.parametrize(
    "case,model_name,book_name,finishes_name,expected_name",
    [
        (
            "fixture1",
            "model_fixture1.json",
            "price_book_fixture.json",
            "finishes_fixture.json",
            "expected_estimate_fixture1.json",
        ),
        (
            "case2",
            "model_case2.json",
            "price_book_case2.json",
            "finishes_case2.json",
            "expected_estimate_case2.json",
        ),
    ],
)
def test_phase1_estimate_is_reproduced(case, model_name, book_name, finishes_name, expected_name):
    model = _model(model_name)
    book = _book(book_name)
    finishes = _finishes(finishes_name, book=book)
    est = _estimate(case, model, book, finishes)
    expected = _phase1(expected_name)
    assert est["total"] == expected["total"]
    assert est["subtotals_by_category"] == expected["subtotals_by_category"]
    assert est["unpriced_line_ids"] == expected["unpriced_line_ids"]
    assert est["total_label"] == expected["total_label"]
    expected_lines = {l["line_id"]: l for l in expected["lines"]}
    assert [l["line_id"] for l in est["lines"]] == list(expected_lines)
    for line in est["lines"]:
        want = expected_lines[line["line_id"]]
        for key in (
            "quantity",
            "unit",
            "status",
            "material_cost",
            "labour_cost",
            "line_total",
            "reason",
        ):
            assert line.get(key) == want.get(key), f"{line['line_id']}.{key}"
        assert line["provenance"]["price_item_id"] == want["provenance"]["price_item_id"]
        assert line["provenance"]["element_ids"] == want["provenance"]["element_ids"]


def test_phase1_totals_are_the_reported_ones():
    model, book, finishes = _fixture1()
    assert _estimate("fixture1", model, book, finishes)["total"] == 6697.19
    model, book, finishes = _case2()
    assert _estimate("case2", model, book, finishes)["total"] == 19028.96


# ------------------------------------------------------------- loader refusal
def test_loader_accepts_the_fixture_books():
    book = _book("price_book_fixture.json")
    assert book["header"]["price_book_version"] == "fixture-1.0"
    assert book["header"]["region"] == "FIXTURE"


def test_fixture_books_are_labelled_fixture_and_carry_no_market_data():
    for name in ("price_book_fixture.json", "price_book_case2.json"):
        book = _book(name)
        assert "FIXTURE" in book["header"]["kind"]
        assert book["header"]["region"] == "FIXTURE"
        assert book["header"]["source"] == "fixture"
        for item in book["items"]:
            assert item["source"] == "fixture"


def _write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    return path


def test_price_book_refuses_a_missing_header_field(tmp_path):
    book = _book("price_book_fixture.json")
    del book["header"]["currency"]
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "missing_header_field"
    assert exc.value.details["field"] == "currency"


def test_price_book_refuses_an_item_without_a_source(tmp_path):
    book = _book("price_book_fixture.json")
    del book["items"][0]["source"]
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_missing_source"


def test_price_book_refuses_a_duplicate_item_id(tmp_path):
    book = _book("price_book_fixture.json")
    book["items"].append(copy.deepcopy(book["items"][0]))
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "duplicate_item_id"


def test_price_book_refuses_a_negative_price(tmp_path):
    book = _book("price_book_fixture.json")
    book["items"][0]["material_unit_price"] = -1.0
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_negative_material_price"


def test_price_book_refuses_an_item_without_a_price(tmp_path):
    book = _book("price_book_fixture.json")
    del book["items"][0]["material_unit_price"]
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_missing_material_price"


def test_price_book_refuses_an_unknown_category_unit_or_labour_basis(tmp_path):
    book = _book("price_book_fixture.json")
    book["items"][0]["category"] = "roofing"
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_unknown_category"

    book = _book("price_book_fixture.json")
    book["items"][0]["unit"] = "kg"
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_unknown_unit"

    book = _book("price_book_fixture.json")
    book["items"][0]["labour_basis"] = "per_bag"
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_unknown_labour_basis"


def test_price_book_refuses_a_m2_labour_basis_without_a_rate(tmp_path):
    book = _book("price_book_fixture.json")
    book["items"][0]["labour_basis"] = "m2"
    with pytest.raises(te.PriceBookError) as exc:
        te.load_price_book(_write(tmp_path, "book.json", book))
    assert exc.value.code == "item_missing_labour_rate"


def test_finishes_refuse_an_unknown_surface(tmp_path):
    finishes = _finishes("finishes_fixture.json")
    finishes["rows"][0]["surface"] = "soffit"
    with pytest.raises(te.FinishesError) as exc:
        te.load_finishes(_write(tmp_path, "finishes.json", finishes))
    assert exc.value.code == "row_unknown_surface"


def test_finishes_refuse_a_duplicate_assignment(tmp_path):
    finishes = _finishes("finishes_fixture.json")
    finishes["rows"].append(copy.deepcopy(finishes["rows"][0]))
    with pytest.raises(te.FinishesError) as exc:
        te.load_finishes(_write(tmp_path, "finishes.json", finishes))
    assert exc.value.code == "row_duplicate_assignment"


def test_finishes_refuse_a_finish_the_book_does_not_price(tmp_path):
    finishes = _finishes("finishes_fixture.json")
    finishes["rows"][0]["finish_id"] = "marble_slab"
    book = _book("price_book_fixture.json")
    with pytest.raises(te.FinishesError) as exc:
        te.load_finishes(_write(tmp_path, "finishes.json", finishes), book=book)
    assert exc.value.code == "row_unknown_finish_id"


def test_finishes_refuse_a_space_the_model_does_not_contain(tmp_path):
    model, book, finishes = _fixture1()
    finishes["rows"][0]["space_id"] = "ground-999"
    with pytest.raises(te.FinishesError) as exc:
        te.build_estimate("fixture1", model, te.compute_quantities(model), book, finishes)
    assert exc.value.code == "row_unknown_space_id"


def test_model_refuses_a_document_that_is_not_a_building_model(tmp_path):
    with pytest.raises(te.ModelError) as exc:
        te.load_model(_write(tmp_path, "model.json", {"not": "a model"}))
    assert exc.value.code == "not_a_building_model"


def test_model_refuses_invalid_json(tmp_path):
    with pytest.raises(te.ModelError) as exc:
        te.load_model(_write(tmp_path, "model.json", "{not json"))
    assert exc.value.code == "invalid_json"


# ------------------------------------------------------------------------- D2
def test_d2_roof_basis_is_always_stated():
    model, _, _ = _fixture1()
    quantities = te.compute_quantities(model)
    assert quantities["roof"]["basis"] == "flat_roof_union_of_space_polygons"
    assert quantities["roof"]["area_m2"] == 36.96
    assert "wall-thickness" in quantities["roof"]["basis_note"]
    assert quantities["roof_basis_requested"] == "auto"


def test_d2_roof_basis_can_be_pinned_explicitly():
    model, _, _ = _fixture1()
    quantities = te.compute_quantities(model, roof_basis="space_union")
    assert quantities["roof"]["basis"] == "flat_roof_union_of_space_polygons"
    assert quantities["roof_basis_requested"] == "space_union"


def test_d2_roof_planes_basis_without_planes_is_unquantified_with_a_reason():
    model, _, _ = _fixture1()
    quantities = te.compute_quantities(model, roof_basis="roof_planes")
    assert quantities["roof"]["basis"] == "roof_planes_stored_area"
    assert quantities["roof"]["area_m2"] is None
    assert quantities["roof"]["unquantified_reason"] == "no_roof_plane_areas"


def test_d2_stored_roof_planes_win_over_the_space_union():
    model, _, _ = _fixture1()
    with_planes = model.to_dict()["model"]
    with_planes["roof_planes"] = [{"id": "roof-1", "area_m2": 40.0}]
    quantities = te.compute_quantities(with_planes)
    assert quantities["roof"]["basis"] == "roof_planes_stored_area"
    assert quantities["roof"]["area_m2"] == 40.0


def test_d2_an_unknown_roof_basis_is_refused():
    model, _, _ = _fixture1()
    with pytest.raises(ValueError):
        te.compute_quantities(model, roof_basis="gross_footprint")


# ------------------------------------------------------------------------- D3
def _split_north_facade_model():
    """Fixture 1 with the north facade split in two segments and the living
    window straddling the split."""
    model = _model("model_fixture1.json").to_dict()["model"]
    model["envelope"][0] = {
        "area_m2": 12.0,
        "construction_id": "",
        "facade": "north",
        "from_m": [0.0, 0.0],
        "height_m": 3.0,
        "id": "ground-outer-walls#0a",
        "length_m": 4.0,
        "space_id": "ground-101",
        "to_m": [4.0, 0.0],
    }
    model["envelope"].insert(
        1,
        {
            "area_m2": 12.0,
            "construction_id": "",
            "facade": "north",
            "from_m": [4.0, 0.0],
            "height_m": 3.0,
            "id": "ground-outer-walls#0b",
            "length_m": 4.0,
            "space_id": "ground-101",
            "to_m": [8.0, 0.0],
        },
    )
    # the window spans 3.5 m .. 4.7 m: 0.5 m on the first segment, 0.7 m on
    # the second
    model["spaces"]["ground-101"]["openings"][0]["host_interval_m"] = [3.5, 4.7]
    return model


def test_d3_an_opening_spanning_two_segments_deducts_proportionally():
    model = _split_north_facade_model()
    quantities = te.compute_quantities(model)
    segments = {s["segment_id"]: s for s in quantities["envelope_segments"]}
    first = segments["ground-outer-walls#0a"]
    second = segments["ground-outer-walls#0b"]
    assert first["hosted_opening_ids"] == ["living-window"]
    assert second["hosted_opening_ids"] == ["living-window"]
    # 0.5 m of 1.2 m on the first segment, 0.7 m on the second
    assert _approx(first["net_m2"], 12.0 - 1.44 * 0.5 / 1.2)
    assert _approx(second["net_m2"], 12.0 - 1.44 * 0.7 / 1.2)
    assert _approx(quantities["envelope_net_total_m2"], 73.23)


def test_d3_an_opening_without_an_interval_falls_back_to_the_space_id_and_says_so():
    model = _model("model_fixture1.json").to_dict()["model"]
    model["spaces"]["ground-101"]["openings"][0]["host_interval_m"] = None
    quantities = te.compute_quantities(model)
    segment = next(
        s for s in quantities["envelope_segments"] if s["segment_id"] == "ground-outer-walls#0"
    )
    deduction = segment["opening_deductions"][0]
    assert deduction["basis"] == "facade_space_id_fallback"
    assert _approx(segment["net_m2"], 22.56)


def test_d3_an_interval_that_disagrees_with_the_space_id_is_reported():
    model = _split_north_facade_model()
    # the second half of the north facade belongs to the bedroom, but the
    # window that straddles the split belongs to the living room
    model["envelope"][1]["space_id"] = "ground-102"
    quantities = te.compute_quantities(model)
    codes = {w["code"] for w in quantities["warnings"]}
    assert "opening_interval_space_id_mismatch" in codes


# ------------------------------------------------------------------------- D4
def test_d4_a_diagonal_interior_edge_is_reported_not_dropped():
    model = _model("model_fixture1.json").to_dict()["model"]
    # a diagonal edge on the living room
    model["spaces"]["ground-101"]["polygon_m"] = [
        [0.1, 0.1],
        [4.95, 0.1],
        [4.95, 4.9],
        [2.0, 4.9],
    ]
    quantities = te.compute_quantities(model)
    unmatched = quantities["unmatched_edges"]
    assert unmatched
    assert unmatched[0]["reason"] == "diagonal_edge_not_pairable"
    assert unmatched[0]["space_id"] == "ground-101"
    assert unmatched[0]["length_m"] > 0
    stats = quantities["partition_levels"]["ground"]
    assert stats["unmatched_reasons"]["diagonal_edge_not_pairable"] > 0
    assert stats["unmatched_m"] > 0


# ------------------------------------------------------------------------- D5
def test_d5_labour_basis_is_recorded_on_every_priced_line():
    model, book, finishes = _fixture1()
    est = _estimate("fixture1", model, book, finishes)
    for line in est["lines"]:
        if line["status"] != "priced":
            continue
        assert line["labour_basis"] == "unit"
        assert line["labour_basis_stated"] is False  # inferred from the legacy fields


def test_d5_a_stated_m2_labour_basis_prices_per_square_metre():
    model, book, finishes = _fixture1()
    book = copy.deepcopy(book)
    for item in book["items"]:
        if item["item_id"] == "flooring_laminate":
            item["labour_basis"] = "m2"
            item["labour_hours_per_m2"] = 0.6
            item.pop("labour_hours_per_unit")
    est = _estimate("fixture1", model, book, finishes)
    line = next(l for l in est["lines"] if l["line_id"] == "fixture1:flooring:ground-101:floor")
    assert line["labour_basis"] == "m2"
    assert line["labour_basis_stated"] is True
    assert _approx(line["labour_hours"], 23.28 * 0.6)


def test_d5_an_m2_labour_basis_without_an_area_basis_is_flagged():
    model, book, finishes = _fixture1()
    book = copy.deepcopy(book)
    for item in book["items"]:
        if item["item_id"] == "trim_baseboard":
            item["labour_basis"] = "m2"
            item["labour_hours_per_m2"] = 0.15
            item.pop("labour_hours_per_unit")
    est = _estimate("fixture1", model, book, finishes)
    line = next(l for l in est["lines"] if l["line_id"] == "fixture1:trim:ground-101")
    assert line["labour_flag"] == "no_area_basis_for_labour"
    assert line["labour_cost"] is None
    assert line["material_cost"] is not None


# ------------------------------------------------------------------------- D6
def test_d6_partitions_are_reported_and_not_priced():
    model, book, finishes = _fixture1()
    est = _estimate("fixture1", model, book, finishes)
    basis = est["partition_pricing_basis"]
    assert basis["status"] == "not_priced"
    assert basis["reason"] == "per_space_face_basis_covers_partition_faces"
    assert basis["partition_count"] == 1
    assert _approx(basis["partition_area_m2"], 14.4)
    assert not [l for l in est["lines"] if l["category"] == "partition"]
    for part in est["quantities"]["partitions"]:
        assert part["pricing_status"] == "not_priced"
        assert part["not_priced_reason"] == "per_space_face_basis_covers_partition_faces"


def test_d6_a_partition_price_item_in_the_book_is_listed_as_not_priced():
    model, book, finishes = _fixture1()
    book = copy.deepcopy(book)
    book["items"].append(
        {
            "applies_to": {"construction_id": "PART-1"},
            "category": "partition",
            "description": "Partition assembly (fixture)",
            "item_id": "partition_assembly",
            "labour_hours_per_unit": 0.5,
            "labour_rate_per_hour": 65.0,
            "material_unit_price": 30.0,
            "source": "fixture",
            "source_date": "2026-11-20",
            "unit": "m2",
        }
    )
    est = _estimate("fixture1", model, book, finishes)
    assert est["not_priced_items"] == [
        {
            "price_item_id": "partition_assembly",
            "reason": "partition_pricing_not_enabled_in_this_release",
        }
    ]


# ------------------------------------------------- no silent defaults
def test_a_missing_price_item_produces_an_unpriced_line_with_a_reason():
    model, book, finishes = _fixture1()
    book = copy.deepcopy(book)
    book["items"] = [i for i in book["items"] if i["item_id"] != "window_unit"]
    est = _estimate("fixture1", model, book, finishes)
    line = next(l for l in est["lines"] if l["line_id"] == "fixture1:opening:living-window")
    assert line["status"] == "unpriced"
    assert line["reason"] == "no_price_item"
    assert line["line_total"] is None
    assert line["quantity"] == 1.0


def test_a_missing_coverage_produces_an_unpriced_line_with_a_reason():
    model, book, finishes = _fixture1()
    book = copy.deepcopy(book)
    for item in book["items"]:
        if item["item_id"] == "paint_interior":
            item["coverage_m2_per_l"] = None
    est = _estimate("fixture1", model, book, finishes)
    line = next(l for l in est["lines"] if l["line_id"] == "fixture1:paint:ground-101:wall")
    assert line["status"] == "unpriced"
    assert line["reason"] == "no_coverage"


def test_a_space_without_a_height_is_unquantified_with_a_reason():
    model = _model("model_fixture1.json").to_dict()["model"]
    model["spaces"]["ground-101"]["height_m"] = None
    model["spaces"]["ground-101"]["ceiling_height_m"] = None
    model["levels"][0]["wall_height_m"] = None
    quantities = te.compute_quantities(model)
    space = quantities["spaces"]["ground-101"]
    assert space["height_m"] is None
    assert space["height_source"] == "missing"
    assert space["wall_gross_m2"] is None
    assert space["wall_gross_unquantified_reason"] == "no_height_source"


def test_unpriced_line_ids_match_the_unpriced_lines():
    model, book, finishes = _fixture1()
    est = _estimate("fixture1", model, book, finishes)
    unpriced = [l["line_id"] for l in est["lines"] if l["status"] == "unpriced"]
    assert unpriced == est["unpriced_line_ids"]
    assert est["total_label"] == "priced_lines_only"
    for line in est["lines"]:
        if line["status"] == "unpriced":
            assert line["reason"]
            assert line["line_total"] is None


# ---------------------------------------------------------------- properties
@given(
    rects=st.lists(
        st.tuples(
            st.floats(min_value=-1000, max_value=1000, allow_nan=False),
            st.floats(min_value=-1000, max_value=1000, allow_nan=False),
            st.floats(min_value=0.01, max_value=100, allow_nan=False),
            st.floats(min_value=0.01, max_value=100, allow_nan=False),
        ),
        min_size=1,
        max_size=6,
    )
)
@settings(
    max_examples=50,
    deadline=30000,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_union_area_is_at_least_the_largest_single_polygon(rects):
    polys = []
    for (x, y, w, h) in rects:
        polys.append([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])
    union = te.union_area_axis_aligned(polys)
    assert union is not None
    assert union >= max(te.shoelace(p) for p in polys) - 1e-6
    assert union <= sum(te.shoelace(p) for p in polys) + 1e-6


@given(
    width=st.floats(min_value=0.01, max_value=1000, allow_nan=False),
    height=st.floats(min_value=0.01, max_value=1000, allow_nan=False),
)
@settings(max_examples=50, deadline=30000, derandomize=True)
def test_property_shoelace_area_of_a_rectangle_is_width_times_height(width, height):
    poly = [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]
    assert _approx(te.shoelace(poly), width * height, 1e-6)
    assert _approx(te.perimeter(poly), 2 * (width + height), 1e-6)
