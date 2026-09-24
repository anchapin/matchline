# jesse.md — WiSARD symbol classifier and skeleton invariants

`synth/jesse.py` — numpy-only implementation of the Jesse-Vision prototype
(paper: "Zero-Weight Optical Recognition... via One-Hot Pixel String Tuple
Addressing and Empirical Bayesian Beliefs", solidSF Research, 2026).

## Components

| Component | Paper section | Function |
|---|---|---|
| 2-bit thermometer encoding `B(x,y) = (I[I>35], I[I>120])` | §2, Eq. B | `thermometer_encode()` |
| N-tuple addressing `a_k = Σ B[τ_{k,s}]·2^s`, K=160, n=10 | §2 | `tuple_addresses()` |
| Streaming counter training `RAM_c[k, a_k] += 1` | §2 | `WisardClassifier.fit()` |
| Center-of-mass normalization | §4.1, §6.3 | `com_normalize()` |
| Zhang-Suen 1-pixel skeletonization | §9.2 | `zhang_suen()` |
| Topological invariants (endpoints, junctions, holes, χ) | §9.2 + Table 9.4 | `skeleton_invariants()` |

---

## Zhang-Suen Skeletonization — `zhang_suen(binary)`

**Algorithm**: Fully parallel 2-subiteration thinning. Each iteration removes
border pixels that satisfy:
- Sub-iteration 1: `2 ≤ B(p1) ≤ 6`, `A(p1)=1`, `p2*p4*p6=0`, `p4*p6*p8=0`
- Sub-iteration 2: `2 ≤ B(p1) ≤ 6`, `A(p1)=1`, `p2*p4*p8=0`, `p2*p6*p8=0`

Neighbor indexing (clockwise from north): `p2=N, p3=NE, p4=E, p5=SE, p6=S, p7=SW, p8=W, p9=NW`.

**Input**: binary ndarray (0=background, 1=foreground).  
**Output**: binary ndarray — 1-pixel-thick skeleton preserving topology.

**Provenance**: input sheet, revision, method=`jesse.zhang_suen`, confidence=1.0
(algorithm is deterministic given binary input).

---

## Skeleton Invariants — `skeleton_invariants(skel)`

Returns a dict with:

| Key | Meaning |
|---|---|
| `endpoints` | pixels with exactly 1 active 8-neighbor |
| `t_junctions` | T-branch pixels (3 distinct outgoing directions from a junction cluster) |
| `x_junctions` | X-branch/cross pixels (≥4 distinct outgoing directions) |
| `holes` | enclosed background components not touching the image border |
| `chi` | `1 - holes` (Euler characteristic for a single foreground component) |

**Junction clustering convention**: 8-connected clusters of `deg ≥ 3` pixels are
merged into a single junction node; branch count is the number of distinct
`(sign(dy), sign(dx))` direction pairs from the cluster to the surrounding
skeleton pixels. This is the convention that reproduces Table 9.4 for the 5
simple glyphs.

**Background connectivity**: 4-connected background flood fill (dual to the
8-connected foreground). Without this duality, background leaks through
diagonal gaps in ring glyphs.

**Provenance**: input sheet, revision, method=`jesse.skeleton_invariants`, confidence=1.0

---

## Table 9.4 — Clean 1px Glyph Signatures

Zhang-Suen skeleton of clean 1px primitives at 112×112 canvas, before
downsampling:

| Glyph | Paper signature | Reproduced | Invariants |
|---|---|---|---|
| Circularity ○ | E=0, J=0, b₁=1, χ=0 | ✅ exact | `endpoints=0, t_junctions=0, x_junctions=0, holes=1` |
| Concentricity ◎ | E=0, J=0, b₁=2, χ=−1 | ✅ exact | `endpoints=0, t_junctions=0, x_junctions=0, holes=2` |
| Perpendicularity ⊥ | E=3, J_T=1, b₁=0 | ✅ exact | `endpoints=3, t_junctions=1, x_junctions=0, holes=0` |
| Straightness — | E=2, J=0, b₁=0 | ✅ exact | `endpoints=2, t_junctions=0, x_junctions=0, holes=0` |
| Parallelism ∥ | E=4, J=0, b₁=0 | ✅ exact | `endpoints=4, t_junctions=0, x_junctions=0, holes=0` |

**Note**: Complex rows (e.g. True Position `E=4, J_T=4, J_X=1, b₁=1`) could not be
reproduced under any single consistent counting model — the junction convention
for crossing glyphs is genuinely underspecified in the paper. See
`docs/design-docs/open-issues.md` — **Complex GD&T invariant rows**.

---

## Thick-Stroke Skeleton Pre-processing

### The Problem

Architectural drawings (real sheets, not synthetic) are rasterized with
variable stroke widths — typically 0.5–2 pt at 300–600 DPI. Thick strokes
cause **skeleton spurs**: Zhang-Suen produces spurious short branches off the
true skeleton centerline because the stroke fills more than 1 pixel width.
This breaks the Table 9.4 signatures:

```
thick stroke (3px wide)
  ┌─────────┐
  │█████████│  →  skeletonized  →  ─────█─────  (spurious endpoint)
  │█████████│                         ─────────
  └─────────┘
```

The error is NOT in `zhang_suen()` — it is correct. The error is in assuming
the raster input is 1px wide.

### Pre-processing Pipeline for Real Sheets

For real architectural drawings, apply **both** of these before
`skeleton_invariants(zhang_suen(...))`:

#### Step 1 — Binarization with adaptive threshold

```python
import cv2

# Adaptive Gaussian threshold — handles uneven ink density across the sheet
binary = cv2.adaptiveThreshold(
    gray,
    maxValue=255,
    adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
    thresholdType=cv2.THRESH_BINARY_INV,
    blockSize=15,  # pixel neighborhood; tune for your DPI
    C=10,  # constant subtracted from weighted mean
)
```

**Parameters to tune**:
- `blockSize`: odd integer ≥ 3. Larger = smoother threshold but loses fine detail.
  At 300 DPI on architectural drawings, `blockSize=11`–`21` typically works.
  At 600 DPI, use `blockSize=21`–`41`.
- `C`: constant subtracted. Higher = more aggressive binarization (removes
  faint strokes). Start with `C=10`; reduce to `C=3`–`5` for light blueprints.

#### Step 2 — Structural Opening to Merge Adjacent Strokes

Before thinning, close gaps between related strokes and remove isolated noise:

```python
kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)  # close gaps
binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)  # remove noise
```

**Parameters to tune**:
- Kernel size: `(3,3)` for clean line work; `(5,5)` for dense drawings with
  many crossing lines; `(7,7)` for very thick strokes (>2pt at 300DPI).
- For blueprint originals: `MORPH_CLOSE` only (don't open; ink is faint).

#### Step 3 — Skeletonization

```python
from jesse import zhang_suen, skeleton_invariants

skel = zhang_suen(binary.astype(np.uint8))
inv = skeleton_invariants(skel)
```

### Full Pre-processing Function

```python
import cv2
import numpy as np
from jesse import zhang_suen, skeleton_invariants


def preprocess_for_invariants(
    gray: np.ndarray,
    blockSize: int = 15,
    C: int = 10,
    close_kernel: int = 3,
    open_kernel: int = 3,
) -> dict:
    """Thick-stroke-tolerant pre-processing for skeleton invariants.

    Parameters
    ----------
    gray : np.ndarray
        Grayscale input image (0-255, any dtype cast to uint8).
    blockSize : int
        Adaptive threshold block size (odd, ≥ 3). Tune to DPI.
        300 DPI: 11-21.  600 DPI: 21-41.
    C : int
        Adaptive threshold constant. 10 = good starting point.
        Lower values preserve fainter strokes.
    close_kernel : int
        Morphological close kernel size (odd). Merge intra-stroke gaps.
    open_kernel : int
        Morphological open kernel size (odd). Remove isolated noise.

    Returns
    -------
    dict
        skeleton_invariants result dict with keys:
        endpoints, t_junctions, x_junctions, holes, chi
    """
    # Step 1: binarize
    if gray.dtype != np.uint8:
        gray = (
            (gray / gray.max() * 255).astype(np.uint8)
            if gray.max() > 1
            else (gray * 255).astype(np.uint8)
        )
    binary = cv2.adaptiveThreshold(
        gray,
        maxValue=255,
        adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY_INV,
        blockSize=blockSize,
        C=C,
    )
    # Step 2: morphological cleanup
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (close_kernel, close_kernel))
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (open_kernel, open_kernel))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, close_k)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, open_k)
    # Step 3: skeletonize and compute invariants
    skel = zhang_suen(binary)
    return skeleton_invariants(skel)
```

### Ambiguous Signatures — Flag for Human Review

Even after correct pre-processing, thick-stroked glyphs on real sheets may
produce invariant signatures that don't cleanly match any Table 9.4 class. The
result should be treated as **indicative, not authoritative**:

- If the signature matches a Table 9.4 class unambiguously → use it.
- If the signature is **complex** (high endpoint count OR many junctions) →
  the glyph has compound GD&T features (e.g. True Position with multiple
  datum references); **flag the result for human review** with kind
  `gd_complex_row` rather than accepting or rejecting it.

See `docs/design-docs/open-issues.md` — **Complex GD&T invariant rows** and
**Thick-stroke skeleton invariants** for full context.

### Provenance on Results

Every extracted invariant fact carries:
- `provenance.sheet_id`: source drawing sheet
- `provenance.revision`: sheet revision string
- `provenance.method`: `"jesse.zhang_suen+skeleton_invariants"` (pre-processing
  parameters recorded in method docstring if non-default)
- `provenance.confidence`: 1.0 for algorithmic steps; reduced for complex
  or thick-stroke cases where ambiguity is present

---

## Usage in the Pipeline

`symbols` CLI command (`matchline symbols`):

```bash
matchline symbols --out symbols_results.json
```

Runs `run_symbols.py`:
1. Trains WiSARD on 400 jittered synthetic symbols per class
2. Evaluates 100 held-out test symbols (Experiment A)
3. Computes skeleton invariants on clean 1px primitives and checks against
   Table 9.4 (Experiment B)

For real sheets with thick strokes, call `preprocess_for_invariants(gray, ...)`
from `jesse.py` directly in your integration code, then pass the resulting
invariants to `skeleton_invariants()`.

## Limitations

- **Thick-stroke degradation**: The Zhang-Suen skeleton and derived invariants assume 1px clean line work. Strokes thicker than ~2px at the imaging DPI produce distorted skeletons that generate false junctions and endpoints, leading to misclassification.
- **DPI-dependent parameter tuning**: Adaptive threshold `blockSize` and morphological kernel sizes are DPI-sensitive. The documented defaults (300 DPI: 11–21, 600 DPI: 21–41) are starting points, not universal guarantees.
- **Compound GD&T symbols not supported**: Complex GD&T annotations with multiple datum references produce high endpoint/junction counts that do not cleanly map to any Table 9.4 class. These must be flagged for human review rather than classified automatically.
- **No 3D pose invariance**: The method operates on 2D projected skeleton invariants; it does not handle symbols rotated in plane or viewed from non-standard angles without pre-alignment.
- **Training is synthetic-only**: WiSARD is trained on jittered synthetic symbols; real architectural symbol glyphs with slight stylistic variations may fall below classification confidence thresholds.
- **Invariant tables are brittle**: Changes to the GD&T standard table (Table 9.4) require code updates; there is no external data-driven lookup.
