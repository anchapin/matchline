# QUALITY-SCORE.md — per-domain quality grades

**Analysis Date:** 2026-10-06

## Overview

Quality grades per domain/layer. Scores are qualitative assessments based on test coverage, documentation, invariant enforcement, and known debt. Issue numbers in parentheses are the closed issues that moved a grade; see the Changelog at the end for when each re-derivation happened.

---

## Domain Scores

### Core Model (`building_model.py`)

**Grade: A** (unchanged)

- Dataclass tree is well-defined with complete type hints
- Provenance on every fact with `history` for revision tracking; `Provenance = None` no longer accepted (#402)
- `review_queue` with `flag_for_review()` for low-confidence items; `needs_review` defaults fixed (#405)
- `from_dict`/`to_dict` with generic dataclass revival
- `MODEL_VERSION` 1.1 with migration; `SpaceOpening.source_provenance` holds up to two sheets for a merged opening (#663)
- `RoofPlane` for sloped roofs (#613); matchline ids stable through export and re-import (#588)
- No known blocking debt

### Validation (`validate/`)

**Grade: A** (unchanged)

- 48-check invariant battery documented in `docs/validation.md`; `N_CHECKS = len(BATTERY) + len(CONSERVATION_BATTERY)` is tested
- Conservation laws block export via `export_gate()`, including violations introduced by the BEM transform (#230, #403, #415)
- Post-export gbXML/IFC4 schema validation is part of the pipeline (#304, #475)
- Defect injection tests in `tests/test_validate.py` (`model_factory.py`), parametrized for BEM checks (#344, #467)
- Every check has error/warn/skip semantics
- New since 2026-09-22: `hvac_zone_coverage` (#658), `cross_level_dedup` (#664)
- `envelope_closure` now errors only when openings exceed the facade; it no longer caps window-to-wall ratio at 5% (#664)

### Cross-Discipline Linking (`link/`, `registration.py`)

**Grade: A-** (was B+)

- Two registration paths: grid (high confidence) and geometric fallback (flagged)
- Point-in-polygon assignment with `assign_points_to_spaces()`
- Facade interval matching with ambiguity detection; length-only wall matching fixed (#505)
- Cross-level window dedup closed: merges only openings at the same along-wall position (≤5 cm) whose vertical extents meet, keeps both sheets' provenance, and a validation check catches survivors (#404, #422, #664)
- `link.py` split into a package (#256)
- Remaining gap: IFC Tier 1 space attachment (#666, waiting on the #665 design review)

### BEM Export (`bem_export.py`)

**Grade: A-** (was B+)

- gbXML 6.01 and IFC4 export paths, both parsed with `safe_xml` (#272)
- y-down → north-up flip explicit in `model_from_takeoff()`
- Multi-storey export with inter-storey surface matching and shaft stacks (#632, #639)
- OpenStudio round-trip gate in CI; RectangularGeometry-only walls fixed (#627, #628)
- Sloped roofs with closed shells in gbXML and IFC round trip (#618, #619)
- ASHRAE 90.1 Appendix G perimeter/core zoning with room splits and air walls (#630, #638, #648)
- Below-grade boundary types, walk-out basements and atria (#634, #640, #641, #649, #650)
- Remaining gap: the documented template limits in `docs/bem_export.md` (rectangular-room approximation, synthetic schedules, large commercial HVAC not templated)

### IFC Import (`ifc_import.py`)

**Grade: B+** (was B)

- Tier 0 (geometry + openings without space assignment) much more complete: wall centrelines and junctions (#575, #576, #579), lining walls (#577, #597), finish slabs (#584), ceilings and plenums (#583), door operation and glazing (#573), storey fallback (#585)
- Deterministic across runs (#586); fixture builder for mirrored placements (#587)
- Fragment-merge and duplicate-opening parity check (#578); unattached openings counted and reported (#512)
- Untrusted-input guards: pre-open validation and size limits (#270, #271); a broken IfcOpenShell install fails with a clear message (#407); ifcopenshell 0.9 (#528)
- `review_queue` integration for low-confidence items and unclaimed wall loops (#581)
- Tier 1 space attachment landed (#666, #681)

### Geometry Simplification (`geometry_simplify.py`)

**Grade: A-** (was B)

- Area-budgeted simplification with ≤2% drift default
- Area-growth bugs fixed: concave-vertex handling and cost function (#329, #356, #359, #360, #472)
- Convexity invariant tests and convex-only audit (#330, #332, #333); oblique and circulation hard-case fixtures (#502)
- Property tests de-flaked and no longer vacuous on the #472 failure mode (#466, #476)
- Solar-weighted roof mode (#616)
- `SimplifyResult` with `valid` flag and `area_delta_pct`; validation check `_check_simplify_budget`
- Tolerances documented in `docs/geometry_simplify.md`

### HVAC Zoning (`hvac_trace.py`)

**Grade: B+** (was B)

- Duct tracing → terminal units → zone graphs
- Many-to-many zone↔space relationships supported
- Dedicated `hvac_zone_coverage` check: unserved terminal spaces error, unzoned floor area above 5% warns (#658)
- Provenance on returned objects (#431); `validate()` name collision fixed (#319)
- Ceiling height and plenum depth available from IFC (#583)
- Boundary and outside-every-room diffusers resolved by duct connectivity, then nearest room within 0.5 m; ties go to review (#684)
- Diffuser assignment uses each room's `polygon_m` outline when given (L-shaped and angled rooms), falling back to `rect_m` (#697)
- Remaining gap: detection is validated on synthetic mechanical sheets only

### Synthetic Data (`synth/`)

**Grade: A** (unchanged)

- Deterministic generators for buildings, sheets, lighting, mechanical
- Ground truth JSON for every synthetic sheet
- Fixtures for `bldg_3room`, `bldg_open_office`, `bldg_8room`
- Documented in `docs/synth/README.md` (#273)

### Testing (`tests/`)

**Grade: A** (unchanged)

- 1,744 tests with a CI test-count ratchet (`EXPECTED_TEST_COUNT`)
- `conftest.py` with shared fixtures; `model_factory.py` with clean model + `break_*` defect injectors
- Pipeline-level defect injection and end-to-end runs (#347, #421)
- Property-based tests with timeouts (#269, #345); `pytest-timeout` (#349)
- Detector tests run in CI (#507)
- Golden fixtures in `tests/goldens/`
- No external dataset dependencies for core tests

### Code Style (`ruff`)

**Grade: A** (was A-)

- Ruff with E, F, I, W, FA; line length 100 enforced, with inline `# noqa: E501` only on geometry/XML literals (#657)
- Ruff pinned to one version in CI and pre-commit, with grouped Renovate updates (#656)
- Return type annotations on public functions (#444); broad `except` clauses removed (#445)
- Remaining ignores (E701, E702, E741, F821) are documented pre-existing style choices

### CLI (`cli.py`)

**Grade: A** (unchanged)

- Unified `matchline` CLI with subcommands; the `wisard-bem` alias was removed in 0.2.0
- Argument parsing with `--config` YAML support; `--confidence-threshold` range-checked (#480)
- `matchline ifc-export` runs the validation gates (#478)
- Integration tests for every subcommand (#236, #264)
- Help text for every subcommand

---

## Gap Summary

| Domain | Grade | Primary Gap |
|--------|-------|-------------|
| Core Model | A | None |
| Validation | A | None |
| Linking | A- | Cross-level links verified on synthetic buildings only |
| BEM Export | A- | Template limits (rectangular rooms, synthetic schedules) |
| IFC Import | A- | Tier 1 is 2-D footprint probes; no authored-boundary cross-check (Tier 2) |
| Geometry | A- | None |
| HVAC | B+ | Rectangular rooms; synthetic sheets only |
| Synth | A | None |
| Tests | A | None |
| Style | A | None |
| CLI | A | None |

---

## Changelog

- **2026-10-06 (#681)** — IFC Import B+ → A-: Tier 1 complete (opening attachment and side probes #666; host intervals, `facade_unclear`, `adjacency_ambiguous` #681). Linking gap note updated now that #666 is merged.
- **2026-10-07 (#697)** — HVAC: diffuser-to-room assignment and the #684 boundary/nearest/tie rules measure against room polygons, so a diffuser in the notch of an L-shaped room goes to the room that fills it. Grade unchanged (synthetic-only validation remains).
- **2026-10-07 (#693)** — BEM Export: people heat gain now written (gbXML `PeopleHeatGain` W/person Total; IFC `Pset_SpaceThermalLoad.People`) from each DOE prototype row's constant occupancy activity schedule; rows with none (data center) get none.
- **2026-10-07 (#691)** — BEM Export: gbXML (single- and multi-storey) writes per-space people, lighting and equipment densities with their hourly schedules and a source note; IFC4 writes occupancy and thermal-load psets. People heat gain still not written.
- **2026-10-06 (#685)** — BEM Export: missing per-room LPD, occupant and plug-load density and schedules now default from the DOE Commercial Prototype Building Models (ASHRAE 90.1-2019, openstudio-standards v0.8.6) with `doe_prototype_default` provenance; drawing values are never overwritten. Grade unchanged until gbXML/IDF carry them.
- **2026-10-06 (#684)** — HVAC B → B+: diffusers on or near a wall line, or just outside every room, are assigned by duct connectivity then nearest room, and genuine ties go to review instead of the first room listed.
- **2026-10-06** — Re-derived against issues closed since 2026-09-22 (#661). Linking B+ → A- (cross-level dedup #664, multi-provenance #663). BEM Export B+ → A- (multi-storey, OpenStudio gate, sloped roofs, Appendix G). IFC Import B → B+ (Tier 0 coverage and guards; Tier 1 still open). Geometry B → A- (simplifier area-growth fixes and tests). HVAC B- → B (`hvac_zone_coverage` #658). Style A- → A (E501 enforced #657, ruff pin #656). Fixed stale notes: `N_CHECKS` is 47, not 26; the `wisard-bem` alias is gone.
- **2026-09-22** — Initial assessment.

---

*Quality assessment: 2026-10-06*
