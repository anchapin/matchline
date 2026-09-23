# Pipeline: stage chain and intermediate artifacts

`run_pipeline.py` orchestrates the full extraction-to-BEM path. It is the
canonical entry point for `matchline run`.

Each stage writes a self-contained JSON snapshot so operators can inspect any
step in isolation without re-running the full pipeline.

## Stage order

```
Stage 1: generate_building        → stage_01_building.json
Stage 2: build_model              → stage_02_model.json
Stage 3: simplify_ring            → stage_03_simplified.json
Stage 4: run_checks                → stage_04_validation.json
Stage 4b: _run_auto_triage (opt)  → stage_04b_auto_triage.json  [ENABLE_AUTO_TRIAGE=1]
Stage 5: fail-fast gate           (no artifact — exits 1 on validation errors)
Stage 6: BEM export               → stage_06_bem/{name}.xml + {name}.ifc
```

## What each stage consumes / produces

| Stage | Function | Input | Output artifact | Notes |
|---|---|---|---|---|
| 1 | `generate_building` | seed (int) | `stage_01_building.json` | Synthetic `BuildingModel` dict; real-sheet path raises `NotImplementedError` |
| 2 | `build_model` | `stage_01_building.json` | `stage_02_model.json` | Adds elevations, openings, linked spaces; also writes `link_report` |
| 3 | `simplify_ring` | `stage_02_model.json` | `stage_03_simplified.json` | Reduces polygon vertices; tracks `area_delta_pct` (≤ 2 % default) |
| 4 | `run_checks` | `stage_02_model.json` + `stage_03_simplified.json` | `stage_04_validation.json` | 26-check invariant battery; errors **block export** |
| 4b | `_run_auto_triage` | `stage_04_validation.json` | `stage_04b_auto_triage.json` | Opt-in via `ENABLE_AUTO_TRIAGE=1`; mutates `model.review_queue` |
| 5 | `export_gate` | `stage_04_validation.json` | — | Exits with code 1 if any error; no artifact written |
| 6 | `write_gbxml` + `write_ifc4` | `stage_02_model.json` + `stage_03_simplified.json` | `stage_06_bem/{name}.xml`, `{name}.ifc` | gbXML 6.01 + IFC4 from `BEMModel` |

## Intermediate JSON schema (summary)

### `stage_01_building.json`
```json
{
  "building_id": "bldg_3room",
  "seed": 42,
  "name": "...",
  "levels": [...],
  "spaces": {...},
  "zones": {},
  "envelope": [...],
  "review_queue": [...]
}
```
Real-sheet mode writes `source`, `n_detections`, `n_scheduled` instead of the full model.

### `stage_02_model.json`
```json
{
  "model": { /* BuildingModel as dict */ },
  "link_report": { /* LinkReport: spaces linked, openings, etc. */ }
}
```

### `stage_03_simplified.json`
```json
{
  "original_count": 24,
  "simplified_count": 8,
  "simplified_ring": [[x, y], ...],
  "area_delta_pct": -0.31,
  "tolerance_pct": 2.0
}
```

### `stage_04_validation.json`
```json
{
  "ok": true,
  "error_count": 0,
  "warn_count": 2,
  "errors": [],
  "warnings": [...]
}
```
Structure of `errors` / `warnings` entries: `{check_id, message, entity_id, expected, actual}`.

### `stage_04b_auto_triage.json`
```json
{
  "auto_triage": true,
  "items": [{ /* ReviewItem */ }, ...]
}
```
Written only when `ENABLE_AUTO_TRIAGE=1`. Each `ReviewItem` carries `needs_review` and `urgency` post-classifier.

### `stage_06_bem/`
Contains `gbXML` and `IFC4` files named after `model.name` (fallback: `building.xml` / `building.ifc`).

## Auto-triage integration

`_run_auto_triage` is called **after** `run_checks` so that confidence scores
computed during validation are already attached to `ReviewItem` records. It
iterates over `model.review_queue` and calls `model._triage_item(item)` in-place,
enriching each item with `needs_review` and `urgency`.

Skipped silently when the triage classifier is unavailable — all fields retain
their safe defaults (`needs_review=1.0`, `urgency=1`).

Controlled by the `ENABLE_AUTO_TRIAGE` environment variable:

| Value | Behaviour |
|---|---|
| `1`, `true`, `yes` | Run auto-triage, write `stage_04b_auto_triage.json` |
| unset / anything else | Skip |

## Naming convention

All stage artifacts are written under `--out-dir` (default: `bem_out/`):

```
bem_out/
  stage_01_building.json
  stage_02_model.json
  stage_03_simplified.json
  stage_04_validation.json
  stage_04b_auto_triage.json   ← only when ENABLE_AUTO_TRIAGE=1
  stage_06_bem/
    {name}.xml
    {name}.ifc
```

## Fail-fast policy

`export_gate(report)` from `validate.py` is the hard gate before BEM export.
If `report.ok is False` (any error), the pipeline prints each error to stderr
and exits with code 1. No BEM artifact is written. This enforces the
**conservation law invariant**: a model that cannot balance its own books
does not reach gbXML or IFC.
