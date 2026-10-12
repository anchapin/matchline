# Column grids and grid bubbles (#742)

`grid_detect.detect_grids(sheet, sheet_id)` reads structural grids off a vector
plan or elevation sheet from `pdf_ingest` and returns a `GridSet` in sheet
points: one `GridLine` per label (`A`, `AA`, `A.1`, `1`, `12`, `2.5`) with its
orientation (`v` = constant x, `h` = constant y), position, extent, the bubbles
that name it, a confidence and provenance (sheet id, primitive indices).

A grid line is a long axis-aligned stroked run (a dashed path, dash-dot drawn
as separate short strokes, or a solid line) with a closed near-round bubble at
or past one end, centred on its axis, holding a grid label in the text layer.
A line that runs on through a circle is a room tag sitting on the grid, not its
bubble. Dashed lines score 0.90, solid 0.75, +0.05 when bubbles at both ends
agree.

Nothing is dropped silently. `review` gets:

- `grid_bubble_without_line`: a labelled bubble with no line running to it
- `grid_label_conflict`: one label at two positions, or on both axes (left out of `lines`)

`plan_grid_m(gs, "v", m_per_pt, origin_pt)` and `elevation_bubbles(gs)` produce
the `plan_grid_m` and `elev_bubbles` inputs of
`registration.register_elevation_grid()`, so a real plan and elevation register
on their shared labels; two shared labels are enough (partial grids on an
elevation still register), one raises as before.

## Measurement

`scripts/measure_grid_detect.py --seeds 200` writes randomized plan/elevation
pairs as PDFs, ingests them and scores detection and registration. Randomized:
3-8 vertical and 2-5 horizontal grids, letters/numbers/intermediate labels,
bubble radius 7-14 pt, line style, bubbles at one or both ends, clutter (walls,
door swings, numbered room tags, dimension strings, a dashed hidden line), and
elevations showing ~70% of the bubbles.

| | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| Plan grid lines | 1755 | 2 | 3 | 0.999 | 0.998 |
| Elevation grid lines | 773 | 0 | 4 | 1.000 | 0.995 |

Plan/elevation registration: 200/200 pairs registered, max error at the drawn
grid positions 0.000 m. The residual misses are room tags the generator dropped
onto a grid near its end (read as a bubble), two labels sent to review as
conflicts, and a few elevation bubbles left in review.

No real benchmark sheets with grids are available yet: the BSI Clinic IFC has
no `IfcGrid`. Follow-ups: OCR on bubble crops for raster sheets, wiring grid
sets into the real-set pipeline's elevation registration, and a measurement on
real sets.
