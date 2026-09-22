# Phase 03 Plan 01: IFC Round-Trip Export Summary

## Identification

| Field | Value |
|---|---|
| **Phase** | 03-ifc |
| **Plan** | 01 |
| **Plan path** | `.planning/phases/03-ifc/03-ifc-01-PLAN.md` |
| **Requirements** | IFC-01, IFC-02 |
| **Completed** | 2026-09-21 |

## What was done

Built `ifc_export.py` as the BuildingModel→IFC4 exporter with `matchline ifc-export` CLI, completing the IFC round-trip:

- **Task 1 — `ifc_export.py`**: Created `ifc_export.py` with three functions:
  - `_bem_from_model(model: BuildingModel) → BEMModel`: Converts canonical BuildingModel to BEMModel. Maps Space → BEMSpace with y-down→y-north polygon flip + CCW winding. Flattens all SpaceOpenings into deduplicated BEMOpeningUnit list by (tag, width_m, height_m). Builds envelope ring from EnvelopeWall segments in facade order (south→east→north→west). Uses `_ensure_ccw` from bem_export to guarantee CCW ring for IFC geometry.
  - `_export_ifc(model, path)`: Calls adapter then `write_ifc4`
  - `export_ifc(model_path, out_path)`: Loads JSON, calls `_export_ifc`

- **Task 2 — CLI subcommand**: Added `cmd_ifc_export` and `ifc-export` subparser to `cli.py` with `model` (JSON path) and `out` (IFC4 path) positional arguments. Updated module docstring.

## Files modified

| File | Change |
|---|---|
| `ifc_export.py` | Created (3 commits: adapter implementation, CLI wiring, ruff auto-fix) |
| `cli.py` | Added `cmd_ifc_export` function + `ifc-export` subparser + docstring update |

## Commits

| Hash | Message |
|---|---|
| `420664d` | feat(03-ifc-01): add BuildingModel→BEMModel IFC4 export adapter |
| `408d523` | feat(03-ifc-01): add matchline ifc-export CLI subcommand |
| `2819d76` | style(ifc_export): ruff auto-fix — format + unused import removal |

## Verification results

### Adapter smoke test
```
python3 -c "from ifc_export import _bem_from_model; ..."
adapter OK
```

### IFC4 smoke test
```
python3 -c "from ifc_export import _bem_from_model; import bem_export, tempfile, os; ..."
IFC4 smoke OK
```

### CLI help
```
$ matchline ifc-export --help
usage: matchline ifc-export [-h] model out
  model       BuildingModel JSON file path
  out         Output IFC4 file path
```

### Full IFC4 validation
```
validate_ifc4: OK
Schema: IFC4 OK
IFC4 full validation PASSED
```

### Existing tests
```
$ pytest tests/test_ifc_import.py -x -q
Pytest: 11 passed
```

## Success criteria — all met

| Criterion | Result |
|---|---|
| `matchline ifc-export model.json out.ifc` exits 0 | PASS |
| `validate_ifc4(out.ifc)` returns `(True, [])` | PASS |
| `ifcopenshell.open(out.ifc).schema == "IFC4"` | PASS |
| `test_ifc_import.py` still passes | PASS (11/11) |

## Deviations from plan

None — plan executed exactly as written.
