"""Semi-exterior envelope between spaces (#747 slice 5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import real_set  # noqa: E402
from building_model import BuildingModel, Provenance, ReviewItem, Space  # noqa: E402
from semi_exterior import boundary_kind, semi_exterior_boundaries  # noqa: E402


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
    assert report.notes == ["semi-exterior envelope: 1 space pair(s) settled, 1 to review"]


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
