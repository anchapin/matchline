# `review/corrections.py` — Correction history (#997)

Every accepted review decision is recorded once, beside the model, so a
correction is never a silent overwrite.

## What is recorded

`corrections.json` in the project dir (beside `model.json`) holds one entry
per accepted correction:

- **seq** — one-based position in the history, append-only;
- **ts** — UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`);
- **item_id** / **kind** — the review item the decision acted on;
- **target** — the entity the item points at, copied in full;
- **field** / **before** / **after** — the model field the decision wrote
  and its values (empty/None for a decision that only closes an item);
- **action** — `confirm`, `reject`, `edit` or `revert`;
- **source** — `human` (CLI flags, decisions file, HTML report) or `auto`
  (a programmatic resolver calling the same API);
- **state_before** — the item's own state before the decision, so an undo
  can put it back;
- **undone** — set by `--undo` or `--revert-to-auto`; nothing is deleted.

Replaying the same decisions file again records nothing (idempotent,
matching the #749 replay rules).

## Undo of the last N

```bash
matchline review model.json --undo 3
```

Takes back the last three still-standing corrections, newest first: each
gets its field value written back (`before`), its review item restored to
the recorded `state_before`, and a `undo <item> <action>` revision-log
entry. Undoing past the history simply undoes what is there.

## Revert-to-automatic per field

```bash
matchline review model.json --revert-to-auto opening:S-W1:width_m
```

Puts one field back to the automatic output: the value the earliest
still-standing correction on that field read before it wrote. Every
correction on the field is marked `undone` with reason
`revert-to-automatic`; the review items stay as the reviewer left them.

## Survival and export

The history is written once by the review workflow; later stages never
clear or regenerate it. The export package (#750) copies
`corrections.json` into the package when one exists and the trust report
(JSON, HTML and PDF) carries a Corrections section with the counts
(accepted, human vs auto, undone) and names the file.

## Listing

```bash
matchline review model.json --corrections
```

prints the history: one line per entry with seq, timestamp, action, item,
source, the field change when there was one, and an `[undone]` mark.
