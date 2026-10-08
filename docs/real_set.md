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
  owns it, at the scheduled size (provenance `plan_gap_schedule_width`). A gap no row
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
