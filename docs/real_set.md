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

This slice only reads and registers. Joining elevation openings to plan gaps (sill
and head heights, storey height from the outline) is the next slice.
