# Detector track — fine-tuned YOLO baseline + PID-style SAHI tiling

Monday-critical path: evaluate a fine-tuned Ultralytics YOLO detector on
Alex's clean commercial drawings. See `docs/ecosystem_audit.md` for the
rationale (PID two-stage pattern: class-agnostic YOLO + SAHI tiling, then
one-shot legend/schedule classification; plain fine-tuned YOLO as baseline).

## Environments

The detector track has **two venvs** — one for CPU work, one for GPU training:

| venv | purpose | pytest | converters | GPU |
|---|---|---|---|---|
| `detector/.venv-det` | CPU inference, testing, evaluation | yes | yes (pymupdf, lxml) | no |
| `detector/.venv-det-rocm` | GPU training only | no | no | yes (ROCm 7.2.3, gfx1030) |

**`.venv-det`** — the CPU venv. Use for: harness validation, SAHI inference,
`eval_zero_shot.py`, running pytest, and running the converters
(`convert_cubicasa.py`, `convert_aec.py`, `convert_floorplancad.py`). See
`requirements-detector.txt` and `QUICKSTART.md` for the install recipe.

**`.venv-det-rocm`** — the ROCm GPU venv. Use for: `train.py` on GPU. Does NOT
include pytest, pymupdf, or lxml — those are CPU-only dependencies in the CPU venv.
Install with `requirements-detector-rocm.txt`; generic rocm7.2 wheels ship gfx1030
(RX 6600 XT) kernels, so no AMD per-arch index is needed.

Both venvs are gitignored (`.venv*/` in `.gitignore`).

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

## Status (2026-09-28)

- [x] Full CubiCasa conversion: train 4200 / val 400 / test 400 plans,
      42,392 doors + 35,419 windows, integrity-checked (`check_dataset.py`)
- [x] AEC eval set: 15 sheets, 678 doors + 494 windows (render verified)
- [x] FloorPlanCAD eval set: 5,308 images, 12,697 doors + 1,952 windows
- [x] Harness validation: 3 epochs, 400/100 subset -> val mAP50=0.65
      (P=0.741, R=0.655) — pipeline works end to end
- [x] SAHI tiling + NMS proven on a 7201x4801 AEC sheet (26 preds, ~30s CPU)
- [x] Zero-shot eval harness (`eval_zero_shot.py`) proven on sheet_01
- [x] Full ECA A/B: 2 arms × 3 seeds × 50 epochs on the RX 6600 XT
      (`detector/results/ab_2026-09-28/`, ~5.1 h GPU total). Numbers and
      disposition in `analysis.md` there.
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

**Seeds.** `--seed` is forwarded to `model.train(seed=...)` and that forwarding is
load-bearing. `train.py` also calls `random.seed`/`np.random.seed`/
`torch.manual_seed` first, but Ultralytics re-seeds all three from *its own*
`seed` argument at the start of training, overriding them. Omitting the kwarg
silently pinned every run to Ultralytics' default of `0`: `--seed 1` and
`--seed 2` produced byte-identical `results.csv` to `--seed 0`, so a three-seed
A/B was one sample measured three times. **A summary with three identical rows
is a failed experiment, not three confirmations** — check that first before
reading any delta. Guarded by `tests/test_detector_train_seed.py` in the main
suite (pure argument forwarding, so it needs no GPU, dataset, or detector venv).
Note also that **bit-reproducibility at fixed seed is unmeasured, not
measured-absent** (#509, closed 2026-09-29). It was long assumed the ROCm path
was not reproducible, on the strength of two 1-epoch runs scoring 0.05485 and
0.21352 mAP50 — but that pair is a confound, not a replicate: `seedtest_s0` ran
`epochs: 1` and `yolo11n_baseline_s0` ran `epochs: 50`, so the two sat on
different learning-rate schedules (epoch-1 `lr/pg0` 0.001667 vs 0.000553554),
and `seedtest_s1` is `seed: 1`, not a second seed-0 run. The honest position
is that seed-to-seed and run-to-run noise have not been separated, so 3 seeds
understate total variance and the within-arm spread in the 2026-09-28 A/B
(seed-to-seed ~0.006 mAP50 for both arms) should be read as a floor rather
than a confidence interval. Establishing the real run-to-run floor still
needs one config at one seed repeated N times **at a fixed epoch count**;
that has not been done.

**Status (2026-09-28): measured on RX 6600 XT, kept as opt-in arm.** Full
matrix — 2 arms × 3 seeds × 50 epochs — completed in ~5.1 h GPU time. Numbers
and provenance live in `detector/results/ab_2026-09-28/`:

| metric | baseline (mean ± sd, n=3) | +ECA (mean ± sd, n=3) | Δ |
|---|---|---|---:|
| mAP50 | 0.96805 ± 0.00303 | 0.96903 ± 0.00173 | +0.00098 |
| mAP50-95 | 0.87951 ± 0.00457 | 0.88319 ± 0.00461 | +0.00368 |
| Precision | 0.93356 ± 0.00355 | 0.94108 ± 0.00500 | +0.00752 |
| Recall | 0.92748 ± 0.00636 | 0.92360 ± 0.00756 | −0.00388 |

The mAP50 delta is ~1/6 of one arm's seed-to-seed range and the mAP50-95 delta
is ~40% of it. Direction is positive and consistent across seeds (especially
on the wider-IoU metric), but the magnitude is inside the within-arm noise
at n=3. See `analysis.md` in that directory for the full spread-vs-delta
breakdown, the P↑/R↓ pattern (consistent with attention sharpening), and
the run-to-run variance caveat from #509. **Disposition: kept as opt-in
arm** — the cost is essentially zero (+18 params, +0.0001% FLOPs) and the
infra is in place if a future larger-budget A/B is run.

## Limitations

- Latency measured on CPU with train-mode forward semantics; a GPU number is
  the relevant one for deployment and has not been taken.
- The smoke dataset is synthetic and its labels are not physically aligned to
  the drawn symbols; it validates plumbing only, never accuracy.

