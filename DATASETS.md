# External Datasets

External datasets are **not committed** to the repository. All demos and commands that
require external data fail clearly when the data is absent.

Base path convention: all external datasets live under `~/workspace/datasets/`.

---

## AEC-Bench (AEC Geometric Bench)

**Local path:** `~/workspace/datasets/aec-geometric-bench/dataset`

**Environment variable:** None (path passed via `--aec-bench` CLI flag or `AEC_ROOT` in scripts)

**What it is:** Annotated architectural drawings (15-sheet release) with XML annotations
(`annotations_15.xml`) covering rooms, doors, windows, and wall boundaries.

**Used for in matchline:**

- `matchline run --aec-bench /path/to/aec-bench` — unified pipeline on real drawings
- `synth/gap_experiment.py` — gap analysis against synthetic vs. real building geometry
- `datasets_adapter.py` — validation of the synthetic data adapter against real annotations
- `detector/convert_aec.py` — conversion script producing `~/workspace/datasets/detector_yolo/aec/`

**Setup:**

```bash
# Clone or extract AEC-Bench into the expected location
mkdir -p ~/workspace/datasets/aec-geometric-bench/
# Place dataset contents so that annotations_15.xml is directly under:
# ~/workspace/datasets/aec-geometric-bench/dataset/annotations_15.xml
```

**Reference:** <http://aec-bench.github.io/>

---

## CMP Facade Database

**Local path:** `~/workspace/datasets/cmp-facade`

**Environment variable:** None (overridden via `--data-root` CLI flag or `DATA_ROOT` in scripts)

**What it is:** Annotated facade images with bounding-box XML annotations for windows,
doors, and facade elements. License: CC BY-SA.

**Used for in matchline:**

- `matchline facade-takeoff --n 5` — CMP Facade area takeoffs (wall/glazing/door fractions)
- `run_facade_takeoff.py` — generates `facade_priors.json` for the BEM review queue
- `facade_takeoff.py` — core extraction logic for facade elements

**Setup:**

```bash
mkdir -p ~/workspace/datasets/cmp-facade
# Download from http://cmp.felk.cvut.cz/~tylecr1/facade/
# Extract so images and XML annotations are directly under ~/workspace/datasets/cmp-facade/
```

**Reference:** <http://cmp.felk.cvut.cz/~tylecr1/facade/> — Tylecek & Sara

---

## CubiCasa5K

**Local path:** `~/workspace/datasets/cubicasa5k/cubicasa5k`

**Environment variable:** None

**What it is:** 5,000 annotated floor plan images with room labels, door/window symbols,
and spatial hierarchy. Used widely in the building performance community.

**Used for in matchline:**

- `detector/convert_cubicasa.py --src ~/workspace/datasets/cubicasa5k/cubicasa5k --out ~/workspace/datasets/detector_yolo/cubicasa` — converts to YOLO format for detector fine-tuning

**Setup:**

```bash
mkdir -p ~/workspace/datasets/cubicasa5k/
# Download from official CubiCasa5K source and place contents under:
# ~/workspace/datasets/cubicasa5k/cubicasa5k/
```

**Output:** YOLO-formatted annotations are written to `~/workspace/datasets/detector_yolo/cubicasa/`

---

## FloorPlanCAD

**Local path:** `~/workspace/datasets/floorplancad/train-00000-of-00001.parquet`

**Environment variable:** None

**What it is:** CAD-grade floor plan dataset in Parquet format, covering architectural
symbols, walls, doors, windows, and room labels at high spatial precision.

**Used for in matchline:**

- `detector/convert_floorplancad.py --src ~/workspace/datasets/floorplancad/train-00000-of-00001.parquet --out ~/workspace/datasets/detector_yolo/floorplancad` — converts to YOLO format for detector fine-tuning

**Setup:**

```bash
mkdir -p ~/workspace/datasets/floorplancad/
# Place the parquet file:
# ~/workspace/datasets/floorplancad/train-00000-of-00001.parquet
```

**Output:** YOLO-formatted annotations are written to `~/workspace/datasets/detector_yolo/floorplancad/`

---

## Detector Runs (YOLO fine-tuning output)

**Local path:** `~/workspace/datasets/detector_runs/<name>/`

**Environment variable:** None

**What it is:** Output directory for YOLO fine-tuning runs started via `detector/train.py`.
Each run gets its own timestamped subdirectory.

**Used for in matchline:**

- `detector/train.py` — YOLO fine-tuning entry point; reads source datasets and writes run outputs here
- `detector/eval_zero_shot.py` — zero-shot evaluation reads `~/workspace/datasets/detector_yolo/aec/labels/eval`

**Setup:** Created automatically by `detector/train.py`. No manual setup required.

**Note:** These directories are never created inside the repo — they always live under
`~/workspace/datasets/detector_runs/`.

---

## Converted YOLO Datasets (intermediate artifacts)

**Local path:** `~/workspace/datasets/detector_yolo/{aec,cubicasa,floorplancad}/`

**Environment variable:** None

**What it is:** YOLO-format annotation directories produced by the conversion scripts
(`convert_aec.py`, `convert_cubicasa.py`, `convert_floorplancad.py`) from the raw
external datasets. Used as inputs to `detector/train.py`.

| Source dataset | Output subdirectory |
|---|---|
| AEC-Bench | `~/workspace/datasets/detector_yolo/aec/` |
| CubiCasa5K | `~/workspace/datasets/detector_yolo/cubicasa/` |
| FloorPlanCAD | `~/workspace/datasets/detector_yolo/floorplancad/` |

**Setup:** Produced automatically by running the respective `convert_*.py` script.

---

## Dataset Presence Check

When a dataset is required but absent, the CLI fails with a clear error rather than
producing incorrect results. For example:

```bash
# matchline run --aec-bench ~/workspace/datasets/aec-geometric-bench/dataset
# → fails clearly if annotations_15.xml is not found

# matchline facade-takeoff --data-root ~/workspace/datasets/cmp-facade
# → fails clearly if CMP Facade XML/image files are not found
```

See `docs/cli.md` for the `--data-root` and `--aec-bench` flags, and each dataset's
conversion script for the exact expected file layout.
