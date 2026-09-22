---
phase: 05-realdata-ci
plan: "03"
type: execute
wave: 1
depends_on: []
files_modified:
  - datasets_adapter.py
autonomous: true
requirements:
  - CI-03
  - CI-04
must_haves:
  truths:
    - "`load_floorplancad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError"
    - "`load_archcad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError"
  artifacts:
    - path: "datasets_adapter.py"
      provides: "FloorPlanCAD and ArchCAD loaders"
      exports: ["load_floorplancad", "load_archcad"]
  key_links:
    - from: "load_floorplancad()"
      to: "_floorplancad_from_parquet() or _floorplancad_from_svg()"
      via: "auto-detection of available data format"
    - from: "load_archcad()"
      to: "HF parquet export layout"
      via: "auto-detection of available data format"
---

<objective>
Implement `load_floorplancad()` and `load_archcad()` in `datasets_adapter.py` so they return valid `SymbolSample` and `TakeoffResult` objects without raising `NotImplementedError`.

Purpose: Both functions currently raise NotImplementedError. The dataset formats need to be inspected (or known) and the loaders implemented.
Output: Working implementations of both functions.
</objective>

<execution_context>
@/home/alex/.agents/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
@datasets_adapter.py (lines 674-750 - existing stub implementations)
@datasets_adapter.py (lines 1-50 - imports, SymbolSample, TakeoffResult, Region classes)
@datasets_adapter.py (lines 579-656 - load_aec_bench as reference implementation)

# Key context from datasets_adapter.py:

The file already has:
```python
# FloorPlanCAD (lines 660-715):
# - Tries parquet first (train-*.parquet or *.parquet in root)
# - Falls back to svg_gt/*.svg + JSON annotation layout
# - _floorplancad_from_parquet raises NotImplementedError with TODO to confirm column names
# - _floorplancad_from_svg raises NotImplementedError "not yet validated"

# ArchCAD (lines 718-745):
# - Checks if root has any files
# - Raises FileNotFoundError if empty
# - Raises NotImplementedError pending HF export layout inspection
```

Both functions should return: `tuple[list[SymbolSample], TakeoffResult]`

The `SymbolSample` and `TakeoffResult` types are imported from local modules. Check the imports at the top of datasets_adapter.py.

From load_aec_bench (reference):
- `SymbolSample(crop, label, source, bbox)` where crop is a normalized image array
- `TakeoffResult` has `regions: List[Region]` and `scale: DrawingScale`
- `Region(category, polygon, source, drawing_type, label)`

FloorPlanCAD is described as:
- SVG-based dataset with svg_gt/*.svg + JSON annotation files
- HF parquet export available
- ~30 thing classes (doors, windows, furniture) + wall

ArchCAD-400K:
- 413,062 chunks from 5,538 drawings
- HF release at jackluoluo/ArchCAD
- Vector primitives grouped into symbols (SVG line inputs)

IMPORTANT DISCOVERY NOTE: Before implementing, you MUST inspect the actual dataset format on disk:
1. Check if FloorPlanCAD parquet or SVG exists at ~/workspace/datasets/ or similar path
2. Check if ArchCAD parquet or files exist at ~/workspace/datasets/
3. If datasets are not downloaded, the functions should raise FileNotFoundError with a helpful message (not NotImplementedError)

For FloorPlanCAD SVG path: Look for svg_gt/ directory and JSON annotation files. Parse them to extract symbol bounding boxes and categories.
For ArchCAD: Inspect the HF parquet schema if available. If parquet isn't downloaded, raise FileNotFoundError.

The existing stub implementations raise NotImplementedError which is wrong - the correct error when data isn't available is FileNotFoundError.
</context>

<tasks>

<task type="auto">
  <name>Inspect FloorPlanCAD dataset and implement loader</name>
  <files>datasets_adapter.py</files>
  <action>
1. First, look for FloorPlanCAD data:
   - Check `~/workspace/datasets/floorplancad/` for parquet or SVG files
   - If found, inspect the actual format (parquet schema or SVG structure)
   - Document what you find

2. Implement `_floorplancad_from_parquet(path, size, scale_m_per_px)`:
   - Read the parquet file
   - Determine the actual column names (the TODO says they need to be confirmed)
   - Extract image data and symbol annotations
   - Return `tuple[list[SymbolSample], TakeoffResult]`

3. Implement `_floorplancad_from_svg(split_dir, size, scale_m_per_px)`:
   - Look at the SVG + JSON annotation layout described in the comment
   - Parse SVG files for symbol regions
   - Match with JSON annotations for labels
   - Return `tuple[list[SymbolSample], TakeoffResult]`

4. Update `load_floorplancad()`:
   - If neither parquet nor SVG found, raise FileNotFoundError (NOT NotImplementedError)
   - If parquet found, call `_floorplancad_from_parquet`
   - If SVG found, call `_floorplancad_from_svg`
   - Use the same pattern as `load_aec_bench` for constructing SymbolSample and TakeoffResult

Key reference: `load_aec_bench` at line 579 shows the pattern for:
- Creating SymbolSample with normalized crop, label, source, bbox
- Creating Region with category, polygon, source, drawing_type, label
- Building a TakeoffResult from regions list

For FloorPlanCAD, the AEC_OBJECT_TO_TAKEOFF mapping (used in load_aec_bench) won't apply directly - you need to create appropriate category mappings for FloorPlanCAD's own class labels.
</action>
  <verify>
    <automated>python -c "
from pathlib import Path
from datasets_adapter import load_floorplancad
try:
    result = load_floorplancad(Path.home() / 'workspace' / 'datasets' / 'floorplancad')
    samples, takeoff = result
    print(f'OK: {len(samples)} samples, {len(takeoff.regions)} regions')
except FileNotFoundError as e:
    print(f'FileNotFoundError (expected if not downloaded): {e}')
except NotImplementedError as e:
    print(f'NotImplementedError (NEEDS FIX): {e}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
" 2>&1</automated>
  </verify>
  <done>`load_floorplancad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError</done>
</task>

<task type="auto">
  <name>Inspect ArchCAD dataset and implement loader</name>
  <files>datasets_adapter.py</files>
  <action>
1. First, look for ArchCAD data:
   - Check `~/workspace/datasets/archcad/` or similar path
   - If found, inspect the actual format
   - Document what you find

2. Implement `load_archcad(root, size, scale_m_per_px)`:
   - Check if root is empty → raise FileNotFoundError with message about downloading
   - Auto-detect format: parquet or directory of images + metadata
   - Parse the data to extract symbol samples and regions
   - Return `tuple[list[SymbolSample], TakeoffResult]`

3. Update the NotImplementedError to FileNotFoundError if data isn't available:
   - The current implementation raises NotImplementedError which is wrong
   - FileNotFoundError is correct when the dataset hasn't been downloaded
   - Only implement the actual loading once you can inspect the format

Key reference: Same SymbolSample and TakeoffResult pattern as FloorPlanCAD.

The comment says the HF export layout is "not yet inspected" - you MUST inspect it first if available. The loader should NOT just raise NotImplementedError forever.

IMPORTANT: The existing comment at line 741-745 says:
"Contents: " + ", ".join(sorted(p.name for p in root.iterdir())[:10])
This suggests the function DOES list directory contents but raises NotImplementedError anyway. Fix this to properly handle the available data or raise FileNotFoundError if nothing is there.
</action>
  <verify>
    <automated>python -c "
from pathlib import Path
from datasets_adapter import load_archcad
try:
    result = load_archcad(Path.home() / 'workspace' / 'datasets' / 'archcad')
    samples, takeoff = result
    print(f'OK: {len(samples)} samples, {len(takeoff.regions)} regions')
except FileNotFoundError as e:
    print(f'FileNotFoundError (expected if not downloaded): {e}')
except NotImplementedError as e:
    print(f'NotImplementedError (NEEDS FIX): {e}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')
" 2>&1</automated>
  </verify>
  <done>`load_archcad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError</done>
</task>

</tasks>

<verification>
Both functions can be called without raising NotImplementedError. If the dataset isn't downloaded, they raise FileNotFoundError with a helpful message about where to get the data.
</verification>

<success_criteria>
- `load_floorplancad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError
- `load_archcad(Path)` returns (list[SymbolSample], TakeoffResult) without NotImplementedError
- If dataset is not present, raise FileNotFoundError with descriptive message
- Both functions return valid objects that can be inspected (samples have .image, .label attributes; takeoff has .regions)
</success_criteria>

<output>
After completion, create `.planning/phases/05-realdata-ci/05-03-SUMMARY.md`
</output>
