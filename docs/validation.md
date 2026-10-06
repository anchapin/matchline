# Validation: the invariant catalog

`validate/` runs a named battery of checks against a `BuildingModel`.
The premise: a building model that cannot balance its own books should not
export to gbXML/IFC. Every check returns pass / warn / error / skip with a
message, expected vs actual numbers, and the offending entity ids.

Run: `python -m pytest tests/ -q` · demo: `python3 run_validation.py`

## Severity policy

| Severity | Meaning | Blocks export? |
|---|---|---|
| **error** | A conservation law is violated, a reference dangles, or a fact has no provenance. The books don't balance. | **Yes** — `export_gate(report)` returns False |
| **warn** | A plausibility tripwire fired: absurd LPD, impossible sill/head, a unit-conversion smell. Needs a human look. | No — but must be acknowledged |
| **skip** | Check not applicable: no gbXML/IFC path given, no elevation windows linked, no `SimplifyResult` passed. | No |
| **pass** | — | — |

Rule of thumb: **errors are about internal consistency** (two records of the
same quantity disagree); **warns are about the outside world** (the numbers
are consistent with each other but implausible for a real building).

## The battery (47 checks)

### Conservation laws (error)

- `space_area_matches_polygon` — each space's `area_m2` equals
  shoelace(`polygon_m`) within 0.1%. The linker derives area from the
  polygon, so drift means the record was edited or a code path diverged.
- `area_conservation` — **the 20×30 → 600 check.** Per level,
  Σ(space areas) ≈ footprint area (union of space polygons) within **3%**.
  *Why 3%, not 0%:* rooms tile the *interior*; interior partitions have
  thickness. A 20×30 ft plate (600 ft², 100 ft perimeter) with ~60 ft of
  4-inch partitions loses ~20 ft² ≈ 3.3% to wall thickness. Tighter than
  that and real buildings fail on honest geometry; looser and a dropped
  room slips through. Synthetic data tiles exactly, so it lands at ~0%.
- `space_volume_matches_area_height` — `volume_m3` = area × wall height
  within 0.1% (warn; same reasoning as the area self-check).
- `volume_conservation` — **footprint × height check.** Σ(space volumes)
  ≈ footprint × floor-to-floor height within **3%**, same wall-thickness
  rationale as area.
- `envelope_area_matches_perimeter` — Σ(envelope wall areas) ≈ footprint
  perimeter × height within **1%**. The envelope runs and the footprint
  union are built by *different code paths*, so this cross-checks them.
  *Why 1%:* exterior wall runs vs room-face polygons differ by wall
  thickness; on a 100 m perimeter with 0.2 m walls that's ~0.8%.
  IFC-imported segments sit on wall body centrelines (#579), so when a
  level's segments carry a known wall thickness *t*, the footprint is also
  compared offset by −t/2 and +t/2 (spaces drawn to the exterior or the
  interior face) and the closest of the three counts.
- `simplify_budget` — the simplifier's own area delta stays within its
  budget (default 2%, configurable 1–5%). Pass a `SimplifyResult` as
  `sres=`; skipped otherwise.
- `convention_bias` — **the roadmap item 1 measurement, not a conservation
  law.** Per level, reports the air-volume delta between the interior-face
  derivation (spaces tile the interior; this is canonical) and an
  exterior-face derivation (footprint offset outward by exterior wall
  thickness). Informational: it never blocks export, because there is no
  correct value to fail against and an export whose bias is honestly reported
  is the goal. Thickness comes from `IfcMaterialLayerSet` sums first
  (analytical, per roadmap item 1), then `BimElement.thickness_m`; with
  neither it **skips and says the bias is unmeasurable, never zero** — a
  guessed thickness would produce a guessed bias. Warns in exactly two cases,
  both of which mean the *number* is untrustworthy rather than the building
  unusual: a thickness outside 0.05–1.20 m (a units slip, e.g. an inch value
  in a metre field) and a bias above 10% of air volume. *Geometry:* offsetting
  a ring outward by t grows area by perimeter × t plus the Steiner corner term
  π t²; at t = 0.2 m that corner term is 0.13 m² in total, far below the 3%
  conservation tolerances, while perimeter × t is the term that matters.
  Non-convex plates make the corner term a slight overestimate only.
- `skylight_within_roof` — **roadmap item 3, roof glazing.** Per space,
  Σ(skylight areas) ≤ the roof area over that space. The model has no roof
  entity yet, so this check states the convention the gbXML exporter already
  uses: one **flat roof** over the footprint, so the roof over a space on the
  top level is that space's floor area and spaces below the top level have
  none. Error when a space's skylights exceed its roof (a dimension slip or a
  wrong host space; no convention makes it real). Warn when a skylight is
  hosted below the top level (an atrium or light well the model cannot yet
  represent, or a level-assignment error). Skylight area resolves as the
  opening's own area, then width × height, then its schedule entry; a
  skylight with none of these is named and left out of the sum, never
  counted as zero. Skips when there are no skylights. Sloped roofs (roadmap
  item 2) replace the flat-roof assumption when they land. Skylights are
  excluded from `facade_opening_closure`, which is about walls.
- `daylight_zones` — **roadmap item 3, toplighting, plus sidelighting.**
  Every daylight zone (primary, secondary, under-skylight) must lie inside
  its space (at most 1% of the zone outside) and its stored area must match
  its polygon within the area tolerance. The toplit area of each space is
  recomputed here as the union of the toplit polygons, with code separate
  from `daylight_skylights.py`, and must match `toplit_m2` and not exceed the
  floor area; overlapping skylight zones are therefore never double counted.
  A toplit zone must be classed `under_skylight` and name a skylight of the
  same space. Errors for any of these. Warns when skylights are listed as
  unplaced (no plan position or no ceiling height), since the toplit area is
  then a lower bound. Skips when no space has a daylight zone; on the drawing
  path, skylights carry no position yet, so nothing is computed there.
- `wall_construction_coverage` — **roadmap item 6, per-space wall
  constructions.** Each `EnvelopeWall` segment carries a `construction_id`
  (a key into `BuildingModel.constructions`) and the `space_id` it encloses;
  the space's wall U-value is Σ(Uᵢ × Aᵢ) / ΣAᵢ over its own segments, stored
  as `Space.wall_u_value_w_m2k` by `constructions.apply_wall_u_rollup`. The
  check recomputes that number with its own loop rather than calling the
  rollup, so the rollup cannot vouch for itself. Error when a segment names
  an undefined construction or a missing space, or when a stored space U
  differs from the recompute by more than 0.5% (or is set where no segment
  can contribute). Warn on partial coverage: segments with no construction,
  referenced constructions with no U-value, or spaces with a computable U
  but none stored. Segment area resolves as its own area, then length ×
  height; segments with neither are left out of the weighting, never
  counted as zero. Skips when the model declares no constructions (the
  drawing path does not extract wall types yet). U is SI (W/m²K); the
  ASHRAE 90.1 U-factor checks still read their own IP field and are not
  wired to this yet.
- `shading_host_reference` — **roadmap item 5, shading surfaces.**
  Overhangs, fins and balconies live in `BuildingModel.shading`, outside
  `envelope`, so the envelope area budget and closure checks never count
  them. Each surface is placed in its host wall's frame (`along_m` from the
  wall's `from_m` end, `z_m` above the level floor, `depth_m` out from the
  wall face). Error when the host wall or host opening does not exist, when
  the host opening is on a different facade from the host wall (not
  adjacent), or when depth, overhang/balcony width, or fin height is not
  positive. Warn when a surface has no host wall (most likely a detection
  error: a dimension line or a neighbouring building read as a projection),
  runs past the ends of its host wall by more than 5 cm, sits above the
  wall's height, or projects more than 5 m. Skips when there is no shading.
- `space_type_accounting` — **roadmap item 4, non-room polygons.** Shafts,
  closets and elevator/stair cores (`Space.poly_type`, set by
  `polygon_classify` with its confidence and reasons in the space history)
  stay their own spaces: no area is merged into a corridor or split between
  neighbours, so nothing is inflated and nothing vanishes. Error when a space
  has a type outside room/shaft/closet/elevator_core/unassigned. Warn when
  the classifier could not decide (`unassigned`), naming the spaces to
  review. Otherwise pass, reporting each non-room type's floor area and
  share so loads and LPD can be read against rooms only. Skips with no
  spaces.
- `roof_plan_coverage` — **roadmap item 2, sloped roofs (#617).** The plan
  projection of the top level's `roof_planes` must cover the union of that
  level's space polygons exactly once. Error when more than 1% of the
  footprint has no roof over it (a missing facet; the spaces under the gap
  are named) or more than 1% is roofed twice (a doubled facet; the
  overlapping plane ids are named), and when the top level has no roof
  planes while other levels do. Eaves past the footprint are not counted.
  Skips with no roof planes (the flat roof at wall height).
- `roof_solar_aperture` — **roadmap item 2, sloped roofs (#617). Warn-only**
  until the export writes sloped roofs. Compares the solar aperture
  (`solar_aperture.py`) of `roof_planes` against `source_roof_planes`, which
  `roof_simplify.apply_roof_simplification` keeps (the first source, so
  repeated runs still compare against the true original), at
  `site_latitude_deg`. Warns when the total moves more than 2% and names the
  orientations (flat/N/E/S/W) that moved past it; the payload carries area
  and aperture deltas and both per-orientation breakdowns. Skips when the
  roof was never simplified or no latitude is known (none is assumed).
- `facade_opening_closure` — per facade, Σ(opening areas) ≤ gross wall
  area, i.e. opaque = gross − openings ≥ 0. Epsilon 0.5% for rounding.
  An opening bigger than its wall is a schedule-join or placement bug.

### Closure laws (error) — the second battery

These three live in `validate.CONSERVATION_BATTERY` rather than `BATTERY`
and are run in addition to it, so `N_CHECKS` = 44 + 3 = 47.

- `area_closure` — gross floor area (union of space polygons) ≈ Σ of each
  room's reported area. A mismatch means the space boundaries and the area
  calculations disagree.
- `volume_closure` — footprint area × characteristic height ≈ Σ of each
  room's reported volume.
- `envelope_closure` — Σ(`EnvelopeWall.area_m2`) ≈ facade area minus
  window + door areas, i.e. the facade is fully accounted for.

### Takeoff closure (error)

- `takeoff_counts_reconcile` — per window tag, count × schedule dims must
  equal the summed recorded opening areas. Two independent paths to "window
  area per tag" must agree.
- `fixture_schedule_join` / `opening_schedule_join` — every fixture tag
  resolves to schedule watts, every opening tag to schedule dims — **or**
  the miss is in the review queue. Unflagged misses are errors (silent 0 W
  / 0 m²); flagged ones are warns (reported, not silent).
- `no_negative_areas` — no negative areas or non-positive dimensions.

### Plausibility guards (warn, except where noted)

- `lpd_bounds` — per-space LPD within (0, 25] W/m². *Why 25:* ASHRAE 90.1
  space allowances cluster ~5–16 W/m²; above 25 is almost always a ft²→m²
  slip (×10.76) or double-counted fixtures. Zero with fixtures present is
  a join bug (also warn).
- `lpd_unit_consistency` — `lpd_w_ft2` == `lpd_w_m2` / 10.7639 (**error**:
  internal unit hygiene, not plausibility).
- `sill_head_sanity` — 0 ≤ sill < head ≤ wall height. Skipped when no
  openings carry heights.

### Cross-discipline closure (error)

- `assignment_uniqueness` — every fixture/diffuser/sensor lives in exactly
  one space (no double counting).
- `zone_nonempty` — every zone has ≥1 diffuser and ≥1 space. (Warns, not
  errors, when the model has no zones at all — mech plan simply not
  linked yet.)
- `zone_space_referential` — `zone.space_ids` ↔ `space.zone_ids` resolve
  in **both directions**, reciprocally. Catches half-linked zones.
- `hvac_zone_coverage` — HVAC conservation (#658). A space holding a duct
  terminal (diffuser or terminal unit) must be served by ≥1 zone that lists
  it back (**error**: its airflow would vanish from the BEM). Among spaces
  with any HVAC evidence, the share resolving to no zone must stay ≤
  `hvac_unzoned_warn_frac` (default 5%, a `run_checks` argument; warn
  above it). Each zone's diffuser ids must reconcile with the diffusers its
  served spaces hold (warn). Spaces with no HVAC evidence are not counted.
  Skipped when the model has no zones.
- `space_id_hygiene` — ids shaped `{level}-{number}`; duplicate room
  numbers on one level are a warn (numbers are the human key).
- `elevation_placement_consistency` — openings with exact along-wall
  positions: head == sill + height, s_center inside host interval,
  area == w × h. Skipped when elevation instance detection hasn't run.
- `window_tag_coverage` — every opening carries a non-empty schedule tag.
- `window_double_link` — two windows in the same space sharing a tag and
  an overlapping centre (within 0.15 m along the host wall). Two elevation
  runs of one facade can duplicate a `SpaceOpening` before
  `_dedupe_space_openings()` runs; this is the safety net for that.
- `cross_level_dedup` — opening conservation across levels (#664). **error**
  when two wall openings on adjacent levels still match as one physical
  opening (same facade, tag and category; along-wall centres within 5 cm;
  matching width; vertical extents that overlap or touch), so its glazing
  would be counted twice. **warn** when, on any level, more than
  `cross_level_dedup_warn_frac` (default 1%) of wall openings are unplaced
  (no along-wall position, so dedup could not confirm them) or duplicated
  across spaces on that level. **skip** with no wall openings. Matching
  rules live in `opening_identity.py`, shared with `_dedupe_space_openings`.

### Provenance / auditability (error, except where noted)

- `provenance_complete` — **every** fact (space polygon, opening, fixture,
  diffuser, sensor, terminal unit, zone, wall) cites sheet/revision/method.
  A number without provenance is unauditable.
- `review_queue_sound` — every review item is well-formed (kind,
  description, provenance, valid status). Open items are *fine* — queued
  for humans is the opposite of dropped; the count is reported.
- `review_queue_acknowledged` — every item with `needs_review=True` is
  either `acknowledged` or resolved to `confirmed`/`rejected`. This is what
  makes "the review queue blocks BEM export" enforceable: an item that is
  neither acknowledged nor decided blocks the export.
- `revision_log_present` — ≥1 ingest event (warn if empty: hand-built
  model?).

### Export checks (skipped unless paths given)

- `gbxml_space_areas` — file parses; Space count matches the model; every
  Space has positive Area and Volume.
- `gbxml_opening_refs` — every Opening is hosted on an identified Surface.
- `ifc_entity_counts` — IfcSpace count matches the model; ≥1 IfcWall.
  Skipped when IfcOpenShell is unavailable.
- `gbxml_wall_areas` — cross-pipeline reconciliation: wall areas in the
  exported gbXML match the model's `EnvelopeWall.area_m2`. Catches an
  export path that silently drops or re-scales walls. Skipped unless a
  gbXML path is given, so it only bites after `bem-export --format gbxml`.

### ASHRAE 90.1-2019 envelope / lighting (warn)

ASHRAE 90.1 compliance checks are diagnostic (severity: warn). They do not
block export but surface code-compliance issues for human review.

- `ashrae_wall_u_factor` — wall assembly U-factor ≤ 0.5 W/m²K (ASHRAE
  90.1-2019 Table 5.5.4.2). Skipped when no walls in model.
- `ashrae_roof_u_factor` — roof assembly U-factor ≤ 0.35 W/m²K (ASHRAE
  90.1-2019 Table 5.5.4.2). Skipped when no roofs in model.
- `ashrae_window_u_factor` — window U-factor ≤ 2.8 W/m²K (ASHRAE 90.1-2019
  Table 5.5.4.2-1) **and** window SHGC ≤ the climate-zone maximum
  (ASHRAE 90.1-2019 Table 5.5.4; defaults to 0.40). Both are evaluated by
  the one function `validate.ashrae90_1.window_envelope()`, so there is no
  separate SHGC check id. Skipped when no windows in model.
- `ashrae_lighting_power_density` — space lighting power density ≤ 10.5
  W/m² (ASHRAE 90.1-2019 Table 9.5.1). Skipped when no conditioned
  spaces in model.
- `ashrae_hvac_efficiency` — `Space.hvac` cooling EER / heating COP meet
  the minimums for the equipment type (ASHRAE 90.1-2019 Tables
  6.8.1-6.8.3). Walks zones → `space_ids` → `space.hvac`. Skipped when no
  zones or no conditioned spaces carry HVAC data.

## How to add a check

1. Write `_check_<name>(ctx) -> CheckResult` in `validate/`. Use
   `ctx.model`, `ctx.level_of`, `ctx.footprint_area`, `ctx.wall_height`,
   and the tolerances on `ctx`. Keep it read-only; a check that raises is
   caught and reported as an error, but prefer returning skip over
   raising.
2. Append it to `BATTERY`. Order is cosmetic; `N_CHECKS` updates itself.
3. Update `tests/test_validate.py::test_battery_size_documented` — it
   greps this file for the count, so it fails until you do.
   `test_every_check_is_documented` fails until the new check gets a
   bullet here. Both failures are the intended workflow, not paperwork.
4. Add a defect mutator in `tests/model_factory.py` + a parametrize row
   proving the new check fires with the right severity.
5. Regenerate goldens: `python -m tests.make_goldens` — and eyeball the
   diff before committing.

## Open tolerances

- **LPD warn threshold (25 W/m²):** calibrated against 90.1-2019 space
  allowances; specialty spaces (retail display, labs) can legitimately
  approach it. If false warns appear on real buildings, make it
  space-type-aware instead of raising it.
- **Area/volume 3%:** assumes rooms tile the interior plate. Buildings
  with thick masonry walls or large unassigned shafts may need 4–5%;
  the tolerance is a parameter (`tol_area=`, `tol_volume=`), not a
  constant.
- **Envelope 1%:** assumes exterior runs and room faces differ only by
  wall thickness. Curtain-wall buildings with deep mullion zones may
  need more.
- **`convention_bias` thickness band (0.05–1.20 m)** and **10% bias warn:**
  neither is a physical law. The band exists to catch units slips, not to
  judge assemblies, and 10% is roughly a 0.5 m wall on a 20 × 30 m plate.
  Both are module constants (`THICKNESS_PLAUSIBLE_M`, `BIAS_WARN_FRACTION`)
  rather than `ctx` tolerances, because a bias number is reported, not
  enforced. The check averages thickness across exterior walls; a building
  with genuinely mixed constructions needs per-segment thickness first, which
  is roadmap item 6.
- **`simplify_budget`** re-runs the simplifier from the raw footprint
  to verify the self-reported delta. If the re-verified original area
  differs from the stored value by more than 0.1 m², an error is raised.
- **`gbxml_wall_areas`** (new) verifies that gbXML-exported wall areas
  match the canonical model's envelope wall areas. Cross-pipeline numeric
  reconciliation closes the gap between structural-only export checks and
  semantic verification.

## Limitations

- **Conservation laws are not checked for HVAC, plumbing, or electrical**: Only envelope, lighting, and vertical transport are validated. HVAC sizing, duct/pipe routing, and electrical load conservation are out of scope.
- **Tolerance thresholds are not calibrated against real buildings**: The 3% area/volume, 1% envelope, and 25 W/m² LPD thresholds are based on ASHRAE standards and engineering judgment, not validated against a corpus of real buildings.
- **No cross-sheet reconciliation**: `area_conservation` and `volume_conservation` validate sheets independently; errors that cancel across sheets are not detected.
- **Simplification delta is checked only against itself**: `simplify_budget` verifies the simplified polygon against itself; it does not verify that the simplified polygon is geometrically close to the original un-simplified polygon.
- **gbXML export must exist for `gbxml_wall_areas`**: The check requires a prior `bem-export --format gbxml` run; if that step is skipped, the check silently passes.
- **No validation of occupancy or operational schedules**: The validator does not check whether occupancy schedules, internal gain profiles, or setpoint schedules are realistic or compliant.
