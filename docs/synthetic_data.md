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
| `synth/e2e_test.py` | End-to-end: GT detections → schedule rollup → floor area → room labels → geometry simplification |
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
~/workspace/.venv-ocr/bin/python synth/e2e_test.py        # ~20 s
~/workspace/.venv-ocr/bin/python synth/gap_experiment.py  # ~20 s
~/workspace/.venv-ocr/bin/python synth/make_dataset.py --sheets 6 --crops-per-class 250 --seed 7
```
