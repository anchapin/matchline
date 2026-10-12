# CFG-05: IFC Tier 1 along-wall direction probe

**Decision**: Proposed (awaiting maintainer review, HITL). Keep the current identity-first, position-then-length matcher in `ifc_import._wall_direction_from_envelope`, keep refusing to guess on a tie, and make the `RefDirection` fallback an explicit, separately counted tier rather than a silent substitute.

**Status**: Draft

**Date**: 2026-10-06

**Source**: Issue #665 (supersedes #655). Blocks #666.

---

## Problem

Tier 1 binds an IFC opening to the space(s) either side of its host wall. Placing the opening needs the wall's along-wall unit direction: the opening's `s_center_m` is measured from the wall origin along that axis, and the side test that picks the space depends on its sign. A wrong direction puts the reconstructed point on the wrong side of the wall or off the wall entirely, which mis-attributes the opening (#505). Wrong attachment is worse than no attachment, because it passes the closure checks silently while an unattached opening is visible in the review queue and `opening_attachment_summary` (#512).

## Algorithm (as implemented today, proposed to keep)

Inputs: the wall `BimElement` (`placement_m`, `length_m`, GlobalId, raw IFC entity) and `model.envelope` (Tier 0 segments, built from walls).

1. **Identity.** If a Tier 0 envelope segment carries this wall's GlobalId, use its direction, oriented to start at the wall's placement. This resolves collinear facade runs split per space, where neighbouring segments share the wall's origin and often its length.
2. **Position.** Otherwise, candidate edges are those with an endpoint within `_ENVELOPE_TOL_M` (0.1 m) of the wall placement. Each candidate's direction is oriented away from the placement, so the sign comes from geometry, never from export order.
3. **Length tie-break.** Rank candidates: rank 0 if `|edge_len - wall.length_m| < 0.1 m`, else rank 1. Keep only the best rank.
4. **Refuse to guess.** If the best rank holds more than one distinct direction, return `ambiguous_tie`. If there are no candidates, return `no_envelope_edge`.
5. **Fallback.** On either failure, use the wall's own `ObjectPlacement.RelativePlacement.RefDirection` (normalised XY). If that is also missing or degenerate, the openings stay unattached with reason `no_ref_direction`.

### Change made in #666 (approved by the maintainer)

Today a successful `RefDirection` fallback attaches with the same confidence as an envelope match. I'd record it distinctly: attach, but stamp the opening provenance `method="ifc_ref_direction"` with confidence 0.85 (above `REVIEW_CONFIDENCE` 0.80, so it is not auto-queued) and count it in a new `opening_attachment_summary.ref_direction_fallback` counter. RefDirection is authored by the exporter and is usually right, but it is the one path where we trust the file over our own geometry, so it should be observable. The alternative is to queue every fallback for review; that is safer but would flood the queue for exporters that never write envelope-consistent placements.

## Test-case matrix

| # | Case | Expected | Why |
|---|---|---|---|
| 1 | Wall with its own GlobalId-tagged envelope segment | attach via identity | Strongest evidence; immune to collinear ties |
| 2 | Rectangle room, no identity tag, one edge starts at the placement with matching length | attach via position | The common Tier 0 case |
| 3 | Collinear facade split per space: two segments meet at the placement, equal length, opposite directions, no identity tag | refuse: `ambiguous_tie`, then RefDirection fallback | Position and length both tie; guessing flips the side |
| 4 | Two edges touch the placement, one length-matches (rank 0), one doesn't | attach via the rank-0 edge | Length breaks a genuine position tie |
| 5 | Two edges touch the placement, neither length-matches, different directions | refuse: `ambiguous_tie` | Rank 1 tie carries no evidence either way |
| 6 | Interior wall with no envelope edge near its placement, valid RefDirection | attach via RefDirection fallback (proposed: counted, confidence 0.85) | Interior walls are never in the envelope |
| 7 | Interior wall, no envelope edge, no RefDirection (or zero-length vector) | unattached, `no_ref_direction`, review item | Nothing left to trust |
| 8 | Placement 0.08 m from an edge endpoint (inside tolerance) | attach | Exporter rounding |
| 9 | Placement 0.15 m from the nearest endpoint (outside tolerance) | `no_envelope_edge`, then fallback | Beyond rounding; likely a different wall |
| 10 | `length_m` missing or empty `placement_m` | `no_envelope_edge`, then fallback | Cannot match by position |

Cases 1-5 and 7 are covered by the existing #505/#512 tests in spirit; #666 should add explicit fixtures for 6, 8 and 9, and assert the summary counters for 3, 5, 6 and 7.

## Threshold rationale

`_ENVELOPE_TOL_M = 0.1 m` is used for both the endpoint match and the length match.

- It is well above coordinate noise from IFC exporters (mm-level float rounding, unit conversion from feet) and Tier 0's own segment construction, which reuses wall placements exactly.
- It is below a typical interior wall thickness (0.1-0.2 m) and far below any room dimension, so an endpoint within 0.1 m of the placement is almost certainly the same corner, not a neighbouring wall.
- For length, 0.1 m distinguishes the short and long sides of any realistic room while tolerating exporters that report centreline vs. face lengths (a difference of one wall thickness at most at each end is the main risk; see open question 2).

No separate angular threshold is needed: directions are compared as a set of distinct vectors from distinct edges, and two edges from one point only collide in direction when they are the same edge.

## Decisions (resolved for #666)

1. RefDirection fallback attaches at confidence 0.85, method `ifc_ref_direction`, counted in `ref_direction_fallback`. Not routed to review.
2. Length tie-break tolerance stays 0.1 m until a real IFC shows the centreline/face problem.
3. `space_opening_attachment` is warn severity, per level. Never error.
