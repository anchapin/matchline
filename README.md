# Matchline

Cross-sheet entity resolution for construction drawings: deterministic, auditable
extraction of building energy modeling (BEM) inputs from
architectural drawings: floor plans, elevations, lighting plans, and mechanical plans.

The pipeline turns clean digital construction drawings into quantity takeoffs,
cross-discipline-linked building models, and validated gbXML / IFC exports —
with every extracted fact carrying sheet, revision, bounding box, method,
confidence, and provenance.

Branching: `develop` is the working branch. `main` is reserved for releases.

## Install

```bash
git checkout develop
pip install -e ".[test]"     # editable install; extras: [ocr] [detector]
python -m pytest tests/ -q   # 324 tests, hermetic, a few seconds
```

Python ≥ 3.10. External datasets (AEC Bench, CMP Facade, CubiCasa5K,
FloorPlanCAD) live outside the repo — see `DATASETS.md` under
`~/workspace/datasets/`; demos that need them say so and fail clearly
without them. Contributing: see `CONTRIBUTING.md`.

## CLI

Every demo script is a subcommand (the old `python3 run_*.py` scripts still
work as thin wrappers):

```bash
matchline validate              # validation battery demo (synthetic)
matchline run --seed S         # unified pipeline: generate + link + validate + export
matchline bem-export --sheet-id sheet_007
matchline elevation-windows     # exact window placement + daylight (synthetic)
matchline room-labels          # OCR room labeling (synthetic)
matchline symbols              # symbol eval + GD&T invariant check
matchline facade-takeoff --n 5 # CMP Facade area takeoffs
matchline ifc-import bldg.ifc --out model.json  # IFC Tier-0 import
matchline ifc-export model.json out.ifc  # IFC4 export
matchline review model.json     # review queue: list/confirm/reject
matchline multidiscipline       # 3 synthetic buildings
matchline mnist --data-dir data # needs data/mnist_X.npy + mnist_y.npy
```

## Pipeline

```
drawings → symbol spotting → schedule join (count × dims) → room labeling
         → cross-discipline linking (arch = canonical frame)
         → HVAC zoning from duct tracing → area-budgeted geometry simplification
         → validation battery → gbXML / IFC export
```

Elevations additionally feed exact window placement (along-wall position,
sill/head heights) and facade area takeoffs (wall / glazing / door fractions).

## Modules

| Module | What it does |
|---|---|
| `jesse.py` | WiSARD classifier (paper reproduction; deterministic, explainable research asset) |
| `symbols.py` | Synthetic symbol/floor-plan generation + detection experiments |
| `datasets_adapter.py` | Detection → schedule join → `count × width × height` takeoffs |
| `room_labels.py` | OCR room names/numbers, associate to floor-space polygons |
| `geometry_simplify.py` | Area-budgeted BEM geometry reduction (default ≤2% area drift) |
| `lighting.py` | Fixture detection → installed watts, per-space LPD |
| `hvac_trace.py` | Duct tracing → terminal units → HVAC zone graphs |
| `building_model.py` | Canonical `Space`-centric model, provenance, review queue |
| `registration.py` | Grid + gridless (geometric) cross-sheet registration |
| `link.py` | Cross-discipline linking: fixtures/sensors/diffusers/windows → rooms |
| `elevation_windows.py` | Exact window placement from elevations + daylight zones |
| `facade_takeoff.py` | Facade wall/glazing/door area fractions (CMP Facade) |
| `bem_export.py` | gbXML 6.01 + IFC4 export |
| `ifc_import.py` | IFC4 → canonical model (Tier 0, no space boundaries needed) |
| `cli.py` | Unified `matchline` CLI (one subcommand per demo script) |
| `validate.py` | 26-check invariant battery; errors block export |

## Quickstart

```bash
python3 -m pytest tests/ -q        # 324 tests: units, invariants, defect injection, goldens
python3 run_multidiscipline.py     # end-to-end: link 3 synthetic buildings
python3 run_validation.py          # invariant battery demo
python3 run_bem_export.py          # gbXML + IFC export demos
python3 run_facade_takeoff.py      # facade takeoffs — needs ~/workspace/datasets/cmp-facade
```

(Or the equivalent `matchline <command>` forms above.)

## Validation

`validate.py` enforces conservation laws over the model and its exports:
per-level floor-area conservation, volume conservation, envelope closure,
takeoff closure, LPD plausibility, cross-discipline referential integrity,
provenance closure. Errors block export; warnings require acknowledgment.
See `docs/validation.md` for the full invariant catalog and tolerance rationales.

## Datasets

Real-data work uses public sets under `~/workspace/datasets/` (not committed):
AEC Geometric Bench, CMP Facade (CC BY-SA), CubiCasa5K (CC BY-NC-SA 4.0),
FloorPlanCAD test split. See `DATASETS.md` there.

## Status

Research prototype. Synthetic end-to-end paths are green; real-drawing
validation against clean commercial sheets is the next milestone. The WiSARD
classifier is a deterministic/explainable asset; production symbol spotting
follows a fine-tuned YOLO + tiling/legend-learning pattern.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
