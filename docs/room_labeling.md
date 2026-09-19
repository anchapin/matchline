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
4. **Association** — `assign_labels()`:
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
- Multi-line labels ("OPEN\nOFFICE\n201" stacked) are read as separate boxes
  and not merged — needs a line-grouping pass.
- Schedule-table parsing (`parse_schedule_table`) is still a stub; room
  labels are independent of it.
