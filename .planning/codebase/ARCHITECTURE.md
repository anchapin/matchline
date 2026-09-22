# Architecture

**Analysis Date:** 2026-09-22

## Pattern Overview

**Overall:** Canonical Building Model with Cross-Discipline Linking

**Key Characteristics:**
- Architectural floor plan is the reference frame (canonical meters, y-down)
- Rooms identified by `"{level}-{number}"` as primary key across all disciplines
- Every derived fact carries `Provenance` (sheet_id, revision, method, confidence)
- Low-confidence results flagged for human review — never silently accepted
- Validation errors block export (conservation laws enforced)
- Synthetic-first development: all paths tested against `synth/` fixtures before real data

## Layers

**CLI Entry Point:**
- Location: `cli.py`
- Contains: `matchline` CLI with subcommands for each pipeline stage
- Depends on: all core modules
- Used by: human operators, automation scripts

**Canonical Building Model:**
- Location: `building_model.py`
- Purpose: The ONE model all disciplines register into
- Contains: `BuildingModel`, `Space`, `Zone`, `Level`, `EnvelopeWall`, `BimElement`, `Provenance`, `ReviewItem`
- Depends on: dataclasses, json (no external dependencies)
- Used by: all pipeline modules

**Cross-Discipline Linking:**
- Location: `link.py`
- Purpose: Orchestrates registration + linking into BuildingModel
- Contains: `build_model()` function
- Depends on: `building_model`, `registration`, discipline-specific modules
- Used by: CLI, pipeline runners

**Sheet Registration:**
- Location: `registration.py`
- Purpose: Affine2D transforms to align discipline sheets into arch-plan coordinates
- Contains: `Affine2D`, `PlanRegistration`, `FacadeRegistration`, `point_in_polygon`
- Depends on: `building_model`, numpy
- Used by: `link.py`, elevation_windows

**Validation:**
- Location: `validate.py`
- Purpose: 26-check invariant battery; errors block export
- Contains: `run_checks()`, `ValidationReport`, `export_gate()`
- Depends on: `building_model`, `datasets_adapter`
- Used by: `link.py`, CLI commands

**BEM Export:**
- Location: `bem_export.py`
- Purpose: gbXML 6.01 + IFC4 export from canonical model
- Contains: `model_from_takeoff()`, export functions
- Depends on: `building_model`, `geometry_simplify`
- Used by: CLI commands, pipeline

**IFC Import (Reverse):**
- Location: `ifc_import.py`
- Purpose: IFC4 → canonical model (Tier 0; Tier 1 space attachment open)
- Contains: `import_ifc()`, BIM element parsing
- Depends on: `building_model`, ifcopenshell
- Used by: CLI commands

**Geometry Simplification:**
- Location: `geometry_simplify.py`
- Purpose: Area-budgeted surface reduction (≤2% area drift default)
- Contains: `footprint_from_regions()`, simplification logic
- Depends on: `shapely`, `scipy`
- Used by: `validate.py`, `bem_export.py`

**HVAC Zoning:**
- Location: `hvac_trace.py`
- Purpose: Duct tracing → terminal units → zone graphs
- Contains: Zone inference from diffuser positions
- Depends on: `building_model`
- Used by: `link.py`

**Elevation Windows:**
- Location: `elevation_windows.py`
- Purpose: Exact window placement from elevations + sidelit daylight zones
- Contains: Window registration, daylight zone derivation (ASHRAE 90.1)
- Depends on: `building_model`, `registration`
- Used by: `link.py`

**Lighting:**
- Location: `lighting.py`
- Purpose: Fixture detection → installed watts + per-space LPD
- Contains: Lighting takeoffs
- Depends on: `building_model`
- Used by: `link.py`

**Room Labels:**
- Location: `room_labels.py`
- Purpose: OCR room names/numbers → polygon association
- Contains: Label extraction
- Depends on: `building_model` (optional OCR dependency)
- Used by: `link.py`

**Facades:**
- Location: `facade_takeoff.py`
- Purpose: Facade wall/glazing/door fractions on CMP Facade dataset
- Contains: Area takeoffs
- Depends on: `building_model`
- Used by: CLI for dataset evaluation

**Synthetic Data:**
- Location: `synth/*.py`
- Purpose: Synthetic building/sheet generation for tests and demos
- Contains: `multidiscipline.py`, `sheets.py`, `lighting.py`, `symbols.py`, `mech.py`
- Depends on: `numpy`, `Pillow`
- Used by: tests, demos

**Detector (Separate Track):**
- Location: `detector/`
- Purpose: YOLO fine-tuning for symbol spotting
- Contains: `train.py`, `sahi_infer.py`, dataset converters
- Has: Separate venv, excluded from main ruff
- Used by: Production symbol detection pipeline

**Review Classifier:**
- Location: `review_classifier/`
- Purpose: Auto-triage for low-confidence review items
- Contains: `triage.py`, `model.py`, `features.py`
- Depends on: `building_model`
- Used by: `building_model.flag_for_review()` when `ENABLE_AUTO_TRIAGE=True`

## Data Flow

**Standard Pipeline:**

1. Architectural floor plan → room polygons + labels (symbol spotting + OCR)
2. Other sheets (lighting, mechanical, elevations) → registered into arch coordinates
3. Cross-discipline linking → fixtures/sensors/diffusers land in spaces by point-in-polygon
4. HVAC zoning → duct tracing builds zone graphs from diffuser positions
5. Elevation windows → exact placement + sidelit daylight zones
6. Geometry simplification → area-budgeted surface reduction
7. Validation battery → 26 checks, errors block export
8. BEM export → gbXML 6.01 / IFC4

**BIM Import Path:**

1. IFC file → raw BIM elements (Tier 0, no space boundaries)
2. Walls mirrored into `BuildingModel.envelope`
3. Openings in `bim_elements` await Tier 1 geometric adjacency inference

**State Management:**
- `BuildingModel` is the single source of truth
- JSON round-trip via `to_json()` / `from_json()`
- `model_version` field enables future schema migration

## Key Abstractions

**Provenance:**
- Purpose: Every extracted fact carries its origin
- Examples: `Space.core_provenance`, `ComponentRef.provenance`, `SpaceOpening.provenance`
- Pattern: Dataclass with sheet_id, revision, method, confidence, bbox

**Review Queue:**
- Purpose: Low-confidence links flagged for humans, never silently dropped
- Examples: `BuildingModel.review_queue`, `flag_for_review()`
- Pattern: Items have `needs_human`, `urgency`, `auto_resolved` fields when auto-triage enabled

**Revision Log:**
- Purpose: Audit trail of model changes per sheet revision
- Examples: `BuildingModel.revision_log`, `log_revision()`
- Pattern: `RevisionEvent` with seq, sheet_id, revision, action, note

**Coordinate Frame:**
- Canonical: meters, **y growing DOWNWARD** (drawing frame)
- BEM export: meters, **north-up** (y flipped in `bem_export.model_from_takeoff`)

## Entry Points

**CLI:**
- Location: `cli.py:main()`
- Triggers: `matchline <command>` or `python cli.py <command>`
- Responsibilities: Argument parsing, subcommand dispatch

**Pipeline Runners:**
- `run_pipeline.py`: Unified pipeline (generate + link + validate + export)
- `run_bem_export.py`: Synthetic sheet GT → takeoffs → gbXML + IFC4
- `run_validation.py`: Build 2 synthetic buildings, run validation battery
- `run_elevation_windows.py`: Exact window placement + daylight demo
- `run_multidiscipline.py`: End-to-end 3-building cross-discipline linking

**Direct Imports:**
- `ifc_import.import_ifc()`: Import IFC to BuildingModel
- `ifc_export.export_ifc()`: Export BuildingModel JSON to IFC4
- `validate.run_checks()`: Run validation battery

## Error Handling

**Strategy:** Validation-then-Export with Review Queue

**Patterns:**
- Conservation law violations → `error` severity, blocks export via `export_gate()`
- Plausibility issues → `warn` severity, does not block export
- Missing provenance → always `error`, unauditable
- Review items → `flag_for_review()` with confidence threshold

## Cross-Cutting Concerns

**Logging:** Not centralized; modules use `print()` for CLI feedback

**Validation:** `validate.py` enforces all invariants; `N_CHECKS = 26`

**Authentication:** Not applicable (local file processing)

**Provenance:** Every fact that feeds BEM output must carry `Provenance`

---

*Architecture analysis: 2026-09-22*
