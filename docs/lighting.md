# Lighting Takeoff

Extends the three-stage count × schedule pipeline (`datasets_adapter.py`) to
lighting fixtures, producing **total installed lighting power (W)** for the
building and **lighting power density (LPD, W/m² and W/ft²) per space**.

## Pipeline

1. **Fixture spotting** — classify fixture symbols on the floor plan → count
   per schedule tag (`Detection` with `tag`, e.g. `"A"`). Same WiSARD
   classifier, trained on synthetic fixture crops.
2. **Schedule parsing** — lighting fixture schedule → `{tag: watts/fixture}`
   via `parse_lighting_schedule_csv` (columns: `tag, description, lamp_type,
   watts`; category defaults to `"lighting"`). `ScheduleEntry` gained
   `watts`, `description`, `lamp_type` fields (all optional; window/door
   rows are unaffected).
3. **Power rollup** — `lighting.py::lighting_takeoff`: count × watts per tag
   = installed watts; each fixture is assigned to a room polygon by centroid
   point-in-polygon (reusing `room_labels`), giving per-space watts and LPD.

Watts need **no drawing scale** (they come from the schedule). LPD needs room
areas: pass `space_areas_m2` (e.g. synthetic GT) or a `DrawingScale` for
px→m². Spaces with unknown area still get watts and fixture counts; LPD is
`None` rather than guessed.

Unmatched tags (no schedule entry) and unassigned fixtures (centroid in no
room) are **reported, never dropped**; unassigned fixtures still count toward
the building total (they are real installed power).

## Fixture set (`synth/lighting.py`)

| Class | Schedule tag | Watts |
|---|---|---|
| Troffer 2x4 | A — 2x4 recessed LED troffer, 4000K | 45 |
| Troffer 2x2 | B — 2x2 recessed LED troffer, 4000K | 30 |
| Downlight | C — 6" LED downlight, 3000K | 12 |
| Pendant | D — LED pendant, direct/indirect | 24 |
| Wall Sconce | E — LED wall sconce | 15 |
| Exit Sign | X — LED exit sign, battery backup | 5 |

Glyphs follow drafting convention where it aids discrimination: the sconce
is drawn on a wall stub (vs. the downlight's free circle+cross), the exit
sign's rect hugs its EXIT text (vs. troffer louver grids).

## Validation (`synth/lighting_e2e_test.py`) — ALL PASS

Two synthetic lighting sheets (seeds 201–202), full pipeline:

| Sheet | Classification | Total W | Per-room W + LPD | Unmatched/unassigned |
|---|---|---|---|---|
| lighting_201 | 96.9% (31/32) | 933.0 W exact | exact, 7/7 rooms | correct |
| lighting_202 | 100% (30/30) | 1015.0 W exact | exact, 5/5 rooms | correct |

- WiSARD train accuracy on the 6 synthetic fixture classes: **94.9%**.
- Total watts and per-room watts/LPD match ground truth **exactly**
  (detections carry GT tags — the true pipeline contract, since tags come
  from schedule callouts).
- A bogus-tag detection lands in `unmatched` without polluting totals; an
  off-plan detection lands in `unassigned` while still counting toward the
  building total.

## Gaps (honest)

1. **Real fixture symbols vary by manufacturer and firm.** The window/door
   gap experiment showed synthetic→real transfer is poor for varied drafting
   styles; expect the same here. Real drawings are the required training
   corpus (arriving Monday).
2. **Emergency/exit fixtures are often on separate life-safety plans**, not
   the lighting plan — a production system must ingest both sheets and avoid
   double-counting.
3. **No real schedule-table parsing yet**: `parse_schedule_table` is still a
   stub; v1 takes the lighting schedule as CSV (same as windows/doors).
4. **Tag extraction is the v2 gap**: detections currently need the schedule
   tag from GT/annotations; reading the tag callout next to each fixture via
   OCR is the critical next step (shared with the window/door path).
5. **Dimming/daylight zones, controls, and ASHRAE 90.1 LPD allowances** are
   out of scope — this module reports installed power and LPD; compliance
   comparison is downstream work.
6. LPD uses a single area per space; multi-use spaces and atria need
   sub-area handling later.

## Limitations

- **Fixture classification has not been validated on real drawings**: Training used synthetic symbols only; real architectural drafting styles, manufacturer-specific fixture glyphs, and non-standard notation will degrade classification accuracy.
- **No OCR-based tag reading**: Fixture labels are currently read from annotated CSV schedules; callout text adjacent to fixtures on the drawing is not parsed. Tag extraction is deferred to v2.
- **Emergency and exit fixtures are on separate life-safety plans**: A production system must ingest both the lighting plan and life-safety plan and de-duplicate fixture types to avoid over-counting.
- **No controls or daylighting modeling**: The module reports installed power and LPD only. Dimming controls, occupancy sensors, daylight zones, and ASHRAE 90.1 LPD allowances are not modeled.
- **Single area per space**: Multi-use spaces and atrium geometries use a single area scalar; sub-area zoning is not yet supported.
- **Schedule table parsing is a stub**: `parse_schedule_table` does not yet extract from native drawing schedule tables; a CSV input is required for v1.
