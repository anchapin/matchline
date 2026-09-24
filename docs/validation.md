# Validation: the invariant catalog

`validate.py` runs a named battery of checks against a `BuildingModel`.
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

## The battery (29 checks)

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
- `simplify_budget` — the simplifier's own area delta stays within its
  budget (default 2%, configurable 1–5%). Pass a `SimplifyResult` as
  `sres=`; skipped otherwise.
- `facade_opening_closure` — per facade, Σ(opening areas) ≤ gross wall
  area, i.e. opaque = gross − openings ≥ 0. Epsilon 0.5% for rounding.
  An opening bigger than its wall is a schedule-join or placement bug.

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
- `space_id_hygiene` — ids shaped `{level}-{number}`; duplicate room
  numbers on one level are a warn (numbers are the human key).
- `elevation_placement_consistency` — openings with exact along-wall
  positions: head == sill + height, s_center inside host interval,
  area == w × h. Skipped when elevation instance detection hasn't run.
- `window_tag_coverage` — every opening carries a non-empty schedule tag.

### Provenance / auditability (error, except where noted)

- `provenance_complete` — **every** fact (space polygon, opening, fixture,
  diffuser, sensor, terminal unit, zone, wall) cites sheet/revision/method.
  A number without provenance is unauditable.
- `review_queue_sound` — every review item is well-formed (kind,
  description, provenance, valid status). Open items are *fine* — queued
  for humans is the opposite of dropped; the count is reported.
- `revision_log_present` — ≥1 ingest event (warn if empty: hand-built
  model?).

### Export checks (skipped unless paths given)

- `gbxml_space_areas` — file parses; Space count matches the model; every
  Space has positive Area and Volume.
- `gbxml_opening_refs` — every Opening is hosted on an identified Surface.
- `ifc_entity_counts` — IfcSpace count matches the model; ≥1 IfcWall.
  Skipped when IfcOpenShell is unavailable.

## How to add a check

1. Write `_check_<name>(ctx) -> CheckResult` in `validate.py`. Use
   `ctx.model`, `ctx.level_of`, `ctx.footprint_area`, `ctx.wall_height`,
   and the tolerances on `ctx`. Keep it read-only; a check that raises is
   caught and reported as an error, but prefer returning skip over
   raising.
2. Append it to `BATTERY`. Order is cosmetic; `N_CHECKS` updates itself.
3. Update `tests/test_validate.py::test_battery_size_documented` and the
   count in this doc.
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
- **No cross-sheet reconciliation**: `check_area_balance` and `check_volume_balance` validate sheets independently; errors that cancel across sheets are not detected.
- **Simplification delta is checked only against itself**: `simplify_budget` verifies the simplified polygon against itself; it does not verify that the simplified polygon is geometrically close to the original un-simplified polygon.
- **gbXML export must exist for `gbxml_wall_areas`**: The check requires a prior `bem-export --format gbxml` run; if that step is skipped, the check silently passes.
- **No validation of occupancy or operational schedules**: The validator does not check whether occupancy schedules, internal gain profiles, or setpoint schedules are realistic or compliant.
