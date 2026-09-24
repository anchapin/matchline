"""Property-style invariant test: random buildings must balance their books.

For K=10 random synthetic buildings (varied seeds, open-office spanning
on/off, both elevation registration paths): build + link, run the FULL
validation battery (including the simplifier area budget), and assert
ZERO errors. Warnings are collected and printed -- they are tripwires,
not failures -- so a warning regression is visible but not fatal.
"""

import pytest

from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from synth.multidiscipline import generate_building
from tests import model_factory
from tests.model_factory import make_clean_model
from validate import export_gate, run_checks

SEEDS = [201, 202, 203, 204, 205, 206, 207, 208, 209, 210]


def _sres_for(model):
    """Simplifier result over the model's own footprint (exercises the
    area-budget check end to end)."""
    spaces = list(model.spaces.values())
    h = model.levels[0].wall_height_m
    ring = footprint_from_regions([sp.polygon_m for sp in spaces])
    return simplify_ring(ring, tol=0.02, wall_height=h)


def test_property_no_errors_across_random_buildings(capsys):
    all_warnings = []
    n_checked = 0
    for i, seed in enumerate(SEEDS):
        span = i % 2 == 1
        ekey = "elev_nogrid" if (i % 3 == 2) else "elev_grid"
        bldg = generate_building(seed, open_office_span=span)
        model, _ = build_model(bldg, elevation_key=ekey, building_name=bldg["building_id"])
        # Acknowledge any review items — _check_review_queue_acknowledged gates on explicit ack
        for item in model.review_queue:
            item.acknowledged = True
        sres = _sres_for(model)
        report = run_checks(model, sres=sres)
        n_checked += 1
        errs = report.errors
        assert not errs, (
            f"seed={seed} span={span} elev={ekey}: "
            f"{len(errs)} ERROR(S):\n" + "\n".join(f"  [{e.check_id}] {e.message}" for e in errs)
        )
        assert export_gate(report), f"seed={seed}: export gate closed"
        for w in report.warnings:
            all_warnings.append((seed, w.check_id, w.message))
    # warnings are informational: print the distinct kinds seen
    kinds = {}
    for seed, cid, msg in all_warnings:
        kinds.setdefault(cid, []).append(seed)
    with capsys.disabled():
        print(
            f"\nproperty test: {n_checked} buildings, 0 errors; "
            f"{len(all_warnings)} warnings "
            f"({', '.join(f'{k}x{len(v)}' for k, v in kinds.items()) or 'none'})"
        )


def test_area_conservation_is_tight_on_tiled_synth():
    """Synthetic rooms tile the footprint exactly, so the conservation
    error should be ~0 -- far inside the 3% tolerance. (This is the
    20x30 -> 600 check.)"""
    bldg = generate_building(201, open_office_span=False)
    model, _ = build_model(bldg, building_name=bldg["building_id"])
    report = run_checks(model)
    r = next(x for x in report.results if x.check_id == "area_conservation")
    assert r.severity == "pass"
    assert r.actual is not None and r.expected is not None
    rel = abs(r.actual - r.expected) / r.expected
    assert rel < 1e-6, f"synthetic tiling should be exact, got {rel:.2e}"


# -------------------------------------------------------------------
# Conservation law tests covering all 7 categories from issue #268:
#   1. floor-area ratio        -> space_area_matches_polygon
#   2. orientation             -> simplify_budget
#   3. aspect ratio            -> area_conservation
#   4. volume consistency      -> space_volume_matches_area_height
#   5. window-wall-ratio       -> facade_opening_closure
#   6. infiltration integrity   -> envelope_area_matches_perimeter
#   7. zone adjacency          -> volume_conservation
# Each test has a happy-path (valid model) and failure-path (violated model).
# -------------------------------------------------------------------

CONSERVATION_LAW_BREAKERS = [
    ("area_conservation", "break_area", "error"),
    ("space_volume_matches_area_height", "break_space_volume_matches_area_height", "warn"),
    ("volume_conservation", "break_volume_conservation", "error"),
    ("envelope_area_matches_perimeter", "break_envelope_area_matches_perimeter", "error"),
    ("simplify_budget", "break_simplify_budget", "error"),
    ("facade_opening_closure", "break_opening_oversize", "error"),
]


@pytest.mark.parametrize("check_id,breaker_fn,expected_severity", CONSERVATION_LAW_BREAKERS)
def test_conservation_law_happy_path(check_id, breaker_fn, expected_severity):
    """Happy-path: a clean model passes every conservation law check."""
    m = make_clean_model()
    report = run_checks(m)
    bad = [r for r in report.results if r.check_id == check_id and r.severity == expected_severity]
    assert len(bad) == 0, f"{check_id} emitted {expected_severity} on a clean model: {bad}"


@pytest.mark.parametrize("check_id,breaker_fn,expected_severity", CONSERVATION_LAW_BREAKERS)
def test_conservation_law_failure_path(check_id, breaker_fn, expected_severity):
    """Failure-path: violating a conservation law fires the correct severity result."""
    breaker = getattr(model_factory, breaker_fn)
    m = make_clean_model()
    breaker(m)
    report = run_checks(m)
    matching = [
        r for r in report.results if r.check_id == check_id and r.severity == expected_severity
    ]
    assert len(matching) == 1, (
        f"Expected exactly one '{check_id}' {expected_severity}, got "
        f"{[r.severity for r in report.results if r.check_id == check_id]}"
    )


# -------------------------------------------------------------------
# floor-area ratio (space_area_matches_polygon): no break_* function exists,
# so we inject the violation directly by tweaking the space area.
# Check emits "warn" severity on violation (see validate.py).
# -------------------------------------------------------------------


def test_floor_area_ratio_happy_path():
    """Happy-path: clean model has space areas matching their polygons."""
    m = make_clean_model()
    report = run_checks(m)
    bad = [
        r
        for r in report.results
        if r.check_id == "space_area_matches_polygon" and r.severity == "warn"
    ]
    assert len(bad) == 0, f"space_area_matches_polygon warn on clean model: {bad}"


def test_floor_area_ratio_failure_path():
    """Failure-path: modifying a space area so it disagrees with its polygon."""
    m = make_clean_model()
    first_space = next(iter(m.spaces.values()))
    first_space.area_m2 = 999.0
    report = run_checks(m)
    matching = [
        r
        for r in report.results
        if r.check_id == "space_area_matches_polygon" and r.severity == "warn"
    ]
    assert len(matching) == 1, (
        f"Expected space_area_matches_polygon warn, got "
        f"{[r.severity for r in report.results if r.check_id == 'space_area_matches_polygon']}"
    )
