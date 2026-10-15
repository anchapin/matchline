# Sheet index (`sheet_index.py`)

Indexes an ingested drawing set (#738) so the pipeline knows which page is the
first-floor plan, which is the mechanical plan for that floor, and which are
elevations, schedules, details or the cover sheet.

```bash
matchline ingest set.pdf --out sheets/
matchline index sheets/        # writes sheets/sheet_index.json
```

## What it reads

- **Title block** (right edge or bottom of the sheet): sheet number (the largest
  sheet-number text in the block), title (stacked lines joined), revision, date.
- **Drawing index** on any sheet: rows of `<sheet number>  <title>`, used when
  there are 3 or more rows, or a `DRAWING INDEX` / `SHEET INDEX` heading.

## Classification

| Field | From |
|---|---|
| discipline | sheet-number prefix, US National CAD Standard (`A` architectural, `M` mechanical, `FP` fire protection, ...) |
| type | title keywords (floor plan, RCP, enlarged, partial, roof, site, elevation, section, schedule, detail, diagram, general, cover); else the NCS sheet-type digit (`A-5xx` detail, `M-6xx` schedule) |
| level | title (`FIRST FLOOR`, `3RD FLOOR`, `LEVEL 02`, `LEVEL B1`, `BASEMENT`, `MEZZANINE`, `ROOF`), else the drawing index, else another discipline's sheet with the same sequence (`M-102` borrows `A-102`'s level) |

Levels are keyed `B1`, `L1`, `L2`, ..., `MEZZ`, `PH`, `ROOF`; ground floor is `L1`.

Every field records its `source` (`title_block`, `drawing_index`,
`sheet_number_prefix`, `ncs_type_digit`, `paired_sheet_number`) and, where it
came from a text box, the text and box.

## Takeoff roles

- Floor plans with a level: `use_for_takeoff`.
- Enlarged plans (title, or NCS `4xx` large-scale views): never, their area is
  already on a floor plan.
- Partial plans: excluded when a full plan for the same discipline and level
  exists; otherwise `needs_review`, since stitching at match lines is a follow-up.

## Review flags

`needs_review` is set when the title block and the drawing index disagree
(different level or type, or titles that differ beyond abbreviation), a sheet
number repeats, a sheet has no number, or a floor plan has no level. Index
entries with no matching sheet are listed in `warnings`.

## Geometry fallback for legacy sheets (#1016)

A sheet whose title block yields no sheet number and no classifiable type is
not dropped: `_plan_fallback_evidence` reads the vector primitives and asks
whether the geometry itself looks like a floor plan. The gates are calibrated
on 2006-era plan sheets: primitive density of at least 1200 per square
megapoint of page area (plan pages sit near 2300, everything else below 950),
at least 10 closed interior regions in the room-area band (0.3% to 15% of the
page, page background excluded), and no single interior region above 25% of
the page (a schedule table is one giant open cell, not rooms).

A sheet that passes is classified `plan` with provenance source
`geometry_fallback` and an `evidence` block, marked `needs_review`, and routed
to takeoff even without a level: the set builder models each level-less plan
as its own level (`LX<n>`). When a number *was* found but the type stayed
unclassified, the fallback also flags that the number may be a label rather
than the sheet number. A real classification (title text or NCS type digit) is
never overridden.

A sheet routed this way still needs a scale before walls read: with no scale
note or bar, one or two agreeing dimensions estimate it, flagged for
review (#1019).
