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

## Stated U, SHGC and VT on door and window schedules

A door or window schedule with a `U-FACTOR` / `U-VALUE` / `U` column, an `SHGC`
column or a `VT` / `VLT` / `VISIBLE` column also fills `u_value_w_m2k`, `shgc`,
`vt`, `thermal_confidence` and `thermal_note` on its `ScheduleEntry`.

- U units come from the column header: `BTU` or `IP` is read as Btu/h-ft2-F and
  converted (x 5.678263), and `W/M2K` or `SI` is read as written. Both get
  confidence 0.9.
- A header that names no units is read as IP only when the same schedule gives
  its sizes in feet and inches, at confidence 0.75, and the note says so.
  Otherwise the U is not used and the note says why.
- SHGC and VT must lie in (0, 1]; anything else is not read and is noted.
  Nothing is defaulted.

On `matchline run --set`, a plan gap matched to scheduled rows that all state
the same U/SHGC/VT gets a stated construction (`SCHED-WINDOW-U2.0442-S0.3800-V0.4200`,
method `pdf_schedule_thermal`, cited to the schedule sheet). Rows that agree on
size but not on these values give an `opening_thermal_ambiguous` review item and
no construction. Table 5.5 (#747) fills only openings that still have none.

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

## Lighting power from fixture tags

On `matchline run --set`, each lighting schedule tag (`A`, `B2`) written on a
reflected ceiling plan or an electrical floor plan counts as one fixture of
that type (`real_set._place_fixtures`). The sheet registers to the
architectural plan of its level the same way mechanical plans do; a sheet
that does not register gets `lighting_plan_unregistered` and nothing is
counted. Text inside a column-grid bubble is a grid label, not a fixture.
When a level has both an RCP and a lighting plan with tags, only the sheet
with more tags is used, and a note names the other, so fixtures shown on both
are not counted twice.

Each room gets `Space.lighting.fixtures` (tag, schedule description, position,
watts, tag bbox), `total_w` (scheduled watts summed) and the LPD, provenance
`plan_fixture_tags` at confidence 0.5. One tag is counted as one fixture, and
drawings often tag one fixture of a group, so each lit level gets a
`lighting_from_tags` review item listing its rooms (it does not block
export). A fixture type with no watts on the schedule is counted but adds
nothing (`fixture_no_watts`). The watts reach the gbXML as
`LightPowerPerArea` and the IFC as `LightingPower`; rooms with no fixture tags
keep the space-use default.

## Mechanical equipment in rooms

On `matchline run --set`, each scheduled mechanical tag (`VAV-1`) written on a
mechanical floor plan is placed in the room that contains it (`mech_tags.py`).
The mechanical sheet is registered to the architectural plan of the same level
by the shared column grid (confidence 0.85; offsets must agree within 0.15 m,
at least one line each way), else by the same page size and scale
(confidence 0.6, noted as `frame:` on the record). A sheet that registers
neither way gets a `mech_plan_unregistered` review item and its tags are not
placed.

Placed records in the report's `schedules.equipment` gain `level_id`,
`space_id` and `located` (plan sheet, tag bbox in sheet points, position in
model metres, registration, confidence). A tag in no room is
`equipment_outside_rooms`; a tag in two rooms is `equipment_ambiguous`; both
get no room. A note lists scheduled tags no mechanical plan shows.

Each placed terminal unit (schedule kind `vav` or `fcu`) becomes an HVAC
zone `<level>-Z-<tag>` serving the room it is tagged in: `Zone.space_ids`,
`Zone.terminal_unit` (method `mech_plan_tag`, the placement's confidence),
and the room's `hvac.zone_ids` / `hvac.terminal_units`. The equipment record
gets `zone_id`. AHUs, fans and air outlets do not make zones.

## Not done yet

A zone holds only the room its terminal unit is tagged in; rooms it serves
through ductwork need duct tracing. Tags are read from text only, not tied to
detected symbols.
Scanned schedules are not read.

## Unruled schedules

A schedule drawn as whitespace-aligned text, with no cell lines, is read after
the ruled tables (`method: "pdf_unruled_table"`, cell confidence 0.75). It
needs a title containing SCHEDULE and, within three line heights under it, a
header row of at least three cells, one of them a tag header (MARK, TAG, TYPE,
NO., ...). Each header cell owns the band halfway to its neighbours; the outer
columns reach half the typical header gap past the first and last cell.

Under the header, each line needs a value in the tag column. An untagged line
tight under a row (within 1.4 line heights) continues it, so a wrapped
description joins its row. One text run across several columns is a note. A
gap of more than 2.2 line heights, or an untagged line further down, ends the
table. A value crossing a column boundary, or a repeated tag, leaves the table
`unparsed` with the reason instead of a guess. Two-line headers (a group header
over WIDTH / HEIGHT) are not read on unruled tables yet: the first header line
sets the columns.

