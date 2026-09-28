# Detector track — fine-tuned YOLO baseline + PID-style SAHI tiling

Monday-critical path: evaluate a fine-tuned Ultralytics YOLO detector on
Alex's clean commercial drawings. See `docs/ecosystem_audit.md` for the
rationale (PID two-stage pattern: class-agnostic YOLO + SAHI tiling, then
one-shot legend/schedule classification; plain fine-tuned YOLO as baseline).

## Taxonomy

Unified 2-class taxonomy (`classes.py`): **door** (0), **window** (1) — the
takeoff-critical symbols. Per-dataset label maps:

- CubiCasa5K SVG: all `Door *` leaf groups (Swing Beside/Opposite, None,
  Slide, Zfold, RollUp, ParallelSlide, Fold) -> door; `Window Regular`,
  `Window Sauna` -> window. Container group `Doors` excluded.
- AEC Geometric Bench: Single/Double Swing Door -> door; Window -> window.
  Wall boxes and Area polygons skipped (not in baseline taxonomy).
- FloorPlanCAD: single/double/sliding_door -> door; window/bay_window/
  blind_window -> window.

## Converters

| script | source | notes |
|---|---|---|
| `convert_cubicasa.py` | CubiCasa5K `model.svg` | Walks SVG accumulating affine transforms; bbox of descendant geometry per symbol group (door bbox includes swing arc). Images **rendered from SVG via PyMuPDF** at uniform scale (longest side 1408) — verified that `F1_scaled.png` does NOT match the SVG viewBox geometry, so it is not used. Box alignment verified against independent visual grounding (9/9 symbols, centers within ~20px). |
| `convert_aec.py` | AEC bench PDF + CVAT XML | Renders each sheet PDF at 300dpi, resizes to exact XML dims. 15 sheets -> `eval` split (zero-shot set). |
| `convert_floorplancad.py` | FloorPlanCAD parquet | 1000x1000 PNGs, bboxes 0-1000 -> /1000. Test split only -> `eval` split. |

## Training

`train.py` — config-driven fine-tune (default `yolo11n.pt`, COCO-pretrained):

```bash
# harness validation: 3 epochs on 400-image subset
python3 train.py --data configs/cubicasa.yaml --epochs 3 \
    --subset-train 400 --subset-val 100 --name harness_check
# full run (background)
nohup python3 train.py --data configs/cubicasa.yaml --epochs 25 \
    --name cubi_yolo11n > /tmp/train_full.log 2>&1 &
```

Runs go to `~/workspace/datasets/detector_runs/` (never committed).

## Inference (SAHI tiling)

`sahi_infer.py` — sliding window (default 1024px tiles, 20% overlap) + NMS
merge, for 4800x7200+ sheets:

```bash
python3 sahi_infer.py --weights ~/workspace/datasets/detector_runs/cubi_yolo11n/weights/best.pt \
    --image ~/workspace/datasets/detector_yolo/aec/images/eval/sheet_01.png \
    --out /tmp/sheet01_preds.json
python3 eval_zero_shot.py --preds /tmp/sheet01_preds.json \
    --labels ~/workspace/datasets/detector_yolo/aec/labels/eval
```

## Status (2026-09-19)

- [x] Full CubiCasa conversion: train 4200 / val 400 / test 400 plans,
      42,392 doors + 35,419 windows, integrity-checked (`check_dataset.py`)
- [x] AEC eval set: 15 sheets, 678 doors + 494 windows (render verified)
- [x] FloorPlanCAD eval set: 5,308 images, 12,697 doors + 1,952 windows
- [x] Harness validation: 3 epochs, 400/100 subset -> val mAP50=0.65
      (P=0.741, R=0.655) — pipeline works end to end
- [x] SAHI tiling + NMS proven on a 7201x4801 AEC sheet (26 preds, ~30s CPU)
- [x] Zero-shot eval harness (`eval_zero_shot.py`) proven on sheet_01
- [ ] Full training run: 10 epochs YOLO11n, all 4200 train images (in progress,
      ~6h CPU) -> `~/workspace/datasets/detector_runs/cubi_yolo11n_e10/`
- [ ] Re-run SAHI + zero-shot P/R on 2-3 AEC sheets with the full model
- [ ] Monday: fine-tune/eval on Alex's commercial drawings; legend one-shot
      classification prototype (PID stage 2)

Environment note (updated 2026-09-28): the earlier "broken torchvision" note is
**resolved and retracted**. The C++ extension issue was specific to that sandbox;
on current hardware stock `torchvision==0.29.0+cpu` imports cleanly and
`torchvision.ops.nms` returns correct results (verified on a toy overlap case).
The hand-written `torchvision.ops.nms` stub has been removed from the pins, so
`requirements-detector.txt` is now installable from scratch — it previously
required `--extra-index-url https://download.pytorch.org/whl/cpu` *and* an
unpublished local stub, and could not be satisfied on a fresh machine.

## ECA experiment (#500)

Testing whether FloorYOLO's Efficient Channel Attention helps discriminate
visually similar intra-class door/window symbol variants.

- `eca.py` — the ECA block (ECA-Net) plus `transfer_aligned`
- `configs/yolo11n_eca.yaml` — yolo11n backbone + 4 ECA rows
- `configs/yolo11n_baseline.yaml` — the control arm (yolo11n, `nc: 2`)

Measured cost at 640px, both arms at `nc: 2`:

| | params | GFLOPs | fwd latency (CPU) |
|---|---|---|---|
| baseline | 2,590,230 | 6.50066 | 55.1 ms |
| +ECA | 2,590,248 (+18, +0.0007%) | 6.50066 (+0.0001%) | 56.8 ms (+3.0%) |

The FLOP cost is effectively zero; the ~3% is per-op dispatch overhead on a CPU
path, not arithmetic. The 18 added parameters are four `1x1xk` convs (k=3,5).

**Why `--pretrained` is not plain `model.load()`.** Inserting ECA rows shifts
every later layer index, so parameter *names* stop corresponding: in the ECA arm
index 3 is an `ECA` where the checkpoint has a `Conv`, index 4 is a `Conv` where
it has `C3k2`, and so on — 23 of 28 positions misalign. Ultralytics' name-based
load reports "Transferred 52/503 items" and leaves the ECA arm training from
scratch while the control arm starts from COCO, which would confound the whole
experiment. `train.py --pretrained` instead uses `transfer_aligned`, which skips
the inserted blocks and pairs the rest by forward order. Verified: on an
unmodified architecture it is bit-identical to Ultralytics' own `load()`, and
both arms then start from identical COCO weights (2,560,305 params, 98.8%; the
51 mismatched tensors are the `nc` 80→2 Detect head, which fine-tunes anyway).

Run the A/B (identical seed, split, epochs):

```bash
COMMON="--data configs/cubicasa.yaml --epochs 25 --seed 0 --pretrained yolo11n.pt"
python train.py --model configs/yolo11n_baseline.yaml $COMMON --name eca_base
python train.py --model configs/yolo11n_eca.yaml     $COMMON --name eca_eca
```

**Status: infrastructure complete, mAP delta NOT yet measured.** Both arms train
end to end on a synthetic smoke dataset. The measurement is blocked on (a) no
dataset present at `~/workspace/datasets/` on this machine and (b) the control
run above never having completed (see the unchecked box in Status). The ECA arm
is small, measurable, and revertible, but any mAP number quoted before the
control arm exists would be a comparison against nothing.

## Limitations

- Latency measured on CPU with train-mode forward semantics; a GPU number is
  the relevant one for deployment and has not been taken.
- The smoke dataset is synthetic and its labels are not physically aligned to
  the drawn symbols; it validates plumbing only, never accuracy.

