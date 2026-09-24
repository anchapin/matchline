# Phase 2: Detector Integration

**Goal**: YOLO detector wired into the pipeline as Stage-1 symbol spotting backend; schedule tables parsed from drawing images (not just CSV).

**Depends on**: Phase 1 (orchestration)

**Requirements**: DET-01, DET-02, DET-03, DET-04

**Success Criteria**:
1. `detector/sahi_infer.py` JSON output is consumed as `Detection` list by `matchline run`
2. `parse_schedule_table(drawing_image)` extracts `{tag: ScheduleEntry}` without raising NotImplementedError
3. `sliding_window_tag_extract` on real elevation sheets extracts tag text from text-adjacent regions
4. `WindowDetectorBackend` YOLO backend wired into `elevation_windows.py`; produces `ElevationWindowObs` comparable to contour backend

**Plans**: 2 plans in 2 waves
