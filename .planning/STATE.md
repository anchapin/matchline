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
| **Active phase** | Phase 2: Detector Integration (complete) |
| **Active plan** | All Phase 2 plans executed |
| **Status** | Phase 2 complete — DET-01, DET-02, DET-03, DET-04 done |
| **Progress** | `[══════════════════════════════════════] 20%` (5/25 requirements done) |

## Performance Metrics

| Metric | Value |
|--------|-------|
| Total requirements | 25 |
| Requirements done | 8 (ORCH-01, ORCH-02, ORCH-03, ORCH-04, DET-01, DET-02, DET-03, DET-04) |
| Requirements gaps | 24 |
| Phases | 6 |
| Phases completed | 1 (Phase 1: Orchestration) |
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
| ~~Detector (YOLO) outputs JSON but nothing in main pipeline reads it~~ — RESOLVED: DET-01 in Wave 1 | Phase 2 |
| ~~`bem_export.write_ifc4` only accepts `BEMModel`, not full `BuildingModel`~~ — RESOLVED: adapter in Phase 1 plan | Phase 3 |

### TODOs

- [x] Initiate Phase 1: design `run_pipeline.py` API and stage contract
- [x] Initiate Phase 1: write stage intermediate JSON schemas
- [x] Run Phase 1 plan: `/gsd:plan-phase 1`
- [x] After Phase 1: verify `matchline run` passes on all 3 synthetic buildings
- [x] Initiate Phase 2: plan detector integration (2 plans, 2 waves)
- [x] Execute Phase 2 Wave 1: YOLO pipeline integration + WindowDetectorBackend (DET-01, DET-04) — 3 commits
- [x] Execute Phase 2 Wave 2: parse_schedule_table + sliding-window WiSARD (DET-02, DET-03) — 2 commits
- [ ] Initiate Phase 3: write `ifc_export.py` stub
- [ ] Initiate Phase 4: write `matchline review` CLI
- [ ] Initiate Phase 5: write GitHub Actions workflow
- [ ] Initiate Phase 6: design per-building config YAML schema

## Session Continuity

| Last session | Next action |
|-------------|-------------|
| 2026-09-22: Phase 2 complete (all 4 DET requirements done) | Initiate Phase 3: `/gsd:plan-phase 3` |
