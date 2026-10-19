# Migrations: 0.x to 1.0

This guide covers every breaking change between the 0.x line and the 1.0
gate, and how older artifacts move forward. For the versioning rules
themselves see [schema_versioning.md](schema_versioning.md).

## BuildingModel JSON (`model_version`)

The wrapper is `{"model_version": <str>, "model": {...}}`. A payload with
no wrapper version loads as `1.0`.

| Version | Change | Consumer action |
| --- | --- | --- |
| 1.0 | Initial shape | None |
| 1.0 to 1.1 (#669) | `SpaceOpening` gained `source_provenance` (1-2 per-sheet provenance records; the primary record stays in `provenance`) | None: `_migrate_model_dict` seeds it from `provenance` on load |
| 1.1 to 1.2 (#918 wave, #900) | `SpaceOpening` gained `adjacent_space_id` (the space on the far side), default `None` | None: additive with a default |

**New reader behavior:** versions newer than the running build, or
malformed version strings, now raise `ValueError` with the supported
versions and remediation instead of being silently re-versioned and
loaded. If you see this error after moving artifacts between machines,
re-export the model with a current matchline; do not hand-edit the
version string.

## Review artifacts

- `decisions.json`: `schema` is `matchline.review_decisions/1`; the
  reader requires an exact match and rejects anything else.
- Package `manifest.json` (`matchline.package/1`) and `trust_report.json`
  (`matchline.trust_report/1`): unchanged since introduction.

## Telemetry

`spec_version` is unchanged through 0.2.x; payloads are validated against
the spec at build time, so an old writer cannot emit a shape the current
spec rejects.

## Python API

- `StageError` moved to `pipeline_exceptions.py` (#1161);
  `from run_pipeline import StageError` still works (run_pipeline imports
  it from there). `IfcImportError` subclasses it and `ValueError`.
- The public API surface is pinned in `tests/api_surface.json` (#1159,
  `docs/api.md`): removing or changing a public symbol needs a
  one-minor-cycle deprecation; additive changes only regenerate the
  snapshot.
- While pre-1.0, minor bumps may include `BuildingModel` schema breaking
  changes (RELEASING.md); each lands here as a version row above.
