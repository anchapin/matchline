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

---

# Part II — Future directions

A categorized backlog from an eight-persona review (legal, marketing, product
design, project management, software architecture, MEP engineering, energy
modeling, incumbent vendor) on 2026-09-19. These are *not* sequenced or
numbered: they are directions to pull into the numbered roadmap as priorities
crystallize. Items marked with multiple-persona support independently came up
in more than one review.

## Trust, legal, professional

- Professional-use disclaimer and E&O framing ★
- Stampable, dated sign-off report with source/revision traceability ★
- Versioned data-contribution agreement for the flywheel ★
- Training-data license ledger and quarantine for noncommercial data/weights ★
- CLA or DCO for code contributions
- Privacy/security-sensitive-building policy and guaranteed local-only mode
- GUI/service terms governing liability and data retention/deletion
- Defensive publication of novel methods
- Legal review of professional-licensure / "practicing engineering" risk

## Product/UX and drawing-set reality

- Drawing-set ingestion via cover-sheet index/title-block parsing
- Revision/addenda tracking and takeoff diffs
- Match lines, split sheets, and enlarged-plan deduplication
- Per-project legend/tag/convention learning ★
- SD/DD/CD design-phase awareness
- Explicit area definitions: GSF, BOMA rentable, program/assignable
- Worst-first, keyboard-first review queue with bulk actions
- Correction history, undo, and revert-to-automatic output
- Loud, specific failure modes for bad inputs
- Progressive takeoff mode vs full-BEM mode
- Export package: model + one-page trust report + editable decisions file + share-back preview ★

## BEM last mile

- ASHRAE 90.1 Appendix G perimeter/core thermal zoning
- Below-grade detection and correct ground/outdoor boundaries
- Space-use classification to cited load/schedule templates
- Versioned construction library with cited U-values; never fabricate missing values
- Blocking OpenStudio importer round-trip gate
- Inter-story surface matching and atrium/shaft consistency

## MEP

- Mechanical schedule parsing and system-type classification
- Engineering reconciliation gates and discipline-specific accuracy bars
- Lighting controls and control zones
- Riser/one-line topology parsing
- First-class system entities linked to rooms
- Plumbing fixture takeoffs and service-water-heating inputs
- Architect-facing plan-vs-schedule coordination QA

## Go-to-market

- Positioning: "auditable extraction, not AI magic"; feed incumbents rather than replace them
- Real-building benchmark on 3–5 commercial buildings ★
- Five-minute demo, bundled sample project, results gallery ★
- Quiet beta with friendly firms before public launch
- Community presence through Unmet Hours, LinkedIn, IBPSA/ASHRAE
- Visible monthly flywheel/model changelog
- Rename before public launch because WiSARD is not the production detector

## Strategy

- Revit/AutoCAD plugin
- Enterprise trust package: SSO, audit logs, isolation, SOC 2 story, air-gapped/on-prem mode
- Standalone BIM import health score
- Platform-risk hedge: keep IFC and web GUI first-class
- Deliberate open-core commercial model
- Publish validation checks as an industry benchmark

## Process

- Define Monday validation exit criteria before fine-tuning
- Define v0.1.0 scope and release cadence
- Maintain an in-repo risk register
- Add a dependency/sequencing map
- Add explicit non-goals and parked ideas
- Set a review-bandwidth budget, maximum PR size, review cadence, and SLA ★

★ = raised independently by multiple personas — highest signal.
