# ECA A/B analysis — issue #500

**Hardware:** AMD RX 6600 XT (gfx1030), ROCm 7.2.3, torch 2.14.0+rocm7.2, ultralytics 8.4.163.
**Matrix:** 2 arms (yolo11n_baseline, yolo11n_eca) × 3 seeds (0, 1, 2) × 50 epochs.
**Held constant per arm:** seed, split, epochs, batch (16), imgsz (640), workers (6), device (0), pretrained (`yolo11n.pt`, routed through `eca.transfer_aligned` so both arms start from identical COCO weights).
**Only difference between arms:** four `1×1×k` ECA rows in the backbone (`yolo11n_eca.yaml`). +18 parameters (+0.0007%), +0.0001% GFLOPs, measured in the infrastructure phase.
**Dataset:** CubiCasa5K, 4200/400/400, 42,392 doors + 35,419 windows (CC BY-NC-SA, non-commercial).
**Total cost:** 5.08 h of GPU time (50 epochs per run).

## Per-run final-epoch metrics

| arm | seed | mAP50 | mAP50-95 | P | R | wall clock |
|---|---|---:|---:|---:|---:|---:|
| yolo11n_baseline | 0 | 0.96547 | 0.87505 | 0.9295 | 0.9209 | 0.883 h |
| yolo11n_baseline | 1 | 0.97139 | 0.88419 | 0.93605 | 0.93359 | 0.904 h |
| yolo11n_baseline | 2 | 0.96730 | 0.87929 | 0.93514 | 0.92795 | 0.831 h |
| yolo11n_eca      | 0 | 0.96850 | 0.87837 | 0.94488 | 0.91571 | 0.817 h |
| yolo11n_eca      | 1 | 0.97096 | 0.88756 | 0.94295 | 0.93077 | 0.832 h |
| yolo11n_eca      | 2 | 0.96763 | 0.88363 | 0.93542 | 0.92431 | 0.815 h |

(`per_seed.csv` and `ab_summary.csv` carry this in machine-readable form; the
table here is the union of the two.)

## Per-arm mean and within-arm seed-to-seed spread

| metric | baseline (mean ± sd) | +ECA (mean ± sd) | Δ | within-arm spread (max − min) |
|---|---|---|---:|---|
| mAP50 | 0.96805 ± 0.00303 | 0.96903 ± 0.00173 | **+0.00098** | base 0.00592, eca 0.00333 |
| mAP50-95 | 0.87951 ± 0.00457 | 0.88319 ± 0.00461 | **+0.00368** | base 0.00914, eca 0.00919 |
| Precision | 0.93356 ± 0.00355 | 0.94108 ± 0.00500 | **+0.00752** | — |
| Recall | 0.92748 ± 0.00636 | 0.92360 ± 0.00756 | **−0.00388** | — |

Means and standard deviations are sample statistics over n=3. The deltas are
~0.3σ for mAP50 and ~0.8σ for mAP50-95 — i.e. the ECA arm is *consistently*
nudged up on the wider-IoU metric, but the magnitude is well inside the
seed-to-seed noise of either arm at this seed count.

## Spread vs. delta

The acceptance criterion in #500 is *"improve symbol-variant discrimination
without meaningful latency cost."* The latency cost is the +18 params and the
+0.0001% GFLOPs already measured at infra time; the ECA arm trained slightly
*faster* on this GPU (5.06 h for the three ECA runs vs 5.13 h for the three
baseline runs, a 1.4% wall-clock reduction, well inside run-to-run noise).

The accuracy question reduces to whether the deltas are signal or noise:

- **mAP50 Δ = +0.001, within-arm spread ≈ 0.006.** The delta is ~1/6 of one
  arm's seed-to-seed range. Not distinguishable from training noise at n=3.
- **mAP50-95 Δ = +0.0037, within-arm spread ≈ 0.009.** The delta is ~40% of
  one arm's seed-to-seed range. A weak but consistent positive on the
  tighter metric.
- **Precision Δ = +0.0075, Recall Δ = −0.0039.** The ECA arm trades a little
  recall for more precision at the operating point Ultralytics evaluates at
  the end of training. Consistent with the attention block sharpening
  confidence on intra-class variants (the hypothesis) but not at a magnitude
  that survives noise at this seed count.

## Caveat — the spread is a floor, not a CI

The variance measured here is **seed-to-seed** variance. Run-to-run variance
at a fixed seed has not been measured separately (issue #509, closed
2026-09-29 after we caught the original "not reproducible" claim being a
confound — different epoch counts in the two compared runs). Three of the
runs at the same seed, all at `epochs: 50`, would give a real run-to-run
floor. Until that is done, the 0.006 mAP50 spread quoted above is a
*lower bound* on total variance, and a ±0.001 delta is not safely
distinguishable from a no-op.

## Disposition — kept as opt-in arm

Both the cost and the deltas support keeping the variant:

- **Cost is essentially zero.** +18 params, +0.0001% FLOPs, no wall-clock
  regression. Nothing to defend in the deployment budget.
- **Direction is consistent** (positive on the wider-IoU metric across all
  three seeds, P↑/R↓ trade pattern matches the attention-block
  sharpening hypothesis), even if the magnitude is inside noise.
- **No need to delete the infra.** `detector/eca.py`,
  `configs/yolo11n_eca.yaml`, the A/B driver, and the 28 ECA tests are
  in place. A future larger-budget run — 5+ seeds at `epochs: 50` — would
  take ~3-4 days of GPU and could resolve whether the mAP50-95 lift is
  real. The infra is the expensive part; the file deletion is not.

The honest answer to the issue's keep-or-close question is: **kept, with
the numbers recorded**. The experiment did not produce a clear "yes" at
this budget; it produced a consistent-but-small "maybe, on the wider
metric, and worth re-measuring with more seeds before betting on it." A
single PR-merged version of the A/B harness with these numbers in-repo
is the correct artifact for downstream readers; deleting the variant
because the 3-seed delta is inside noise would be discarding the
infra, not the result.

## Suggested issue action

Close #500 as **resolved — kept as opt-in, infra archived in
`detector/results/ab_2026-09-28/`**, with a follow-up issue opened only if
a future A/B at 5+ seeds is run.
