# Phase 1 Plan: Pipeline Orchestration

## Context

The goal of Phase 1 is to create `run_pipeline.py` that unifies all existing pipeline stages behind a single `matchline run` entry point. Currently, stages run as separate scripts (`run_validation.py`, `run_bem_export.py`, etc.) with no unified chaining, no intermediate JSON outputs, and no fail-fast behavior on validation errors.

## Current State

- `ORCH-04` is DONE: `run_validation.py` exists and `matchline validate` wraps it.
- ORCH-01, ORCH-02, ORCH-03 are GAPS: no `run_pipeline.py`, no intermediate artifacts, no unified error handling.

## What to Build

### Pipeline Stages

```
Stage 1: synth.multidiscipline.generate_building() → stage_01_building.json
Stage 2: link.build_model()                       → stage_02_model.json
Stage 3: geometry_simplify.simplify_ring()        → stage_03_simplified.json
Stage 4: validate.run_checks()                     → stage_04_validation.json
Stage 5: bem_export.model_from_takeoff() + write   → final output
```

### Input Sources

The pipeline must support two modes:
- **Synthetic mode**: Uses `generate_building(seed, open_office_span)` — existing path.
- **Real-drawing mode**: Uses detector outputs + schedule parsing (Phase 2 scope, stub OK for Phase 1).

For Phase 1, implement synthetic mode only.

## Requirements Coverage

| Requirement | Plan | How Addressed |
|-------------|------|---------------|
| ORCH-01 | 01-orchestration-01 | `run_pipeline.py` chains all stages; `matchline run` CLI |
| ORCH-02 | 01-orchestration-01 | Each stage writes `<out-dir>/stage_NN_<name>.json` |
| ORCH-03 | 01-orchestration-01 | `export_gate()` False → non-zero exit + stage name |
| ORCH-04 | (already done) | `run_validation.py` / `matchline validate` |

## Files Modified

- `run_pipeline.py` (NEW)
- `cli.py` (add `run` subcommand)

## Success Criteria

1. `matchline run --seed 101` produces identical output to manually chaining `run_validation.py` + `run_bem_export.py` on the same seed (golden-file diff)
2. Each stage writes its intermediate JSON to `bem_out/stage_NN_<name>.json`
3. Validation failure in Stage 4 exits with code 1 and prints which check failed
4. `matchline validate` (ORCH-04) continues to pass on `bldg_3room`, `bldg_open_office`, `bldg_8room`
