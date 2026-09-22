# open-issues.md — open technical questions

Known limitations with documented workarounds or downstream fixes. Not bugs — design constraints acknowledged by the team.

---

## Cross-sheet window deduplication (elevations)

**Location**: `link.py` + `docs/building_model.md`

Two elevations of the same facade are linked in **separate model runs**. If the same window appears in both, it is linked twice (two `SpaceOpening` entries in the same space, one per elevation run).

**Status**: Open. Cross-sheet observation deduplication (recognizing two sightings of the same window) is not yet implemented.

**Workaround**: Link each elevation in a separate run and dedupe by window tag or geometric proximity in a post-processing step.

---

## IFC Tier 1: space attachment

**Location**: `ifc_import.py` + `docs/ifc_import.md`

Tier 0 recovers wall geometry, openings, and dimensions **without** `IfcRelSpaceBoundary`. Openings are NOT attached to spaces in Tier 0. Space assignment (Tier 1) requires geometric adjacency inference — an open question.

**Status**: Open. Tier 0 is implemented and tested.

**Workaround**: Use the drawing import path for room-linked takeoffs; use IFC only for geometry and envelope.

---

## Thick-stroke skeleton invariants

**Location**: `jesse.py` + `REPRODUCTION_NOTES.md`

The Zhang-Suen skeleton invariants (endpoints, junctions, holes) are resolution-sensitive. Thick-stroke rasterizations produce skeleton spurs that break the Table 9.4 signatures.

**Status**: Known limitation. Clean 1px glyphs reproduce Table 9.4 exactly. Real drawing sheets with variable stroke width may not.

**Workaround**: Pre-process real sheets with thinning/binarization tuned to the target resolution. Flag glyphs with ambiguous invariant signatures for human review.

---

## Complex GD&T invariant rows (Table 9.4)

**Location**: `REPRODUCTION_NOTES.md` §[U5]

Complex invariant rows (e.g., True Position E=4, J_T=4, J_X=1, b₁=1) could not be reproduced from the paper text alone. The junction convention for crossing glyphs is genuinely underspecified.

**Status**: Known limitation. The WiSARD paper's 99.93% GD&T figure rests partly on conventions the paper does not disclose.

**Workaround**: Treat complex-row GD&T results as indicative, not authoritative. Validate against known-good reference drawings.
