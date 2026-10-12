"""Semi-exterior envelope between spaces (#747 slice 5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import real_set  # noqa: E402
from building_model import BuildingModel, Level, Provenance, ReviewItem, Space  # noqa: E402
from semi_exterior import (  # noqa: E402
    boundary_kind,
    semi_exterior_boundaries,
    semi_exterior_floor_boundaries,
    vestibule_interior_boundaries,
)


def _sp(sid, x0, x1, cat=None, level="L1", y0=0.0, y1=10.0):
    return Space(
        id=sid,
        level_id=level,
        polygon_m=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        conditioning=None if cat is None else {"category": cat, "reasons": []},
    )


def _row(*cats):
    """Spaces side by side, 5 m wide each, sharing 10 m walls."""
    return {f"S{i}": _sp(f"S{i}", 5.0 * i, 5.0 * (i + 1), c) for i, c in enumerate(cats)}


@pytest.mark.parametrize(
    "a,b,kind",
    [
        ("conditioned", "unconditioned", "semi_exterior"),
        ("semiheated", "conditioned", "semi_exterior"),
        ("conditioned", "conditioned", None),
        ("semiheated", "unconditioned", None),
        ("unconditioned", "unconditioned", None),
        ("conditioned", "review", "review"),
        ("conditioned", None, "review"),
        ("unconditioned", "review", "review"),
        (None, "semiheated", "review"),
        ("review", "review", "review"),
        (None, None, None),
    ],
)
def test_boundary_kind(a, b, kind):
    assert boundary_kind(a, b)[0] == kind
    assert boundary_kind(b, a)[0] == kind


def test_conditioned_next_to_unconditioned_is_semi_exterior():
    recs = semi_exterior_boundaries(_row("conditioned", "unconditioned"))
    assert len(recs) == 1
    r = recs[0]
    assert r["spaces"] == ["S0", "S1"] and r["kind"] == "semi_exterior"
    assert r["categories"] == ["conditioned", "unconditioned"]
    assert r["length_m"] == pytest.approx(10.0)
    assert r["segments"] == [[[5.0, 0.0], [5.0, 10.0]]] or r["segments"] == [
        [[5.0, 10.0], [5.0, 0.0]]
    ]


def test_exterior_ring_and_like_pairs_are_not_reported():
    # S0|S1 both conditioned: interior; S1|S2 semi-exterior; S0 and S2 don't touch
    recs = semi_exterior_boundaries(_row("conditioned", "conditioned", "semiheated"))
    assert [(r["spaces"], r["kind"]) for r in recs] == [(["S1", "S2"], "semi_exterior")]


def test_no_categories_stays_silent():
    assert semi_exterior_boundaries(_row(None, None, None)) == []


def test_unsettled_side_goes_to_review():
    recs = semi_exterior_boundaries(_row("conditioned", None))
    assert [r["kind"] for r in recs] == ["review"]
    assert "unknown" in recs[0]["reason"]


def test_levels_do_not_mix():
    sp = {
        "A": _sp("A", 0, 5, "conditioned", level="L1"),
        "B": _sp("B", 5, 10, "unconditioned", level="L2"),
    }
    assert semi_exterior_boundaries(sp) == []


def test_partial_shared_edge_length():
    sp = {
        "A": _sp("A", 0, 5, "conditioned"),
        "B": _sp("B", 5, 10, "unconditioned", y0=4.0, y1=10.0),
    }
    (r,) = semi_exterior_boundaries(sp)
    # the same 0.05 m snap tolerance the gbXML interior walls use (#765)
    assert r["length_m"] == pytest.approx(6.0, abs=0.06)


def _model(spaces):
    return BuildingModel(spaces=spaces)


def test_real_set_writes_records_and_review_items():
    m = _model(_row("conditioned", "unconditioned", None))
    review, report = [], type("R", (), {"notes": []})()
    real_set._semi_exterior(m, review, report, Provenance, ReviewItem)
    assert [(r["spaces"], r["kind"]) for r in m.semi_exterior] == [
        (["S0", "S1"], "semi_exterior"),
        (["S1", "S2"], "review"),
    ]
    assert [i.id for i in review] == ["rq-semi-exterior-S1-S2"]
    assert review[0].kind == "semi_exterior_envelope" and review[0].needs_review
    assert report.notes == [
        "semi-exterior envelope: 1 space pair(s) settled, 0 vestibule sentence, 1 to review"
    ]


def test_real_set_skips_when_nothing_classified():
    m = _model(_row(None, None))
    review, report = [], type("R", (), {"notes": []})()
    real_set._semi_exterior(m, review, report, Provenance, ReviewItem)
    assert m.semi_exterior == [] and review == [] and report.notes == []


def test_semi_exterior_round_trips_json():
    m = _model(_row("conditioned", "semiheated"))
    real_set._semi_exterior(m, [], type("R", (), {"notes": []})(), Provenance, ReviewItem)
    back = BuildingModel.from_json(m.to_json())
    assert back.semi_exterior == m.semi_exterior and back.semi_exterior


def _stack(a_cat, b_cat, shift=0.0):
    """Conditioned-side test pair on adjacent levels sharing a 5x10 footprint."""
    sp = {
        "A": _sp("A", 0.0, 5.0, a_cat, level="L0"),
        "B": _sp("B", shift, shift + 5.0, b_cat, level="L1"),
    }
    return sp, [Level(id="L0", elevation_z_m=0.0), Level(id="L1", elevation_z_m=3.0)]


def test_floor_between_conditioned_and_unconditioned_is_semi_exterior():
    sp, levels = _stack("conditioned", "unconditioned")
    (r,) = semi_exterior_floor_boundaries(sp, levels)
    assert r["spaces"] == ["A", "B"]
    assert r["kind"] == "semi_exterior"
    assert r["area_m2"] == pytest.approx(50.0)
    assert r["plane"] == "floor"


def test_floor_kind_is_symmetric_across_levels():
    sp, levels = _stack("unconditioned", "conditioned")
    recs = semi_exterior_floor_boundaries(sp, levels)
    assert len(recs) == 1 and recs[0]["kind"] == "semi_exterior"
    assert recs[0]["categories"] == ["unconditioned", "conditioned"]


def test_floor_both_conditioned_is_silent():
    sp, levels = _stack("conditioned", "conditioned")
    assert semi_exterior_floor_boundaries(sp, levels) == []


def test_floor_semiheated_against_unconditioned_is_silent():
    sp, levels = _stack("semiheated", "unconditioned")
    assert semi_exterior_floor_boundaries(sp, levels) == []


def test_floor_unsettled_side_goes_to_review():
    sp, levels = _stack("conditioned", None)
    (r,) = semi_exterior_floor_boundaries(sp, levels)
    assert r["kind"] == "review" and "unknown" in r["reason"]


def test_floor_non_adjacent_levels_ignored():
    sp, levels = _stack("conditioned", "unconditioned")
    levels.insert(1, Level(id="L05", elevation_z_m=1.5))
    assert semi_exterior_floor_boundaries(sp, levels) == []


def test_floor_no_footprint_overlap_ignored():
    sp, levels = _stack("conditioned", "unconditioned", shift=20.0)
    assert semi_exterior_floor_boundaries(sp, levels) == []


def test_floor_sliver_overlap_ignored():
    sp, levels = _stack("conditioned", "unconditioned", shift=4.9999)
    assert semi_exterior_floor_boundaries(sp, levels) == []


def test_real_set_records_walls_then_floors():
    sp = {
        "A": _sp("A", 0.0, 5.0, "conditioned", level="L0"),
        "B": _sp("B", 5.0, 10.0, "unconditioned", level="L0"),
        "C": _sp("C", 0.0, 5.0, "unconditioned", level="L1"),
    }
    levels = [Level(id="L0", elevation_z_m=0.0), Level(id="L1", elevation_z_m=3.0)]
    m = BuildingModel(spaces=sp, levels=levels)
    review, report = [], type("R", (), {"notes": []})()
    real_set._semi_exterior(m, review, report, Provenance, ReviewItem)
    planes = [r["plane"] for r in m.semi_exterior]
    assert planes == ["wall", "floor"]
    assert m.semi_exterior[0]["spaces"] == ["A", "B"]
    assert m.semi_exterior[1]["spaces"] == ["A", "C"]
    assert review == []
    assert report.notes == [
        "semi-exterior envelope: 2 space pair(s) settled, 0 vestibule sentence, 0 to review"
    ]


def test_real_set_floor_and_wall_records_keep_distinct_review_ids():
    sp = {
        "S0": _sp("S0", 0.0, 5.0, "conditioned", level="L0"),
        "S1": _sp("S1", 5.0, 10.0, "unconditioned", level="L0"),
        "S2": _sp("S2", 0.0, 5.0, "conditioned", level="L1"),
        "S3": _sp("S3", 5.0, 10.0, None, level="L1"),
    }
    levels = [Level(id="L0", elevation_z_m=0.0), Level(id="L1", elevation_z_m=3.0)]
    m = BuildingModel(spaces=sp, levels=levels)
    review, report = [], type("R", (), {"notes": []})()
    real_set._semi_exterior(m, review, report, Provenance, ReviewItem)
    planes = [r["plane"] for r in m.semi_exterior]
    assert planes == ["wall", "wall", "floor"]
    assert m.semi_exterior[0]["spaces"] == ["S0", "S1"]
    assert m.semi_exterior[1]["spaces"] == ["S2", "S3"]  # wall review on L1
    assert m.semi_exterior[2]["spaces"] == ["S1", "S3"]  # S3 overlaps S1 only
    assert [i.id for i in review] == [
        "rq-semi-exterior-S2-S3",
        "rq-semi-exterior-floor-S1-S3",
    ]
    assert report.notes == [
        "semi-exterior envelope: 1 space pair(s) settled, 0 vestibule sentence, 2 to review"
    ]


def test_construction_stage_skips_floor_records():
    from constructions import semi_exterior_walls

    m = _model({})
    m.semi_exterior = [
        {
            "spaces": ["A", "B"],
            "kind": "semi_exterior",
            "plane": "floor",
            "area_m2": 25.0,
        }
    ]
    assert semi_exterior_walls(m) == {}


def test_default_semi_exterior_wall_leaves_floor_records_alone():
    from construction_library import _default_semi_exterior

    m = _model({})
    m.semi_exterior = [
        {
            "spaces": ["A", "B"],
            "kind": "semi_exterior",
            "plane": "floor",
            "area_m2": 25.0,
        }
    ]
    summary = type("S", (), {"defaulted": {}, "unmatched": {}, "warnings": []})()
    _default_semi_exterior(m, summary, "5A")
    assert m.semi_exterior[0].get("construction_id") is None
    assert summary.defaulted == {}


# --- 5.5.2 vestibule sentence: interior surfaces of unconditioned vestibules (#747) ---


def _sp2(sid, x0, x1, cat=None, vest=False):
    return Space(
        id=sid,
        level_id="L1",
        polygon_m=[[x0, 0.0], [x1, 0.0], [x1, 10.0], [x0, 10.0]],
        conditioning=None if cat is None else {"category": cat, "reasons": []},
        is_vestibule=vest,
    )


def _vest_row(*specs):
    """specs: (cat or None, is_vestibule) per 5 m space."""
    out = {}
    for i, (cat, vest) in enumerate(specs):
        out[f"S{i}"] = _sp2(f"S{i}", 5.0 * i, 5.0 * (i + 1), cat, vest)
    return out


def test_vestibule_against_unconditioned_gets_vestibule_record():
    recs = vestibule_interior_boundaries(
        _vest_row(("unconditioned", True), ("unconditioned", False))
    )
    assert len(recs) == 1
    r = recs[0]
    assert r["spaces"] == ["S0", "S1"] and r["kind"] == "vestibule"
    assert r["plane"] == "wall" and r["length_m"] == pytest.approx(10.0)
    assert "5.5.2" in r["reason"]


def test_vestibule_against_conditioned_not_duplicated():
    # conditioned pair is already a semi-exterior record; the vestibule
    # machinery must not add a second record for the same pair
    spaces = _vest_row(("unconditioned", True), ("conditioned", False))
    assert vestibule_interior_boundaries(spaces) == []
    recs = semi_exterior_boundaries(spaces)
    assert len(recs) == 1 and recs[0]["kind"] == "semi_exterior"


def test_vestibule_against_unsettled_neighbor_stays_review_only():
    # unsettled neighbor: the ordinary machinery already reports review;
    # the vestibule machinery adds nothing
    for other in ("review", None):
        spaces = _vest_row(("unconditioned", True), (other, False))
        assert vestibule_interior_boundaries(spaces) == []
        recs = semi_exterior_boundaries(spaces)
        assert len(recs) == 1 and recs[0]["kind"] == "review"


def test_vestibule_side_must_be_settled_unconditioned():
    # a conditioned or unsettled vestibule gets no vestibule record
    assert (
        vestibule_interior_boundaries(_vest_row(("conditioned", True), ("unconditioned", False)))
        == []
    )
    assert vestibule_interior_boundaries(_vest_row((None, True), ("unconditioned", False))) == []
    assert (
        vestibule_interior_boundaries(_vest_row(("unconditioned", False), ("unconditioned", False)))
        == []
    )


def test_vestibule_against_vestibule_one_record():
    recs = vestibule_interior_boundaries(
        _vest_row(("unconditioned", True), ("unconditioned", True))
    )
    assert len(recs) == 1 and recs[0]["kind"] == "vestibule"


def test_semiheated_against_unconditioned_without_vestibule_unreported():
    # unchanged from slice 5: no vestibule mark, no record
    assert (
        vestibule_interior_boundaries(_vest_row(("semiheated", False), ("unconditioned", False)))
        == []
    )
