# Codebase Structure

**Analysis Date:** 2026-09-22

## Directory Layout

```
matchline/
├── *.py                  # Core pipeline modules (standalone, no import barriers)
├── synth/                # Synthetic data generators (test fixture package)
├── detector/             # YOLO fine-tuning (separate venv, excluded from ruff)
├── tests/               # Pytest suite
├── docs/                # Module guides and design documents
├── schemas/             # JSON schema definitions
├── review_classifier/   # Auto-triage for review queue
└── .planning/           # GSD planning artifacts
```

## Directory Purposes

**Root Python Modules:**
- Purpose: Core pipeline modules, each is a separate concern
- Contains: `building_model.py`, `link.py`, `registration.py`, `bem_export.py`, `ifc_import.py`, `ifc_export.py`, `validate.py`, `geometry_simplify.py`, `hvac_trace.py`, `elevation_windows.py`, `facade_takeoff.py`, `lighting.py`, `room_labels.py`, `datasets_adapter.py`, `polygon_classify.py`, `cli.py`, `jesse.py`, `symbols.py`, and runner scripts (`run_*.py`)
- Key files: `building_model.py` (core types), `cli.py` (entry point)

**`synth/` (Synthetic Data Package):**
- Purpose: Synthetic building/sheet generation for tests and demos
- Contains: `__init__.py`, `multidiscipline.py`, `sheets.py`, `lighting.py`, `lighting_sheets.py`, `lighting_e2e_test.py`, `mech.py`, `symbols.py`, `make_dataset.py`, `e2e_test.py`, `gap_experiment.py`
- Key files: `multidiscipline.py` (generates linked multi-discipline buildings), `sheets.py` (sheet rendering with ground truth)
- Note: This is a test data package listed in `pyproject.toml` as `packages = ["synth"]`

**`detector/` (Separate Venv Track):**
- Purpose: YOLO fine-tuning for production symbol spotting
- Contains: `train.py`, `sahi_infer.py`, `eval_zero_shot.py`, `classes.py`, dataset converters (`convert_*.py`)
- Note: Separate venv, excluded from main ruff (`exclude = ["detector"]` in `pyproject.toml`)

**`tests/` (Pytest Suite):**
- Purpose: Test suite with fixtures and model factories
- Contains: `conftest.py` (shared fixtures), `model_factory.py` (deterministic test models), `test_*.py` files
- Key fixtures: `bldg_3room`, `bldg_open_office`, `bldg_8room` (from `conftest.py`)
- Key files: `model_factory.py` (clean model + `break_*` mutators for defect injection)

**`docs/`:**
- Purpose: Module guides and design documentation
- Contains: `design-docs/`, `plans/`, `README.md`, individual module `.md` files
- Key files: `docs/README.md`, `docs/validation.md`

**`schemas/`:**
- Purpose: JSON schema definitions
- Contains: Schema files for validated JSON structures

**`review_classifier/`:**
- Purpose: Auto-triage classifier for review queue items
- Contains: `triage.py`, `model.py`, `features.py`, `data.py`, `train.py`, `evaluate.py`

## Key File Locations

**Entry Points:**
- `cli.py`: `matchline` CLI entry point, `main()` function
- `run_pipeline.py`: Unified pipeline orchestrator
- `run_bem_export.py`: BEM export runner
- `run_validation.py`: Validation demo runner

**Configuration:**
- `pyproject.toml`: Package metadata, dependencies, ruff config, pytest config

**Core Logic:**
- `building_model.py`: `BuildingModel` dataclass tree and core types
- `link.py`: `build_model()` cross-discipline linking orchestrator
- `registration.py`: `Affine2D`, sheet registration transforms
- `validate.py`: 26-check validation battery

**Synthetic Data:**
- `synth/multidiscipline.py`: `generate_building()` for linked multi-discipline buildings
- `synth/sheets.py`: `generate_sheet()` sheet rendering with ground truth

**Testing:**
- `tests/conftest.py`: Pytest fixtures (`bldg_3room`, `bldg_open_office`, `bldg_8room`)
- `tests/model_factory.py`: `make_clean_model()` + `break_*()` defect injectors

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `building_model.py`, `bem_export.py`)
- Test files: `test_*.py` (e.g., `test_validate.py`)
- Synthetic generators: `snake_case.py` (e.g., `sheets.py`, `mech.py`)
- CLI runners: `run_*.py` (e.g., `run_bem_export.py`)

**Directories:**
- Lowercase with underscores: `synth/`, `detector/`, `tests/`, `docs/`

**Functions/Classes:**
- PascalCase for classes: `BuildingModel`, `Space`, `Affine2D`, `ValidationReport`
- snake_case for functions: `build_model()`, `run_checks()`, `import_ifc()`
- `_check_*` private functions in `validate.py`

**Variables:**
- snake_case: `wall_height_m`, `area_m2`, `px_per_m`
- Constants: UPPER_SNAKE: `N_CHECKS`, `REVIEW_CONFIDENCE`, `MODEL_VERSION`

**Types:**
- PascalCase dataclasses: `Space`, `Zone`, `Provenance`, `CheckResult`
- Type hints on all public functions

## Where to Add New Code

**New Pipeline Module:**
- Location: Root level as `new_module.py`
- Pattern: Standalone module with no intra-package import barriers
- Add to `pyproject.toml` `py-modules` if CLI-accessible
- Requirements: Type hints, provenance on extracted facts, tests

**New Synthetic Generator:**
- Location: `synth/new_generator.py`
- Pattern: Follow `synth/sheets.py` structure
- Export generating function as `generate_*()`

**New Test:**
- Location: `tests/test_new_feature.py`
- Pattern: `test_*.py` with pytest
- Use fixtures from `conftest.py`
- Include defect injection test if invariants exist

**New Validation Check:**
- Location: Add to `validate.py`
- Pattern: `_check_*()` function returning `CheckResult`
- Add to `BATTERY` list
- Add test in `test_validate.py` with defect injector in `model_factory.py`

**New BEM Export Format:**
- Location: `bem_export.py`
- Pattern: Function taking `BuildingModel` → output format
- Add validation check in `validate.py` if needed

## Special Directories

**`detector/`:**
- Purpose: YOLO fine-tuning track
- Generated: No
- Committed: Yes
- Note: Separate venv, excluded from ruff linting

**`synth/`:**
- Purpose: Synthetic data package
- Generated: No (generates test data at runtime)
- Committed: Yes
- Note: Listed as `packages = ["synth"]` in pyproject.toml

**`.planning/`:**
- Purpose: GSD planning artifacts
- Generated: Yes (by GSD orchestrator)
- Committed: Yes (part of repo)
- Note: Contains `codebase/`, `phases/`, `REQUIREMENTS.md`, etc.

**`tests/goldens/`:**
- Purpose: Golden test fixtures for regression testing
- Generated: No
- Committed: Yes

---

*Structure analysis: 2026-09-22*
