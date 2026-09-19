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

## Research tracks (near-term)

These sit alongside the numbered items above: time-boxed research spikes whose
outcomes decide which detection technology we productionize. They are labelled
R1/R2 (not numbered) so they merge cleanly next to the numbered roadmap items.

### R1. Vector-native geometry parsing: read drawings as vectors, not pixels

Context (2026-09-19): Kamai (kamai.io) is an Israeli startup doing exactly
this — proprietary in-house "geometric models" that read vector geometry
directly from PDF/CAD drawings (no rasterization, claimed sub-mm measurement),
return typed structured output (rooms, wall polygons, openings with
widths/relations/tags like "D-01"), and expose it as a public REST API. Their
approach validates the direction; this track is to build our own reproducible,
auditable version and know when it beats the raster path.

Candidate approaches:
- **Know the input alphabet first.** Survey what a plan sheet actually exposes
  as vector primitives (lines, arcs, polylines, hatches, positioned text) via
  parsing libraries (pdfplumber/pypdf for PDFs, ezdxf for DXF/DWG). Decide the
  model only after the primitive inventory is understood.
- **VecFormer as the leading learned candidate.** VecFormer (NeurIPS 2025) is
  a transformer over *vector line primitives* rather than pixels — potentially
  both more accurate and more auditable than raster detectors on vector PDFs.
  Evaluate it as the vector-native counterpart to YOLOv11+SAHI.
- **Spike prototype.** Parse vector line primitives from one real PDF plan
  sheet; extract entities (walls as parallel-line pairs, openings as gaps in
  runs, rooms as closed loops); compare extraction accuracy head-to-head
  against the raster YOLO+SAHI path on the same sheet. Time-boxed.
- **Benchmark target.** Replicate Kamai's AEC-Geometric-Bench protocol:
  object F1 at IoU 0.50 across doors/windows/fixtures, 8 classes. Accuracy
  target: **meet or beat Kamai's published 0.929** with a reproducible,
  auditable vector-native pipeline.
- **Licensing caveat.** The bench data is CC BY-NC 4.0 — usable for
  *evaluation* but **not for training weights that will be commercially
  distributed**; NC-trained weights stay quarantined per the legal gating
  already flagged. Also, Kamai's number is vendor-published on vendor-owned
  data (bias openly disclosed); reproduce with their public 15-sheet scorer,
  and report our numbers on the same protocol plus our own drawing-set
  results.

### R2. Modular detection-provider interface

Context: detection backends are currently hard-wired (YOLOv11+SAHI). If R1
shows a vector-native path wins, or a third-party API becomes worth paying
for, swapping backends must be a config change, not a rewrite.

Candidate approaches:
- **Define the provider contract.** A `DetectionProvider` interface that takes
  a sheet (raster and/or vector) and returns typed entities with geometry,
  class, confidence, and provenance — the same schema the canonical model
  already expects — so simplify → validate → export stay backend-agnostic.
- **Backends behind the contract:** (1) the current YOLOv11+SAHI raster
  detector; (2) the R1 vector-native prototype; (3) third-party APIs —
  evaluated candidates named below, with Kreo as the only verified Kamai
  alternative and an open-source self-hosted option.
- **Evaluated third-party candidates.**
  - *Kamai* — cost-blocked for now. Self-serve API access starts at the
    Contractor tier ($800/mo, ~800 sheets, ~$1–2/sheet) — too expensive for a
    quick evaluation. Evaluate Kamai integration **only if a free trial or
    partner evaluation access is obtained** (their stated go-to-market is
    B2B2B/partner sales; a sales conversation is the realistic path to eval
    access). In the meantime, the R1 track doubles as our own research into
    what their geometric models do.
  - *Kreo Software — Auto Measure API* (https://www.kreo.net/features/api) —
    the only other verified public drawing-in → structured-geometry-out REST
    API. Accepts PDF/DWG/DXF/DWF/PNG/JPG/TIFF; returns walls (ext/int),
    doors, windows, rooms/areas (GEA/GIA/NIA) as JSON with per-object pixel
    contours, OCR text, area/perimeter/length/thickness. Example pricing
    ≈$0.85/request (~$670/mo at 500 requests); sandbox + limited free
    requests via sales. **Unverified:** data retention, privacy/DPA terms,
    hosting region, and on-premise options — resolve before any production
    use.
  - *FloorPlanAnalyzer* (https://github.com/mageaustralia/FloorPlanAnalyzer) —
    open-source YOLOv8 + OCR, self-hosted (self-hosting solves the
    privacy/on-prem story outright). Accuracy on commercial construction
    drawings unverified; license terms unverified.
- **Excluded (with reason).** Togal.ai: no verified public/self-serve
  detection API (partner-managed integrations only). Rasterscan: an API and
  claimed on-premise exist, but a paying customer reports API failures with
  no support response — too risky for now. magicplan/CubiCasa: capture
  workflows (phone scans), not arbitrary-drawing parsers. Autodesk ACC
  Takeoff, STACK, Bluebeam: public APIs exist but cannot trigger AI
  detection headlessly (read/file-management APIs only).
- **Provider-agnostic conformance.** Run the same AEC sheets through each
  backend under the same F1-at-IoU-0.50 protocol; report per-backend deltas in
  the audit artifact. Backend disagreement becomes a cross-check, not just a
  number.
