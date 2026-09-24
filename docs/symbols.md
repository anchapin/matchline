# symbols: programmatic GD&T symbol rendering for synthetic training data

`symbols.py` provides a programmatic symbol renderer producing crisp vector-style glyphs (112×112, downsampled to 28×28) with configurable stroke thickness and random jitter.

## Background

Training a symbol spotter requires large quantities of labelled glyph images. Rather than annotating real drawings, `symbols.py` synthesises realistic-looking GD&T symbols with random rotation, translation, and scale perturbations — approximating the variation found in architectural drawings.

## Symbol Classes (10 glyphs)

| Class | Name | Description |
|-------|------|-------------|
| 0 | circle | Filled circle with thick stroke |
| 1 | square | Rectangle / feature control frame |
| 2 | triangle | Three-point polygon |
| 3 | cross | Plus sign (position tolerance base) |
| 4 | perpendicularity | T-shape (perpendicularity tolerance) |
| 5 | parallelism | Two parallel vertical bars |
| 6 | angularity | Angle shape with oblique line |
| 7 | position | Circle with crosshair (position tolerance) |
| 8 | door | Rectangle with quarter-arc swing indicator |
| 9 | window | Double horizontal line with three vertical mullions |

## API

### `draw(cls: int, thick: float = 7.0) -> np.ndarray`

Draw symbol class `cls` (0–9) on a 112×112 canvas with given stroke thickness. Returns float32 array (0.0=background, 1.0=stroke).

### `jitter(img: np.ndarray, rng: np.random.Generator) -> np.ndarray`

Apply random rotation (±8°), translation (±6 px), and scale (0.92–1.08) to an image.

### `to28(img: np.ndarray) -> np.ndarray`

Block-average 112×112 → 28×28 and scale to 0–255 (uint8).

### `make_dataset(n_train: int, n_test: int, seed: int)`

Generate a labelled train/test split for the 10-class symbol dataset.

```python
from symbols import make_dataset

Xtr, ytr, Xte, yte = make_dataset(n_train=500, n_test=100, seed=42)
# Xtr.shape == (5000, 28, 28), ytr.dtype == int64
# Xte.shape == (1000, 28, 28), yte.dtype == int64
```

## Limitations

- **Simplified geometry**: All symbols are hand-coded vector approximations; they do not reproduce the full range of real GD&T notation variants.
- **No dimension annotations**: Symbols are rendered without numeric tolerances or datum references. Only the glyph class is captured.
- **Fixed canvas**: 112×112 input, 28×28 output — not configurable without modifying the module.
- **Synthetic only**: These symbols are not derived from real drawings. A spotter trained solely on synthetic data may not generalise to printed/hand-drawn glyphs. Use as a bootstrap, not a replacement for real annotated data.
- **Limited class set**: Only 10 classes are defined. Real architectural drawings contain many more symbol types (electrical, plumbing, HVAC, structural).
