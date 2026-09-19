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

Environment note: this sandbox's torchvision wheels ship a broken C++
extension against torch 2.14.0+cpu (`_C_stable.so` fails to load), so the
project venv (`~/workspace/.venv-det`, isolated) uses a minimal pure-torch
`torchvision.ops.nms` stub — NMS behavior verified on a toy case. Revisit on
better hardware Monday.
