# Room Name/Number Labeling

`room_labels.py` assigns every floor-plan space polygon the room name and
number printed on the drawing (e.g. "OPEN OFFICE 201").

## Pipeline

1. **OCR** — `detect_text()` runs PP-OCRv4 (detection + recognition) via
   `rapidocr_onnxruntime`: ONNX models, CPU-only, fully local, no cloud API.
   Chosen because the environment has no tesseract binary and no torch
   (easyocr would need both); rapidocr needs only onnxruntime. The package
   lives in a venv at `~/workspace/.venv-ocr` (system pip is blocked by a
   debian-owned numpy) — run OCR code with `~/workspace/.venv-ocr/bin/python`.
2. **Dimension rejection** — `looks_like_dimension()` drops dimension strings,
   area tags, and steel tags (`17'-6"`, `5X5`, `1/2"`, `@ 16 O.C.`, `W12X72`,
   `67 SF`) before parsing. Real sheets are full of these; they must never
   become room labels.
3. **Parsing** — `parse_room_label()` applies tolerant regexes for real-drawing
   formats: `201`, `201A`, `C1`, `RM 203`, `OPEN OFFICE 201`, `207 KITCHEN`,
   `LOBBY` (name-only). A digit-adjacent `O`→`0` fix handles the classic
   drawing-OCR confusion (`2O5A` → `205A`); plain words (`ROOM`) are untouched.
   Unparseable text falls back to `(text, "", 0.5)` — never an exception.
4. **Line grouping** — `_group_stacked_labels()` / `_room_label_from_group()`
   (in `room_labels.py`) regroup vertically stacked OCR boxes that belong to
   one multi-line label, before association:
   - a line joins the group of the line above it when the vertical gap is at
     most one line height (`GROUP_MAX_GAP_FACTOR = 1.25` × the larger line
     height) **and** the boxes are horizontally aligned/overlapping in x;
   - when room polygons are known, a line strictly inside a *different*
     polygon than the group is never merged (lines of one label share a room);
     a line inside no polygon (doorway, hallway) never blocks a merge;
   - the merged label's text is the lines joined top-to-bottom with spaces
     (`"OPEN" / "OFFICE" / "201"` → `"OPEN OFFICE 201"` → name `OPEN OFFICE`,
     number `201`);
   - the merged bbox is the union of the member boxes;
   - the merged OCR confidence is the **minimum** member confidence (a merged
     label is only as trustworthy as its least certain line);
   - single-line labels are untouched, and side-by-side text on one line is
     never grouped.
5. **Association** — `assign_labels()`:
   - text centroid strictly inside a polygon → `enclosed`
     (smallest area wins on nesting), confidence = ocr × parse;
   - else nearest polygon edge within `max_nearest_px` (default 150 px) →
     `nearest`, confidence scaled by 0.6 and distance falloff — covers labels
     sitting in doorways/hallways;
   - else → reported in `unmatched_labels`, never dropped.
   - One label per space keeps the highest-confidence claim; spaces with no
     label keep `name == number == ""` and appear in `unlabeled_spaces`.

## Output contract

- `LabeledSpace`: `polygon_px`, `name`, `number`, `label_confidence`,
  `label_source` (`"enclosed"`/`"nearest"`/`""`), `label_bbox`.
- `LabeledTakeoff`: `spaces`, `labels` (all OCR labels), `unmatched_labels`,
  `n_labeled`/`n_total`, `unlabeled_spaces` property.
- `attach_room_labels(takeoff_result, sheet_image)` labels the `"room"`
  regions of an existing `TakeoffResult` in place (adds `.spaces`,
  `.unmatched_labels`).

## Validation

No open corpus carries room-label ground truth: AEC-geometric-bench redacts
annotations (its 823 `Room` polygons have no text attributes), so validation
uses a programmatically drawn 10-room plan (`synthesize_test_plan`) with
edge cases — number-only, name-only, tiny text, near-wall label, unlabeled
room, stray far-field label — plus unit tests. `run_room_labels.py`:

| metric | result |
|---|---|
| association (label → correct polygon) | **9/9 = 100%** |
| exact (name, number) string match | 7/9 = 77.8% |
| parse unit tests | 11/11 |
| dimension-rejection unit tests | 10/10 |
| nearest-fallback unit test (doorway gap) | pass |
| unlabeled room / stray label handling | correct |
| runtime | ~2–5 s per 1600×1200 sheet (CPU) |

The two exact-match misses are OCR-level, on adversarially tight synthetic
rendering: a word-merge (`OPENOFFICE`) and a dropped inter-word space
(`RESTRO0M205A`). Association was correct in both cases.

Real-sheet sanity check (aec-geometric-bench `sheet_01`, 4801×7201 @ 200 dpi):
129 text boxes in 9.5 s; room names (`BATHROOM`, `BEDROOM`, `PLAYROOM`,
`MECH`, `WIC`, conf 0.97–1.00) detected cleanly. This sheet's labels carry
**names but no numbers** (redaction), so number-parsing on real drawings is
still unvalidated — Alex's unredacted drawings (Monday) are the right corpus.

## Known gaps

- Rotated/vertical text: PP-OCR detection handles mild skew; fully vertical
  room labels are untested.
- Name-part OCR errors (`RESTROOM`→`RESTRO0M`) are not corrected — only the
  number part gets the O→0 heuristic. A drawing-vocabulary spellcheck is
  future work.
- `W12X72`-style tags are rejected as dimensions; a structural plan that
  legitimately needs them would need an allowlist.
- Multi-line labels ("OPEN\nOFFICE\n201" stacked) are now merged by the
  line-grouping pass (see step 4) — a stack of lines with a gap of about one
  line height and overlapping x-ranges becomes one `RoomLabel`. Remaining
  gap: the pass is geometric only, so a stack whose lines sit more than one
  line height apart, or whose x-ranges do not overlap, is still read as
  separate labels.
- Schedule-table parsing (`parse_schedule_table`) is still a stub; room
  labels are independent of it.

## Limitations

- **OCR accuracy degrades with poor scan quality**: Low-resolution PDFs, skewed scans, and architectural fonts with ligatures will produce OCR errors that propagate to incorrect room label assignments.
- **Multi-line labels are merged only when geometrically stacked**: A label split across vertically stacked boxes (e.g., "OPEN", "OFFICE", "201") is merged when the lines are roughly one line height apart and horizontally aligned. Boxes that are side by side, or stacked with a large gap, stay separate labels.
- **No spatial disambiguation**: When two rooms share the same label (e.g., identical apartment units), the module assigns the same label to both without disambiguation. Downstream modules may need tie-breaking by polygon area or sheet location.
- **Non-standard room labels are not caught**: The module maps known label types from the label vocabulary; non-standard or informal room names (e.g., "Jeff's office") are silently dropped unless they match known suffixes.
- **Schedule table parsing is a stub**: `parse_schedule_table` does not yet extract room data from native drawing schedule tables; CSV input is required for v1.
