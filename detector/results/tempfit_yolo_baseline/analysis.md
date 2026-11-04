# YOLO baseline on FloorPlanCAD (corrected labels) — issue #1329

**What this note is.** The P/R baseline that the template-fit arm (#1328) must be
compared against: a plain YOLO11n detector run over a deterministic slice of the
corrected FloorPlanCAD eval split, on the clean images and on the documented
scale/rotated variants of the same images.

**Headline:** on this subset the YOLO baseline is **effectively zero**
(clean: door P=0.00000 R=0.00000, window P=0.00000 R=0.00000; 366 GT boxes,
0 true positives). This is a *genuine zero-shot transfer failure of the
fine-tuned detector*, not a bug in the driver, the SAHI-style tiling, or the
label conversion — see "Premise correction" and "Direct-inference probe".

---

## 1. Premise correction (read this first)

The issue text names `yolo11n.pt` as the baseline weights. **That file cannot
produce a door/window baseline**: it is the stock Ultralytics COCO model with
80 classes, and COCO has no "door" and no "window" class. Running it on this
subset can only produce false positives on unrelated COCO objects (traffic
light, sports ball, …), which is exactly what Run A below shows.

Two runs were therefore made:

| run | weights | role |
|---|---|---|
| **A — "COCO"** | `$MATCHLINE_DATASETS/yolo11n.pt` (stock COCO, 80 classes) | **negative control only** — proves that the stock checkpoint has no door/window classes. NOT the baseline. |
| **B — "doorwindow"** | `$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt` (fine-tuned 2-class door/window detector) | **THIS IS THE BASELINE** — the "YOLO baseline" of the detector track that #1328 compares against. |

Run B is the baseline because the fine-tuned checkpoint is the detector track's
door/window model: `model.names == {0: 'door', 1: 'window'}` (verified in
§5), trained on the CubiCasa5K door/window conversion
(`detector/configs/cubicasa.yaml`, `names: {0: door, 1: window}`).

Run A is **not** discarded — it is kept and reported below as a labelled
negative control, so that the "zero" result cannot be misread as "the driver
found nothing at all in the images". Run A does find things (53 COCO-class
boxes on the clean subset); it just never finds a door or a window, because
COCO has no such classes.

**The zero is model behaviour, not a pipeline artefact.** The same weights
score mAP50 **0.91641 / 0.92185 / 0.94773** (epochs 1/2/3 as recorded in
`$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/results.csv`) on their own
CubiCasa validation set, and score **0.00000** precision and recall on the
FloorPlanCAD eval subset. The domain gap (CubiCasa renders vs. FloorPlanCAD
floor plans) is total for this model.

---

## 2. Results

Matching: greedy IoU at **IoU = 0.5**, the shared algorithm
`detector/eval_robustness.py:match_predictions` (identical to the one used by
`detector/eval_zero_shot.py`). TP/FP/FN are counted per class.

### 2.1 Run B — the baseline (fine-tuned door/window weights)

Clean subset (100 images, `clean` variant only):

| class | P | R | tp | fp | fn | n_gt | n_pred |
|---|---:|---:|---:|---:|---:|---:|---:|
| door | 0.00000 | 0.00000 | 0 | 0 | 341 | 341 | 0 |
| window | 0.00000 | 0.00000 | 0 | 4 | 25 | 25 | 4 |
| **overall** | **0.00000** | **0.00000** | 0 | 4 | 366 | 366 | 4 |

Scale/rotated subset (`scale_0.8` + `scale_1.25` + `rot_p15` + `rot_m15`,
400 images):

| class | P | R | tp | fp | fn | n_gt | n_pred |
|---|---:|---:|---:|---:|---:|---:|---:|
| door | 0.00000 | 0.00000 | 0 | 125 | 1364 | 1364 | 125 |
| window | 0.00000 | 0.00000 | 0 | 73 | 100 | 100 | 73 |
| **overall** | **0.00000** | **0.00000** | 0 | 198 | 1464 | 1464 | 198 |

Per-variant (Run B) — every variant is zero as well:

| variant | door P | door R | window P | window R | overall P | overall R |
|---|---:|---:|---:|---:|---:|---:|
| clean | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| scale_0.8 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| scale_1.25 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| rot_p15 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |
| rot_m15 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 | 0.00000 |

### 2.2 Run A — COCO negative control (NOT the baseline)

Clean subset, stock COCO `yolo11n.pt`:

| class | P | R | tp | fp | fn | n_gt | n_pred |
|---|---:|---:|---:|---:|---:|---:|---:|
| door | 0.00000 | 0.00000 | 0 | 0 | 341 | 341 | 0 |
| window | 0.00000 | 0.00000 | 0 | 0 | 25 | 25 | 0 |
| cls9 (COCO) | 0.00000 | 0.00000 | 0 | 50 | 0 | 0 | 50 |
| cls38 (COCO) | 0.00000 | 0.00000 | 0 | 1 | 0 | 0 | 1 |
| cls74 (COCO) | 0.00000 | 0.00000 | 0 | 2 | 0 | 0 | 2 |
| **overall** | **0.00000** | **0.00000** | 0 | 53 | 366 | 366 | 53 |

Scale/rotated subset, stock COCO `yolo11n.pt`:

| class | P | R | tp | fp | fn | n_gt | n_pred |
|---|---:|---:|---:|---:|---:|---:|---:|
| door | 0.00000 | 0.00000 | 0 | 1 | 1364 | 1364 | 1 |
| window | 0.00000 | 0.00000 | 0 | 0 | 100 | 100 | 0 |
| cls9 / 11 / 25 / 30 / 33 / 38 / 74 (COCO) | 0.00000 | 0.00000 | 0 | 378 | 0 | 0 | 378 |
| **overall** | **0.00000** | **0.00000** | 0 | 379 | 1464 | 1464 | 379 |

Reading: the stock model finds COCO objects but **zero** doors and **zero**
windows. That is the expected behaviour of a 80-class COCO checkpoint on this
data, and it is why Run B is the baseline. (The COCO row aggregates the
per-class FP boxes of classes 9, 11, 25, 30, 33, 38 and 74 — 378 boxes in
total; the 379th overall false positive is the single door-class box listed
above.)

---

## 3. Provenance

| item | value |
|---|---|
| driver | `detector/floorplancad_yolo_baseline.py` (new in #1329) |
| predictions source | **regenerated** (both runs: `config.predictions_source == "regenerated"` in the report JSONs) |
| dataset root | `$MATCHLINE_DATASETS/detector_yolo/floorplancad` (split `eval`) |
| GT labels | corrected FloorPlanCAD eval labels, `$MATCHLINE_DATASETS/detector_yolo/floorplancad/labels/eval` |
| Run A weights | `$MATCHLINE_DATASETS/yolo11n.pt` |
| Run B weights | `$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt` |
| inference | `detector/sahi_infer.py:infer_sheet` — conf **0.25**, tile **1024**, overlap **0.2**, imgsz **1024**, device **cpu**; class-wise NMS (IoU 0.5, contain 0.8) |
| matching | `detector/eval_robustness.py:match_predictions`, greedy IoU **0.5** |
| n images | 100 (Run A and Run B) |
| variants | `clean, scale_0.8, scale_1.25, rot_p15, rot_m15` (5 × 100 = 500 inferences per run) |
| toolchain | `detector/.venv-det` (worktree venv), ultralytics 8.4.163, torch 2.14.0+cpu, Python 3.12 |
| hardware | CPU only (no GPU used, no ROCm) |

### 3.1 Driver commands

The two runs are the same driver invocation with different `--weights` /
output paths. All inference parameters below are taken verbatim from the
`config` block of the committed report JSONs (`report_yolo11n_coco.json`,
`report_yolo11n_doorwindow.json`), which record the run's settings; the shell
history of the original session is not part of the repo, so the flag list is
reconstructed from that recorded config:

```
# Run A — COCO negative control
python detector/floorplancad_yolo_baseline.py \
  --n 100 --conf 0.25 --tile 1024 --overlap 0.2 --imgsz 1024 --device cpu \
  --weights "$MATCHLINE_DATASETS/yolo11n.pt" \
  --out detector/results/tempfit_yolo_baseline/report_yolo11n_coco.json \
  --images-out detector/results/tempfit_yolo_baseline/images.txt \
  --per-image-out detector/results/tempfit_yolo_baseline/per_image_yolo11n_coco.csv \
  --dump-preds detector/results/tempfit_yolo_baseline/preds_yolo11n_coco

# Run B — THE baseline (fine-tuned door/window weights)
python detector/floorplancad_yolo_baseline.py \
  --n 100 --conf 0.25 --tile 1024 --overlap 0.2 --imgsz 1024 --device cpu \
  --weights "$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt" \
  --out detector/results/tempfit_yolo_baseline/report_yolo11n_doorwindow.json \
  --per-image-out detector/results/tempfit_yolo_baseline/per_image_yolo11n_doorwindow.csv \
  --dump-preds detector/results/tempfit_yolo_baseline/preds_yolo11n_doorwindow
```

(Commands above were launched with the worktree venv,
`detector/.venv-det/bin/python`, and with `MATCHLINE_DATASETS` exported so
that the dataset root resolves; the committed reports store the root in the
`$MATCHLINE_DATASETS/...` convention, never as a machine path.)

The two verification commands that were actually executed in this session are
quoted verbatim in §4 and §5.

### 3.2 Transform spec (copied from the report JSONs)

| variant | transform (image) | transform (GT boxes) |
|---|---|---|
| `scale_0.8` | resize to `round(W*0.8) x round(H*0.8)` | box edges multiplied by 0.8 |
| `scale_1.25` | resize to `round(W*1.25) x round(H*1.25)` | box edges multiplied by 1.25 |
| `rot_p15` | PIL `rotate(+15°)` about the image centre, `expand=True`, white fill | box corners rotated about the centre, axis-aligned bounds kept (canvas-growth offset applied) |
| `rot_m15` | PIL `rotate(−15°)` about the image centre, `expand=True`, white fill | idem |

The rotations are counter-clockwise in image coordinates (y grows downwards),
which the driver's `rotate_point` implements as the transpose of the standard
rotation matrix. The transform is verified numerically in §6.

---

## 4. Subset rule and the reuse contract for #1328

**Subset rule:** the **first 100 image stems in sorted order** of
`$MATCHLINE_DATASETS/detector_yolo/floorplancad/images/eval` (the corrected
eval split). Verified: the 100 stems in `images.txt` are unique, sorted, and
equal to `sorted(eval stems)[:100]`.

**The image list is committed here:**

```
detector/results/tempfit_yolo_baseline/images.txt
```

100 stems, one per line, e.g. the first three are
`690a547c0420c654cb79d521`, `690a547c0420c654cb79d525`,
`690a547c0420c654cb79d529`.

**The image list (100 stems, verbatim):**

```
690a547c0420c654cb79d521
690a547c0420c654cb79d525
690a547c0420c654cb79d529
690a547c0420c654cb79d52b
690a547c0420c654cb79d52e
690a547c0420c654cb79d559
690a547c0420c654cb79d58b
690a547c0420c654cb79d5ab
690a547c0420c654cb79d5d1
690a547c0420c654cb79d5ec
690a547c0420c654cb79d627
690a547d0420c654cb79d64b
690a547d0420c654cb79d651
690a547d0420c654cb79d657
690a547d0420c654cb79d65b
690a547d0420c654cb79d66b
690a547d0420c654cb79d670
690a547d0420c654cb79d67c
690a547d0420c654cb79d686
690a547d0420c654cb79d690
690a547d0420c654cb79d693
690a547d0420c654cb79d69d
690a547d0420c654cb79d6a4
690a547d0420c654cb79d6b3
690a547d0420c654cb79d6b8
690a547d0420c654cb79d6be
690a547d0420c654cb79d6c4
690a547d0420c654cb79d6c6
690a547d0420c654cb79d6c8
690a547d0420c654cb79d6cd
690a547d0420c654cb79d6d7
690a547d0420c654cb79d6dc
690a547d0420c654cb79d6e5
690a547d0420c654cb79d6f0
690a547d0420c654cb79d6f9
690a547d0420c654cb79d705
690a547d0420c654cb79d70f
690a547d0420c654cb79d726
690a547d0420c654cb79d737
690a547d0420c654cb79d748
690a547d0420c654cb79d751
690a547d0420c654cb79d75c
690a547d0420c654cb79d776
690a547d0420c654cb79d787
690a547d0420c654cb79d78a
690a547d0420c654cb79d78c
690a547d0420c654cb79d795
690a547d0420c654cb79d7a2
690a547d0420c654cb79d7ab
690a547d0420c654cb79d7b4
690a547d0420c654cb79d7d3
690a547d0420c654cb79d7df
690a547d0420c654cb79d7ee
690a547d0420c654cb79d7fb
690a547d0420c654cb79d7fd
690a547d0420c654cb79d80b
690a547d0420c654cb79d810
690a547d0420c654cb79d81b
690a547d0420c654cb79d820
690a547d0420c654cb79d825
690a547d0420c654cb79d828
690a547d0420c654cb79d82b
690a547d0420c654cb79d840
690a547d0420c654cb79d846
690a547d0420c654cb79d896
690a547d0420c654cb79d89d
690a547d0420c654cb79d8a0
690a547d0420c654cb79d8a2
690a547d0420c654cb79d8a6
690a547d0420c654cb79d8c0
690a547d0420c654cb79d8ea
690a547e0420c654cb79d923
690a547e0420c654cb79d959
690a547e0420c654cb79d967
690a547e0420c654cb79d97c
690a547e0420c654cb79d981
690a547e0420c654cb79d985
690a547e0420c654cb79d999
690a547e0420c654cb79d9ad
690a547e0420c654cb79d9c7
690a547e0420c654cb79d9e2
690a547e0420c654cb79d9ec
690a547e0420c654cb79d9f0
690a547e0420c654cb79d9fb
690a547e0420c654cb79da05
690a547e0420c654cb79da18
690a547e0420c654cb79da26
690a547e0420c654cb79da31
690a547e0420c654cb79da55
690a547e0420c654cb79daaf
690a547e0420c654cb79dad4
690a547e0420c654cb79dad6
690a547e0420c654cb79dae4
690a547e0420c654cb79daee
690a547e0420c654cb79daf7
690a547e0420c654cb79db00
690a547e0420c654cb79db0a
690a547e0420c654cb79db12
690a547e0420c654cb79db32
690a547e0420c654cb79db41
```

> **Reuse contract for #1328:** the template-fit arm **MUST** evaluate on
> **this exact image list** (`images.txt`, verbatim, no re-selection, no
> different slice size) and **MUST** use **the exact same five variants**
> (`clean`, `scale_0.8`, `scale_1.25`, `rot_p15`, `rot_m15`) with the
> **transform spec of §3.2**, the **same corrected eval labels**
> (`$MATCHLINE_DATASETS/detector_yolo/floorplancad/labels/eval`), and the
> **same matching** (greedy IoU 0.5 via
> `detector/eval_robustness.py:match_predictions`). Any deviation makes the
> two arms incomparable and the #1323 adopt/reject comparison invalid.

Note that the images in this split are 1000×1000 FloorPlanCAD renders
(`images/eval` holds 5308 images, `labels/eval` 5308 label files).

---

## 5. Determinism evidence

Two independent checks, both measured in this session.

### 5.1 Score-only rerun against the saved Run B predictions

The driver was re-run in score-only mode (`--preds-dir`, no inference) and its
report written to `/tmp` (not committed):

```
detector/.venv-det/bin/python detector/floorplancad_yolo_baseline.py \
  --n 100 --weights "$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt" \
  --preds-dir detector/results/tempfit_yolo_baseline/preds_yolo11n_doorwindow \
  --out /tmp/det1329/report_rerun_scoreonly.json \
  --work-dir /tmp/det1329/work_score
```

Printed tables of the rerun:

```
== subset: clean ==
class              P         R      tp      fp      fn      gt    pred
----------------------------------------------------------------------
door         0.00000   0.00000       0       0     341     341       0
window       0.00000   0.00000       0       4      25      25       4
overall      0.00000   0.00000       0       4     366     366       4

== subset: scale_rot ==
class              P         R      tp      fp      fn      gt    pred
----------------------------------------------------------------------
door         0.00000   0.00000       0     125    1364    1364     125
window       0.00000   0.00000       0      73     100     100      73
overall      0.00000   0.00000       0     198    1464    1464     198
```

Machine diff of the two reports (saved Run B report vs. the rerun report),
comparing the full `subsets` and `variants` tables (every class, every
subset, every variant):

```
subsets equal: True
variants equal: True
max |P/R difference| over all classes/subsets/variants: 0.0
stems identical: True
config diff keys: {'predictions_source': ('regenerated', 'precomputed')}
```

The only difference between the two report JSONs is the
`predictions_source` field (`regenerated` for the committed run, `precomputed`
for the score-only rerun) and the timing block. **Determinism floor: 0.00000.**

### 5.2 Full re-inference on a 10-image subset

Ten images (the first ten stems) were re-inferred from scratch, with the same
weights and the same settings, into a fresh work dir under `/tmp`:

```
detector/.venv-det/bin/python detector/floorplancad_yolo_baseline.py \
  --n 10 --weights "$MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt" \
  --conf 0.25 --tile 1024 --overlap 0.2 --imgsz 1024 --device cpu \
  --out /tmp/det1329/report10.json \
  --per-image-out /tmp/det1329/per_image10.csv \
  --dump-preds /tmp/det1329/preds10 --work-dir /tmp/det1329/work10
```

Result (50 prediction JSONs = 10 images × 5 variants) compared against the
committed `preds_yolo11n_doorwindow/`:

```
new pred files: 50
identical: 50 different: 0
```

The re-inferred per-variant counters also reconcile with the committed
per-image CSV restricted to the same ten stems (door: 155 GT / 10 FP over the
five variants; window: 20 GT / 0 FP), i.e. the metrics reproduce as well as
the raw boxes.

**Conclusion: the pipeline is bit-reproducible on this machine.** The zero
result is not a fluke of one run.

---

## 6. Direct-inference probe (model behaviour, not a SAHI/driver artefact)

Plain Ultralytics inference — **no SAHI tiling, no driver code path**, just
`YOLO(weights).predict(...)` — on the first three images of `images.txt`:

```
weights: $MATCHLINE_DATASETS/detector_runs/cubi_yolo11n_e10/weights/best.pt
model.names: {0: 'door', 1: 'window'}
690a547c0420c654cb79d521  conf=0.25  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d521  conf=0.05  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d521  conf=0.01  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d525  conf=0.25  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d525  conf=0.05  n_boxes=1  classes=[0]  confs=[0.0583]
690a547c0420c654cb79d525  conf=0.01  n_boxes=4  classes=[0, 1, 1, 1]  confs=[0.0583, 0.0183, 0.0179, 0.0168]
690a547c0420c654cb79d529  conf=0.25  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d529  conf=0.05  n_boxes=0  classes=[]  confs=[]
690a547c0420c654cb79d529  conf=0.01  n_boxes=0  classes=[]  confs=[]
```

i.e. at the driver's operating point (conf 0.25) plain Ultralytics returns
**0 boxes on all three images**; lowering the threshold to 0.05 surfaces a
single weak door (conf 0.0583) on the second image, and 0.01 surfaces four
very low-confidence boxes on it. `model.names` confirms the checkpoint is the
2-class door/window model. So the "no detections" outcome is produced by the
model itself, in the plainest possible inference path — it is a genuine
zero-shot transfer failure of the CubiCasa-trained detector on FloorPlanCAD
renders, **not** a tiling/SAHI, NMS, label-conversion or driver bug.

---

## 7. Label and transform verification

### 7.1 Labels: converter math vs. the committed eval label files

The corrected eval labels were re-derived from the FloorPlanCAD parquet with
the fixed converter's own math
(`detector/convert_floorplancad.py:coco_bbox_to_yolo`, COCO `[x, y, w, h]`
bboxes) and compared line by line with the label files on disk. Output:

```
parquet rows: 5308; label files: 5308

=== 690a547c0420c654cb79d525 (parquet row 1) ===
expected (converter math): ['0 0.859751 0.303696 0.140000 0.088250', '0 0.509881 0.201136 0.150000 0.083130']
actual   (label file)    : ['0 0.859751 0.303696 0.140000 0.088250', '0 0.509881 0.201136 0.150000 0.083130']
MATCH
  raw parquet bbox [sliding_door] = [789.7505493164062, 259.570556640625, 140.0, 88.25] (COCO x,y,w,h)
  raw parquet bbox [sliding_door] = [434.88055419921875, 159.570556640625, 150.0, 83.12999725341797] (COCO x,y,w,h)
  raw parquet bbox [wall] = [0.0, 0.0, 1000.0, 289.570556640625] (COCO x,y,w,h)

=== 690a547c0420c654cb79d529 (parquet row 2) ===
expected (converter math): ['0 0.779751 0.791136 0.150000 0.083130', '0 0.268191 0.194571 0.083120 0.150000']
actual   (label file)    : ['0 0.779751 0.791136 0.150000 0.083130', '0 0.268191 0.194571 0.083120 0.150000']
MATCH
  raw parquet bbox [wall] = [0.0, 0.0, 1000.0, 939.570556640625] (COCO x,y,w,h)
  raw parquet bbox [sliding_door] = [704.7505493164062, 749.570556640625, 150.0, 83.12999725341797] (COCO x,y,w,h)
  raw parquet bbox [sliding_door] = [226.63055419921875, 119.570556640625, 83.12000274658203, 150.0] (COCO x,y,w,h)

=== 690a547c0420c654cb79d525 / ...521 (parquet row 0) ===
expected (converter math): []
actual   (label file)    : []
MATCH
  raw parquet bbox [wall] = [309.7525634765625, 0.0, 690.2474365234375, 720.570556640625] (COCO x,y,w,h)
```

(Last block header truncated for brevity: stem `690a547c0420c654cb79d521`,
parquet row 0.) The GT used for scoring is therefore the corrected
label set, and the conversion round-trips exactly.

### 7.2 Transforms: painted pixels vs. transformed GT boxes

For two eval images, the first GT box was painted red into a grayscale copy of
the drawing, the variant transform was applied, and the bounding box of the
red pixels was compared with the transformed GT box (IoU), against a control
that keeps the *untransformed* box:

```
=== 690a547c0420c654cb79d525 (1000x1000), 2 GT boxes ===
  scale_0.8  painted-red bbox=[632, 208, 744, 278] transformed-GT bbox=[631.8, 207.7, 743.8, 278.3] IoU=0.9880
             control (untransformed GT box) IoU=0.0000
  scale_1.25 painted-red bbox=[988, 325, 1162, 435] transformed-GT bbox=[987.2, 324.5, 1162.2, 434.8] IoU=0.9875
             control (untransformed GT box) IoU=0.0000
  rot_p15    painted-red bbox=[831, 270, 989, 391] transformed-GT bbox=[830.7, 269.5, 988.7, 391.0] IoU=0.9921
             control (untransformed GT box) IoU=0.3231
  rot_m15    painted-red bbox=[933, 456, 1090, 577] transformed-GT bbox=[932.3, 455.8, 1090.3, 577.2] IoU=0.9893
             control (untransformed GT box) IoU=0.0000

=== 690a547c0420c654cb79d529 (1000x1000), 2 GT boxes ===
  scale_0.8  painted-red bbox=[564, 600, 684, 666] transformed-GT bbox=[563.8, 599.7, 683.8, 666.2] IoU=0.9891
             control (untransformed GT box) IoU=0.0000
  scale_1.25 painted-red bbox=[881, 938, 1069, 1041] transformed-GT bbox=[880.9, 937.0, 1068.4, 1040.9] IoU=0.9856
             control (untransformed GT box) IoU=0.0000
  rot_p15    painted-red bbox=[876, 763, 1042, 882] transformed-GT bbox=[875.4, 762.3, 1041.8, 881.4] IoU=0.9834
             control (untransformed GT box) IoU=0.0000
  rot_m15    painted-red bbox=[725, 908, 891, 1026] transformed-GT bbox=[724.7, 907.1, 891.1, 1026.2] IoU=0.9882
             control (untransformed GT box) IoU=0.0000
```

Every transformed GT box matches the painted pixels with IoU ≥ 0.983, while the
untransformed control collapses to ≈ 0. The scale/rotated subset therefore
carries correct labels (an equivalent check lives permanently in the test file
as `test_rotation_label_follows_the_painted_geometry`).

---

## 8. Interpretation (and what is deliberately *not* decided here)

The YOLO baseline on this subset is **effectively zero**: 0 true positives out
of 366 ground-truth boxes on the clean images, and 0 out of 1464 on the
scale/rotated images, at every precision/recall point reported. The detector
track's door/window model does not transfer from CubiCasa renders to
FloorPlanCAD floor plans at all at the operating point used here.

Consequences for the #1323 adopt/reject rule as applied by #1328:

* The comparison bar is **very low**. Any template-fit arm that produces even
  a handful of true positives at IoU 0.5 will beat this baseline
  numerically. A "the template-fit method beats YOLO" result on this subset is
  therefore a *weak* statement: it mostly measures that YOLO fails to
  transfer, not that the template fit is good.
* The zero baseline also means the baseline provides **no useful
  failure-mode signal** for the template-fit arm (no FP/FP-vs-TP profile to
  compare against).
* The adopt/reject decision **belongs to #1328** and is **not** made here.
  #1329 only supplies the baseline number and the reuse contract.

Caveats worth carrying into #1328:

* The subset is 100 images (the deterministic first-100 slice), not the whole
  5308-image eval split; the zero is measured on this subset.
* Run B was scored at conf 0.25 (the driver's operating point). At conf 0.05
  the model emits a handful of very low-confidence boxes (§6), which are still
  far below any usable precision; the qualitative conclusion is unchanged.
* Run A (stock COCO) shows that the driver does detect *something* on these
  images when the weights are applicable — a useful control that the zero is
  about the model, not about the images being empty.

---

## 9. Files in this directory

| file | content |
|---|---|
| `analysis.md` | this note |
| `images.txt` | the 100 eval stems (the #1328 subset contract) |
| `report_yolo11n_doorwindow.json` | Run B report (THE baseline) |
| `report_yolo11n_coco.json` | Run A report (COCO negative control) |
| `per_image_yolo11n_doorwindow.csv` | Run B per-image rows (500 = 100 × 5) |
| `per_image_yolo11n_coco.csv` | Run A per-image rows (500 = 100 × 5) |
| `preds_yolo11n_doorwindow/` | 500 Run B prediction JSONs (100 × 5) |
| `preds_yolo11n_coco/` | 500 Run A prediction JSONs (100 × 5) |

No machine path is stored in any of these files (the reports use the
`$MATCHLINE_DATASETS/...` convention; the prediction JSONs store only the
image file name, its size and the boxes).
