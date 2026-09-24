# ADR-010: Conservation Law Verification in validate.py

**Date:** 2026-09-23
**Status:** Accepted
**Deciders:** Matchline team

## Context

The validation battery (`validate.py`) enforces conservation laws to ensure that
extracted BEM inputs are physically plausible and that data flowing through
the pipeline is not silently corrupted. Two gaps were identified:

1. **`simplify_budget`** checked the simplifier's self-reported `area_delta_pct`
   but did not independently verify it. A buggy simplifier could report a delta
   within tolerance while storing the wrong `original_area`.

2. **Cross-pipeline reconciliation** — the export checks were structural
   (space counts, opening refs, positivity) but never verified that the
   canonical model's wall areas matched the numbers written to gbXML.

## Decision

We add two new verification steps to the conservation-law layer of the
validation battery:

### 1. simplify_budget: re-run verification

In `_check_simplify_budget`, after the self-reported delta check passes,
we re-run the full simplification pipeline on the raw space polygons:

```python
# Re-verify the simplifier's stored original area
raw_polys = [sp.polygon_m for sp in ctx.model.spaces.values()
              if getattr(sp, "polygon_m", None) is not None]
ring = footprint_from_regions(raw_polys)
wall_height = ctx.model.levels[0].wall_height_m
fresh = simplify_ring(ring, tol=sres.tol, wall_height=wall_height)

if abs(fresh.original_area - sres.original_area) / sres.original_area > 0.001:
    # flag error: stored original_area does not match independent computation
```

If the independently computed original area differs from the simplifier's stored
value by more than 0.1%, the check fails with a specific error message.
This catches bugs in `simplify_ring`, in `footprint_from_regions`, or in
the area formula itself.

### 2. gbXML wall-area cross-pipeline reconciliation

A new check `_check_gbxml_wall_areas` parses the exported gbXML file,
extracts each `ExteriorWall` surface's area from its `RectangularGeometry`
(`length = distance(bottom-left, bottom-right)`) and the adjacent space's
`wall_height_m`, sums them, and compares against the canonical model total:

```python
canonical = sum(w.area_m2 for w in ctx.model.envelope)
rel_err   = abs(gbxml_total - canonical) / canonical

if rel_err > ctx.tol_envelope (default 2 %):
    # flag error: gbXML export does not match canonical model
```

This closes the gap between structural export checks and semantic verification.

## Consequences

- **New check count**: BATTERY grows from 28 to 29 checks.
- **Tolerance**: The cross-pipeline tolerance is set to 2 % (slightly looser than
  the 1 % intra-pipeline envelope tolerance) to absorb minor rounding in
  XML serialization.
- **No gbXML path**: If no gbXML file is provided, `_check_gbxml_wall_areas`
  skips — this is the correct behavior for pipelines that do not export gbXML.
- **Defect injection**: A corresponding `break_gbxml_wall_areas` helper is added
  to `tests/model_factory.py` for parametrized defect testing.

## References

- Issue #188: `validate.py` conservation law coverage is incomplete
- `validate.py:_check_simplify_budget`
- `validate.py:_check_gbxml_wall_areas`
- `docs/validation.md` — Conservation laws section
