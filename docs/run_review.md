# Review Queue — `matchline review`

The review queue is the human-in-the-loop gate for low-confidence extractions. Any extraction with confidence below the triage threshold (default 0.8) is suspended in the queue until a human explicitly acknowledges it. The export pipeline (`validate.py export_gate`) **blocks export** when unacknowledged items are present.

## Concepts

### ReviewItem

Every item in the queue is a `ReviewItem` (defined in `building_model.py::ReviewItem`):

| Field | Type | Description |
|---|---|---|
| `kind` | `Literal` | Extraction type: `fixture_assignment`, `diffuser_assignment`, `window_room_link`, `elevation_conflict`, `gd_complex_row`, etc. |
| `description` | `str` | Human-readable explanation of the uncertainty or conflict. |
| `confidence` | `float` | Extraction confidence in [0, 1]. Values < 0.8 trigger `needs_review=True`. |
| `provenance` | `Provenance` | Provenance record: `sheet_id`, `revision`, `method`. |
| `status` | `str` | `"open"` (awaiting review), `"confirmed"` (accepted), `"rejected"` (dropped). |
| `needs_review` | `bool` | True → this item blocks export until acknowledged. |
| `acknowledged` | `bool` | True → human has seen and accepted/rejected this item. |
| `urgency` | `int` | Priority 0–3. Higher = surfaced first in `matchline review`. |
| `auto_resolved` | `bool` | True → resolved automatically by high-confidence re-run; still needs human ack. |
| `resolution` | `str` | `"accept"`, `"drop"`, or `"reassign"`. |

### Triage Flow

```
Extraction → flag_for_review() → [auto-triage?] → review_queue → [human ack?] → export_gate
```

1. **flag_for_review()** is called during the link pipeline for every extraction with `confidence < 0.8`. It creates a `ReviewItem` and appends it to `BuildingModel.review_queue`.
2. **Auto-triage** (when `ENABLE_AUTO_TRIAGE=1` or `--enable-auto-triage` is set): `ReviewTriage` (`review_classifier/`) runs a TF-IDF + calibrated logistic regression classifier to assign urgency and decide if auto-resolution is safe.
3. **export_gate()** (`validate.py`) checks `any(item.needs_review and not item.acknowledged for item in model.review_queue)`. If True → export blocked.

### Security Model

Acknowledgment is recorded in `BuildingModel.revision_log` tagged with `MATCHLINE_IDENTITY` (default `"cli"`). There is no server-side auth — control access via filesystem permissions on the model JSON.

## Commands

### `matchline review <model.json>`

List all open (unacknowledged) review items for a model.

```
$ matchline review bem_out/model.json
#001  fixture_assignment      conf=0.63  urgency=2  "Diffuser DB-12 → Room 101"
#002  window_room_link        conf=0.71  urgency=1  "Window W3 elevation B → unclear zone"
```

### `matchline review <model.json> --show-all`

List all items including acknowledged ones.

### `matchline review <model.json> --confirm <id>`

Acknowledge and confirm an item (accept the extraction). Sets `status="confirmed"` and `acknowledged=True`. Clears the export block for this item.

### `matchline review <model.json> --reject <id>`

Reject an item (discard the extraction). Sets `status="rejected"` and `acknowledged=True`. The rejected fact will not appear in the exported model.

### `matchline review <model.json> --enable-auto-triage`

Run the auto-triage classifier on all open items. Re-scores urgency and marks items as `auto_resolved` where the classifier is confident. Items still need human acknowledgment to clear the export gate.

## Configuration

`ENABLE_AUTO_TRIAGE` (env var, default `1`)
: Enable auto-triage during model loading. Set to `0` to disable.

`MATCHLINE_IDENTITY` (env var, default `"cli"`)
: Identity recorded in `revision_log` for audit purposes.

`--enable-auto-triage` (CLI flag)
: Override `ENABLE_AUTO_TRIAGE` to enable auto-triage for this run.

## Design Notes

- The review queue is **not** a data-loss mechanism. Rejected items are still recorded in `revision_log`; nothing is deleted.
- Auto-triage is intentionally conservative: it never suppresses items from the queue, it only scores urgency and suggests `auto_resolved`.
- `export_gate()` is called by `validate.py` before any write. It does not delete data — it raises `ValidationError` to abort the export.
