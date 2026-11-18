# Verify smoke (`matchline verify-smoke`, #1411)

The per-change end-to-end smoke named in `CONTRACT.md`'s Verify
set for changes touching pipeline/export paths. One command:

```bash
matchline verify-smoke --sheet-id sheet_007
# or: python run_verify_smoke.py --sheet-id sheet_007
```

## What it does

`run_verify_smoke.py` chains two existing entry points and changes
neither's semantics:

1. **Generate-if-missing** — if `synth/out/sheets/<sheet>.gt.json`
   is absent, it generates exactly that sheet's slice with
   `python -m synth.make_dataset --sheets 1 --seed <n>`, where
   `<n>` is the sheet id's numeric suffix (the seed `make_dataset`
   names that sheet from). If the ground truth is already on disk,
   generation is skipped entirely.
2. **Gated export** — `run_bem_export.main` runs the #1404/#1405
   gated transaction: gbXML (8.01 XSD) and IFC4 (structural)
   validation inside the gate, atomic publish on success, nothing
   published on failure.

Exit code is 0 iff the export published with both validations
passing; any generation failure, validation failure, or gate
refusal exits 1.

## Expected wall times

Measured in plan-coder #94 Phase 0 (full dataset + export) and at
#1411 build time (single-sheet slice): ≤~35 s cold, ≤~10 s warm.
The single-sheet generation slice usually lands well under the
cold bound.

## Limitations

- **Synthetic only.** The smoke proves the export path works on
  the golden synthetic sheet; it says nothing about real drawing
  sets, OCR, or detector quality.
- **One sheet.** `sheet_007` is the default (the bem-export demo
  sheet). Other sheets can be named with `--sheet-id`, but only
  the default is the contract's smoke.
- **Dataset presence is the cache key.** Generate-if-missing keys
  on the ground-truth file's existence, not its freshness: if
  `synth/` generation code changes, delete `synth/out/` to force
  regeneration.
- **Not a replacement for the suite.** It complements the Verify
  set's pytest/ruff runs; it does not subsume them, and the
  publish-nothing export gate remains the final arbiter for
  exports.
