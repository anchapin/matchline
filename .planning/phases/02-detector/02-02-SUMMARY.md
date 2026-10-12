---
phase: "02-detector"
plan: "02"
type: "execute"
wave: 2
tags: [detector, schedule-table, ocr, wisard, tag-extraction]
dependency_graph:
  requires:
    - id: "DET-01"
      path: "datasets_adapter.py"
      provides: "detections_from_yolo_json() — Detection list from YOLO"
    - id: "DET-04"
      path: "elevation_windows.py"
      provides: "YOLOWindowDetectorBackend registered as 'yolo'"
  provides:
    - id: "DET-02"
      path: "datasets_adapter.py"
      provides: "parse_schedule_table implementation using document-layout + OCR"
    - id: "DET-03"
      path: "jesse.py"
      provides: "sliding_window_tag_extract() for WiSARD v2 tag OCR"
tech_stack:
  added:
    - pytesseract OCR integration
    - OpenCV line detection for table grid detection
    - WiSARD sliding-window tag extraction
  patterns:
    - Document-layout table parsing via morphological operations
    - Left-of-symbol OCR for schedule tag extraction
key_files:
  created: []
  modified:
    - path: "datasets_adapter.py"
      change: "Added parse_schedule_table() implementation (129 lines)"
    - path: "jesse.py"
      change: "Added sliding_window_tag_extract() and tag_detections() (61 lines)"
decisions:
  - decision: "parse_schedule_table raises NotImplementedError when no table grid detected"
    rationale: "Clear signal for operators to use parse_schedule_csv() instead"
  - decision: "Tags extracted to the LEFT of detection bbox"
    rationale: "Schedule tags on drawings typically appear as callout labels adjacent to symbols"
metrics:
  duration: "~3 minutes"
  tasks_completed: 2
  commits: 2
  files_modified: 2
  completed: "2026-09-22"
---

# Phase 02 Plan 02: Schedule Table + WiSARD Tag Extraction — SUMMARY

**One-liner:** Implemented `parse_schedule_table()` via document-layout analysis + OCR and `sliding_window_tag_extract()` for WiSARD v2 tag extraction, completing the real-drawing detection pipeline.

## Objective

Implement schedule table parsing from drawing images (DET-02) and sliding-window WiSARD tag extraction from real drawings (DET-03), completing the real-drawing detection pipeline.

## Completed Tasks

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Implement `parse_schedule_table` in `datasets_adapter.py` | ✓ | `bfd1469` |
| 2 | Implement `sliding_window_tag_extract` in `jesse.py` | ✓ | `0c0f3d0` |

## What Was Built

### `datasets_adapter.py` — `parse_schedule_table()`
Document-layout + OCR implementation:
1. **Line detection**: Horizontal and vertical lines detected via morphological closing + threshold
2. **Cell segmentation**: Grid intersection points define row/column boundaries
3. **OCR**: pytesseract extracts text from each cell with `--psm 6` (single uniform block)
4. **Header mapping**: Identifies header row by keywords ("tag", "type", "width", "height", "dims")
5. **Column mapping**: Maps header columns to `ScheduleEntry` fields (tag, category, width_m, height_m, note)
6. **Category inference**: Maps cell text to category ("window", "door", "lighting", "opening")

Raises `NotImplementedError` when:
- Fewer than 2 horizontal or vertical lines detected (no table grid)
- No header row found with recognized column headers

### `jesse.py` — `sliding_window_tag_extract()` and `tag_detections()`

**`sliding_window_tag_extract()`**: For each detection bbox:
- Crops a region to the LEFT of the symbol (2px gap, same height, up to 80px wide)
- Runs pytesseract OCR on the crop
- Extracts tag patterns via regex: `[A-Z]\d*-\d+|[A-Z]\d+` (e.g., D-01, W-2, W12)
- Returns `{detection_index: tag_str}`

**`tag_detections()`**: Wires extracted tags into `Detection` objects in-place, modifying and returning the list.

## Verification

| Criterion | Result |
|-----------|--------|
| `parse_schedule_table(image)` is callable | ✓ `True` |
| `sliding_window_tag_extract(image, detections)` is callable | ✓ `True` |
| `tag_detections(detections, image)` is callable | ✓ `True` |

Note: Full end-to-end test requires a real drawing image with a table and pytesseract installed.

## Commits

- `bfd1469` feat(02-02): implement parse_schedule_table using document-layout + OCR
- `0c0f3d0` feat(02-02): implement sliding_window_tag_extract and tag_detections in jesse.py

## Deviations from Plan

None — plan executed exactly as written.

## Dependencies Satisfied

- DET-01 ✓ (detections_from_yolo_json — wired in Wave 1)
- DET-04 ✓ (YOLOWindowDetectorBackend — implemented in Wave 1)
- DET-02 ✓ (parse_schedule_table — implemented in Wave 2)
- DET-03 ✓ (sliding_window_tag_extract — implemented in Wave 2)

## Self-Check

- [x] `datasets_adapter.py`: `parse_schedule_table` function exists and is callable
- [x] `jesse.py`: `sliding_window_tag_extract` and `tag_detections` functions exist and are callable
- [x] All commits exist with correct hashes
