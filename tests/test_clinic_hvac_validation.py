"""#707: real-model HVAC room assignment check (opt-in) and its helpers."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import validate_clinic_hvac as vch  # noqa: E402


def test_footprint_and_summarize():
    pytest.importorskip("shapely")
    # unit square as two triangles -> one 4-corner ring of area 1
    verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
    ring = vch.footprint(verts, [(0, 1, 2), (0, 2, 3)])
    assert len(ring) == 4
    xs, ys = zip(*ring)
    assert (min(xs), max(xs), min(ys), max(ys)) == pytest.approx((0, 1, 0, 1), abs=1e-5)
    s = vch.summarize(
        [("L1", "correct", "interior"), ("L1", "wrong", "nearest"), ("L2", "review", "ambiguous")]
    )
    assert s["L1"]["accuracy"] == 0.5 and s["L1"]["wrong"] == 1
    assert s["L2"]["review"] == 1 and s["L2"]["accuracy"] == 0.0


CLINIC = os.environ.get("MATCHLINE_CLINIC_HVAC")


@pytest.mark.skipif(not CLINIC or not Path(CLINIC).exists(), reason="set MATCHLINE_CLINIC_HVAC")
def test_clinic_assignment_exact_positions():
    pytest.importorskip("ifcopenshell")
    rooms, terminals = vch.load(CLINIC)
    assert len(terminals) == 437
    rep = vch.evaluate(rooms, terminals, jitter_m=0.0)
    assert all(r["wrong"] == 0 for r in rep.values())
    assert sum(r["correct"] for r in rep.values()) == 437


def test_dedupe_duplicate_rooms_collapses_identical_footprints_and_remaps_gt():
    # #1008: the duplex MEP file carries each space twice (A101 and A101-M,
    # identical geometry, regenerated GlobalIds), so point-in-polygon ties for
    # every terminal. One room per identical footprint, gt referenced copy
    # kept, dropped copies remapped so scoring still matches truth.
    poly = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
    other = [(5.0, 0.0), (9.0, 0.0), (9.0, 3.0), (5.0, 3.0)]
    raw = [
        {"id": "aaa", "name": "A101", "polygon_m": poly, "storey": "L1"},
        {"id": "bbb", "name": "A101-M", "polygon_m": poly, "storey": "L1"},
        {"id": "ccc", "name": "A102", "polygon_m": other, "storey": "L1"},
    ]
    rooms, remap = vch.dedupe_duplicate_rooms(raw, {"bbb"})
    assert sorted(r["id"] for r in rooms) == ["bbb", "ccc"]
    assert remap == {"aaa": "bbb"}
    assert remap.get("aaa", "aaa") == "bbb"  # terminal gt remap path
    # no terminal references either copy: keep the first deterministically
    rooms2, remap2 = vch.dedupe_duplicate_rooms(raw[:2], set())
    assert [r["id"] for r in rooms2] == ["aaa"] and remap2 == {"bbb": "aaa"}
