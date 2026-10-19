#!/usr/bin/env python3
"""Performance and memory budgets on a reference large model (#1162).

Measures the three pipeline phases a user with a large model cares about,
in pipeline order and in one process, on the synthetic reference model
built here (license-safe by construction: no proprietary geometry enters
the repo):

- import:      ``ifc_import.import_ifc`` on the reference IFC;
- validation:  acknowledge the review queue (headless), ``run_checks``,
               ``export_gate`` (must pass), and
               ``validate_bem_conservation`` on the imported model;
- export:      ``bem_ifc4.write_ifc4`` plus ``bem_export.write_gbxml`` on
               the imported model.

Per phase the benchmark records wall-clock elapsed and the process peak
RSS at that point (``ru_maxrss`` is a monotonic high-water mark). The
report's top-level ``peak_rss_mb`` is the final process peak, which is
what the memory budget applies to. The script assembles a JSON report,
checks it against the stated budgets, and with ``--ci`` exits non-zero on
any violation so the CI perf-budget job flags regressions before a release
(see RELEASING.md step 3 and docs/perf_budgets.md).

Usage:
    python scripts/perf_budget.py            # measure, write report, check
    python scripts/perf_budget.py --ci       # exit 1 on budget violation
    python scripts/perf_budget.py --out perf_report.json
    python scripts/perf_budget.py --levels 2 --grid 2x2   # tiny smoke
"""

from __future__ import annotations

import argparse
import json
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reference model scale (#1162): 20 levels x 5x4 grid of 8x5 m spaces
# = 400 spaces, 1600 envelope walls, 400 south windows.
REF_LEVELS = 20
REF_GRID = (5, 4)
SPACE_W_M = 8.0
SPACE_D_M = 5.0
LEVEL_HEIGHT_M = 3.0

# Budgets (#1162). Local reference measurements on the reference runner
# (see docs/perf_budgets.md) are well under each wall-clock budget; the
# headroom absorbs CI runner variance while still catching gross
# regressions. The peak-RSS budget applies to the final process peak.
BUDGETS = {
    "import_s": 60.0,
    "validation_s": 30.0,
    "export_s": 120.0,
    "peak_rss_mb": 3072.0,
}


def peak_rss_mb() -> float:
    """Process peak RSS in MB (ru_maxrss is KB on Linux)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def build_reference_model(levels: int = REF_LEVELS, grid: tuple[int, int] = REF_GRID):
    """Synthetic reference model: grid of spaces per level, 4 envelope walls
    and one south window each. Deterministic; no dataset required."""
    from building_model import BuildingModel, EnvelopeWall, Level, Space, SpaceOpening

    m = BuildingModel(name="perf-reference", auto_triage=False)
    m.levels = [
        Level(
            id=f"L{i}",
            name=f"Level {i}",
            elevation_z_m=i * LEVEL_HEIGHT_M,
            wall_height_m=LEVEL_HEIGHT_M,
        )
        for i in range(levels)
    ]
    gx, gy = grid
    for li in range(levels):
        lid = f"L{li}"
        for ix in range(gx):
            for iy in range(gy):
                x0, y0 = ix * SPACE_W_M, iy * SPACE_D_M
                x1, y1 = x0 + SPACE_W_M, y0 + SPACE_D_M
                sid = f"L{li}-S{ix}-{iy}"
                poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                space = Space(
                    id=sid,
                    level_id=lid,
                    polygon_m=poly,
                    area_m2=(x1 - x0) * (y1 - y0),
                )
                space.openings.append(
                    SpaceOpening(
                        id=f"{sid}-win",
                        tag="A",
                        category="window",
                        width_m=1.5,
                        height_m=1.2,
                        sill_m=0.9,
                        head_m=2.1,
                        host_facade="south",
                        host_interval_m=[1.0, 2.5],
                        s_center_m=1.75,
                        area_m2=1.8,
                    )
                )
                m.spaces[sid] = space
                m.envelope.extend(
                    [
                        EnvelopeWall(
                            id=f"{sid}-N",
                            facade="north",
                            from_m=[x0, y0],
                            to_m=[x1, y0],
                            space_id=sid,
                        ),
                        EnvelopeWall(
                            id=f"{sid}-E",
                            facade="east",
                            from_m=[x1, y0],
                            to_m=[x1, y1],
                            space_id=sid,
                        ),
                        EnvelopeWall(
                            id=f"{sid}-S",
                            facade="south",
                            from_m=[x1, y1],
                            to_m=[x0, y1],
                            space_id=sid,
                        ),
                        EnvelopeWall(
                            id=f"{sid}-W",
                            facade="west",
                            from_m=[x0, y1],
                            to_m=[x0, y0],
                            space_id=sid,
                        ),
                    ]
                )
    return m


def _acknowledge_review_queue(model) -> int:
    """Mark every open needs_review item acknowledged (headless runs).

    Mirrors run_pipeline._acknowledge_review_queue and the export gate's own
    criteria (validate/export.py review_queue_acknowledged) so the benchmark
    exercises the same export path a headless run uses.
    """
    n = 0
    for item in model.review_queue:
        if (
            item.needs_review
            and not item.acknowledged
            and item.status not in ("confirmed", "rejected")
        ):
            item.acknowledged = True
            n += 1
    return n


def run_benchmark(workdir: Path) -> dict:
    """Run the three measured phases in pipeline order, in one process.

    Phase boundaries match what ``run_pipeline --ifc`` does: import the IFC,
    validate the imported model, export from it. ``peak_rss_mb`` is the
    process peak RSS at the end of each phase (``ru_maxrss`` is a monotonic
    high-water mark); the report's top-level ``peak_rss_mb`` is the final
    process peak, which is what the memory budget applies to.
    """
    from bem.bem_export import write_gbxml
    from bem.bem_ifc4 import write_ifc4
    from ifc.ifc_export import _bem_from_model
    from ifc.ifc_import import import_ifc
    from validate import export_gate, run_checks
    from validate.conservation import validate_bem_conservation

    phases: dict = {}

    t0 = time.monotonic()
    model = import_ifc(workdir / "reference.ifc")
    phases["import"] = {
        "elapsed_s": round(time.monotonic() - t0, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "spaces": len(model.spaces),
    }

    t0 = time.monotonic()
    acknowledged = _acknowledge_review_queue(model)
    report = run_checks(model)
    gate_ok = bool(export_gate(report, model))
    bem_results = validate_bem_conservation(_bem_from_model(model))
    conservation_bad = sum(1 for r in bem_results if r.severity not in ("pass", "skip"))
    phases["validation"] = {
        "elapsed_s": round(time.monotonic() - t0, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "acknowledged": acknowledged,
        "checks_ok": bool(report.ok),
        "gate_ok": gate_ok,
        "conservation_bad": conservation_bad,
    }

    t0 = time.monotonic()
    bem = _bem_from_model(model)
    p_ifc = write_ifc4(bem, workdir / "out.ifc")
    p_gbx = write_gbxml(bem, workdir / "out.xml")
    phases["export"] = {
        "elapsed_s": round(time.monotonic() - t0, 3),
        "peak_rss_mb": round(peak_rss_mb(), 1),
        "ifc_bytes": p_ifc.stat().st_size,
        "gbxml_bytes": p_gbx.stat().st_size,
    }
    return phases


def parse_grid(s: str) -> tuple[int, int]:
    """Parse a "5x4" grid spec."""
    a, _, b = s.partition("x")
    return int(a), int(b)


def prepare_reference_dir(levels: int, grid: tuple[int, int], workdir: Path) -> dict:
    """Build the reference model and write the reference IFC (unmeasured setup).

    Returns the reference metadata for the report.
    """
    from bem.bem_ifc4 import write_ifc4
    from ifc.ifc_export import _bem_from_model

    model = build_reference_model(levels=levels, grid=grid)
    meta = {
        "levels": levels,
        "grid_x": grid[0],
        "grid_y": grid[1],
        "space_m": [SPACE_W_M, SPACE_D_M],
        "spaces": len(model.spaces),
        "walls": len(model.envelope),
        "openings": sum(len(s.openings) for s in model.spaces.values()),
        "license": "synthetic, generated in-process; nothing is committed",
    }
    write_ifc4(_bem_from_model(model), workdir / "reference.ifc")
    return meta


def check_budgets(report: dict, budgets: dict[str, float] | None = None) -> list[str]:
    """Return the list of budget violations in a report (empty = clean)."""
    budgets = budgets if budgets is not None else BUDGETS
    violations: list[str] = list(report.get("violations", []))
    for phase in ("import", "validation", "export"):
        entry = report.get("phases", {}).get(phase)
        if not isinstance(entry, dict):
            violations.append(f"{phase}: no result recorded")
            continue
        if "error" in entry:
            continue  # already flagged by the caller
        elapsed = entry.get("elapsed_s")
        budget = budgets.get(f"{phase}_s")
        if elapsed is not None and budget is not None and elapsed > budget:
            violations.append(f"{phase}: {elapsed}s > budget {budget}s")
        if entry.get("gate_ok") is False:
            violations.append(f"{phase}: export gate did not pass")
    peak = report.get("peak_rss_mb", 0.0)
    peak_budget = budgets.get("peak_rss_mb")
    if peak_budget is not None and peak > peak_budget:
        violations.append(f"peak_rss: {peak}MB > budget {peak_budget}MB")
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--levels", type=int, default=REF_LEVELS, help="reference-model level count")
    ap.add_argument(
        "--grid",
        default=f"{REF_GRID[0]}x{REF_GRID[1]}",
        help="reference-model per-level grid, e.g. 5x4",
    )
    ap.add_argument("--ci", action="store_true", help="exit 1 on any budget violation")
    ap.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    args = ap.parse_args(argv)

    report: dict = {
        "reference": {},
        "budgets": dict(BUDGETS),
        "phases": {},
        "violations": [],
    }

    tmp = Path(tempfile.mkdtemp(prefix="matchline-perf-"))
    try:
        t0 = time.monotonic()
        report["reference"] = prepare_reference_dir(args.levels, parse_grid(args.grid), tmp)
        report["reference"]["setup_s"] = round(time.monotonic() - t0, 3)

        try:
            report["phases"] = run_benchmark(tmp)
        except Exception as exc:  # report the failure, then gate on it
            report["violations"].append(f"benchmark failed: {exc}")
        report["peak_rss_mb"] = peak_rss_mb()
        report["violations"] = check_budgets(report)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")

    if report["violations"]:
        print("PERF BUDGET VIOLATIONS:", file=sys.stderr)
        for v in report["violations"]:
            print(f"  {v}", file=sys.stderr)
        if args.ci:
            return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
