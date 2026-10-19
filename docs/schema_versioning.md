# Schema versioning policy

Serialized artifacts matchline writes carry version fields so consumers
can read them without guessing the shape. This page is the written policy
for what is versioned, how versions bump, and what readers accept.

## What is versioned

| Artifact | Version field | Current |
| --- | --- | --- |
| Canonical model JSON (`model.json`, API payloads) | `model_version` (wrapper) | `1.2` (`MODEL_VERSION`) |
| Review decisions file (`out/review/decisions.json`) | `schema` | `matchline.review_decisions/1` |
| Export package manifest | `schema` | `matchline.package/1` |
| Trust report | `schema` | `matchline.trust_report/1` |
| Telemetry payload | `spec_version` | set by `telemetry_spec` |
| Public Python API surface | pinned snapshot | `tests/api_surface.json` (#1159) |

## Reader rules

- **Canonical model** (`BuildingModel.from_dict` / `from_json`): a payload
  at the current version loads as-is; a payload at an older known version
  is migrated forward by `_migrate_model_dict`; anything else (a version
  newer than this build, or a string that is not a version) raises
  `ValueError` naming the supported versions and the remediation. A
  payload with no `model_version` wrapper is treated as `1.0` (the
  oldest shape) and migrated.
- **Review artifacts** (`review_report.check`, the package loaders): the
  schema string must match exactly; a mismatch raises `ValueError`
  naming both the found and expected strings. There is no silent
  re-versioning.
- **Telemetry** payloads are validated against the spec before they can
  leave the emitter; a non-conforming payload raises at build time.

## Writer and bump rules

- A shape change to a serialized artifact bumps its version exactly once
  per release (the `model_version` string, or the artifact's `schema`
  string). Never reuse a version value.
- A bump that moves data adds a migration step to `_migrate_model_dict`
  (pure: never mutates the caller's payload) and a row to
  [migrations.md](migrations.md).
- Any new timestamped or environment-dependent field in a golden-pinned
  artifact must land in `VOLATILE_KEYS` (tests/make_goldens.py), or the
  golden goes red on the next run for the wrong reason (#1127).

## Golden tests

`tests/goldens/` pins the byte-stable shape of the validation report, the
gbXML export, the review decisions file, the package manifest, the
schedule score, the canonical model JSON and a telemetry payload
(`SHA256SUMS.json` carries the digests). A golden diff means the schema
changed: regenerate with `python -m tests.make_goldens`, review the diff,
and bump the version if the shape change is real.
