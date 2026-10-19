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
pip install matchline        # from PyPI; extras: [ocr] [mech] [ui] (see pyproject.toml)
```

Python ≥ 3.10. The [Quickstart](#quickstart) below runs a full demo on the
installed package. To work on the repo itself, see
[Local development](#local-development).

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
matchline ingest set.pdf --out sheets/  # PDF drawing set → per-page raster, vectors, text
matchline scale sheets/        # drawing scale per sheet: notes, scale bars, dimensions
matchline index sheets/        # sheet index: discipline, type, level; plans paired by level
matchline walls sheets/        # walls (centreline + thickness) and rooms from vector floor plans
matchline schedules sheets/    # door, window, lighting and mechanical schedule tables
matchline ifc-import bldg.ifc --out model.json  # IFC Tier-0 import
matchline ifc-export model.json out.ifc  # IFC4 export
matchline review model.json     # review queue: list/confirm/reject
matchline takeoff-diff --old a.json --new b.json  # diff two revisions' takeoffs (rooms, openings, symbols)
matchline takeoff-diff --old a.json --new b.json --review-items model.json  # feed the diff into the review queue
matchline learn-legend out_dir --tag 'SUPPLY GRILLE=grille'  # learn legend tags from set reports; suggests mappings
matchline multidiscipline       # 3 synthetic buildings
matchline mnist --data-dir data # needs data/mnist_X.npy + mnist_y.npy
```

## Web interface

The same pipeline is reachable from a browser. The `ui` extra adds a one-page
web app that shells out to `run_pipeline`, so its numbers match the CLI:

```bash
pip install "matchline[ui]"
matchline-ui                 # opens http://127.0.0.1:7860
```

Drop a drawing set (one PDF, or a zip of PDFs that is unpacked and read in
filename order), an IFC model, or a single sheet image, or tick the demo box
for a synthetic building. Set how aggressively similar surfaces merge, press
Run, and download the gbXML, the building model JSON, and the review page.
A sheet image is labeled honestly in the app: it yields symbols and a takeoff
only, never walls or rooms, and the pipeline says so instead of exporting a
half model. IFC runs go through the same stages as a drawing set; validation
findings (review items and conservation checks) block export exactly as they
do for any other input, with the review page written so you can work through
them (`matchline review out/stage_02_model.json --confirm <id>`, then re-run).
The server binds to 127.0.0.1 and keeps uploaded files on the machine that
runs it.

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
| `link/` | Cross-discipline linking: fixtures/sensors/diffusers/windows → rooms |
| `elevation_windows.py` | Exact window placement from elevations + daylight zones |
| `facade_takeoff.py` | Facade wall/glazing/door area fractions (CMP Facade) |
| `bem_export.py` | gbXML 8.01 + IFC4 export |
| `pdf_ingest.py` | PDF drawing set → per-page raster + vector primitives + text, scans flagged |
| `drawing_scale.py` | Sheet scale from scale notes, scale bars and dimension strings; NTS refused |
| `sheet_index.py` | Sheet index from title blocks and the drawing index: discipline, type, level, takeoff role |
| `pdf_schedules.py` | Schedule tables from vector PDF sheets: ruled cells, merged headers, door/window sizes, fixture watts, VAV/AHU airflow, unreadable tables flagged |
| `construction_library.py` | Cited envelope U-values: wall-type and roof notes resolved to ASHRAE 90.1-2019 Table 5.5 by climate zone (`--climate-zone`), stated values never overwritten ([docs](docs/construction_library.md)) |
| `plan_walls.py` | Walls and rooms from vector floor plans: wall line pairs and solid poché, openings, closed faces, review queue |
| `ifc_import.py` | IFC4 → canonical model (Tier 0, no space boundaries needed) |
| `cli.py` | Unified `matchline` CLI (one subcommand per demo script) |
| `validate/` | 49-check invariant battery; errors block export |

## Quickstart

One command, sample project to finished package:

```bash
pip install matchline          # if you have not installed it yet
matchline demo                 # ~1 minute: model, gbXML + IFC, review and trust report
```

The run leaves everything in `matchline-demo/`: the one-page trust report
(`package/trust_report.html`), the review report of low-confidence calls
(`review/review.html`), and the gbXML and IFC exports. On a terminal it opens
the trust report in your browser; pass `--no-open` to skip that. See
`docs/cli.md` for the full command set.

The public API surface, what counts as a breaking change, and the
deprecation policy are documented in `docs/api.md`.

## Validation

`validate/` enforces conservation laws over the model and its exports:
per-level floor-area conservation, volume conservation, envelope closure,
takeoff closure, LPD plausibility, cross-discipline referential integrity,
provenance closure. Errors block export; warnings require acknowledgment.
See `docs/validation.md` for the full invariant catalog and tolerance rationales.

## Datasets

Real-data work uses public sets under `~/workspace/datasets/` (not committed):
AEC Geometric Bench, CMP Facade (CC BY-SA), CubiCasa5K (CC BY-NC-SA 4.0),
FloorPlanCAD test split — see `CONTRIBUTING.md` for the full setup convention.

## Status

Research prototype. Synthetic end-to-end paths are green; real-drawing
validation against clean commercial sheets is the next milestone. The WiSARD
classifier is a deterministic/explainable asset; production symbol spotting
follows a fine-tuned YOLO + tiling/legend-learning pattern.

## Local development

Clone the repo and install in editable mode with the test extras:

```bash
git clone https://github.com/anchapin/matchline.git
cd matchline
git checkout develop         # develop is the working branch; main is reserved for releases
pip install -e ".[test]"     # editable install; extras: [ocr] [detector]
python -m pytest tests/ -q   # ~3,500 tests (exact count: EXPECTED_TEST_COUNT in ci.yml), hermetic
```

External datasets (AEC Bench, CMP Facade, CubiCasa5K,
FloorPlanCAD) live outside the repo under `~/workspace/datasets/`
(see `CONTRIBUTING.md` for the full convention); demos that need them
say so and fail clearly without them.

## Contributing

Contributions are welcome, human- or agent-authored alike. See
[CONTRIBUTING.md](CONTRIBUTING.md) for setup, the test-count gate, and the
review norms for external PRs, and our
[Code of Conduct](CODE_OF_CONDUCT.md). Every commit needs a DCO
`Signed-off-by` line.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
