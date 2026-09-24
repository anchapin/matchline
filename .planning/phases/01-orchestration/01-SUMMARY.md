---
phase: "01-orchestration"
plan: "01"
subsystem: pipeline
tags: [orchestration, pipeline, cli, bem-export]

# Dependency graph
requires: []
provides:
  - "run_pipeline.py: 6-stage pipeline runner with intermediate JSON output"
  - "matchline run CLI: unified command for generate + link + validate + export"
  - "model_from_linked_model() adapter: BuildingModel -> BEMModel conversion"
affects:
  - Phase 2 (detector integration needs run_pipeline.py output)
  - Phase 3 (IFC export needs model_from_linked_model adapter)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Stage-gated pipeline: each stage writes intermediate JSON for operator inspection"
    - "Fail-fast validation: export_gate() returns False -> sys.exit(1)"
    - "Adapter pattern: BuildingModel -> BEMModel for backend-agnostic export"

key-files:
  created:
    - "run_pipeline.py" - unified pipeline runner
  modified:
    - "cli.py" - added 'run' subcommand

key-decisions:
  - "model_from_linked_model() extracts spaces, openings, and ring from BuildingModel to BEMModel structure"

patterns-established:
  - "Intermediate JSON per stage enables debugging at any pipeline point"
  - "Fail-fast validation blocks export on errors (not silent)"

requirements-completed:
  - "ORCH-01"
  - "ORCH-02"
  - "ORCH-03"

# Metrics
duration: 10min
completed: 2026-09-21
---

# Phase 1: Orchestration Summary

**Unified pipeline runner `run_pipeline.py` chaining 6 stages with intermediate JSON output and fail-fast validation**

## Performance

- **Duration:** 10 min
- **Started:** 2026-09-21T18:05:00Z
- **Completed:** 2026-09-21T18:15:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Created `run_pipeline.py` with 6-stage pipeline: generate → build_model → simplify → validate → fail-fast → export
- Added `model_from_linked_model()` adapter converting BuildingModel to BEMModel for gbXML/IFC export
- Added `matchline run` CLI subcommand with --seed, --out-dir, --open-office-span, --elevation-key, --simplify-tol options
- Verified pipeline produces 4 stage JSONs + BEM output on seed 101
- Verified `matchline validate` still passes on all 3 synthetic buildings

## Task Commits

Each task was committed atomically:

1. **Task 1: Create run_pipeline.py** - `462ea3a` (feat)
2. **Task 2: Add matchline run CLI subcommand** - `462ea3a` (feat, same commit - atomic)

**Plan metadata:** `8976642` (docs: create phase 1 plan)

## Files Created/Modified
- `run_pipeline.py` - 6-stage pipeline runner with model_from_linked_model adapter
- `cli.py` - added `matchline run` subcommand

## Decisions Made

- model_from_linked_model() extracts spaces from BuildingModel.spaces.values(), openings from each space's openings, and ring from simplifier result
- Used ValidationReport.to_dict() for JSON serialization (existing method)
- Used SimplifyResult.ring (not simplified_ring) for simplified ring output

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

- Phase 2 (detector integration) can use run_pipeline.py output for integration
- Phase 3 (IFC export) can extend model_from_linked_model() as needed
- Blocker noted in STATE.md: bem_export.write_ifc4 only accepts BEMModel, not full BuildingModel - addressed by adapter

---
*Phase: 01-orchestration*
*Completed: 2026-09-21*
