# Tier-1 Synthetic CAD Data Generator

Procedural synthetic floor plans + symbol crops for the Jesse-Vision AEC
takeoff pipeline. Built 2026-09-19. Tier-1 = clean, single-style CAD
glyphs; no attempt to mimic multi-firm drafting variation.

## Files

| File | Purpose |
|---|---|
| `synth/symbols.py` | PIL renderer for 8 AEC classes; `make_symbol_dataset()` → 28×28 crops + labels |
| `synth/sheets.py` | Full-sheet generator: footprint, rooms, walls, doors, windows, labels, schedules; `generate_sheet(seed)` → (image, ground-truth dict) |
| `synth/make_dataset.py` | CLI: `--sheets N --crops-per-class M --seed S` → `synth/out/` |
| `synth/gap_experiment.py` | Sim-to-real: synth-trained classifier vs 1,626 real AEC-bench crops |
| `scripts/synth_e2e_test.py` | End-to-end: GT detections → schedule rollup → floor area → room labels → geometry simplification |
| `synth/out/` | Persisted sample: 6 sheets + 2,000 crops + `gap_results.json` |

## Symbol classes

Single Swing Door, Double Swing Door, Window, Sink, Toilet, Bathtub,
Shower, Cooktops (AEC-bench compatible) + Sliding Door (extra).

Training-crop variations (`render_symbol`): line width ∈ {3,4,5}, scale
0.85–1.12, translation ±7px, 90° rotations, horizontal mirror, ±5°
rotation, Gaussian noise σ ≤ 3.5.

## Sheet generator

- Rectangular or L-shaped footprint, 4–8 subdivided rooms, interior /
  exterior walls, swing doors (interior + entrance), windows (exterior).
- Room name + number labels; same-sheet window/door schedules with
  realistic dimensions (e.g. A: 1.8×1.5 m, D1: 0.9×2.1 m).
- Perfect JSON ground truth: room polygons/areas, symbol type/tag/bboxes,
  schedule entries, expected window/door/floor-area totals.

**Key design decision:** sheet doors/windows are rendered by calling the
*same glyph functions* used for training crops, on a temp canvas sized at
the training proportion (w/CANVAS = 84/128), oriented to the wall and
pasted onto the sheet. Crop distribution therefore matches training by
construction. Placement keeps the paste region clear of corners/walls
(effective width 1.6× symbol width).

## E2E results (seeds 101–103) — ALL PASS

| Sheet | Classification | Window area err | Door area err | Floor area err | Room labels | Simplify |
|---|---|---|---|---|---|---|
| 101 | 1.00 | 0% | 0% | 0% | 8/8 | 14→6, 0% dA |
| 102 | 1.00 | 0% | 0% | 0% | 5/5 | 8→4, 0% dA |
| 103 | 1.00 | 0% | 0% | 0% | 6/6 | 10→4, 0% dA |

Takeoff rollups match ground truth exactly on all sheets; room-label
association 100%; geometry simplification holds 0% area drift.

## Sim-to-real gap (honest numbers)

Trained on 3,200 synthetic crops, tested on all 1,626 real AEC-bench
crops; baseline = 80/20 real→real split:

| Metric | Real→real baseline | Synth→real |
|---|---|---|
| 8-class accuracy | 44.21% | **27.12%** |
| Door-vs-window accuracy | 65.96% | **44.20%** |

Per-class synth→real: Window 84.8%, Cooktops 12.5%, Toilet 6.7%,
Single Swing Door 1.1%, Double Swing Door 0%, Sink 0.5%, Bathtub 0%,
Shower 0%. Confusion matrix shows the synth-trained classifier dumps
nearly all real symbols into "Window" — real door/fixture styles vary far
more than Tier-1 glyphs capture. (Note the real→real baseline itself is
only 44%: the redacted multi-firm bench is hard even in-distribution.)

## Limitations (explicit)

1. **Tier-1 does not transfer to real doors/fixtures.** Windows transfer
   (84.8%); everything else needs Tier-2 style augmentation mined from
   real sheets, or training on real crops directly.
2. Single drafting style; no dimension lines, hatching, furniture, or
   annotation clutter; no rotated/multiline room labels.
3. Schedules are synthetic and same-sheet; real schedule-table parsing
   across separate sheets is unvalidated (public sheets are redacted).
4. Sheet symbols reuse training glyphs — the 100% e2e classification is
   in-distribution by construction; it validates the *pipeline*, not
   real-world recognition.
5. Geometry simplifier validated on rectangular/L footprints only.

## Reproduce

```
~/workspace/.venv-ocr/bin/python scripts/synth_e2e_test.py        # ~20 s
~/workspace/.venv-ocr/bin/python synth/gap_experiment.py  # ~20 s
~/workspace/.venv-ocr/bin/python synth/make_dataset.py --sheets 6 --crops-per-class 250 --seed 7
```

## Scan-degradation family (#1241, 2026-10-22)

Real drawings arrive as imperfect scans. `detector/degrade_scans.py` applies a
family of six degradations to synthetic sheets so detection and parsing train
on realistic scans instead of clean pages:

| Member | Models | Severity mapping |
|---|---|---|
| `rotation_skew` | scanner feed tilt (rotation + shear) | 0.1-1.5 degrees, 30% as shear |
| `low_dpi` | 150-200 DPI copies of a 300 DPI render | back-resample factor 1.0-0.5 |
| `jpeg` | JPEG-compressed PDFs | quality 95-50 |
| `contrast_loss` | faded copies | contrast 1.0-0.6, brightness +0-15% |
| `bleed_through` | mirrored ink from the sheet behind | blend alpha 0.05-0.20 |
| `blur` | soft optics, one member of the family, not the whole | Gaussian radius 0.3-1.3 px |

Design rules (from the issue):

- **Severity sampled per sheet, never fixed.** `apply_scan_family(img, seed)`
  draws one member and one severity from a single seeded rng; training uses
  the full range. Evaluation pins `EVAL_SEVERITY = 0.7` so clean-vs-degraded
  deltas are comparable across runs.
- **Truth stays tied to source geometry.** `transform_boxes` /
  `transform_points` / `transform_gt` apply the same forward transform the
  image received; only `rotation_skew` moves geometry (boxes become the
  axis-aligned bbox of the rotated corners, clipped to the sheet; walls keep
  their `t_px`, rooms keep their areas, which rotation preserves and the
  shear changes by <=0.03%). Every other member leaves boxes untouched.
- **Clean undegraded validation set.** `convert_synth --degrade-family-seed N`
  degrades TRAIN sheets only (one member + severity per sheet, gt transformed
  in the same pass, counts and member histogram in the summary); the val
  split is copied byte-identical, by construction.
- **Measure before/after.** `eval_robustness.py --family` evaluates the
  family at the pinned severity against clean with truth-box alignment; the
  robustness gain on degraded sheets must not cost accuracy on the clean set,
  which the same report shows side by side.

Everything is deterministic (same seed reproduces the same sheet), pure
PIL/numpy, and CPU-friendly. Tests: `tests/test_degrade_scans.py` (33) plus
convert integration tests in `tests/test_convert_synth.py`.

Note: the measured clean-vs-degraded robustness delta needs a trained
detector (weights or precomputed preds via `--preds-dir`); the pipeline and
harness above are what the training run consumes.
