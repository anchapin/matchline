---
phase: "02-detector"
plan: "01"
type: "execute"
wave: 1
tags: [detector, yolo, pipeline-integration, sahi, elevation-windows]
dependency_graph:
  requires:
    - id: "sahi_infer"
      path: "detector/sahi_infer.py"
      provides: "YOLO tiled inference output format"
  provides:
    - id: "DET-01"
      path: "datasets_adapter.py"
      provides: "detections_from_yolo_json() converting sahi JSON to Detection list"
    - id: "DET-04"
      path: "elevation_windows.py"
      provides: "YOLOWindowDetectorBackend using sahi_infer internally"
tech_stack:
  added:
    - sahi-style tiled inference integration
    - WindowDetectorBackend YOLO implementation
  patterns:
    - Backend registration pattern for detector backends
    - Real-sheet pipeline path (--image) vs synthetic path (--seed)
key_files:
  created: []
  modified:
    - path: "datasets_adapter.py"
      change: "Added detections_from_yolo_json() and CLASS_NAMES import"
    - path: "run_pipeline.py"
      change: "Added --image/--detections/--schedule-csv/--weights CLI flags; real-sheet path"
    - path: "elevation_windows.py"
      change: "Added YOLOWindowDetectorBackend class; registered as 'yolo' backend"
decisions:
  - decision: "detections_from_yolo_json() returns Detection objects with .tag='' (empty)"
    rationale: "Tag extraction is handled by separate OCR step (DET-02/DET-03)"
  - decision: "Stage 2 raises NotImplementedError for real-sheet path"
    rationale: "Building model from drawing requires architectural plan + link step; not available in simplified real-sheet path"
metrics:
  duration: "~5 minutes"
  tasks_completed: 3
  commits: 3
  files_modified: 3
  completed: "2026-09-22"
---

# Phase 02 Plan 01: YOLO Pipeline Integration — SUMMARY

**One-liner:** Wired the YOLO detector into the unified pipeline via `detections_from_yolo_json()` and `YOLOWindowDetectorBackend`, enabling real-sheet processing with `--image/--detections`.

## Objective

Wire the YOLO detector into the unified pipeline for real-sheet processing; implement the YOLO WindowDetectorBackend for elevation_windows.py.

## Completed Tasks

| # | Task | Status | Commit |
|---|------|--------|--------|
| 1 | Add `detections_from_yolo_json()` to `datasets_adapter.py` | ✓ | `ee1f00c` |
| 2 | Add `--image` and `--detections` flags to `run_pipeline.py` | ✓ | `514315b` |
| 3 | Implement `YOLOWindowDetectorBackend` in `elevation_windows.py` | ✓ | `38d9a3b` |

## What Was Built

### `datasets_adapter.py` — `detections_from_yolo_json()`
Converts sahi_infer.py JSON output (`{image, width, height, preds:[{cls,conf,x0,y0,x1,y1}]}`) into `Detection` objects with:
- `label` from `CLASS_NAMES[cls]`
- `bbox` in sheet pixel coordinates
- `source` set to the sheet identifier
- `tag` left empty (filled by DET-02/DET-03 OCR step)

### `run_pipeline.py` — real-sheet CLI path
Added mutually exclusive `--seed` (synthetic) vs `--image` (real-sheet) group:
- `--image`: path to real sheet image
- `--detections`: path to sahi_infer.py JSON predictions
- `--schedule-csv`: optional schedule CSV for tag→dimensions mapping
- `--weights`: path to YOLO weights (default: `detector/best.pt`)

When `--image` is provided, stage 1 loads real detections and skips synthetic generation. Stage 2 raises `NotImplementedError` with clear guidance (building model from drawing needs arch plan + link step).

### `elevation_windows.py` — `YOLOWindowDetectorBackend`
Sahi-style tiled inference backend:
- Converts grayscale images to RGB for YOLO
- Writes temp PNG, calls `infer_sheet()`, deletes temp
- Filters to windows only (cls==1), builds `ElevationWindowObs`
- Registered as `"yolo"` backend alongside existing `"contour"` backend

## Verification

| Criterion | Result |
|-----------|--------|
| `detections_from_yolo_json` converts sahi JSON → Detection list | ✓ `1 window (100.0, 200.0, 300.0, 500.0)` |
| `run_pipeline.py --help` shows flags | ✓ `--seed`, `--image`, `--detections`, `--schedule-csv` all present |
| `YOLOWindowDetectorBackend` registered as `"yolo"` | ✓ `True YOLOWindowDetectorBackend` |
| Backward compatibility (`--seed` still works) | ✓ Unchanged synthetic path |

## Commits

- `ee1f00c` feat(02-01): add detections_from_yolo_json() converting sahi JSON to Detection list
- `514315b` feat(02-01): add --image/--detections flags for real-sheet processing
- `38d9a3b` feat(02-01): implement YOLOWindowDetectorBackend for elevation_windows.py

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

- [x] `datasets_adapter.py`: `detections_from_yolo_json` function exists and works
- [x] `run_pipeline.py`: `--image/--detections/--schedule-csv` flags present in CLI
- [x] `elevation_windows.py`: `YOLOWindowDetectorBackend` class exists and registered as `"yolo"`
- [x] All commits exist with correct hashes
