# Run-to-run noise floor — issue #516 (the missing #500 spread)

**Hardware / toolchain:** AMD RX 6600 XT (gfx1030), ROCm 7.2.3, torch 2.14.0+rocm7.2, ultralytics 8.4.171. Detector venv was rebuilt on 2026-10-02 from `detector/requirements-detector-rocm.txt` (it was not present at session start). Gating kernel check: `gfx1030` in `torch.cuda.get_arch_list()`, 4096×4096 fp16 matmul at 17.69 TFLOPS sustained, max abs diff vs CPU ref 3.76e-04. The wheel is correct for this hardware.

**Budget:** 10 epochs, 3 repeats, fixed seed=0, fixed everything except the repeat index. The matrix in `detector/results/ab_2026-09-28/` ran at 50 epochs; this measurement is the 10-epoch floor. **A 10-epoch floor is not automatically a 50-epoch floor — see the caveat at the end.**

**Why the floor was unmeasured before this run:** #500 asked "does ECA improve mAP50?" and was answered with a +0.00098 mAP50 delta over a 0.00592 within-arm spread. The spread is seed-to-seed, not run-to-run, and is therefore an *upper bound* on total variance, not a CI for the delta. #516 measures the run-to-run component separately, so the three numbers can be put side by side: a between-arm delta that is smaller than the run-to-run range is not a result.

## The three spreads (mAP50)

| spread | value | what it includes |
|---|---:|---|
| run-to-run at fixed seed (#516) | **0.00000** | pure noise; nothing but the repeat index varies |
| within-arm seed-to-seed (baseline) | 0.00592 | noise + seed variance |
| within-arm seed-to-seed (ECA) | 0.00333 | noise + seed variance |
| between-arm (ECA − baseline, n=3×50ep) | **+0.00098** | what #500 is asking about |

The full per-repeat numbers are in `per_repeat.csv`. All three repeats produced a final-epoch mAP50 of **0.88435** to five decimal places, and the entire `results.csv` (losses, P, R, mAP50, mAP50-95) is byte-identical across repeats except for the wall-clock column. The one sub-5dp difference is in epoch 1 of `r2` (0.34367 vs 0.34557), which vanishes by epoch 2.

## Verdict band applied to #500

| condition | status |
|---|---|
| `|delta| <= run-to-run range` | 0.00098 ≤ 0.00000 → **does not apply** (delta does clear the floor) |
| `run-to-run range < |delta| <= within-arm range` | 0.00000 < 0.00098 ≤ 0.00592 → **applies** |
| `|delta| > within-arm range` | does not apply |

→ **UNDERPOWERED.** The mAP50 delta (+0.00098) clears the run-to-run noise (0.00000) but sits inside the within-arm seed spread (0.00592). More seeds are needed before a claim holds. This is the same outcome `report_ab_stats.py` prints, archived here in machine-readable form so it is not re-derived each time.

## Verdict for #500, restated

The mAP50 delta of +0.00098 is *technically* distinguishable from pure training noise at this seed=0 floor (it clears 0.00000). It is **not** distinguishable from seed variance at n=3. **The mAP50 direction is not consistent across the three seeds**: per-seed Δ is +0.00303 at seed 0, **−0.00043 at seed 1**, +0.00033 at seed 2; the positive mean is driven by seed 0, and seed 1 went the other way. The magnitude is still inside the within-arm noise. A 5+-seed confirmation at 50 epochs is the only thing that would resolve whether the mAP50-95 lift of +0.0037 (the wider metric, where the direction is consistent and positive across all three seeds: +0.00332, +0.00337, +0.00434) is real.

## Caveat — the 10-epoch floor is a strict lower bound on the 50-epoch floor

This measurement is at 10 epochs. The matrix it judges is at 50 epochs. Sources of variance that grow with epoch count:

- cuDNN dispatch order and reduction order can diverge between runs that share a seed but run for longer, especially with the autocast / AMP paths Ultralytics uses. The 10-epoch trajectory is short enough that a single reduction order dominates; over 50 epochs, accumulated divergence can become visible.
- The Dataloader workers (6 here) re-seed from the worker's own RNG, and the order in which workers start contributing batches can differ between runs in a way that compounds with epoch count.
- The 5dp identity of all metrics in this run may itself be an artifact of the 10-epoch budget: a different run on the same hardware at 50 epochs could show a non-zero range that would, in turn, change the verdict band.

So a 10-epoch floor of 0.00000 is a **real measurement at 10 epochs**, and it does correctly classify the #500 delta as "above the floor" for that budget. It is *not* a guarantee that the 50-epoch floor is also ≤ 0.00098, and it is *not* sufficient on its own to say "ECA improves mAP50." A 50-epoch, 3-repeat confirmation would close that gap, at a cost of ~3 × 50 epochs × 0.197 h ≈ **~5 h** of GPU time on this hardware.

## What would falsify the current verdict

1. **A 50-epoch, 3-repeat run comes back with a run-to-run range > 0.00098.** Then the delta no longer clears the floor and the verdict moves to "no effect measurable at this budget."
2. **A 5-seed run at 50 epochs shrinks the within-arm spread below 0.00098** while the delta stays around +0.001. Then the verdict moves to "delta clears both floors," i.e. ECA measurably improves mAP50.
3. **Either arm's seed-to-seed range at 5 seeds exceeds the +0.00098 delta by a lot.** Then the original "underpowered" call was right and a delta of this size is not actionable.

## Suggested disposition

- **Do not close #500 as "ECA improves mAP50" on the strength of this run.** The 10-epoch floor is a meaningful but partial measurement.
- **Do not reopen the 5-seed question by default** — the cost is real and the magnitude is small.
- **Record this artifact in the repo** alongside `detector/results/ab_2026-09-28/`, so the "noise floor was 0 at 10 epochs, 2026-10-02" claim has provenance and is not re-derived from a stale reporter.
- **The right time to run the 50-epoch confirmation is when a downstream decision depends on the answer.** A +0.001 mAP50 lift at the operating point we currently train to is not load-bearing.
