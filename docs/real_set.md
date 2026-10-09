# Running a real drawing set (#741)

`python run_pipeline.py --set drawings.pdf --out out/` runs the whole pipeline on a
PDF drawing set instead of the synthetic generator.

Stage 1 reads the set sheet by sheet: ingest (#737), sheet index (#738), scale (#739),
walls and rooms (#740). Every sheet gets a status per stage in
`stage_00_set_report.json` (`ok`, `skipped` or `failed`, with a reason). A sheet that
cannot be read is reported and the run carries on; the run stops only when no floor
plan produced a room, and then the report says why for each sheet.

Levels come from the sheet index (B1, L1..L10, MEZZ, PH, ROOF) in that order. Rooms on
architectural floor plans become spaces on their level. Exterior walls are the outline
of each level's rooms at wall centrelines, one wall per outline edge, faced by its
outward normal.

What this slice does not invent:

- Storey height. Plans do not show it. `--storey-height` sets it; without it the run
  uses 3.0 m and adds an `elevation_extraction` review item so the default is visible.
- Door and window sizes. A plan gap carries a width and a position but no tag. A gap
  whose middle lies within 0.5 m of the level outline is exterior; it is matched to the
  door/window schedules in the set by width (within 0.05 m). When every matching row
  agrees on category and height, the gap is modelled on that wall, in the room that
  owns it, at the scheduled size (provenance `plan_gap_schedule_width`). A schedule tag (`W3`, `D-12`, `SF-1`) written on the plan
  within 1.5 m of the gap names the row outright (provenance `plan_gap_tag`, confidence
  0.85), even where rows of the same width disagree; a tag whose scheduled width or
  category contradicts the gap sends it to review instead, and a tag not in the
  schedule is ignored (#793). A gap no row
  explains, or one rows disagree on, becomes an `opening_unsized` review item and is
  not modelled. Interior gaps are counted only. Each level reports
  `openings: {exterior, interior, modelled, unsized}` beside `plan_openings`.
  Window sills use the export's 0.9 m convention until elevations are read.
  A matched gap whose schedule rows state U, SHGC or VT gets a stated construction
  from them (see `docs/pdf_schedules.md`); rows that disagree go to review.
- A storefront run with doors drawn inside it (`doors_in_glazing`, #793): a
  window row within 0.05 m of the whole run includes its doors, and the window
  note says so. A window row within 0.05 m of the run less its doors is the
  glass alone: the window takes the row's width centred on the glass left once
  the doors are cut out, and each door is modelled at its jambs from the door
  schedule by width (provenance `plan_glazing_door`, confidence 0.75, 0.7 when
  several same-size rows agree). A door no scheduled door explains, or one
  same-width rows disagree on, goes to review as `opening_unsized` (gap
  `drawn: "door"`) and is not modelled. The glass and its door share one
  stretch of wall, so their along-wall intervals overlap: areas are right, the
  exact placement of the glass around the door is not.
- Mechanical symbols. Mechanical plans are marked `failed` at the symbols stage unless a
  detector provider runs. `--detector-config` picks one (see
  `docs/detection_provider.md`); the report's `detector` block records which ran and
  whether its weights are evaluation only. Detections are counted per sheet but not yet
  joined to openings or tags. A reviewer can add the missing opening with an edit on that item (a schedule tag or typed sizes; see [review_report.md](review_report.md), #798).

Validation closes area and volume per level, so stacked storeys no longer double-count
against a single footprint.

## Single sheet images (`--image`)

`--image sheet.png --detections preds.json` reads symbols and rolls up a takeoff into
`stage_01_building.json`, then stops with a Stage 2 error: one raster sheet has no walls
or rooms to put the symbols in (raster wall finding is not built). The error and its hint
are printed to stderr and the run exits 1; use `--set drawings.pdf` to build a model.

## Elevation sheets (#810, first slice)

An elevation sheet whose title names exactly one facade ("SOUTH ELEVATION") is
read for its building outline and the window and door rectangles inside it
(`elevation_sheets.py`). It is registered to that facade of the first-floor plan's
exterior footprint: by shared column-grid labels when both sheets show at least
two of the same grid bubbles (confidence 0.95), otherwise by matching the outline
to the facade's length (0.65). Positions run from the facade's left end as seen
from outside, so north and east elevations run right to left in plan terms.

Each opening is written to `sheets/<sheet>.elevation.json` with `s0_m`/`s1_m`
along the facade, width, sill and head heights; the sheet's `elevation` stage and
`SetReport.elevations` carry the counts and registration. A title naming several
facades or none, or an outline more than 5% off the plan's facade length, goes to
the review queue as `elevation_extraction`.

### Joined to the plan's openings (second slice)

Each elevation opening is matched to the modelled plan opening of the same
category on the same facade whose centre is nearest, within 0.3 m. A match sets
the opening's `sill_m` (0 for doors) and `head_m` = sill + the scheduled height,
and adds an `elevation_join` entry to its history. The schedule's size stands: a
width more than 0.15 m or a height more than 0.1 m off the schedule goes to review.
An elevation opening with no plan opening, or a plan opening on that facade the
elevation does not draw, goes to review with its sill left unknown. Each entry in
`SetReport.elevations` carries `matched`, `unmatched_elevation` and
`unmatched_plan`. Lowest level only.

Review kinds (#817), the same ones the building-JSON elevation path uses:

- elevation and schedule disagree on a matched opening's size: `elevation_conflict`;
- a plan opening on that facade the elevation does not draw: `elevation_conflict`;
- an elevation opening with no plan opening: `window_room_link`;
- an elevation that was not read or not registered, and the storey-height default,
  stay `elevation_extraction`.

`window_reconciliation` (two elevations placing one window differently) is left for
partial elevations of one facade (#818). Item ids and `target` are unchanged, so
saved decisions files and the review page's sheet links still apply. All of these
route to the `route_to_review` task, as `elevation_extraction` did.

### Both sheets on each review item (third slice)

Every review item the join raises carries `target["ends"]`, one entry per sheet
it involves, for the review page (#801):

- elevation end: `{"side": "elevation", "sheet", "sheet_id", "el": "elev:<id>", "box"}`,
  the opening's box in elevation-sheet points (y down);
- plan end: `{"side": "plan", "sheet", "sheet_id", "el": "op:<id>", "point"}`, the
  plan opening's centre in plan-sheet points (y down).

A size mismatch has both ends; an elevation opening with no plan match has the
elevation end (plus the nearest plan opening of another category, if one is within
0.3 m); a plan opening the elevation does not draw has the plan end. Matched pairs
are also written back to `sheets/elevation_NNN.json`: a top-level `plan` names the
plan sheet, and each opening gets `plan_opening` (the model opening id, or null)
and `plan_point`.

### A facade drawn in parts

A sheet whose title marks it as part of a facade ("SOUTH ELEVATION - EAST HALF",
"PART 1 OF 2", "PARTIAL"), or any sheet when two or more sheets name the same
facade, is read as a part. A part is placed by at least two grid labels it shares
with the plan; otherwise by the end its title names (north/south/east/west, or
left/right as drawn). A part with neither is not read and goes to review. A part
drawn the full facade length is treated as a whole facade.

Each read records `partial` and `span_m` (the stretch of facade it covers) in
`sheets/elevation_NNN.json`. The join matches each read only against the plan
openings in its span. A plan opening goes to review only when no read of its
facade shows it: the item names the sheets covering it, or, when it falls between
the parts, the spans they cover. An opening shown by two sheets takes its sill
from the first; if the two disagree on sill or size beyond the join tolerances,
one `elevation_conflict` item (`rq-elev-<opening>-<sheet1>-<sheet2>`) names both
sheets, with both elevation ends and the plan end in `target["ends"]`.

### Storey height from level marks

Level marks on vector elevations ("FIRST FLOOR / EL. 100'-0\"", "ROOF EL. +3.600")
are read with their names. Each mark's stated value is checked against where it is
drawn above the facade outline's base, so a note or dimension that happens to say
"EL." is dropped. Floors and the roof count; T.O. PLATE, PARAPET and GRADE do not.
Each step is the height of the storey whose mark sits at its bottom, matched to a
plan level by the mark's name (FIRST FLOOR / LEVEL 1 to L1, SECOND FLOOR to L2,
BASEMENT to B1, MEZZANINE to MEZZ, PENTHOUSE to PH; ROOF and PENTHOUSE ROOF only
close the top step). When one elevation has both GROUND FLOOR and FIRST FLOOR, it is
read the British way: GROUND is L1, FIRST is L2 (#822). When no plan level matches
any mark by name (marks named by datum such as LEVEL 100 / LEVEL 115), each
elevation whose storey count equals the plan level count gives its k-th storey to
the k-th level (`height_match: order` in the report, `name` otherwise); when none
does, every level keeps the default and `rq-storey-height` gives the counts. Each level takes its own height when
every elevation that states it agrees within 0.05 m (`height_source:
elevation_level_marks`, `height_m` per level in the report, with a note naming the
sheets), and level elevations are the running sum, so a 4.5 m first floor puts L2 at
4.5 m. A level whose marks disagree, or that no mark names, keeps the 3 m default and
is named in `rq-storey-height` (target `{"kind": "level", "ids": [...]}`; raised for
review when marks disagree, informational when a level simply has no mark). Uniform
heights give the same model as before (#814). A `storey_height_m` in the run config
still wins over the marks for every level. A map sets only the levels it names and
leaves the rest to the marks, then the default (#821):

```yaml
wall_height: {L1: 4.5, L2: 3.6}   # or one number for every level
```

`--storey-height` beats the config. A level id that is not in the set fails the run
and names the set's levels; when only some levels default, `rq-storey-height` says
which keys to add.
