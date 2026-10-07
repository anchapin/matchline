# Schedule tables from PDF sheets (#746)

`pdf_schedules.py` reads door, window, lighting fixture and mechanical schedules
drawn as ruled tables on vector PDF sheets (the `matchline.sheet/1` output of
`matchline ingest`).

```
matchline ingest set.pdf sheets/
matchline schedules sheets/        # writes sheets/schedules_NNN.json
```

`matchline run --set` runs it on every sheet. Door, window and lighting rows go
to `model.schedules` (tag -> `ScheduleEntry` dict, the shape the takeoff join and
`lighting.py` already read). Mechanical rows go to the set report's
`schedules.equipment` list.

## How a table is read

1. Axis-aligned stroked segments are collected from every path (rectangles
   included) and collinear pieces are joined.
2. Segments that touch form a candidate grid. Grids need at least two rows and
   two columns, rows no taller than 1 inch, and at most 400 rows by 60 columns.
3. Only grids titled as a schedule are read: a full-width top row, or the text
   line just above the frame, has to contain `SCHEDULE`. A plan, title block or
   legend grid is left alone.
4. The fine grid is merged wherever a cell line is missing, so merged header
   cells (`SIZE` over `WIDTH` / `HEIGHT`, `MARK` spanning two header rows) and
   merged data cells come out as single cells. Text lines inside one cell are
   joined top to bottom.
5. Header rows run from the first row below the title until no group header or
   tall header cell needs another row. Column names join the header text above
   each column (`SIZE WIDTH`). Full-width rows under the header are notes.

## Records

| Kind | Columns used | Record |
|---|---|---|
| door / window | `WIDTH`, `HEIGHT`, or `SIZE` like `3'-0" x 7'-0"` | `ScheduleEntry(width_m, height_m)` |
| lighting | `WATTS` / `INPUT W` / `LOAD`, `DESCRIPTION`, `LAMP` | `ScheduleEntry(watts, description, lamp_type)` |
| mechanical | `MAX CFM`, `MIN CFM`, `CFM`, `NECK` / `INLET` / `SIZE` | `{tag, kind, cfm_max, cfm_min, cfm, neck_size, values}` |

The tag column is the first header starting with MARK, TAG, SYMBOL, TYPE, NO,
NUMBER, ID or UNIT, else the first column. Tags go through
`datasets_adapter.normalize_tag` (#479), so `d 1` and `D1` join. A value that
does not parse stays `None` with a note; nothing is defaulted.

## Provenance

Every cell carries `Provenance(sheet_id, 0, "pdf_ruled_table", 0.9, bbox)`, with
the cell's bbox in sheet pixels.

## When a table is not read

The table is reported with `status: "unparsed"` and a reason, and no rows:

- a merged cell that is not a rectangle
- text crossing a cell line
- no header row, or no data rows under it
- a data row with no tag
- a tag that appears twice

In a set run that sheet's `schedules` stage is `failed` and a `fixture_schedule`
review item names the table. A tag scheduled on two sheets with different values
keeps the first and goes to review.

## Not done yet

Joining mechanical rows onto HVAC zones needs detected tags on the plans (#743),
and plan openings are still counted, not modelled, until door and window tags
are read off the plans. Unruled (whitespace-aligned) tables and scanned
schedules are not read.
