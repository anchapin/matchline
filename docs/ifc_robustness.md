# IFC frontend robustness (roadmap gate #1161)

Real IFCs are messy. Every malformed input gets a pinned per-case outcome:
a **coded refusal** (`IfcImportError` with a stable machine-readable code
plus a remediation line) or a **degrade-with-warning** (the file parses;
the omission is announced, never silent). Nothing emits a bare traceback
for bad input: `IfcImportError` subclasses `StageError` (the pipeline's
stage handler prints it cleanly and exits 1) and `ValueError` (size and
element-limit callers pin `ValueError`).

The corpus lives in `tests/test_ifc_robustness.py` and runs on every CI
PR, so the behavior cannot drift silently. The code set is frozen in
`ifc_errors.IFC_ERROR_CODES`; renaming a code needs a deprecation cycle
like any API change (docs/api.md).

## Error taxonomy

| Code | Trigger | Outcome |
| --- | --- | --- |
| `ifc.path_outside_workspace` | relative input path resolves outside the working directory | refuse |
| `ifc.file_too_large` | file exceeds `MATCHLINE_MAX_IFC_SIZE_MB` (default 100 MB) | refuse |
| `ifc.element_limit_exceeded` | element count exceeds `MATCHLINE_MAX_IFC_ELEMENTS` (default 500k) | refuse |
| `ifc.unreadable_file` | empty file, or the file cannot be read/opened at all | refuse |
| `ifc.invalid_header` | no ISO-10303-21 STEP magic, or the STEP header does not match the payload | refuse |
| `ifc.truncated_file` | file ends without the `END-ISO-10303-21` terminator | refuse |
| `ifc.unsupported_schema` | declared schema variant outside IFC4/IFC2X3 (e.g. IFC2X3_TC1) | refuse |
| `ifc.missing_units` | no `IfcUnitAssignment`, or no LENGTHUNIT in it | refuse |
| `ifc.broken_length_unit` | `IfcConversionBasedUnit` conversion factor unreadable | refuse |
| `ifc.storey_elevation` | a storey's `Elevation` attribute cannot be read | refuse |
| `ifc.parse_error` | any other `ifcopenshell` parse failure | refuse |

Truncation matters most: ifcopenshell happily opens a truncated STEP file
and returns a silently partial model, which is exactly the silent-failure
class #417 exists for. The frontend therefore checks the STEP terminator
before parsing and refuses with `ifc.truncated_file` instead of importing
half a building.

## IFC2x3 vs IFC4 matrix

| Behavior | IFC4 | IFC2X3 |
| --- | --- | --- |
| Parse + import | yes | yes, upgraded in memory via the vendored migrator (#929) |
| Entities the migrator cannot map | n/a | collected, never silently dropped; `source_schema` reads `IFC2X3 (N entities skipped in upgrade)` |
| Truncated file | refuse (`ifc.truncated_file`) | refuse (`ifc.truncated_file`) |
| Schema variant outside the pair | refuse (`ifc.unsupported_schema`) | refuse (`ifc.unsupported_schema`) |
| Units (`IfcUnitAssignment` / LENGTHUNIT) | required, refuse if missing | required, refuse if missing |
| No `IfcProject` | degrade-with-warning: imports with a fallback name, `warnings.warn` announces it | same |
| Storey elevation unreadable | refuse (`ifc.storey_elevation`) | refuse (`ifc.storey_elevation`) |
| Zero-length wall / degenerate geometry | tolerated, item flagged (#417 battery) | same |
| Declared schema lying about the body (e.g. IFC4 body labeled IFC2X3) | n/a | imported through the declared-schema path; the declared schema is trusted and recorded, never guessed away |
