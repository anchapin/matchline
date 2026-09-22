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
- **ArchCAD-400K** — 40K-sample subset on HuggingFace (pending validation).

## Output contract

| Type | Role |
|---|---|
| `SymbolSample` | Cropped symbol image for classifier training (H×W grayscale, 0..255) |
| `Detection` | One spotted symbol: label, schedule tag, bbox, score, source |
| `ScheduleEntry` | One schedule row: tag → category + dimensions (m) |
| `TakeoffLine` | Joined row: count × dims = area for one tag |
| `TakeoffResult` | Per-tag lines + per-category m² totals + unmatched detections |
| `Region` | Polygon with a takeoff category (floor_area, wall, window, door…) |
| `DrawingScale` | m/px for polygon→area conversion |

Measurement paths:
- **Count × dims** (windows, doors) — scale-independent, requires symbol spotting + schedule parsing.
- **Polygon measurement** (floor areas) — requires `DrawingScale.m_per_px`.

## Key functions

- `parse_schedule_csv(path_or_rows)` — parse window/door schedule CSV.
- `rollup_takeoff(detections, schedule, drawing_type)` — join detections to schedule, roll up m² per category.
- `measure_takeoff(regions, drawing_type, scale)` — sum polygon areas in m².
- `normalize_crop(crop, size=28)` — prepare crops for WiSARD classifier input.

## Limitations

- **v1 schedule input is CSV** — real drawings need Jesse's document-layout analysis (v2 gap).
- **Tag text extraction is a v2 gap** — current annotation sets carry glyph labels but not schedule tag text.
- **Drawing scale must be parsed separately** — the module does not read title blocks or calibration marks.
- **No HVAC quantities** — fixture counts are tracked but physical dimensions are not.
- **FloorPlanCAD and ArchCAD loaders are pending validation** — not yet wired for production use.
