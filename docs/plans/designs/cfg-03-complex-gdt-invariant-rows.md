# CFG-03: Complex GD&T Invariant Rows

**Decision**: Flag complex GD&T invariant rows rather than silently classifying them, routing them to human review.

**Status**: Completed

**Date**: 2026-09-23

**Source**: Issue #4 (migrated from `docs/design-docs/open-issues.md`)

---

## Context

Complex invariant rows (e.g., True Position E=4, J_T=4, J_X=1, b₁=1) could not be reproduced from the paper text alone. The junction convention for crossing glyphs is genuinely underspecified.

## Decision

Rows exceeding the simple Table 9.4 envelope (endpoints > 4, any junction > 1, holes > 2, or compound T+X junctions) are flagged with `kind="gd_complex_row"` in the review queue rather than being silently accepted or rejected.

## Implementation

- `jesse.is_complex_invariant()` — detection function
- Complex rows routed to `gd_complex_row` review items for human validation

## Validation

Complex-row GD&T results should be treated as indicative, not authoritative. Validate against known-good reference drawings.

## Impact

Without this check, complex GD&T rows produce irreproducible results that are silently classified with incorrect confidence.
