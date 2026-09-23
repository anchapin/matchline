# ARCHITECTURE.md — Matchline

## Project type

Single-package Python pipeline. Root-level `*.py` modules are standalone (no intra-package import barriers). `synth/` is a test data package; `detector/` is a separate ML track with its own venv.

## Pipeline

```
drawings
  → symbol spotting (detector / synthetic)
  → schedule join: count × dims  →  datasets_adapter
  → room labeling (OCR)          →  room_labels
  → cross-discipline linking      →  link.build_model  ★ canonical frame
  → HVAC zoning (duct tracing)   →  hvac_trace
  → geometry simplification       →  geometry_simplify  (≤2% area drift)
  → validation battery           →  validate  (errors block export)
  → BEM export                  →  bem_export (gbXML 6.01 / IFC4)

See [`docs/pipeline.md`](docs/pipeline.md) for the full stage chain, intermediate
artifact schemas, and auto-triage integration.
  → BIM import (reverse)        →  ifc_import (Tier 0, no space boundaries)
```

**Elevations** feed exact window placement and daylight zones into the canonical model, and facade area takeoffs into `facade_takeoff.py`.

## Core abstraction: BuildingModel

`building_model.py` defines the canonical dataclass tree:

```
BuildingModel
  levels: List[Level]
  spaces: Dict[str, Space]        ← primary key: "{level}-{number}"
  zones: Dict[str, Zone]          ← many-to-many with spaces (open office spans 2 zones)
  envelope: List[EnvelopeWall]     ← from arch plan; simplified geometry feeds export
  bim_elements: List[BimElement]  ← IFC Tier 0 raw inventory
  schedules: Dict[str, dict]       ← tag → ScheduleEntry
  review_queue: List[ReviewItem]   ← low-confidence links flagged, never silently dropped
  revision_log: List[RevisionEvent]
```

Every derived fact carries a `Provenance` record: `sheet_id`, `revision`, `method`, `confidence`, `bbox`. Facts from a newer revision of the same sheet SUPERSEDE older facts — kept in `history`, never silently overwritten.

## Coordinate conventions

| Context | Frame |
|---|---|
| Sheet pixel input | px |
| Canonical model | meters, **y growing DOWNWARD** (drawing frame) |
| BEM export (gbXML/IFC) | meters, **north-up** (y flipped in `bem_export.model_from_takeoff`) |

## Module ownership

| Module | Role |
|---|---|
| `building_model.py` | Core types + `BuildingModel` dataclass + `Provenance` |
| `link.py` | Orchestrates cross-sheet registration + linking into BuildingModel |
| `registration.py` | Affine2D transforms, cross-sheet coordinate registration |
| `datasets_adapter.py` | Detector → schedule join → count×width×height takeoffs |
| `validate.py` | 28-check invariant battery; errors **block export** |
| `bem_export.py` | gbXML 6.01 + IFC4 export from canonical model |
| `ifc_import.py` | IFC4 → canonical model (Tier 0; Tier 1 = space attachment, open) |
| `geometry_simplify.py` | Area-budgeted surface reduction (≤2% area drift default) |
| `hvac_trace.py` | Duct tracing → terminal units → zone graphs |
| `elevation_windows.py` | Exact window placement from elevations + sidelit daylight zones |
| `facade_takeoff.py` | Facade wall/glazing/door fractions on CMP Facade dataset |
| `lighting.py` | Fixture detection → installed watts + per-space LPD |
| `room_labels.py` | OCR room names/numbers → polygon association |
| `registration.py` | Grid + gridless (geometric) cross-sheet registration |
| `jesse.py` | WiSARD classifier — deterministic research asset, **not** production |
| `cli.py` | Unified `matchline` CLI entry point |
| `synth/` | Synthetic building/sheet generation for tests and demos |
| `detector/` | YOLO fine-tuning track — separate venv, excluded from main ruff |

## Integration points

- **Arch plan = canonical frame**: rooms identified by `"{level}-{number}"`; all other sheets (lighting, mech, elevations) registered into arch coordinates.
- **Zones ↔ Spaces**: many-to-many via diffuser positions (no cross-sheet ID matching).
- **BIM (IFC)**: Walls mirrored into `BuildingModel.envelope` for the BEM path; openings live in `bim_elements` until Tier 1 assigns them to spaces.
- **Windows from elevations**: registered to facade wall runs, linked to rooms via facade wall segment correspondence.

## Key design decisions

- **Provenance required**: every extracted fact carries sheet, revision, method, confidence. Low-confidence results go to the review queue — nothing is silently accepted.
- **Conservation laws block export**: `validate.py` enforces area/volume/envelope closure; errors are fatal.
- **Synthetic-first validation**: end-to-end paths are tested against `synth/` fixtures (`bldg_3room`, `bldg_open_office`, `bldg_8room`) before any real-data run.
- **YOLO fine-tuning is a separate track**: `detector/` has its own venv; the main package has no torch dependency.
- **WiSARD is a research asset**: `jesse.py` is a deterministic, explainable paper reproduction; production symbol spotting uses the YOLO pipeline.
