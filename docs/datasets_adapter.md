# Dataset Adapters — `datasets_adapter.py`

Bridges architectural drawing datasets to the Matchline extraction pipeline and provides the quantity-takeoff measurement layer.

## Architecture

```
Dataset → DatasetAdapter → SymbolSample / Detection ──┐
Schedule CSV → parse_schedule_csv → ScheduleEntry ────────> rollup_takeoff → TakeoffResult
Polygon regions → measure_takeoff → TakeoffResult
```

The module has two distinct halves:

1. **Dataset adapters** — convert per-dataset formats to Matchline canonical types (`SymbolSample`, `Detection`, `Region`).
2. **Measurement layer** — count × scheduled dimensions → m² rollup for windows/doors; polygon area measurement for floor areas.

Supported datasets:
- **AEC-geometric-bench** — 15 real construction sheets with CVAT 1.1 XML annotations (validated).
- **FloorPlanCAD** — HuggingFace parquet export (pending validation).
- **ArchCAD** — 40K-sample subset on HuggingFace (`jackluoluo/ArchCAD`, gated, CC BY-NC 4.0). `load_archcad` reads its JSON modality; see below.

## Output contract

| Type | Role |
|---|---|
| `SymbolSample` | Cropped symbol image for classifier training (H×W grayscale, 0..255) |
| `Detection` | One spotted symbol: label, schedule tag, bbox, score, source, optional `tag_score` (tag-read confidence; None = trusted tag) |
| `ScheduleEntry` | One schedule row: tag → category + dimensions (m) |
| `TakeoffLine` | Joined row: count × dims = area for one tag |
| `TakeoffResult` | Per-tag lines + per-category m² totals + `unmatched` (tagged, no schedule row) + `untagged` / `untagged_counts` (no tag read yet, counts per label) + `tag_review` (counted, low tag confidence) |
| `Region` | Polygon with a takeoff category (floor_area, wall, window, door…) |
| `DrawingScale` | m/px for polygon→area conversion |

Measurement paths:
- **Count × dims** (windows, doors) — scale-independent, requires symbol spotting + schedule parsing.
- **Polygon measurement** (floor areas) — requires `DrawingScale.m_per_px`.

## Key functions

- `parse_schedule_csv(path_or_rows)` — parse window/door schedule CSV.
- `rollup_takeoff(detections, schedule, drawing_type, tag_review_below=TAG_REVIEW_THRESHOLD)` — join detections to schedule, roll up m² per category. Tags on both sides go through `normalize_tag` first. Detections with no tag go to `untagged` (per-label counts in `untagged_counts`), never into `unmatched`. Tags with `tag_score` below the threshold (default 0.5) are counted and also listed in `tag_review`.
- `normalize_tag(tag)` — canonical tag form: all whitespace removed, uppercased (`" w - 1 "` → `"W-1"`). Also used by `parse_schedule_csv`.
- `measure_takeoff(regions, drawing_type, scale)` — sum polygon areas in m².
- `normalize_crop(crop, size=28)` — prepare crops for WiSARD classifier input.
- `load_archcad(root, size=28, scale_m_per_px=None, max_samples=None)` — ArchCAD symbols + takeoff regions.

## ArchCAD loader

The HuggingFace release is not a parquet export. It ships five zips under `data/` (`json`, `svg`, `png`, `point`, `caption`), one drawing slice per file, behind a manual access request. `load_archcad` reads the JSON modality from an extracted `json.zip` (any `*.json` under `root`) or from `json.zip` itself.

Each JSON file is a list of primitives (a dict with an `entities` or `primitives` list is also accepted). Column mapping:

| Key | Meaning | Used as |
|---|---|---|
| `type` | `LINE`, `CIRCLE`, `ARC`, or a polyline type | picks the geometry keys below; other types (text, hatch) are skipped |
| `start`, `end` | LINE endpoints `[x, y]` | geometry |
| `center`, `radius` | CIRCLE / ARC | geometry |
| `start_angle`, `end_angle` | ARC sweep in degrees | geometry |
| `points` / `vertices` | polyline `[[x, y], ...]` | geometry |
| `semantic` | class id (0–29, 100) or name (`single_door`, `Single Door`) | `SymbolSample.label`, takeoff category |
| `instance` | object id shared by one countable object's primitives | groups primitives into one symbol |

Each countable instance becomes one `SymbolSample`: its primitives are rasterized and passed through `normalize_crop`, with `source = "archcad:<slice>"` and `bbox` the geometry extent. Doors (single, double, parent-child, other) become `door` regions, sinks, urinals, toilets, bathtubs and squat toilets `fixture`, holes `opening`, and elevators and staircases `floor_area`. Furniture, columns, parking spaces, piles and hydrants are symbols with no takeoff category. Non-countable classes (walls, glass, grid, beams, rebar) have no instance and are skipped.

A missing key or wrong type on a primitive, an unknown class, or a countable primitive with no `instance` raises `ArchCADFormatError`, so schema drift fails loudly instead of returning zero counts. A root with no JSON raises `FileNotFoundError` with download instructions (and says so when it only finds parquet).

Only `LINE` and `CIRCLE` appear on the dataset card; the `ARC` and polyline keys, and whether coordinates run y-down, are unconfirmed until the loader is run on the real download.

## Limitations

- **v1 schedule input is CSV** — real drawings need Jesse's document-layout analysis (v2 gap).
- **Tag text extraction is a v2 gap** — current annotation sets carry glyph labels but not schedule tag text.
- **Drawing scale must be parsed separately** — the module does not read title blocks or calibration marks.
- **No HVAC quantities** — fixture counts are tracked but physical dimensions are not.
- **FloorPlanCAD loader is pending validation**; the ArchCAD loader is built to the dataset card's documented JSON layout and not yet run on the real download (access is gated).
- **ArchCAD has no drawing scale** — areas stay in drawing units² unless `scale_m_per_px` is given.

## OCR schedule tags (#668)

`extract_with_tags(detections, image, config=None, backend=None, schedule_tags=None)` reads the schedule tag printed beside each untagged detection and fills `Detection.tag` and `Detection.tag_score`. For each symbol it OCRs a window around the bbox (grown by `ocr_pad_frac` x the symbol size on every side), keeps reads that look like a tag (`A`, `W-1`, `D2`, `101A`; words like `OFFICE` are ignored) and reach `ocr_min_conf`, then picks a tag that is in the schedule first, then the read closest to the symbol, then the most confident. Detections that already have a tag are left alone. Low-confidence reads still count in the takeoff and show up in `TakeoffResult.tag_review`.

If no OCR backend is installed, the detections come back unchanged (tags stay empty) and a warning is logged. The pipeline never fails on OCR.

### Configuration

Settings resolve in this order, later wins: built-in defaults, the `[tool.matchline.datasets]` table in `./pyproject.toml`, then the `datasets:` section of the per-building YAML config passed with `matchline run --config`.

| Key | Default | Meaning |
|---|---|---|
| `ocr_backend` | `auto` | `auto` (pytesseract, then rapidocr), `pytesseract`, `rapidocr`, or `none` |
| `ocr_pad_frac` | `1.0` | search window margin, as a multiple of the symbol's larger side |
| `ocr_min_conf` | `0.3` | drop OCR reads below this confidence (0 to 1) |

```yaml
# pipeline.yaml
datasets:
  ocr_backend: rapidocr
  ocr_pad_frac: 1.5
```

```toml
# pyproject.toml
[tool.matchline.datasets]
ocr_backend = "pytesseract"
```

Backends: `pip install -e .[ocr]` for rapidocr (local ONNX, no system packages), or `pip install -e .[ocr-tesseract]` plus the `tesseract` binary for pytesseract. An unknown `ocr_backend` value raises `ValueError`.
