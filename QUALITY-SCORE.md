# QUALITY-SCORE.md — per-domain quality grades

**Analysis Date:** 2026-09-22

## Overview

Quality grades per domain/layer. Scores are qualitative assessments based on test coverage, documentation, invariant enforcement, and known debt.

---

## Domain Scores

### Core Model (`building_model.py`)

**Grade: A**

- Dataclass tree is well-defined with complete type hints
- Provenance on every fact with `history` for revision tracking
- `review_queue` with `flag_for_review()` for low-confidence items
- `from_dict`/`to_dict` with generic dataclass revival
- `model_version` for schema migration
- No known blocking debt

### Validation (`validate.py`)

**Grade: A**

- 28-check invariant battery documented in `docs/validation.md`
- Conservation laws block export via `export_gate()`
- Defect injection tests in `tests/test_validate.py` (`model_factory.py`)
- Every check has error/warn/skip semantics
- `N_CHECKS` is documented and tested (must equal 26)

### Cross-Discipline Linking (`link.py`, `registration.py`)

**Grade: B+**

- Two registration paths: grid (high confidence) and geometric fallback (flagged)
- Point-in-polygon assignment with `assign_points_to_spaces()`
- Facade interval matching with ambiguity detection
- P1 debt: window deduplication across elevation runs
- P1 debt: IFC Tier 1 space attachment unimplemented

### BEM Export (`bem_export.py`)

**Grade: B+**

- gbXML 6.01 and IFC4 export paths
- y-down → north-up flip explicit in `model_from_takeoff()`
- Coordinate frame documented and tested
- No known blocking debt

### IFC Import (`ifc_import.py`)

**Grade: B**

- Tier 0 implemented (geometry + openings without space assignment)
- Tier 1 (space attachment) explicitly open debt (P1)
- No fallback when IfcOpenShell unavailable
- `review_queue` integration for low-confidence items

### Geometry Simplification (`geometry_simplify.py`)

**Grade: B**

- Area-budgeted simplification with ≤2% drift default
- `SimplifyResult` with `valid` flag and `area_delta_pct`
- Validation check in `validate.py` (`_check_simplify_budget`)
- Tolerances documented in `docs/geometry_simplify.md`

### HVAC Zoning (`hvac_trace.py`)

**Grade: B-**

- Duct tracing → terminal units → zone graphs
- Many-to-many zone↔space relationships supported
- Depends on point-in-polygon for diffuser assignment
- No dedicated validation check (covered by general referential checks)

### Synthetic Data (`synth/`)

**Grade: A**

- Deterministic generators for buildings, sheets, lighting, mechanical
- Ground truth JSON for every synthetic sheet
- Fixtures for `bldg_3room`, `bldg_open_office`, `bldg_8room`
- Module-level docstrings explain the model

### Testing (`tests/`)

**Grade: A**

- `conftest.py` with shared fixtures
- `model_factory.py` with clean model + `break_*` defect injectors
- `test_validate.py` with defect injection → expected check mapping
- Golden fixtures in `tests/goldens/`
- No external dataset dependencies for core tests

### Code Style (`ruff`)

**Grade: A-**

- Ruff with E, F, I, W; ignores E501, E701, E702, E741
- Type hints on public functions
- `from __future__ import annotations` where it helps
- P2 debt: E501 suppressions are broad, not per-literal

### CLI (`cli.py`)

**Grade: A**

- Unified `matchline` CLI with subcommands
- Deprecated `wisard-bem` alias documented for removal in 0.2.0
- Argument parsing with `--config` YAML support
- Help text for every subcommand

---

## Gap Summary

| Domain | Grade | Primary Gap |
|--------|-------|-------------|
| Core Model | A | None |
| Validation | A | None |
| Linking | B+ | Window deduplication, IFC Tier 1 |
| BEM Export | B+ | None |
| IFC Import | B | Tier 1 unimplemented |
| Geometry | B | None |
| HVAC | B- | No dedicated validation check |
| Synth | A | None |
| Tests | A | None |
| Style | A- | Broad E501 suppression |
| CLI | A | None |

---

*Quality assessment: 2026-09-22*
