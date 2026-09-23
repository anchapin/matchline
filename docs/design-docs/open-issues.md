# open-issues.md — known open issues and their workarounds

Known limitations with documented workarounds or downstream fixes. Not bugs — design
constraints acknowledged by the team. See also `docs/jesse.md` for the thick-stroke
pre-processing reference implementation.

---

## 1. Cross-sheet window deduplication (elevations)

**Location**: `link.py` + `docs/building_model.md`

Two elevations of the same facade are linked in **separate model runs**. If the same window appears in both, it is linked twice (two `SpaceOpening` entries in the same space, one per elevation run).

**Status**: ✅ **ADDRESSED** — deduplication is implemented via `_dedupe_space_openings()` in `link.py`. Entries with matching tag + overlapping `host_interval_m` (within `OPENING_DEDUP_TOL_M = 0.15 m`) are merged into a single `SpaceOpening` carrying compound provenance (`sheet_id1+sheet_id2`) and the higher confidence.

**Validation safety net**: `_check_window_double_link()` in `validate.py` detects pre-dedup double-links by flagging windows in the same space that share a tag and have overlapping `s_center_m` positions. This catches cases where dedupe tolerance may be too tight or two distinct sightings were not recognized as the same window.

**Approach**: Dedupe by tag (primary key for tagged entries) + geometric proximity for untagged entries. The 0.15 m center-distance tolerance is documented in `docs/link.md` §"Cross-sheet window dedup is approximate".

**Owner**: @alex
**Timeline**: Completed (validation check added 2026-09-23)
**Acceptance criteria**: Two `SpaceOpening` entries with identical `(tag, sill_m, s_center_m, host_facade)` on the same space are reduced to one after `_dedupe_space_openings()`; `_check_window_double_link` emits `error` if such duplicates exist before dedupe.

---

## 2. IFC Tier 1: space attachment

**Location**: `ifc_import.py` + `docs/ifc_import.md`

Tier 0 recovers wall geometry, openings, and dimensions **without** `IfcRelSpaceBoundary`. Openings are NOT attached to spaces in Tier 0. Space assignment (Tier 1) requires geometric adjacency inference — an open question.

**Status**: Open. Tier 0 is implemented and tested.

**Workaround**: Use the drawing import path for room-linked takeoffs; use IFC only for geometry and envelope.

---

## 3. Thick-stroke skeleton invariants

**Location**: `jesse.py` + `REPRODUCTION_NOTES.md` + `docs/jesse.md`

The Zhang-Suen skeleton invariants (endpoints, junctions, holes) are resolution-sensitive. Thick-stroke rasterizations produce skeleton spurs that break the Table 9.4 signatures.

**Status**: ✅ **ADDRESSED by CFG-02** — see `docs/jesse.md` for the full pre-processing
pipeline including adaptive thresholding and morphological opening/closing parameters
tuned to DPI. The `is_complex_invariant()` function flags ambiguous signatures for
human review.

**Impact on pipeline**: Without correct pre-processing, skeleton invariants on real
sheets will not match Table 9.4 signatures, causing GD&T classification to fail.

**Workaround**: Apply adaptive Gaussian binarization (`blockSize`, `C`) + morphological
`MORPH_CLOSE/MORPH_OPEN` with kernel size tuned to stroke width before skeletonization.
See `docs/jesse.md` §"Thick-Stroke Skeleton Pre-processing" for the full reference
implementation and parameter tuning guide.

---

## 4. Complex GD&T invariant rows (Table 9.4)

**Location**: `REPRODUCTION_NOTES.md` §[U5] + `jesse.py` (`is_complex_invariant()`)

Complex invariant rows (e.g., True Position E=4, J_T=4, J_X=1, b₁=1) could not be reproduced from the paper text alone. The junction convention for crossing glyphs is genuinely underspecified.

**Status**: ✅ **ADDRESSED by CFG-03** — see `jesse.is_complex_invariant()`.
Rows exceeding the simple Table 9.4 envelope (endpoints > 4, any junction > 1, holes > 2,
or compound T+X junctions) are flagged with `kind="gd_complex_row"` in the review queue
rather than being silently accepted or rejected.

**Impact on pipeline**: Without this check, complex GD&T rows produce irreproducible
results that are silently classified with incorrect confidence.

**Workaround**: Complex rows are routed to `gd_complex_row` review items for human
validation. Treat complex-row GD&T results as indicative, not authoritative. Validate
against known-good reference drawings.
