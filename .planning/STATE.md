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
| **Active phase** | Phase 6: Production Hardening |
| **Active plan** | All 4 Phase 6 requirements complete (CFG-01 through CFG-04) |
| **Status** | Phase 6 complete — 4/4 done |
| **Progress** | `[==============] 48%` (12/25 requirements done) |

## Performance Metrics

| Metric | Value |
|--------|-------|
| Total requirements | 25 |
| Requirements done | 12 (ORCH-01, ORCH-02, ORCH-03, ORCH-04, DET-01, DET-02, DET-03, DET-04, CFG-01, CFG-02, CFG-03, CFG-04) |
| Requirements gaps | 13 |
| Phases | 6 |
| Phases completed | 5 (Phase 1–5 complete; Phase 6 in progress) |
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
- [x] Initiate Phase 3: write `ifc_export.py` stub
- [x] Initiate Phase 4: write `matchline review` CLI
- [x] Execute Phase 4: run_review.py + review classifier + training pipeline
- [x] Initiate Phase 5: write GitHub Actions workflow
- [x] Initiate Phase 6: design per-building config YAML schema
- [x] CFG-01: per-building YAML config — `--config` arg, `simplify_tolerance`/`wall_height`/`min_review_confidence` applied at pipeline stages 3 & 6 + validate.py filter
- [x] CFG-02: thick-stroke skeleton preprocessing documented in `docs/jesse.md` (adaptive threshold + morphological ops)
- [x] CFG-03: `jesse.is_complex_invariant()` + `gd_complex_row` review kind for complex GD&T rows
- [x] CFG-04: `docs/design-docs/open-issues.md` updated with all 4 issues and workarounds (CFG-02/CFG-03 cross-referenced)

## Session Continuity

| Last session | Next action |
|-------------|-------------|
| 2026-09-22: Phase 5 complete (CI + real-data path + all 64 tests pass) | 2026-09-22: Phase 6 all 4 requirements complete — CFG-01 (YAML config), CFG-02 (jesse.md thick-stroke docs), CFG-03 (gd_complex_row), CFG-04 (open-issues.md) |
