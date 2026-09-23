# CFG-02: Thick-Stroke Skeleton Pre-processing

**Decision**: Adopt adaptive Gaussian binarization + morphological opening/closing as pre-processing before skeletonization to handle thick-stroke architectural drawings.

**Status**: Completed

**Date**: 2026-09-23

**Source**: Issue #3 (migrated from `docs/design-docs/open-issues.md`)

---

## Context

The Zhang-Suin skeleton invariants (endpoints, junctions, holes) are resolution-sensitive. Thick-stroke rasterizations produce skeleton spurs that break the Table 9.4 signatures used for GD&T classification.

## Decision

Apply adaptive Gaussian binarization (`blockSize`, `C`) + morphological `MORPH_CLOSE/MORPH_OPEN` with kernel size tuned to stroke width before skeletonization.

## Implementation

- `jesse.py` — pre-processing pipeline
- `docs/jesse.md` — full reference implementation and parameter tuning guide
- `is_complex_invariant()` function flags ambiguous signatures for human review

## Validation

Without correct pre-processing, skeleton invariants on real sheets will not match Table 9.4 signatures, causing GD&T classification to fail. The `is_complex_invariant()` function provides a safety net.

## Impact

Addresses skeleton spur false positives on thick-stroke drawings. Complex or ambiguous signatures are routed to review queue as `gd_complex_row` items.
