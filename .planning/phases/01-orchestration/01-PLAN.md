---
phase: "01-orchestration"
plan: "01"
type: "execute"
wave: 1
depends_on: []
files_modified:
  - "run_pipeline.py"
  - "cli.py"
autonomous: true
requirements:
  - "ORCH-01"
  - "ORCH-02"
  - "ORCH-03"
must_haves:
  truths:
    - "`matchline run --seed 101` produces stage JSON files in --out-dir"
    - "Validation failure exits with non-zero code and prints failed check name"
    - "`matchline run --seed 101` output matches manual chaining of run_validation.py + run_bem_export.py"
  artifacts:
    - path: "run_pipeline.py"
      provides: "Unified pipeline with stage chaining and intermediate JSON output"
      min_lines: 100
    - path: "cli.py"
      provides: "matchline run CLI subcommand"
      exports:
        - "cmd_run"
  key_links:
    - from: "cli.py"
      to: "run_pipeline.py"
      via: "cmd_run() calls run_pipeline.main()"
      pattern: "run_pipeline.main"
    - from: "run_pipeline.py"
      to: "synth.multidiscipline"
      via: "generate_building()"
      pattern: "generate_building"
    - from: "run_pipeline.py"
      to: "link"
      via: "build_model()"
      pattern: "build_model"
    - from: "run_pipeline.py"
      to: "validate"
      via: "run_checks()"
      pattern: "run_checks"
    - from: "run_pipeline.py"
      to: "bem_export"
      via: "model_from_takeoff(), write_gbxml(), write_ifc4()"
      pattern: "model_from_takeoff"
---

## Objective

Create `run_pipeline.py` that chains all pipeline stages into one `matchline run` command with intermediate JSON outputs at each stage boundary and fail-fast on validation errors.

## Purpose

Unifies the currently-separate `run_validation.py` and `run_bem_export.py` into a single pipeline. Each stage writes its output to `<out-dir>/stage_NN_<name>.json` so operators can inspect what happened at any step. Validation errors block export (exit code 1, not silent).

## Output

- `run_pipeline.py` — unified pipeline runner
- `cli.py` updated with `matchline run` subcommand

## Pipeline Stages

```
Stage 1: generate_building()           -> stage_01_building.json
Stage 2: build_model()                 -> stage_02_model.json
Stage 3: simplify_ring()               -> stage_03_simplified.json
Stage 4: run_checks()                  -> stage_04_validation.json
Stage 5: model_from_linked_model()     -> BEMModel -> write_gbxml/write_ifc4
```

## Tasks

<task type="auto">
  <name>Task 1: Create run_pipeline.py</name>
  <files>run_pipeline.py</files>
  <action>
Create `run_pipeline.py` with:

1. **Imports**: `generate_building` from `synth.multidiscipline`, `build_model` from `link`, `footprint_from_regions` and `simplify_ring` from `geometry_simplify`, `run_checks` and `export_gate` from `validate`, `model_from_takeoff` and `write_gbxml` and `write_ifc4` from `bem_export`, plus `json`, `sys`, `pathlib.Path`, `dataclasses.asdict`.

2. **CLI parser** accepting:
   - `--seed` (int, required; mutually exclusive with `--building-id`)
   - `--out-dir` (Path, default `bem_out/`)
   - `--open-office-span` (bool, default False)
   - `--elevation-key` (str, default "elev_grid")
   - `--simplify-tol` (float, default 0.02)

3. **Stage 1**: Call `generate_building(seed, open_office_span=open_office_span)`, write JSON to `out_dir / "stage_01_building.json"`.

4. **Stage 2**: Call `build_model(bldg, elevation_key=elevation_key, building_name=bldg["building_id"])`, write `model.to_json()` and `link_report` to `out_dir / "stage_02_model.json"`.

5. **Stage 3**: Get `wall_height = model.levels[0].wall_height_m`, build `ring = footprint_from_regions([sp.polygon_m for sp in model.spaces.values()])`, call `simplify_ring(ring, tol=simplify_tol, wall_height=wall_height)`, write simplify result to `out_dir / "stage_03_simplified.json"`.

6. **Stage 4**: Call `run_checks(model, sres=sres)`, write report to `out_dir / "stage_04_validation.json"`.

7. **Stage 5 (fail-fast)**: If `export_gate(report)` is False, print "VALIDATION FAILED: {n_errors} error(s)" and each failed check name + detail, then `sys.exit(1)`.

8. **Stage 6 (BEM export)**: Since `bem_export.model_from_takeoff()` expects `TakeoffResult` path (not linked `BuildingModel`), implement a minimal adapter `model_from_linked_model(model: BuildingModel, simplified_geometry) -> BEMModel` that extracts `model.spaces`, `model.openings`, and `model.ring_m` into a `BEMModel`-compatible structure. Then call `write_gbxml` and `write_ifc4`.

9. **Helper** `write_json(p: Path, data)`: `p.parent.mkdir(parents=True, exist_ok=True)` then `p.write_text(json.dumps(data, indent=2, default=str))`.

10. **Main**: `def main(args)`, `if __name__ == "__main__": main(parse_args())`.
  </action>
  <verify>
    <automated>cd /home/alex/Projects/matchline && python run_pipeline.py --seed 101 --out-dir /tmp/pipeline_test && ls /tmp/pipeline_test/stage_*.json | wc -l</automated>
  </verify>
  <done>`run_pipeline.py --seed 101 --out-dir /tmp/test` produces `stage_01_building.json` through `stage_04_validation.json` and exits 0</done>
</task>

<task type="auto">
  <name>Task 2: Add matchline run CLI subcommand</name>
  <files>cli.py</files>
  <action>
Add to `cli.py`:

1. `def cmd_run(args) -> None:` that imports `run_pipeline` and calls `run_pipeline.main(seed=args.seed, out_dir=args.out_dir, open_office_span=args.open_office_span, elevation_key=args.elevation_key, simplify_tol=args.simplify_tol)`.

2. In `build_parser()`, add:
```python
p = sub.add_parser("run", help="unified pipeline: generate + link + validate + export")
p.add_argument("--seed", type=int, required=True)
p.add_argument("--out-dir", default="bem_out")
p.add_argument("--open-office-span", action="store_true")
p.add_argument("--elevation-key", default="elev_grid", choices=["elev_grid", "elev_nogrid"])
p.add_argument("--simplify-tol", type=float, default=0.02)
p.set_defaults(func=cmd_run)
```
  </action>
  <verify>
    <automated>cd /home/alex/Projects/matchline && python -c "import cli; cli.main(['run', '--help'])" 2>&1 | grep -c "usage"</automated>
  </verify>
  <done>`matchline run --seed 101` is a working CLI command</done>
</task>

## Verification

1. **Unit-level:**
   ```bash
   python run_pipeline.py --seed 101 --out-dir /tmp/pipeline_test
   ls /tmp/pipeline_test/stage_*.json   # 4 files
   python -c "import json; d=json.load(open('/tmp/pipeline_test/stage_04_validation.json')); print(d['n_errors'])"  # should be 0
   ```

2. **Golden-file (ORCH-01):** Compare pipeline output against manual chaining of `run_validation.py` + `run_bem_export.py` on seed 101.

3. **Fail-fast (ORCH-03):** Temporarily modify a space area in stage 02 JSON to break conservation law, re-run pipeline, verify exit code 1 and failed check name printed.

4. **ORCH-04:** `matchline validate` continues to pass on all 3 buildings (existing behavior).

## Success Criteria

1. `python run_pipeline.py --seed 101 --out-dir /tmp/test` creates 4 stage JSON files and exits 0
2. `python run_pipeline.py --seed 101 --out-dir /tmp/test` produces output comparable to manually running `run_validation.py` + `run_bem_export.py`
3. Validation errors produce exit code 1 with a clear error message naming the failed check
4. `matchline validate` passes on `bldg_3room`, `bldg_open_office`, `bldg_8room`
5. `matchline run --seed 101 --out-dir /tmp/test` is a working CLI command

## Output

After completion, create `.planning/phases/01-orchestration/01-SUMMARY.md`
