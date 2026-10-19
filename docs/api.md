# Public API surface (gate #1159)

matchline ships its modules under the repo root and in small packages (`bem/`, `synth/`, `link/`, `validate/`, ...). This page states the line between the
public API and internals, what counts as a breaking change, and how
deprecation works. The CI guard that enforces it lives in
`tests/test_api_surface.py` against the pinned snapshot
`tests/api_surface.json` (schema `matchline.api_surface/1`).

## Classification rule

A top-level module is **public** when it has a module-named guide under
`docs/` (or, for `run_pipeline`, the guide at `docs/pipeline.md`). A package
is **public** when the README module table documents it (`link/`,
`validate/`). Everything else is **internal**: its names, signatures, and
file layout may change at any time without notice, and 1.0 users must not
import it directly.

The rule is checkable, not aspirational: a test fails if a module gains a
docs page without being declared public, so the surface only grows on
purpose.

## Public surface (37 modules, 2 packages, 372 symbols)

| Module | Guide |
|---|---|
| `bem.bem_export` (moved from the root by #1179) | [docs/bem_export.md](docs/bem_export.md) |
| `building_model.py` | [docs/building_model.md](docs/building_model.md) |
| `cli.py` | [docs/cli.md](docs/cli.md) |
| `construction_library.py` | [docs/construction_library.md](docs/construction_library.md) |
| `review/corrections.py` | [docs/corrections.md](docs/corrections.md) |
| `datasets_adapter.py` | [docs/datasets_adapter.md](docs/datasets_adapter.md) |
| `detection_provider.py` | [docs/detection_provider.md](docs/detection_provider.md) |
| `drawing_scale.py` | [docs/drawing_scale.md](docs/drawing_scale.md) |
| `elevation_windows.py` | [docs/elevation_windows.md](docs/elevation_windows.md) |
| `export_package.py` | [docs/export_package.md](docs/export_package.md) |
| `facade_takeoff.py` | [docs/facade_takeoff.md](docs/facade_takeoff.md) |
| `geometry_simplify.py` | [docs/geometry_simplify.md](docs/geometry_simplify.md) |
| `grid_detect.py` | [docs/grid_detect.md](docs/grid_detect.md) |
| `hvac_trace.py` | [docs/hvac_trace.md](docs/hvac_trace.md) |
| `ifc/ifc_export.py` | [docs/ifc_export.md](docs/ifc_export.md) |
| `ifc/ifc_import.py` | [docs/ifc_import.md](docs/ifc_import.md) |
| `jesse.py` | [docs/jesse.md](docs/jesse.md) |
| `legend_learning.py` | [docs/legend_learning.md](docs/legend_learning.md) |
| `lighting.py` | [docs/lighting.md](docs/lighting.md) |
| `mech_schedule.py` | [docs/mech_schedule.md](docs/mech_schedule.md) |
| `pdf_ingest.py` | [docs/pdf_ingest.md](docs/pdf_ingest.md) |
| `pdf_schedules.py` | [docs/pdf_schedules.md](docs/pdf_schedules.md) |
| `plan_walls.py` | [docs/plan_walls.md](docs/plan_walls.md) |
| `polygon_classify.py` | [docs/polygon_classify.md](docs/polygon_classify.md) |
| `real_set.py` | [docs/real_set.md](docs/real_set.md) |
| `registration.py` | [docs/registration.md](docs/registration.md) |
| `review/review_report.py` | [docs/review_report.md](docs/review_report.md) |
| `review/review_viewer.py` | [docs/review_viewer.md](docs/review_viewer.md) |
| `room_labels.py` | [docs/room_labels.md](docs/room_labels.md) |
| `run_pipeline.py` | [docs/pipeline.md](docs/pipeline.md) |
| `run_review.py` | [docs/run_review.md](docs/run_review.md) |
| `safe_xml.py` | [docs/safe_xml.md](docs/safe_xml.md) |
| `sheet_index.py` | [docs/sheet_index.md](docs/sheet_index.md) |
| `sim_smoke.py` | [docs/sim_smoke.md](docs/sim_smoke.md) |
| `space_merge.py` | [docs/space_merge.md](docs/space_merge.md) |
| `space_use_defaults.py` | [docs/space_use_defaults.md](docs/space_use_defaults.md) |
| `symbols.py` | [docs/symbols.md](docs/symbols.md) |

| Package | Exported symbols |
|---|---|
| `link/` | `build_model`, `LinkReport`, `match_interval_to_segments` |
| `validate/` | `run_checks`, `export_gate`, `validate_bem_conservation`, `CheckResult`, `ValidationReport` |

The exact pinned symbols per module, with signatures, live in
`tests/api_surface.json`. Regenerate with
`python3 scripts/generate_api_surface.py` after approved changes. Per-module
usage is in each guide linked above.

## Internal modules (66)

Everything not listed above. Names may change or disappear without notice.

- `air_loops`, `area_definitions`, `atomic_io`, `atria`, `below_grade`
- `bem_geometry`, `bem_helpers`, `bem_ifc4`, `bem_layers`, `bem_levels`
- `bem_loads`, `bem_multistorey`, `bem_roof`, `bem_shading`, `bem_space_split`
- `construction_library_data`, `construction_library_prm_data`, `constructions`, `convention_report`, `daylight_skylights`
- `diff`, `door_detect`, `elevation_sheets`, `feedback_capture`, `grade`
- `html_page`, `hvac_legend`, `ifc_roof_planes`, `ifc_space_borders`, `ifc_wall_joins`
- `ifc_wall_linings`, `ifc_wall_loops`, `ifc_wall_split`, `interstory`, `limits`
- `matchlines`, `materials`, `mech_tags`, `opening_attach`, `opening_identity`
- `pipeline_exceptions`, `revisions`, `roof_geometry`, `roof_simplify`, `run_bem_export`
- `run_elevation_windows`, `run_facade_takeoff`, `run_mnist`, `run_multidiscipline`, `run_room_labels`
- `run_symbols`, `run_validation`, `schedule_parse`, `semi_exterior`, `solar_aperture`
- `space_conditioning`, `space_use_defaults_data`, `takeoff_diff`, `telemetry_emitter`, `telemetry_spec`
- `thermal_zoning`, `vestibules`, `viewer_corrections`, `wall_types`, `zone_airflow`
- `zone_capacity`

## What counts as a breaking change

Matchline follows SemVer. From 1.0, these are breaking (major bump):

- Removing or renaming a public symbol (any name pinned in
  `tests/api_surface.json`).
- Changing a public function or constructor's required parameters, or
  turning an optional parameter into a required one.
- Incompatible changes to CLI flags or CLI behavior (`cli.py`, the
  `matchline` entry points).
- Incompatible changes to canonical model schema fields
  (`building_model.py`).
- Incompatible changes to report or artifact formats, including the
  schema-version strings (`matchline.api_surface/1`,
  `matchline.convention_report/1`). Bumping a schema string is a major
  change and must be documented.
- Removing names from a public package's `__all__` (`link`, `validate`).

A minor bump covers additive change only: new symbols, new optional
parameters, new fields, new checks. A patch bump covers bug fixes and
internal changes.

## Deprecation policy

A public symbol is deprecated for one minor cycle before removal: mark it
with `DeprecationWarning` (via `warnings.warn`) in release X.Y, and remove
it no earlier than X.(Y+1). Deprecations are announced in the release
notes (the `docs/release.md` flow). The CI guard keeps removals from being
silent: the pins force every change to a public symbol to be a deliberate,
reviewed regeneration.

## The CI guard

`tests/test_api_surface.py` fails when:

- the live surface, re-parsed with `ast` from the declared public modules,
  no longer matches the pinned snapshot (the failure names the drifted
  symbols and the regeneration command);
- a module has a docs page but was not declared public;
- the snapshot schema string changes;
- a declared public module or package no longer imports.
