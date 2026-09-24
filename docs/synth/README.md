# synth/ — Synthetic Training Data Framework

Procedural generation of floor-plan sheets and symbol crops for training the
Jesse-Vision pipeline. All generators produce paired image + ground-truth JSON
with full provenance.

## Files

| File | Purpose |
|---|---|
| `synth/__init__.py` | Package init; re-exports `make_symbol_dataset`, `generate_sheet`, `generate_building` |
| `synth/sheets.py` | Full-sheet generator: footprint, rooms, walls, doors, windows, labels, schedules |
| `synth/symbols.py` | Per-class glyph renderer: 9 AEC symbol classes with configurable variations |
| `synth/mech.py` | Mechanical-equipment generator (stub — `NotImplementedError`) |
| `synth/make_dataset.py` | CLI entry point: `--sheets N --crops-per-class M --seed S` → `synth/out/` |
| `synth/gap_experiment.py` | Sim-to-real gap evaluation: synth-trained classifier vs real AEC-bench crops |
| `synth/e2e_test.py` | End-to-end pipeline smoke: GT detections → schedule rollup → floor area → simplify |
| `synth/out/` | Persisted samples: 6 sheets + 2,000 crops + gap_results.json |
| `tests/test_synth_*.py` | Property-based sheet tests (area budgets, room count, wall continuity) |

## Core API

```python
from synth import generate_sheet, make_symbol_dataset, generate_building

# Sheet: image + ground-truth dict
image, gt = generate_sheet(seed=42)
# gt keys: rooms, symbols, schedule, window_area_total, door_area_total,
#          floor_area_total, footprint_bbox

# Symbol crops: list of (crop_28x28, label_id)
crops = make_symbol_dataset(per_class=250, seed=7)

# Building: multi-sheet model with inter-sheet alignment
building = generate_building(seed=0)
# building keys: sheets, elevation_annotations, link_graph
```

## Sheet Ground Truth

`generate_sheet` returns a dict with:

- **rooms** — list of `{id, name, number, polygon, area}`; polygon uses
  canonical y-down metres coordinates.
- **symbols** — list of `{type, tag, bbox_px, center_px}`; type ∈
  {Window, SingleSwingDoor, DoubleSwingDoor, Sink, Toilet, Bathtub, Shower,
  Cooktops, SlidingDoor}.
- **schedule** — same-sheet `{symbol_type: {tag: dimensions}}` entries
  mirroring real drawing schedules.
- **_provenance** — `{method: "synthetic", seed, generated_at}`; all
  downstream extractions carry this provenance forward.

## Symbol Variations

`render_symbol` in `symbols.py` applies per-crop variation:

| Parameter | Range |
|---|---|
| Line width | 3, 4, 5 px |
| Scale | 0.85 – 1.12× |
| Translation | ±7 px |
| Rotation | 90° increments + ±5° |
| Horizontal mirror | 50% probability |
| Gaussian noise σ | ≤ 3.5 |

Placement on sheets uses the same glyph functions at training proportions
(84/128 scale), oriented to the wall. The paste region is kept clear of
corners/walls (effective width 1.6× symbol width), so crop distribution
matches training by construction.

## Fixture Relationship (conftest)

`tests/conftest.py` provides the `bldg_3room` fixture — a pre-generated
3-room building model used across the test suite. The synth framework is the
 sole source of this fixture; regenerating with a new seed produces a
different but structurally equivalent building.

## Limitations

1. **Tier-1 glyphs do not transfer to real doors/fixtures.** Windows transfer
   at 84.8%; all other classes need Tier-2 style augmentation or real-crop
   training. See `docs/synthetic_data.md` for honest sim-to-real gap numbers.
2. Single drafting style; no dimension lines, hatching, furniture, or
   annotation clutter; no rotated or multiline room labels.
3. Schedules are synthetic and same-sheet; cross-sheet schedule-table
   parsing is unvalidated.
4. `mech.py` raises `NotImplementedError` — mechanical equipment symbols are
   not yet implemented.
5. Sheet symbols reuse training glyphs — e2e classification results are
   in-distribution and validate the pipeline, not real-world recognition.
6. Geometry simplification (`geometry_simplify`) validated on rectangular/L
   footprints only.

## Reproduce

```bash
python -m pytest tests/test_synth_sheets.py -q      # ~5 s
python synth/e2e_test.py                           # ~20 s
python synth/gap_experiment.py                      # ~20 s
python synth/make_dataset.py --sheets 6 --crops-per-class 250 --seed 7
```
