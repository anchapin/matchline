# UBIQUITOUS LANGUAGE — matchline

The shared domain language for this repo, in the Domain-Driven Design
sense: one name per concept, used the same way in briefs, issues,
code, tests, and docs — by humans and by agent workers alike.
Definitions are traced to the code and docs on `develop`
(`ARCHITECTURE.md`, `AGENTS.md`, `CONTRACT.md`, `README.md`,
`building_model.py`, `docs/design-docs/core-beliefs.md`,
`docs/measurement_layer.md`, `docs/pipeline.md`,
`docs/geometry_simplify.md`, `docs/validation.md`). Terms that could
not be traced to a source are in **Open questions**, not in the
glossary — resolve them there rather than inventing a definition
in a brief.

**How to use it:** One name per concept, used the same way in briefs,
issues, code, tests, and docs. Matchline's whole job is a translation —
architectural-drawing language → BEM language — so a sloppy term here
becomes a wrong number in an export. If a word is not here, check the
*Don't say / say instead* table before inventing one.

Companion files: `CONTRACT.md` (never-regress / hard stops),
`docs/design-docs/core-beliefs.md` (the principles behind half these
definitions). Scope reminder from Alex: the deliverable is **rough
quantity takeoffs** — symbol spotting + schedule parsing over pixel
measurement, except where a term below says otherwise.

---

## Bounded contexts — the map

| Context | Where it lives | Its language is about |
|---|---|---|
| Drawing / sheet | input side, `synth/`, `datasets_adapter.py` | What is printed on a set of drawings |
| Canonical model | `building_model.py`, `link/` | The one linked building everything joins into |
| Detection | `detector/`, `symbols.py` | Finding symbols/fixtures in raster/vector sheets |
| Schedule | `datasets_adapter.py` (schedule join) | Tables that give dimensions per tag |
| Geometry | `geometry/geometry_simplify.py`, `geometry/polygon_classify.py`, `registration.py` | Shapes, coordinates, and reducing them for BEM |
| Validation | `validate/` | Conservation laws that must hold before export |
| BEM export / BIM | `bem/bem_export.py`, `bem/bem_helpers.py`, `ifc/ifc_import.py`, `ifc/ifc_export.py` | gbXML / IFC shapes of the same building |
| Review & provenance | `building_model.py` (`Provenance`, `ReviewItem`) | Why anyone should trust a number |

Pipeline in this language: drawings → symbol spotting → schedule join
→ room labeling → cross-discipline linking (arch plan = canonical
frame) → HVAC zoning → geometry simplification → validation battery →
BEM export.

---

## Drawing / sheet context

- **Sheet** — One page of a drawing set (floor plan, elevation,
  lighting plan, mechanical plan). Every extracted fact names its
  sheet (`sheet_id`). A sheet has **revisions**; facts from a newer
  revision of the same sheet supersede older ones — kept in history,
  never silently overwritten.
- **Clean digital drawing** — Matchline's input assumption: vector /
  born-digital sheets of (typically new) buildings, not degraded
  scans. Briefs must not assume scan noise the pipeline was not built
  for.
- **Arch plan / architectural floor plan** — The **canonical frame**:
  all other sheets are registered into arch-plan coordinates, and
  spaces take their identity from it. Short form in code/docs is
  *arch*, never "the main drawing."
- **Elevation** — A facade view sheet. Elevations feed *exact window
  placement* (along-wall position, sill/head heights) and facade
  takeoffs — information the plan does not carry.
- **Drawing scale / DrawingScale** — The px→meters mapping needed
  only where dimensions are measured from geometry (floor areas via
  polygons). The count × dims takeoff path needs **no** scale —
  dimensions come from the schedule. (measurement layer doc)

## Canonical model context

- **BuildingModel** — The canonical, cross-discipline-linked building
  (`building_model.py`): levels, spaces, zones, envelope, BIM
  elements, schedules, review queue, revision log. Everything joins
  into this or it does not join at all. Distinct from **BEMModel**,
  which is an export shape (below).
- **Space** — One room-sized unit *in the model*, keyed
  `"{level}-{number}"` (e.g. `L1-101`), or `{level}-UNLABELED-{k}`
  when no number was recovered. This is the primary key of the whole
  system. The drawing may call it a room; the model calls it a Space.
- **Room label** — The OCR'd name/number text found on a sheet
  (`RoomLabel`, `room_labels.py`), *before* association to a space
  polygon. A label is evidence; a Space is the conclusion. (The OCR
  stage legitimately says "room" — that is drawing language, and it
  stops at the association boundary.)
- **Level** — One floor of the building (`Level`, id like `L1`).
  First component of every Space key. Not "story" or "floor" in code.
- **Zone (HVAC zone)** — A thermal-control grouping in the model
  (`Zone`, `SpaceHVAC`). **Many-to-many with spaces**: one open
  office can span two zones; there is no zone hierarchy tree.
  ⚠ Cross-repo warning: this is *not* Fluxion's zone, though they are
  cousins — Matchline zones are traced from ducts/terminals on
  drawings; Fluxion zones are simulation balance units. A brief that
  crosses the Matchline → Fluxion handoff must say which it means.
- **Envelope / EnvelopeWall** — The building's exterior boundary;
  one `EnvelopeWall` is one exterior wall run per facade segment.
  Envelope *area* is the conserved quantity simplification must hold.
- **Opening** — A window or door in a wall. Two distinct types, do
  not merge them in prose: **SpaceOpening** (on a space's walls, from
  an elevation) and **BimOpening** (hosted in a BIM element, IFC
  Tier 0, not yet assigned to a space).
- **ComponentRef** — One detected equipment instance (diffuser,
  fixture, terminal) in canonical meters, awaiting or holding a link
  to a space.

## Detection context

- **Symbol spotting** — Finding and classifying equipment/window/door
  symbols on sheets: region in, label/tag out. The production path is
  the fine-tuned **YOLO detector** in `detector/` (its own venv).
- **WiSARD / jesse.py** — The deterministic, explainable WiSARD
  classifier paper reproduction. A **research asset, not production
  symbol spotting**, not imported by any pipeline module. Never brief
  it as "the detector."
- **Detection** — One stage-1 output record: label, schedule tag,
  score, bbox, source (`datasets_adapter.py`). A detection is a claim
  with a confidence, not yet a fact in the model.
- **SymbolLinkage** — The explicit link between a symbol instance and
  its schedule row. If there is no linkage, there is no dimension and
  no takeoff area for that instance.

## Schedule context

- **Schedule** — A table on a sheet (or its CSV interchange form)
  giving dimensions per **tag**: `{tag: width_m × height_m, category}`.
- **Tag** — The type identifier printed next to a symbol (e.g. `W-1`,
  `D1`). Tags are callouts *adjacent to* the glyph, not part of the
  glyph — the known v2 gap is tag extraction by OCR next to the bbox.
- **ScheduleEntry / ScheduleRow** — One parsed row of a schedule:
  tag → category + dimensions. `ScheduleEntry` is the adapter's
  canonical record.
- **Schedule join** — The step that joins detections to schedule rows
  by tag, enabling the takeoff. An **unmatched** detection (tag with
  no schedule entry) is reported, never silently dropped.

## Quantity takeoff context

- **Takeoff / quantity takeoff** — The area rollup:
  **count × scheduled dimensions**, summed per category into
  `TakeoffLine` (tag, category, count, w, h, area_m2) and
  `TakeoffResult`. Every m² traces to a tag × a schedule row. This —
  not pixel measurement — is the product's core measurement method,
  and the deliverable is a **rough** takeoff by design.
- **Facade takeoff** — The elevation-side sibling (`facade_takeoff.py`):
  wall / glazing / door *fractions* of a facade (CMP Facade dataset),
  as opposed to per-tag areas from a schedule join.
- **Floor area** — The exception to count × dims: measured from space
  polygons (`measure_takeoff`), and therefore the one takeoff that
  *does* need DrawingScale.

## Geometry context

- **Registration** — Aligning another sheet into the arch-plan
  canonical frame (`registration.py`, `Affine2D`): grid-based or
  gridless (geometric). After registration, coordinates are canonical.
- **Canonical frame / coordinate conventions** — Canonical model:
  meters, **y growing DOWNWARD** (drawing frame). BEM export: meters,
  **north-up** (y flipped in `bem/bem_export.model_from_takeoff`). The
  flip happens at the export boundary, in one place, nowhere else.
- **Simplification / geometry simplification** — Area-budgeted
  reduction of geometry for BEM handoff (`geometry/geometry_simplify.py`).
  **ON by default** — roof simplification runs unless
  `--no-roof-simplify` or `roof_simplify: false` turns it off
  (standing directive, 2026-10-25; the opt-in Option C doc is stale).
- **Area budget / area drift** — Simplification may move envelope
  area by at most the budget: **default 2%, configurable 1–5%** of
  the original area. An operation that would break the budget does
  not apply. Say "within budget," not "lossless" or "exact."

## Validation context

- **Validation battery** — The 49-check invariant suite in
  `validate/`. Running "validation" without qualification means this
  battery.
- **Conservation laws** — The battery's hard core: per-level
  floor-area conservation, volume conservation, envelope closure,
  takeoff closure, LPD plausibility, cross-discipline referential
  integrity, provenance closure. **Errors block export** — fatal, not
  warnings. Warnings require acknowledgment.
- **LPD** — Lighting power density: installed fixture watts per space
  area (`lighting.py` → per-space LPD), plausibility-checked by the
  battery and by the ASHRAE 90.1 checks.

## BEM export / BIM context

- **BEM** — Building Energy Modeling (the model *for* simulation).
  Matchline's output purpose; Fluxion is a downstream consumer of
  BEM inputs, with no shared code.
- **BEMModel / BEMSpace** — The export-side model shape in
  `bem/bem_export.py` / `bem/bem_geometry.py` (gbXML 8.01 / IFC4). Distinct
  from the canonical `BuildingModel`: export is a projection, and
  the y-flip to north-up happens in this projection.
- **IFC Tier 0 / Tier 1** — Tier 0 import (`ifc/ifc_import.py`): raw BIM
  element inventory into `bim_elements`, no space boundaries needed;
  openings sit there unassigned. Tier 1 (open work): attaching those
  elements/openings to spaces. Walls from IFC are mirrored into
  `BuildingModel.envelope` for the BEM path.
- **BimElement** — One raw IFC element in the Tier 0 inventory.
  Not a Space, not an EnvelopeWall, until Tier 1 / mirroring says so.
- **Daylit zone / DaylitZone** — A sidelit area polygon *inside* a
  space, per ASHRAE 90.1 (`SpaceDaylight`). It is an area within a
  Space, not a Zone in the HVAC sense — the name collision is known;
  always write **DaylitZone** in full.

## Review & provenance context

- **Provenance** — The record attached to *every* extracted fact:
  `sheet_id`, `revision`, `method`, `confidence`, `bbox`. A fact
  without provenance does not enter the model.
- **Confidence** — The extraction score on a fact/link. Links below
  `REVIEW_CONFIDENCE = 0.80` are not accepted silently (next term).
- **Review queue / ReviewItem** — Low-confidence links flagged for a
  human to confirm/reject (`matchline review`). The queue is part of
  the model output, not an error log. Design stance (Alex,
  2026-09-19): a local, calibrated queue — human review stays in the
  loop; nothing low-confidence is auto-accepted.
- **Supersede** — What a newer revision does to an older fact: the
  old fact is kept in `history` / the revision log
  (`RevisionEvent`), never silently overwritten.
- **Synthetic fixture** — A generated building/sheet set from `synth/`
  (`bldg_3room`, `bldg_open_office`, `bldg_8room`). Synthetic-first is
  the validation rule: a path that fails on synthetic data is not run
  on real data. Synthetic ≠ fake-data shortcut; it is the test bed.

---

## Don't say / say instead

| Don't say | Say | Why |
|---|---|---|
| room (in the model) | Space (`L1-101`) | Room is drawing/OCR language; Space is the canonical key |
| the model (unspecified) | BuildingModel / BEMModel | Canonical vs. export projection are different shapes |
| zone (across repos) | HVAC zone (Matchline) / zone (Fluxion) | Same word, traced vs. simulated — handoff briefs must disambiguate |
| daylight zone | DaylitZone | Avoids collision with HVAC Zone |
| the detector = WiSARD | YOLO detector (production) / WiSARD (research) | jesse.py is not in the pipeline |
| pixel-measured takeoff | count × dims takeoff | Areas come from schedules; pixels only for floor area |
| exact simplification | simplification within the 2% (1–5%) area budget | The budget is the contract |
| dropped / ignored (low confidence) | queued for review | Nothing is silently dropped — detections or links |
| floor / story | Level | Code and keys use Level (`L1`) |
| window (any opening) | SpaceOpening / BimOpening / window tag | Three different stages claim "window" |

## Open questions for Alex

1. **ScheduleEntry vs. ScheduleRow vs. BlockSchedule / OleSchedule** —
   code carries several schedule-record names from different parsers.
   One canonical name for briefs, or are parser-specific names fine
   as long as *ScheduleEntry* is the join output?
2. **Linking vs. resolution** — README calls the product "cross-sheet
   entity resolution," code calls it *linking* (`link/`,
   cross-discipline linking). Pick one for outward language?
3. **Takeoff scope word** — you scope the deliverable as *rough*
   quantity takeoffs. Should "rough" be part of the term everywhere
   (to set expectations), or only in product/outward docs?
