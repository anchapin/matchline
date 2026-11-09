# `matchline review` — Review Queue Triage

Interactive review queue tool for confirming or rejecting extracted BEM facts that
need human verification.

---

## Overview

The review queue holds extraction decisions that fall below the confidence threshold
or that the pipeline could not auto-resolve. A review item captures:

- **Provenance** — sheet ID, revision, extraction method
- **Kind** — one of `window_room_link`, `fixture_assignment`, `schedule_mismatch`, `extraction_type`
- **Raw confidence** — the extraction confidence score
- **Triage metadata** — urgency level, auto-resolution flag

Items pending review **block BEM export** via `validate/` until they are
confirmed or rejected.

---

## Ordering and bulk actions (#996)

The queue is listed worst-first everywhere: open items before resolved ones,
then highest urgency, then lowest confidence (the same order the HTML review
page renders). `--list --format json` rows carry `level`, the floor the
item's target belongs to.

Bulk confirm/reject applies one action to every OPEN item matching the
filters, in worst-first order, records each in the correction history beside
the model, and re-runs validation:

```bash
matchline review model.json --bulk-confirm --kind window_room_link
matchline review model.json --bulk-reject  --conf-below 0.5
matchline review model.json --bulk-confirm --conf-above 0.2 --level L1
```

Filters (`--kind`, `--conf-above`, `--conf-below`, `--level`) combine; with
none given every open item matches. Zero matches exits 1 without changing
the model. Bulk flags take no `--confirm`/`--reject` and none of the
correction-history flags.

## Batch triage by filter (#1294)

Review items pile up per kind and per sheet (`air_loop`,
`mech_schedule_value`, ...). The batch triage flags decide a whole class of
items in one run, through the same decision path the single-item
`--confirm`/`--reject` flags use (same state, same revision log entry, same
correction history), and record what they decided in a decisions file in the
report's format (`matchline.review_decisions/1`):

```bash
matchline review model.json --confirm-kind air_loop
matchline review model.json --reject-kind mech_schedule_value
matchline review model.json --confirm-kind air_loop --confirm-sheet A101
matchline review model.json --reject-sheet M201
```

Every **open** item matching the filter is decided; the kind and the sheet
filter combine. The decisions are written to `review_decisions.json` beside
the model (or to `--decisions-out PATH`), in the same format the HTML review
page exports, so the batch can be replayed:

```bash
matchline review model.json --apply review_decisions.json
```

The file records the queue as it was before the batch (automatic states and
the model's SHA-256) plus the decisions taken. A batch that matches nothing
exits 0, changes nothing and writes no decisions file. The batch flags need
at least one filter and take no `--confirm`/`--reject`, no
`--bulk-confirm`/`--bulk-reject` and none of the correction-history flags.

> The flags live on the `run_review.py` CLI (`python3 run_review.py --help`).
> The `matchline review` sub-command in `cli.py` has its own argument parser
> and does not expose them yet.

## Usage

```bash
matchline review model.json                        # list open items (interactive)
matchline review model.json --show-all            # list all items including resolved
matchline review model.json --list                 # list items to stdout, exit 0 immediately
matchline review model.json --list --format json   # list items as JSON
matchline review model.json --confirm RVW-001      # confirm an item, re-run validation
matchline review model.json --undo 3               # undo the last 3 accepted corrections (#997)
matchline review model.json --revert-to-auto opening:S-W1:width_m  # back to automatic (#997)
matchline review model.json --corrections          # print the correction history (#997)
matchline review model.json --reject RVW-002       # reject an item, re-run validation
matchline review model.json --reject RVW-002 --auto-triage  # enable auto-triage on load
matchline review model.json --confirm-kind air_loop  # confirm every open item of a kind (#1294)
matchline review model.json --reject-kind mech_schedule_value  # reject every open item of a kind (#1294)
```

`model`
: Path to a `BuildingModel` JSON file.

`--show-all`
: Also show confirmed and rejected items (default: shows open items only).

`--list`
: List review items to stdout without prompting. Exits 0 immediately after printing.

`--format text|json`
: Output format for `--list` (default: `text`).

`--confirm ID`
: Confirm a review item, exactly as the HTML review report's confirm does: `status confirmed`, `resolution accept`, `needs_review false`, `acknowledged true`, and a revision log entry. Works on a rejected item too; an item already confirmed is left alone. Then re-runs validation.

`--reject ID`
: Reject a review item, exactly as the HTML review report's reject does: `status rejected`, `resolution drop`, `needs_review false`, `acknowledged true`, and a revision log entry. Works on a confirmed item too; an item already rejected is left alone. Then re-runs validation.

`--auto-triage`
: Enable auto-triage for review items (sets `model.auto_triage=True`).
  The `ENABLE_AUTO_TRIAGE=1` environment variable is also respected.

---

## Triage Flow

Each open item is evaluated by a task-specific `TypedDecider` loaded from `.npz`
(never from `.pkl`). The classifier returns a typed decision (`CONFIRM` or
`REJECT`) and a confidence score.

**Auto-triage** resolves items automatically when all three conditions hold:

1. `model.auto_triage` is `True` (set via `--auto-triage` flag or `ENABLE_AUTO_TRIAGE=1`), **and**
2. the item's `needs_human` value is less than `1.0`, **and**
3. the classifier confidence meets or exceeds the confidence threshold
   (default `0.75`; also controllable via `--confidence`).

Auto-resolved items are marked `status = "confirmed"` or `status = "rejected"` and
carry `auto_resolved = True` with `resolution` set to `"CONFIRM"` or `"REJECT"`.
Items that cannot be auto-resolved are left `open` for human review.

### Urgency

Items with `urgency > 0` are highlighted as higher-priority. When listing items,
the sort key is `(-urgency, auto_resolved)` so that high-urgency items needing
human attention appear first.

---

## Security Model

Pickle deserialization is a supply-chain attack vector: a malicious `.pkl` file can
execute arbitrary code on load. For this reason, `run_review.py` ships with
`_check_no_pkl_in_review_classifier()`, a startup guard that walks the
`review_classifier/` directory and raises `SecurityError` if any `.pkl` files are
present.

**Only `.npz` (safe numpy archive) format is permitted** for model files.
`TypedDecider` models are loaded via `TypedDecider.from_npz()` only.

Exit code `1` is returned if a `SecurityError` from a blocked pickle file is raised.

---

## Exit Codes

- `0` — Success (list printed, item confirmed/rejected, or validation passed)
- `1` — Error (file not found, validation failure, classifier error, or
  `SecurityError` from a blocked pickle file)

---

## Limitations

- **Review queue blocks export**: Items in the `open` state block BEM export via
  `validate/`. All open items must be confirmed or rejected to proceed.
- **Auto-triage requires opt-in**: Auto-resolution only runs when
  `ENABLE_AUTO_TRIAGE=1` is set or `--auto-triage` is passed. It is not enabled
  by default.
- **Confidence threshold is global**: The default threshold of `0.75` applies to
  all item kinds; per-kind thresholds are not yet supported.
- **Pickle files are blocked unconditionally**: Legitimate `.pkl` model files
  (e.g., pre-trained weights) cannot be loaded by the review system. All models
  must be converted to `.npz` format first.
- **No incremental review**: The CLI always re-runs full validation after a
  confirm/reject action; there is no partial or staged review mode.
- **`--auto-triage` flag inconsistency**: `run_review.py` exposes `--auto-triage`
  but `cli.py` (the user-facing entry point) uses `--enable-auto-triage`. Both
  are accepted but the discrepancy in naming may cause confusion in scripts.

---

## See Also

- [`building_model.md`](building_model.md) — `ReviewItem` data model
- [`cli.md`](cli.md) — `ENABLE_AUTO_TRIAGE` environment variable reference
- [`pipeline.md`](pipeline.md) — Stage 4b auto-triage in the full pipeline
