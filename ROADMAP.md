# Roadmap: hard problems

Future work items from Alex (2026-09-19), with initial technical framing.
None of these are solved; this doc records the problem statements and candidate
approaches so design can start from something concrete.

A recurring theme: several of these introduce *known, directional bias*
(overestimated air volume, overestimated floor area). The project's answer is
the same everywhere — don't pretend the bias is zero; measure it, attribute it
to the convention that caused it, and report it. The validation battery
(`validate.py`) is the natural home for these "convention bias" numbers.

## 1. Wall thickness → planar BEM surfaces

Current practice: exterior face of exterior walls, centerline of interior
walls. Overestimates zone air volume; few good alternatives exist.

Candidate approaches:
- **Decouple area from volume at export.** gbXML and EnergyPlus both allow an
  explicit space/zone volume independent of the surface geometry. Export
  surfaces on the exterior-face convention (envelope area stays correct) but
  write the interior-face-derived volume into the space volume field. Both
  numbers right, one convention each.
- **Keep thick walls internally.** Model walls as polygons with thickness in
  the canonical model; derive planar surfaces per convention at export time.
  Lets us switch conventions (interior-face, centerline, exterior-face) without
  re-extracting, and lets validation *compute* the bias (e.g. "air volume
  +3.2% under exterior-face convention") instead of hand-waving it.
- Validation: add a `convention_bias` check reporting volume delta between
  interior-face and exterior-face derivations per level.

## 2. Sloped-roof simplification

Complex roofs (hips, valleys, dormers) need the same treatment as the
area-budgeted wall simplifier — but slope drives solar gain, so a pure
geometric area budget is insufficient.

Candidate approaches:
- Extend `geometry_simplify.py` with a roof mode: merge near-coplanar facets
  within an angle tolerance, replace multi-facet hips with equivalent
  single-slope planes.
- Error metric must be **solar-weighted**, not just geometric: preserve
  orientation-weighted aperture (area × cos(incidence) integrated over
  representative sun positions), not just m².
- Validation: add a solar-aperture conservation check alongside the area budget.

## 3. Skylights

Schema, extraction, and tests for roof glazing.

Candidate approaches:
- Extend `SpaceOpening` with `category="skylight"`, `host="roof"`,
  plus tilt/azimuth from the roof plane.
- gbXML has native `Skylight` elements; IFC via `IfcWindow` with appropriate
  predefined type. Both exporters need the new path.
- Daylighting: skylights drive *top-lighting* zones — different geometry than
  the sidelighting rules in `elevation_windows.py` (roughly: floor area under
  skylight + perimeter spread by ceiling height).
- Validation: skylight area ≤ host roof area per space; skylight count
  reconciles against roof-plan annotations.
- Tests: synthetic roof with known skylight layout, same pattern as the
  elevation-window defect-injection tests.

## 4. Non-room polygons: shafts, closets, unnumbered spaces

Not every enclosed polygon is a room. Current practice: split duct shafts
between adjacent spaces; merge elevators/janitor closets into the adjacent
corridor. Both practices inflate space floor areas (affects LPD, loads).

Candidate approaches:
- **Classification first.** Signals: presence of a room-number label (rooms
  have them; shafts/closets often don't), polygon area thresholds, aspect
  ratio, labels on the *mechanical* plan (a polygon called out as a shaft on
  mech but unlabeled on arch is a strong signal), door count (a polygon with
  no door swing is suspicious).
- **Absorbed-area accounting.** Every merge/split records where the area went:
  "corridor C-101 absorbed 4.2 m² from closet J-3 and 1.8 m² from shaft split."
  Stored as provenance on the space. Then:
  - LPD and loads can be reported both ways (as-drawn room vs. as-modeled
    space),
  - the validation battery gets a conservation check: absorbed areas must sum
    exactly to the unassigned-polygon areas — no square foot vanishes.
- Shaft splitting needs an adjacency graph (shared wall segments between
  polygons) — a natural extension of the wall-segment work in `link.py`.
- Open question: should shafts be split by *adjacent wall length* weighting,
  or is there a better rule? Worth asking a mechanical engineer, not guessing.

## 5. Overhangs, fins, and shading surfaces

Balconies, fins, and other projections matter for BEM as shading.

Candidate approaches:
- Detection: projections beyond the exterior wall face on plan/elevation.
  (CMP Facade already labels balconies as class 7 — a training signal exists.)
- Generate shading surfaces with depth = projection distance from the facade;
  attach to host wall/window. gbXML supports shading surfaces natively;
  EnergyPlus via `Shading:Zone:Detailed`.
- Validation: every shading surface must reference an adjacent host
  wall/window; warn on shading with no host (likely a detection error).
- Keep separate from the envelope area budget — shading is not envelope.

## 6. Per-space exterior wall constructions

Different wall types on different envelope portions; the correct overall
U-value is per space, area-weighted over its wall segments.

Candidate approaches:
- Schema: every exterior wall segment carries a `construction_id`; each
  space's envelope references its segments (segments already exist in
  `link.py` — this extends them).
- Per-space area-weighted U-value: Σ(Uᵢ × Aᵢ) / ΣAᵢ, computed in the takeoff
  layer and exported per space.
- Detection: wall-type legends on elevations are usually **hatch patterns** —
  a texture/pattern-classification problem per wall segment, analogous to the
  symbol legend-learning idea in the detector plan.
- Validation: every exterior wall segment has exactly one construction;
  every construction id resolves in the schedule; per-space weighted U-value
  is recomputed independently in the check (don't trust the rollup).
- Unresolved: how wall-type transitions *within* a single wall run are marked
  on real drawings (notes? detail callouts?) — needs real-sheet observation
  before the detector design is fixed.

## Cross-cutting notes

- Items 1 and 4 both create measured overestimation. The unified answer is a
  "convention report" emitted with every export: volume bias %, absorbed-area
  table, area-budget drift — all in one place, all auditable.
- Items 2, 5, 6 all need the wall/roof *segment* as a first-class entity with
  attributes (construction, tilt, host). The segment work in `link.py` is the
  foundation; it should be promoted from an internal detail to a schema-level
  concept.
- Item 4's classifier is the only one here that needs real labeled data before
  design can start; the rest can be prototyped synthetically.

## 7. IFC/BIM as a second input frontend

The drawing pipeline and a BIM pipeline should converge on the same canonical
model. Alex's point: the surface-count reduction and validation logic are
needed in BIM→BEM just as badly, and the industry hasn't handled it well.

Candidate approaches:
- **Two frontends, one core.** The architecture already has the right shape:
  source-specific extraction feeding the source-agnostic canonical model
  (`building_model.py`), then shared downstream (simplify → validate →
  export). IFC becomes a second frontend: parse with IfcOpenShell (already
  vendored for export — reading is the same library in reverse) and populate
  spaces, levels, openings, constructions. Everything downstream is reused
  unchanged.
- **The simplifier is the shared asset.** BIM geometry is often *more*
  over-faceted than drawings (curtain-wall mullions as individual surfaces,
  every stud modeled). The area-budgeted simplifier applies directly, and the
  1–5% budget gives BIM→BEM something it lacks today: a stated, checked
  fidelity bound instead of silent decimation.
- **The validation battery is arguably more valuable on BIM input.** BIM→BEM
  failures are silent: unenclosed zones, sliver spaces, dropped space
  boundaries. Area/volume conservation and closure checks catch exactly these.
- **The hard part is space boundaries.** `IfcRelSpaceBoundary` quality varies
  wildly in the wild — this is where Autodesk and others struggle too. Apply
  the project's review-queue philosophy: low-confidence boundary mappings get
  flagged, never silently accepted or silently dropped.
- **Design the frontend in tiers so space boundaries are optional, not
  load-bearing** (Alex's question, 2026-09-19):
  - *Tier 0 — no boundaries needed.* `IfcSpace` entities carry identity
    (name/number) and their own 3D geometry. `IfcWall/Slab/Roof/Window/Door`
    carry geometry, type info, and material layer sets. `IfcOpeningElement` +
    `IfcRelVoidsElement`/`IfcRelFillsElement` recover window/door placement in
    walls. `IfcRelAggregates` + `IfcRelContainedInSpatialStructure` give the
    storey structure. Tier 0 alone already delivers takeoffs (wall/window/door
    areas), the room list, and the envelope element inventory.
  - *Tier 1 — geometric inference.* Space↔element adjacency via
    proximity/clash queries (walls intersecting the space solid's expanded
    boundary); interior-vs-exterior classification via outward ray tests. Every
    inferred association carries method + confidence + provenance, and
    ambiguous cases go to the review queue. Audited inference, not perfect
    inference.
  - *Tier 2 — authored boundaries as cross-check.* When `IfcRelSpaceBoundary`
    *does* exist, treat it as untrusted input: run Tier 1 independently and
    flag disagreements for review. Inferred-vs-authored agreement becomes a
    validation check rather than a load-bearing dependency.
- **Bonus: IFC solves the wall-thickness problem analytically.**
  `IfcMaterialLayerSet` gives true per-layer thickness — interior and exterior
  faces are derivable, not convention-guessed. This feeds roadmap item 1
  directly: the "derive faces per convention at export" design gets real
  thickness data on the BIM path.
- **Thermal properties remain a gap on both paths.** `Pset_MaterialThermal`
  is usually absent in practice; both frontends need the same
  material→property lookup table.
- **Testing bonus:** IFC round-trip gives ground truth for the drawing
  pipeline. Where both an IFC and drawings of one building exist, the two
  frontends should produce models that agree within the validation tolerances
  — a cross-frontend consistency check.
- **Ecosystem note:** abstract.build (Simon's company) does BIM data analysis
  for thermal simulations — adjacent and complementary, not competitive. They
  start from BIM; wisard-bem starts from drawings and *could* start from BIM.
  Worth showing Simon once the IFC frontend exists; the validation battery and
  simplifier are the components most likely to interest him.

## 9. Opt-in data flywheel: users share corrections back to improve the models

Labeled commercial drawings are the scarcest resource in this project —
synthetic→real transfer is weak, and open datasets don't cover real commercial
practice. The way out is a data flywheel: users opt in to sharing data back,
the models improve, the tool gets better for everyone. Rising tide lifts all
boats — but only if the incentive and the privacy story are both real.

- **Share corrections, not drawings.** The review-queue GUI (item 8) generates
  human-verified labels as a byproduct of normal use. A bundle of "200 verified
  door/window boxes" is a far smaller privacy surface than a full sheet, and
  it's the highest-value training signal. Raw PDFs never need to leave the
  user's machine.
- **Privacy by design.** Opt-in per project, default off. Anonymization pass
  before upload: strip title blocks, generalize or drop room names, remove
  client identifiers. Show the user exactly what will be uploaded before it
  goes — a preview they approve, not a black box.
- **Licensing.** A clear training-use grant at opt-in: contributors keep
  ownership of their drawings; the project gets the right to train on the
  anonymized correction bundles. Get this reviewed before building the pipe.
- **Make the loop visible.** "The tool gets better someday" doesn't motivate.
  "Your 200 corrections improved door detection 3.2% in this month's model"
  does. Credit contributing firms, publish the metric deltas, and consider
  giving contributors the improved model first with public release after.
- **Pipeline shape.** Incoming bundles → quarantine → spot-check quality →
  split into training pool and held-out eval (per-contributor provenance for
  bias tracking). Contaminated or low-quality bundles get rejected with a
  reason, not silently absorbed.
- **Sequencing.** This comes after the GUI review step (item 8), which is what
  generates the labels. No sharing pipe before there's something to share.
