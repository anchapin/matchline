# Performance and memory budgets (#1162)

Matchline states a performance envelope on a **reference large model**: a
user with a big building needs to know the pipeline completes in reasonable
time and memory, and CI flags regressions against that envelope before a
release.

## Reference model

Synthetic and license-safe by construction: `scripts/perf_budget.py`
generates it in-process, so no proprietary geometry and no external dataset
ever enters the repo (the same rule as the #748 benchmark harness, whose
licensed buildings also stay outside the repo).

- 20 levels, each a 5x4 grid of 8x5 m spaces: **400 spaces**
- 4 envelope walls per space: **1,600 walls**
- 1 south window per space: **400 openings**
- written as IFC4 (~1.0 MB) by `bem_ifc4.write_ifc4`

## What is measured

Wall-clock and peak RSS are measured **separately** for the three phases a
large-model user runs, each in its own subprocess so its peak RSS
(`ru_maxrss`) is that phase's peak, not a high-water mark shared across
phases:

| Phase | What runs |
| --- | --- |
| import | `ifc_import.import_ifc` on the reference IFC |
| validation | review-queue acknowledge (headless), `run_checks`, `export_gate` (must pass), `validate_bem_conservation` on the imported model |
| export | `bem_ifc4.write_ifc4` + `bem_export.write_gbxml` on the imported model |

Setup (building the reference model and writing the IFC the import phase
reads) is not measured.

## Budgets

| Budget | Limit |
| --- | --- |
| import | 60 s |
| validation | 30 s |
| export | 120 s |
| peak RSS (worst phase) | 3,072 MB |

Reference measurements (local Linux runner, 2026-10-19, reference scale
above, one process): import 3.7 s, validation 0.6 s, export 4.4 s; final
process peak 648 MB (setup, not measured, 5.1 s). The wall-clock budgets
carry roughly 7-25x headroom over the local reference so shared CI runners
can be several times slower without flaking, while still catching gross
regressions (a 10x slowdown trips the import and export budgets). The
peak-RSS budget leaves about 5x headroom.

The gate also fails if the export gate does not pass on the reference
model (`gate_ok` false) or a phase errors: the envelope is only meaningful
on a clean reference.

## Reproducing

```bash
python scripts/perf_budget.py                 # measure, print report
python scripts/perf_budget.py --out perf_report.json
python scripts/perf_budget.py --ci            # exit 1 on any violation
python scripts/perf_budget.py --levels 2 --grid 2x2   # tiny smoke
```

The JSON report records the reference metadata, per-phase elapsed and
peak-RSS entries, and the violation list.

## CI integration and release gate

The `performance budget` job in `.github/workflows/ci.yml` runs
`scripts/perf_budget.py --ci` on every PR and push (runtime dependencies
only), uploads `perf_report.json` as an artifact, and fails the job on any
violation. `RELEASING.md` step 3 blocks tagging on a red performance
budget, the same way the simulation smoke gate does.

## Changing the budgets

Budgets are stated in `BUDGETS` in `scripts/perf_budget.py` and mirrored
here. If a change legitimately moves the envelope (an intended feature
growth), update `BUDGETS` and this document together with the measured new
reference, and say so in the changelog. Do not widen a budget to absorb an
unexplained regression.
