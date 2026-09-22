# STATE.md — Matchline

## Project Reference

| Field | Value |
|-------|-------|
| **Core value** | Deterministic, auditable extraction of BEM inputs from drawings and BIM |
| **Current focus** | Phase 2: Detector integration |
| **Started** | 2026-09-21 (roadmap created from codebase audit) |
| **Granularity** | Standard (6 phases) |

## Current Position

| Field | Value |
|-------|-------|
| **Active phase** | Phase 1: Orchestration (completed) |
| **Active plan** | None |
| **Status** | Pre-planning for Phase 2 |
| **Progress** | `[══════════════════════════════════════] 8%` (2/25 requirements done) |

## Performance Metrics

| Metric | Value |
|--------|-------|
| Total requirements | 25 |
| Requirements done | 2 (ORCH-01: `matchline run` CLI; ORCH-02: `run_pipeline.py` with stage JSONs; ORCH-03: fail-fast on validation; ORCH-04: `matchline validate` via `run_validation.py`) |
| Requirements gaps | 24 |
| Phases | 6 |
| Phases completed | 0 |
| Test coverage | `tests/` exist for validate, invariants, ifc_import, review_classifier; no integration tests |
| Open issues (docs) | 4 (window dedup, IFC Tier 1, thick-stroke skeletons, complex GD&T rows) |

## Accumulated Context

### Decisions

| When | Decision | Rationale |
|------|----------|------------|
| 2026-09-21 | Phase ordering: Orchestration → Detector → IFC → Review → CI → Config | Orchestration unblocks all other phases; IFC and CI both depend on Phase 1 |
| 2026-09-21 | Phase 3 includes cross-sheet window deduplication (IFC-04) | Already a documented open issue; natural fit with IFC work |
| 2026-09-21 | review_classifier lives in Phase 4, not Phase 2 | Classifier is for review UX, not for auto-fixing low-confidence links silently |

### Blockers

| Blocker | Phase |
|---------|-------|
| ~~No `run_pipeline.py`~~ — RESOLVED: created `run_pipeline.py` with 6 stages | Phase 1 and all subsequent phases |
| Detector (YOLO) outputs JSON but nothing in main pipeline reads it | Phase 2 |
| `bem_export.write_ifc4` only accepts `BEMModel`, not full `BuildingModel` | Phase 3 |

### TODOs

- [x] Initiate Phase 1: design `run_pipeline.py` API and stage contract
- [x] Initiate Phase 1: write stage intermediate JSON schemas
- [x] Run Phase 1 plan: `/gsd:plan-phase 1`
- [x] After Phase 1: verify `matchline run` passes on all 3 synthetic buildings
- [ ] Initiate Phase 2: implement `YOLOWindowDetectorBackend` for `elevation_windows.py`
- [ ] Initiate Phase 3: write `ifc_export.py` stub
- [ ] Initiate Phase 4: write `matchline review` CLI
- [ ] Initiate Phase 5: write GitHub Actions workflow
- [ ] Initiate Phase 6: design per-building config YAML schema

## Session Continuity

| Last session | Next action |
|-------------|-------------|
| 2026-09-21: Codebase audit + roadmap creation | Phase 1 complete - run `/gsd:plan-phase 2` to create Phase 2 plan |
