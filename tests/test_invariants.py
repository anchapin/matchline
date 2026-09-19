"""Property-style invariant test: random buildings must balance their books.

For K=10 random synthetic buildings (varied seeds, open-office spanning
on/off, both elevation registration paths): build + link, run the FULL
validation battery (including the simplifier area budget), and assert
ZERO errors. Warnings are collected and printed -- they are tripwires,
not failures -- so a warning regression is visible but not fatal.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from synth.multidiscipline import generate_building  # noqa: E402
from link import build_model  # noqa: E402
from geometry_simplify import footprint_from_regions, simplify_ring  # noqa: E402
from validate import run_checks, export_gate  # noqa: E402


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
        span = (i % 2 == 1)
        ekey = "elev_nogrid" if (i % 3 == 2) else "elev_grid"
        bldg = generate_building(seed, open_office_span=span)
        model, _ = build_model(bldg, elevation_key=ekey,
                               building_name=bldg["building_id"])
        sres = _sres_for(model)
        report = run_checks(model, sres=sres)
        n_checked += 1
        errs = report.errors
        assert not errs, (
            f"seed={seed} span={span} elev={ekey}: "
            f"{len(errs)} ERROR(S):\n" +
            "\n".join(f"  [{e.check_id}] {e.message}" for e in errs))
        assert export_gate(report), f"seed={seed}: export gate closed"
        for w in report.warnings:
            all_warnings.append((seed, w.check_id, w.message))
    # warnings are informational: print the distinct kinds seen
    kinds = {}
    for seed, cid, msg in all_warnings:
        kinds.setdefault(cid, []).append(seed)
    with capsys.disabled():
        print(f"\nproperty test: {n_checked} buildings, 0 errors; "
              f"{len(all_warnings)} warnings "
              f"({', '.join(f'{k}x{len(v)}' for k, v in kinds.items()) or 'none'})")


def test_area_conservation_is_tight_on_tiled_synth():
    """Synthetic rooms tile the footprint exactly, so the conservation
    error should be ~0 -- far inside the 3% tolerance. (This is the
    20x30 -> 600 check.)"""
    bldg = generate_building(201, open_office_span=False)
    model, _ = build_model(bldg, building_name=bldg["building_id"])
    report = run_checks(model)
    r = next(x for x in report.results
             if x.check_id == "area_conservation")
    assert r.severity == "pass"
    assert r.actual is not None and r.expected is not None
    rel = abs(r.actual - r.expected) / r.expected
    assert rel < 1e-6, f"synthetic tiling should be exact, got {rel:.2e}"
