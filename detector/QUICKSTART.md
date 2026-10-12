# Getting Started with detector/

The `detector/` module is a self-contained YOLO fine-tuning track for door/window
symbol detection on architectural drawings. It has its own isolated Python venv and
is excluded from the main project's ruff checks.

## Environment Setup

### 1. Create the detector venv

```bash
cd /path/to/matchline
python3 -m venv detector/.venv-det
```

### 2. Install dependencies

The `+cpu` local version tags on torch/torchvision exist only on the PyTorch CPU
index, so `--extra-index-url` is **required**:

```bash
detector/.venv-det/bin/pip install \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r detector/requirements-detector.txt
```

> **Note (2026-09-28):** the old `torchvision==0.29.0+cpu.stub` pin is gone. It
> was a hand-written pure-torch NMS shim, needed only because the *original*
> sandbox's torchvision C++ extension failed to load, and it was never
> published to any index — so this requirements file could not actually be
> installed on a fresh machine. Stock `torchvision==0.29.0+cpu` is verified
> working here, `torchvision.ops.nms` included. See `detector/README.md`.


## Dataset Preparation

The detector uses a 2-class taxonomy: **door** (0) and **window** (1). Three
conversion scripts are provided:

| Dataset | Script | Notes |
|---|---|---|
| CubiCasa5K | `convert_cubicasa.py` | Primary training set; door/window leaf groups from SVG |
| AEC Geometric Bench | `convert_aec.py` | Zero-shot eval set; PDF + CVAT XML |
| FloorPlanCAD | `convert_floorplancad.py` | Eval set; parquet → PNG |

Datasets live under `~/workspace/datasets/` per the convention below. Point the
run somewhere else with the `MATCHLINE_DATASETS` env var — `train.py` resolves
`configs/*.yaml` against it, so no machine path is ever written into the repo:

```bash
MATCHLINE_DATASETS=/data/datasets detector/.venv-det/bin/python detector/train.py ...
```

If the dataset root is missing, `train.py` exits with the path it tried and how
to override it, rather than failing later inside Ultralytics.

### CubiCasa5K

1. Download CubiCasa5K (see `~/workspace/datasets/` convention)
2. Convert:

```bash
. detector/.venv-det/bin/python detector/convert_cubicasa.py \
    --src /path/to/cubicasa5k \
    --dst ~/workspace/datasets/detector_yolo/cubicasa
```

3. Verify integrity:

```bash
. detector/.venv-det/bin/python detector/check_dataset.py \
    --root ~/workspace/datasets/detector_yolo/cubicasa
```

Expected output: ~42K doors and ~35K windows across train/val/test splits.

### AEC Geometric Bench

1. Download AEC Geometric Bench PDF + CVAT XML annotations
2. Convert to YOLO format:

```bash
. detector/.venv-det/bin/python detector/convert_aec.py \
    --pdf /path/to/aec/sheets \
    --cvat /path/to/aec/annotations.xml \
    --dst ~/workspace/datasets/detector_yolo/aec
```

The eval split (15 sheets) is used for zero-shot evaluation only.

### FloorPlanCAD

1. Download FloorPlanCAD parquet dataset
2. Convert:

```bash
. detector/.venv-det/bin/python detector/convert_floorplancad.py \
    --src /path/to/floorplancad.parquet \
    --dst ~/workspace/datasets/detector_yolo/floorplancad
```

## Training

Training outputs go to `~/workspace/datasets/detector_runs/` (never committed).

### Harness validation (quick sanity check)

```bash
. detector/.venv-det/bin/python detector/train.py \
    --data detector/configs/cubicasa.yaml \
    --epochs 3 \
    --subset-train 400 \
    --subset-val 100 \
    --name harness_check
```

### Full training run

```bash
detector/.venv-det/bin/python detector/train.py \
    --data detector/configs/cubicasa.yaml \
    --epochs 25 \
    --name cubi_yolo11n
```

### ECA A/B (#500)

Both arms use `--model` + `--pretrained` so they start from identical COCO
weights. `--pretrained` is **not** Ultralytics' name-based `load()`: inserting
ECA rows shifts every later layer index, so names stop corresponding and a plain
load would leave the ECA arm training from scratch. See `detector/README.md`.

```bash
COMMON="--data detector/configs/cubicasa.yaml --epochs 25 --seed 0 --pretrained yolo11n.pt"
detector/.venv-det/bin/python detector/train.py --model detector/configs/yolo11n_baseline.yaml $COMMON --name eca_base
detector/.venv-det/bin/python detector/train.py --model detector/configs/yolo11n_eca.yaml     $COMMON --name eca_eca
```

### Resuming a run

```bash
. detector/.venv-det/bin/python detector/train.py \
    --data detector/configs/cubicasa.yaml \
    --resume ~/workspace/datasets/detector_runs/cubi_yolo11n/weights/last.pt \
    --epochs 10 \
    --name cubi_yolo11n_cont
```

## Evaluation

### SAHI tiling inference

For large architectural sheets (4800×7200+ px), use SAHI sliding-window
tiling + NMS merge:

```bash
. detector/.venv-det/bin/python detector/sahi_infer.py \
    --weights ~/workspace/datasets/detector_runs/cubi_yolo11n/weights/best.pt \
    --image ~/workspace/datasets/detector_yolo/aec/images/eval/sheet_01.png \
    --out /tmp/sheet01_preds.json
```

### Zero-shot evaluation

```bash
. detector/.venv-det/bin/python detector/eval_zero_shot.py \
    --preds /tmp/sheet01_preds.json \
    --labels ~/workspace/datasets/detector_yolo/aec/labels/eval
```

## Integration with Main Pipeline

The detector weights are consumed by the main pipeline via the SAHI inference
step. After training:

1. Note the path to `best.pt` in `~/workspace/datasets/detector_runs/<name>/weights/`
2. The main pipeline's extraction stage calls `sahi_infer.py` with these weights
3. Detected boxes flow into the review queue for human confirmation before BEM export

Run the main pipeline with detector results:

```bash
matchline run --detector-weights ~/workspace/datasets/detector_runs/cubi_yolo11n/weights/best.pt
```

## Dataset Storage Convention

All datasets live under `~/workspace/datasets/` and are **never committed** to the
repo. The detector conversions produce a YOLO-format tree:

```
detector_yolo/
  cubicasa/
    images/train/ ... .png
    images/val/   ... .png
    labels/train/ ... .txt  (class cx cy w h, normalized 0-1)
    labels/val/   ... .txt
  aec/
    images/eval/  ... .png
    labels/eval/  ... .txt
```

## Troubleshooting

**`ModuleNotFoundError: torch`**
→ The detector venv is not activated. Always use `. detector/.venv-det/bin/python`
or activate the venv first.

**`ImportError: cannot import name '_C_stable'`**
→ This is the known broken torchvision C++ extension. The stub in
`requirements-detector.txt` provides a pure-Python workaround. On hardware with
a working torch build, replace `torchvision==0.29.0+cpu.stub` with the standard
package.

**Dataset integrity check fails**
→ Run `detector/check_dataset.py --root <dataset_root>` to diagnose missing
labels or out-of-range class IDs.

## Further Reading

- `detector/README.md` — full technical details, taxonomy, conversion notes
- `docs/ecosystem_audit.md` — rationale for YOLO + SAHI + legend-learning strategy
- `detector/classes.py` — class ID definitions
