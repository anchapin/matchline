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
- Door and window sizes and tags. Plan openings are counted per level
  (`plan_openings`) but are not modelled until schedules from the PDF land (#746).
- Mechanical symbols. Mechanical plans are marked `failed` at the symbols stage until
  the detector provider (#743) is wired in.

Validation closes area and volume per level, so stacked storeys no longer double-count
against a single footprint.
