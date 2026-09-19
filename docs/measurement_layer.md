# Measurement layer: from classification to quantity takeoff

## Architecture: three stages, not segmentation

Per domain input, facades use symbols/tags for window (and door) types, with
a separate schedule table on the drawings giving dimensions per type. The
takeoff pipeline is therefore:

```
(1) SYMBOL SPOTTING ... spot + classify window/door type symbols on
    elevations and floor plans -> count per type tag.
    The WiSARD classifier's job (region in, label out).

(2) SCHEDULE PARSING .. document-layout / table-parsing task: find the
    window/door schedule table on the sheet, parse it into
    {tag: dimensions in meters}. Jesse's document-layout analysis is the
    natural fit here; v1 takes the schedule as CSV.

(3) AREA ROLLUP ....... count x scheduled dimensions, summed per category:
    TakeoffLine(tag, category, count, w, h, area_m2).
```

This replaces the earlier dense-segmentation idea. It is more deterministic
(counts are exact), more auditable (every m² traces to a tag × a schedule
row), and plays to the prototype's strengths (deterministic symbol
classification + deterministic table parsing). **No drawing scale is needed**
for the count × dims path -- dimensions come from the schedule table, not
from pixels.

Floor areas are the exception: they come from polygon measurement of Room /
area regions (`measure_takeoff`), which still needs `DrawingScale`.

## Output contract (`datasets_adapter.py`)

* `Detection` -- stage-1 output: label, schedule tag, score, bbox, source.
* `ScheduleEntry` -- stage-2 output: tag -> category + width_m/height_m.
* `TakeoffLine` / `TakeoffResult` -- stage-3 output: per-tag lines,
  per-category m² totals, and `unmatched` detections (tag with no schedule
  entry -- reported, never silently dropped).
* `SymbolSample` -- classifier training crops (unchanged).
* `Region` / `measure_takeoff` -- polygon path, retained for floor_area.

## Roadmap

* **v1 (done)**: proposals = ground-truth annotations (`detections_from_regions`);
  schedule = CSV (`parse_schedule_csv`); rollup validated on 1,632 real
  detections from AEC-geometric-bench (synthetic tags for the join demo).
* **v2**: proposals = WiSARD sliding-window scan; **tag extraction = OCR of
  text adjacent to each symbol bbox** (type tags like "A"/"W-1" are callouts,
  not part of the glyph -- this is the current gap: annotation sets carry
  class labels but not type tags). Schedule table located by document-layout
  analysis, cells OCR'd, normalized via the CSV interchange format.
* Dropped: dense per-pixel segmentation head. Polygon measurement stays only
  for floor areas.

## Validated numbers (AEC-geometric-bench, 15 real sheets)

Count × dims demo (synthetic schedule: A=1.8×1.5m, B=1.2×1.5m windows,
D1=0.9×2.1m doors):

| tag | category | count | m² |
|-----|----------|-------|------|
| A   | window   | 249   | 672.3 |
| B   | window   | 249   | 448.2 |
| D1  | door     | 1,134 | 2,143.3 |

Floor area via polygons: 109,428,470 px² (≈ 14,163 m² at reference scale).

## Known limitations

1. **Tags are the v2 gap.** Real takeoffs need per-type tags; v1 annotations
   don't have them.
2. **Schedule tables are redacted** in AEC-bench (title blocks removed), so
   the schedule path is validated with synthetic data only.
3. **Overlapping wall polygons double-count** in the polygon path; needs
   polygon union per category before summation.
4. **Elevations not yet exercised** -- all current corpora are plan sets;
   the contract already carries `drawing_type` per region/detection.
5. Door/window "areas" are opening footprints (w×h from schedule); leaf and
   glazing breakdowns need richer schedule columns (additive -- the
   `ScheduleEntry.note` field is the extension point).
