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

- No sheet overlays yet (rooms, walls and detections drawn on the sheet, links between sheets). That is the next slice of #749.
- Edits that change the model (#796): every review item names its `target` (`kind`, `id`, and for editable items the `field`). Editable today, on openings only: `space_id` (room link; value is a space id or a unique room number), `width_m` (metres, 0 to 30; area and host interval follow, centred), and `construction_id` (must exist in the model). The default field comes from the item; `width_m=0.9` picks another opening field. Every edit value in a file is checked before anything changes, and one bad value refuses the whole file. The revision log carries old and new values. `revert`, `confirm` and `reject` (in the page or as CLI flags) put the field back to the automatic value, kept in `target.original` the first time an edit changes it.
- Items whose target has no editable field (spaces, walls, fixtures, BIM elements, detections) keep the #749 behaviour: the value is recorded and the item closed. Creating an opening the pipeline left out (`opening_unsized`) is not an edit yet.
