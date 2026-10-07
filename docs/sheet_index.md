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
