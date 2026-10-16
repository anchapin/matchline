"""Elevation reconciliation + dedup-conflict paths, moved out of the CLI harness (#1064).

These two tests lived inside run_elevation_windows.py, which pytest never collects
(testpaths = ["tests"]), so CI never ran them. They import no cv2-dependent path.
"""

from building_model import BuildingModel, Provenance
from elevation_windows import (
    FacadeWindow,
    MergedWindow,
    merge_elevation_observations,
    reconcile_window_counts,
)


def _win(i, s0, s1, tag):
    return MergedWindow(
        id=f"MW-{i}",
        facade="south",
        s0_m=s0,
        s1_m=s1,
        s_center_m=(s0 + s1) / 2,
        width_m=s1 - s0,
        sill_m=0.9,
        head_m=2.4,
        height_m=1.5,
        tag=tag,
        category_hint="window",
    )


def test_reconciliation_flags_injected_mismatches():
    """Injected mismatches must land in the review queue."""
    model = BuildingModel(name="recon_test")
    plan_counts = {"A": 3, "B": 2}
    # elevation sees: one extra A, one missing B, one untagged
    merged = [
        _win(1, 1.0, 2.2, "A"),
        _win(2, 3.0, 4.2, "A"),
        _win(3, 5.0, 6.2, "A"),
        _win(4, 7.0, 8.2, "A"),
        _win(5, 9.0, 10.8, "B"),
        _win(6, 11.0, 12.0, None),
    ]
    flags = reconcile_window_counts(model, plan_counts, merged, "south", "arch_A101")
    kinds = [i.kind for i in model.review_queue]
    assert len(flags) == 3
    assert kinds.count("window_reconciliation") == 3
    assert any(
        "1 'B'" in f["description"] and "no elevation instance" in f["description"] for f in flags
    )
    assert any(
        "'A'" in f["description"] and "no matching arch-plan" in f["description"] for f in flags
    )
    assert any("UNTAGGED" in f["description"] for f in flags)


def test_dedup_conflict_overlapping_disagreeing_observations():
    """Overlapping-but-disagreeing observations -> conflict, not merge."""
    a = FacadeWindow(
        id="s1:EW-1",
        sheet_id="s1",
        facade="south",
        s0_m=5.0,
        s1_m=6.2,
        s_center_m=5.6,
        width_m=1.2,
        sill_m=0.9,
        head_m=2.4,
        height_m=1.5,
        category_hint="window",
        confidence=0.8,
        provenance=Provenance(sheet_id="s1", revision=1, method="x", confidence=0.8),
    )
    b = FacadeWindow(
        id="s2:EW-1",
        sheet_id="s2",
        facade="south",
        s0_m=5.5,
        s1_m=6.7,
        s_center_m=6.1,
        width_m=1.2,
        sill_m=0.9,
        head_m=2.4,
        height_m=1.5,
        category_hint="window",
        confidence=0.8,
        provenance=Provenance(sheet_id="s2", revision=1, method="x", confidence=0.8),
    )
    merged, conflicts = merge_elevation_observations([[a], [b]], tol_m=0.30)
    # centers differ by 0.5 > tol, intervals overlap -> conflict
    assert len(merged) == 2
    assert len(conflicts) == 1
    assert conflicts[0]["delta_center_m"] == 0.5
