# HTML review report and replayable decisions (#749)

Every `matchline run` writes `out/review/` right after auto-triage, before the
fail-fast gate, so the report exists even when validation blocks the export:

| file | what it is |
|---|---|
| `model.json` | the `BuildingModel` the decisions apply to |
| `decisions.json` | empty decisions file: schema `matchline.review_decisions/1`, the model's SHA-256, and the automatic state of every review item |
| `review.html` | the review queue on one offline page (inline CSS and script, no external assets) |

The same folder can be written for any model with
`matchline review model.json --report DIR`.

## Reviewing

The page lists every review item worst-first: open items before resolved
ones, then urgency high to low, then confidence low to high. Each row shows
the kind, description, sheet and method, the current decision and its
history. Buttons or keys record a decision:

- `j` / `k` move, `c` confirm, `r` reject, `e` edit (asks for the corrected value; the row says which model field it edits, if any), `u` revert to the automatic output
- "open only" hides items that already have a decision or were resolved by triage
- decisions are kept in the browser (local storage, keyed by the model hash) until exported
- "Export decisions.json" downloads the file; "Load decisions" reopens one


## Sheet overlays

When the run started from a drawing set (`matchline set`), the page also draws every floor plan that was read for walls and rooms, below the queue. Each sheet shows its raster (embedded as a downsized JPEG, at most 1800 px on the longest side, so the page still works offline) with four layers you can switch on and off:

- rooms: green outline, orange when the room itself needs review; hover for the space id and label
- walls: grey, drawn at their measured thickness
- openings: blue door, teal window, brown gap, grey air wall; solid when the opening is in the model, dashed when it is not, red when it waits in the review queue
- detections: symbol boxes from the detection provider (`detections_NNN.json` beside the sheet), coloured by label and faded by score

Room ids are the model's space ids and opening ids are the model's opening ids, so the overlay and the model agree. Press `s` (or the item's "sheet" button) to jump to the sheet and highlight what the item is about: the room or opening its target names, or the box in its provenance. Items with neither have no sheet button.

## Replaying

```bash
matchline review out/review/model.json --apply decisions.json [--out reviewed.json]
```

Decisions run in file order and the last one per item wins.

| action | item after replay |
|---|---|
| `confirm` | `status confirmed`, `resolution accept`, `needs_review false`, `acknowledged true` |
| `reject` | `status rejected`, `resolution drop`, `needs_review false`, `acknowledged true` |
| `edit` | `status confirmed`, `resolution reassign`; writes the value to the item's target field (see below) or, with no target field, records it in the revision log |
| `revert` | the automatic state stored in `decisions.json` |

`matchline review --confirm ID` and `--reject ID` apply one decision the same way (same state, same log entry).

Every change the replay makes is written to the model's revision log
(`action: "review"`, note `"<id> <action>[: value]"`). Replaying the same file
twice changes and logs nothing. A file naming an unknown item, an unknown
action or an edit without a value is refused before anything changes. A model
whose hash differs from the file's (for example after an earlier `--apply`)
gets a warning and decisions are matched by item id. The decisions file is
never rewritten, so it keeps the full correction history.

The page's replay (`finalStates` in the inline script) mirrors
`review_report.final_states`; a test runs both on the same file under node.

## Export package

When a run reaches the BEM export, `out/package/review/` carries the page,
the decisions template and the model, and the trust report says how to
replay them (#750).

## Limitations

- Links between sheets (an elevation window drawn to its plan room) are not drawn yet; each sheet is drawn on its own.
- Edits that change the model (#796): every review item names its `target` (`kind`, `id`, and for editable items the `field`). Editable today, on openings only: `space_id` (room link; value is a space id or a unique room number), `width_m` (metres, 0 to 30; area and host interval follow, centred), and `construction_id` (must exist in the model). The default field comes from the item; `width_m=0.9` picks another opening field. Every edit value in a file is checked before anything changes, and one bad value refuses the whole file. The revision log carries old and new values. `revert`, `confirm` and `reject` (in the page or as CLI flags) put the field back to the automatic value, kept in `target.original` the first time an edit changes it.
- Items whose target has no editable field (spaces, walls, fixtures, BIM elements, detections) keep the #749 behaviour: the value is recorded and the item closed. Spaces, fixtures, HVAC and BIM items stay record-only (#799).
- Adding an opening the pipeline left out (#798): an `opening_unsized` item targets its wall with field `opening` and a `gap` record (opening id, room, facade, centre and width of the gap, the drawn kind, the schedule rows that could fit, the sheet). An edit value is either a schedule tag (`W3`: category, size and sill from the schedule) or `category=window width_m=1.2 height_m=1.5 [sill_m=0.9]` (window or door only; sizes in metres). The tag must be in the model's schedules and be a sized door or window, sizes must be in range (width 0 to 30 m, height 0 to 10 m, sill 0 to 10 m), and the width may not exceed the drawn gap by more than 0.05 m, the tolerance the pipeline uses to match a gap to a schedule row. A bad value refuses the whole file before anything changes. The new opening sits centred on the gap, carries provenance `review_edit` (confidence 0.95), takes the assembly other openings of the same tag use, or else the construction library's default when the model has a climate zone, and counts in the takeoff reconcile like any other window (the check says how many were added in review). `revert`, `confirm` and `reject` remove it again: the automatic output for a gap is no opening. Models written before #798 have no `gap` record, so their gap items stay record-only.
