# Detector Robustness Split — `detector/degrade.py`, `detector/eval_robustness.py`

Synthetic-degradation robustness evaluation for the symbol detector (issue #499).
For each validation image, inference runs on the clean image and on deterministic
degraded variants; predictions are matched to ground truth with greedy IoU
matching (the same algorithm as `detector/eval_zero_shot.py`) and per-class
precision/recall deltas vs clean are reported. This guards the "clean digital
drawings" assumption: if real inputs ever degrade, the margin is measured
instead of discovered in production.

Methodology follows arXiv:2609.24565 ("What Survives on Real Drawings"):
matched baselines evaluated under scan noise and thickened strokes.

## Variants (`degrade.py`)

| Variant | Proxy for | Implementation |
|---|---|---|
| `clean` | baseline | unmodified copy |
| `scan_noise` | scanner sensor noise + optics | Gaussian noise (σ=12) + 0.6px blur |
| `thickened_strokes` | heavy plotter pens, bleed-through | dilate dark pixels (3×3 max filter) |

All variants are deterministic (seeded `numpy.random.Generator`; per-image seeds
derived via `variant_seed(base_seed, index)` so results are order-independent)
and geometry-preserving (same size and mode as input, so GT boxes stay valid).

## Running it

```bash
# With weights (runs tiled SAHI inference per image × variant):
python3 detector/eval_robustness.py --root ~/datasets/detector_yolo/cubicasa \
    --weights runs/detector/weights/best.pt --n 50 --seed 7 --out /tmp/robust.json

# With precomputed predictions (no weights needed; one file per <stem>.<variant>.json
# in sahi_infer.py output format) — inference done once on a GPU box, eval re-run anywhere:
python3 detector/eval_robustness.py --root ~/datasets/detector_yolo/cubicasa \
    --preds-dir /tmp/preds --out /tmp/robust.json
```

Output: a console table (per-class P/R per variant plus deltas vs clean in
percentage points) and a JSON report with the full config, per-class stats, and
deltas.

## Tests

`tests/test_detector_robustness.py` (20 tests): degradation determinism,
seed sensitivity, geometry preservation, dark-pixel expansion, greedy-matcher
correctness (perfect/empty/duplicate/below-threshold predictions), and a full
`--preds-dir` run on a synthetic two-image dataset.

## Limitations

- **Degradations are proxies, not scans.** Gaussian noise and morphological
  dilation approximate scanner/sheet degradation; they do not reproduce
  structured artifacts (fold lines, stamps, coffee stains, fax dithering).
  Deltas measured here are a lower bound on real-world robustness, not a
  prediction of it.
- **No inference without weights.** On machines without ultralytics (or a GPU),
  only the `--preds-dir` path runs; degradation generation itself is pure
  PIL/numpy and always works.
- **Greedy matching, not mAP.** The matcher is the repo's existing greedy IoU
  matcher (as in `eval_zero_shot.py`), reporting P/R at IoU 0.5 — not COCO-style
  mAP. Deltas are comparable across variants within this harness, not against
  published mAP numbers.
- **Explicit non-goal (issue #499):** this does not replace the detector with a
  fly-vision or any other model; it only measures the current detector's margin.
