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
