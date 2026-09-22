# ROADMAP.md — Matchline

## Phases

- [ ] **Phase 1: Pipeline Orchestration** — Chain all existing components into one `matchline run` entry point
- [ ] **Phase 2: Detector Integration** — Wire YOLO detector into pipeline; implement schedule table parsing
- [ ] **Phase 3: IFC Round-Trip** — `BuildingModel → IFC4` export; complete IFC Tier 1
- [ ] **Phase 4: Review Queue UX** — CLI tool + classifier integration for review workflow
- [ ] **Phase 5: Real-Data Validation + CI** — Full pipeline on real datasets; GitHub Actions
- [ ] **Phase 6: Production Hardening** — Per-building config; resolve known open issues

---

## Phase Details

### Phase 1: Pipeline Orchestration

**Goal**: A single `matchline run <drawing_path> [--config <yaml>] [--out-dir <dir>]` chains every stage in sequence and writes validated output.

**Depends on**: Nothing (first phase)

**Requirements**: ORCH-01, ORCH-02, ORCH-03, ORCH-04

**Success Criteria** (what must be TRUE when this phase completes):

1. Running `matchline run` on a synthetic building input produces the same result as manually chaining `run_validation.py` + `run_bem_export.py` — verified by golden-file diff
2. Each pipeline stage writes its intermediate output to `<out-dir>/stage_N_<name>.json` (e.g., `stage_01_detections.json`, `stage_02_schedule.json`)
3. A validation error in any stage produces a non-zero exit code and prints which check failed
4. `matchline validate` passes on `bldg_3room`, `bldg_open_office`, `bldg_8room` synthetic buildings

**Plans**: 1 plan

**Plan list:**
- [ ] 01-orchestration-01-PLAN.md — `run_pipeline.py` + `matchline run` CLI

---

### Phase 2: Detector Integration

**Goal**: The YOLO detector in `detector/sahi_infer.py` is the production symbol-spotting backend; schedule tables are parsed from drawing images (not just CSV).

**Depends on**: Phase 1

**Requirements**: DET-01, DET-02, DET-03, DET-04

**Success Criteria** (what must be TRUE when this phase completes):

1. `detector/sahi_infer.py --weights best.pt --image sheet.png --out preds.json` output is consumed by `datasets_adapter.Detection` list in `matchline run`
2. A drawing with a visible schedule table (real or synthetic) produces `ScheduleEntry` records without passing through `parse_schedule_csv`
3. The YOLO backend for `elevation_windows.py` (`WindowDetectorBackend` subclass) produces `ElevationWindowObs` results comparable to the contour backend on synthetic data
4. On AEC-Bench sheets, the pipeline (YOLO → schedule → rollup → link → validate → export) produces no more than 5% count error per symbol class versus ground truth

**Plans**: 2 plans in 2 waves

**Plan list:**
- [ ] 02-detector-01-PLAN.md — YOLO pipeline integration + WindowDetectorBackend (DET-01, DET-04)
- [ ] 02-detector-02-PLAN.md — parse_schedule_table + sliding-window WiSARD (DET-02, DET-03)

---

### Phase 3: IFC Round-Trip

**Goal**: `BuildingModel` round-trips through IFC — import via `ifc_import.py`, export via `ifc_export.py` — with full fidelity for zones, HVAC, lighting, and envelope. Cross-sheet window deduplication is implemented.

**Depends on**: Phase 1

**Requirements**: IFC-01, IFC-02, IFC-03, IFC-04

**Success Criteria** (what must be TRUE when this phase completes):

1. `matchline ifc-import foo.ifc --out model.json` produces a `BuildingModel` with walls, openings, and material layers (Tier 0) — verified against existing test
2. `ifc_export.py model.ifc model.json` writes an IFC4 file that contains all `Space`, `Zone`, `SpaceLighting`, and `SpaceHVAC` data from the model
3. An IFC round-trip (import → export) preserves zone-to-space many-to-many membership and lighting watt totals within 1% tolerance
4. Running elevation linking twice on two separate elevations of the same facade produces exactly one `SpaceOpening` per window (not two)

**Plans**: 2 plans in 2 waves

**Plan list:**
- [ ] 03-ifc-01-PLAN.md — `ifc_export.py` + `matchline ifc-export` CLI + BuildingModel→BEMModel adapter (IFC-01, IFC-02 basic)
- [ ] 03-ifc-02-PLAN.md — Zone + SpaceLighting IFC4 export + cross-sheet window dedup + round-trip test (IFC-02 extended, IFC-03, IFC-04)

---

### Phase 4: Review Queue UX

**Goal**: Low-confidence links are surfaced to a human reviewer through a CLI tool, classifier predictions are shown inline, and accepted decisions update the model and re-run validation.

**Depends on**: Phase 1

**Requirements**: RVIEW-01, RVIEW-02, RVIEW-03, RVIEW-04

**Success Criteria** (what must be TRUE when this phase completes):

1. `matchline review --model model.json` prints each `ReviewItem` with its kind, description, confidence, and (if available) classifier suggestion
2. `matchline review --model model.json --confirm <id>` marks the item as confirmed and re-runs `validate.py`; the confirmed item no longer appears in the queue
3. `matchline review --model model.json --reject <id>` marks the item as rejected and re-runs `validate.py`
4. On a model with 20+ review items, the classifier suggestion matches the human decision in ≥70% of cases (measured over a curated set of real-drawing review items)

**Plans**: TBD

---

### Phase 5: Real-Data Validation + CI

**Goal**: The pipeline is validated on real architectural drawings (AEC-Bench, CMP Facade) end-to-end before any release. GitHub Actions gates on passing tests.

**Depends on**: Phase 2 (detector integration), Phase 3 (IFC)

**Requirements**: CI-01, CI-02, CI-03, CI-04, CI-05

**Success Criteria** (what must be TRUE when this phase completes):

1. GitHub Actions workflow runs `ruff check . && ruff format --check . && python -m pytest tests/ -q` on every PR and blocks merge on failure
2. `matchline run` on all 15 AEC-Bench sheets produces no validation `error` results (only `warn` or `pass`); all 15 sheets export gbXML
3. `load_floorplancad()` returns valid `SymbolSample` and `TakeoffResult` objects for the FloorPlanCAD dataset without raising `NotImplementedError`
4. `load_archcad()` returns valid `SymbolSample` and `TakeoffResult` objects for the ArchCAD dataset without raising `NotImplementedError`
5. CMP Facade sweep (`matchline facade-takeoff --full`) produces `facade_priors.json` covering all 606 facades with no crashes

**Plans**: TBD

---

### Phase 6: Production Hardening

**Goal**: The pipeline is configurable per-building without code changes. Known open issues are resolved or formally documented with workarounds.

**Depends on**: Phase 4 (review queue), Phase 5 (CI)

**Requirements**: CFG-01, CFG-02, CFG-03, CFG-04

**Success Criteria** (what must be TRUE when this phase completes):

1. `matchline run --config building_params.yaml` applies `review_confidence: 0.85`, `simplify_tolerance: 0.01`, `wall_height: 3.5` from the YAML file and produces different validation output than the defaults
2. `matchline run --help` lists all configurable parameters with their default values and valid ranges
3. On real architectural drawings with thick strokes, `jesse.py` produces skeleton invariants that match Table 9.4 signatures after thinning pre-processing (documented in `docs/jesse.md` with the preprocessing parameters used)
4. Complex GD&T invariant rows (True Position E=4, J_T=4, etc.) are flagged in the review queue with a `gd_complex_row` kind rather than silently accepted or rejected

**Plans**: TBD

---

## Progress Table

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Orchestration | 1/1 | Completed | 2026-09-21 |
| 2. Detector Integration | 2/2 | Completed | 2026-09-22 |
| 3. IFC Round-Trip | 0/2 | Planned | - |
| 4. Review Queue UX | 0/4 | Not started | - |
| 5. Real-Data Validation + CI | 0/5 | Not started | - |
| 6. Production Hardening | 0/4 | Not started | - |
