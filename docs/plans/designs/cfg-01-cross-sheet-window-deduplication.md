# CFG-01: Cross-Sheet Window Deduplication

**Decision**: Deduplicate window sightings across elevation sheets by tag + geometric proximity before creating `SpaceOpening` entries.

**Status**: Completed

**Date**: 2026-09-23

**Source**: Issue #1 (migrated from `docs/design-docs/open-issues.md`)

---

## Context

Two elevations of the same facade are linked in **separate model runs**. If the same window appears in both, it is linked twice (two `SpaceOpening` entries in the same space, one per elevation run).

## Decision

Implement deduplication via `_dedupe_space_openings()` in `link.py`. Entries with matching tag + overlapping `host_interval_m` (within `OPENING_DEDUP_TOL_M = 0.15 m`) are merged into a single `SpaceOpening` carrying compound provenance (`sheet_id1+sheet_id2`) and the higher confidence.

## Implementation

- `link.py` — `_dedupe_space_openings()` function
- `docs/link.md` §"Cross-sheet window dedup is approximate" — tolerance documentation
- `validate.py` — `_check_window_double_link()` validation safety net

## Validation

The safety net detects pre-dedup double-links by flagging windows in the same space that share a tag and have overlapping `s_center_m` positions. This catches cases where dedupe tolerance may be too tight or two distinct sightings were not recognized as the same window.

## Approach

Dedupe by tag (primary key for tagged entries) + geometric proximity for untagged entries. The 0.15 m center-distance tolerance balances false positives against false negatives.
