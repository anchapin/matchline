# Revision tracking and takeoff diffs

Issue #993. Parses revision evidence off a vector drawing set and diffs takeoffs
between two revisions. Everything here is heuristic geometry: nothing edits the
model, the pipeline only gains facts and review items.

## What ships

**`matchline/revisions.py`** (schema `matchline.revisions/1`)

- `read_revision_table(sheet)` finds a revision table on a sheet (titles like
  "REVISIONS", "REVISION SCHEDULE", "REV HISTORY"), skips the header row, and
  returns rows of `{mark, date, description, bbox_pt}`. Dates accept common US
  forms; the description is everything that is not the mark or the date.
- `find_revision_clouds(sheet)` finds revision clouds by shape, never by label:
  either one closed path with at least `MIN_ARCS` (12) segments under
  `MAX_CLOUD_SIDE_PT` (500pt on a side), or a union-find chain of at least
  `MIN_LINKS` (5) small primitives whose bounding boxes nearly touch
  (`LINK_GAP_PT` 4pt) and close back to the start. Plain rectangles, leaders and
  furniture outlines fail the segment count or the closing test.
- `analyze_sheet(sheet, title_revision=None)` pairs clouds with table rows: the
  cloud's mark is the first revision-mark-shaped token drawn inside its bbox.
  Ambiguities, one entry each:
  - `cloud_without_table_entry` - a cloud whose mark is missing from the table.
  - `table_entry_without_cloud` - the row the title block claims the sheet is
    at, with no matching cloud. Historical rows the sheet does not claim are
    never flagged.
- `index_revisions(pairs)` maps each revision mark to the sheets that carry it;
  sheets with an addendum note in the title block group under
  `__addenda_sheets__`.

**`sheet_index.py`** gains `SheetEntry.addenda` and `SheetIndex.revisions`
(title-block addendum detection via `ADDENDUM_RX`, revision map built at index
time).

**`matchline/takeoff_diff.py`** (schema `matchline.takeoff_diff/1`)

`diff_takeoffs(old, new, old_report=None, new_report=None)` accepts the
stage-02 wrapper or a raw model. Rooms are reported as
`added / removed / renamed / area_changed / moved_level / unchanged` with the
ids behind each count (area drift under 1% is rounding noise, not a change);
windows and doors count per-category opening deltas across spaces; when set
reports are supplied, symbol counts come from the HVAC legend rows by class
(`provided: true`, plus per-class added/removed). Without reports,
`symbols.provided` is false rather than a silent zero.

## Pipeline and CLI

`run_pipeline` calls the revision pass on every vector sheet and folds the
ambiguities into the review queue as one grouped item per distinct
`(kind, mark, sheet)` with `confidence 0.5, method "revision_tracking"`. The
set report carries a `revisions` block (marks, per-sheet table/cloud counts).

```
python matchline.py takeoff-diff --old old/stage_02_model.json \
    --new new/stage_02_model.json \
    [--old-report old/stage_00_set_report.json --new-report new/...] \
    --out takeoff_diff.json
```

The report is plain JSON; nothing is written back into either model.
