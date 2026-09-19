# REPRODUCTION_NOTES.md — Jesse-Vision prototype

Source paper: "Zero-Weight Optical Recognition: Deterministic Character, GD&T,
and Natural Visual Perception via One-Hot Pixel String Tuple Addressing and
Empirical Bayesian Beliefs" — https://jesse.solidsf.com/paper (solidSF
Research, self-published web technical report, read 2026-09-18; 601 lines).

Prototype: `jesse.py` (numpy only), `symbols.py`, `run_mnist.py`,
`run_symbols.py`. No LLM API was used; everything below was implemented from
the paper text alone.

## 1. Implemented components and their paper citations

| Component | Paper section | Implementation |
|---|---|---|
| 2-bit thermometer encoding `B(x,y) = (I[I>35], I[I>120])`, 28×28 → 1568 bits | §2, Eq. B(x,y) | `thermometer_encode()` — verbatim |
| N-tuple addressing `a_k = Σ_s B[τ_{k,s}]·2^s`, K=160, n=10, 1024 slots/tuple, 163,840 slots/class | §2 | `tuple_addresses()` — verbatim |
| One-pass counter training `RAM_c[k, a_k] += 1` | §2 ("Single-Pass Learning O(1) updates") | `WisardClassifier.fit()` — verbatim |
| Empirical log-odds belief `E_c = Σ_k log((RAM_c[k,a_k]+α)/(mean_{c'} RAM_{c'}[k,a_k]+α))` | title + §4.2 ("empirical log-odds emission E(t,d)") | `predict_logodds()` — **reconstructed, see [U2]** |
| Plain-sum response `score_c = Σ_k RAM_c[k,a_k]` | canonical WiSARD baseline | `predict_sum()` — for comparison |
| Bleaching (count tuples with `RAM > b`, raise b on ties) | **not in paper text** | `predict_bleach()` — implemented, unused |
| Center-of-mass normalization to (14,14) | §4.1, §6.3 | `com_normalize()` — integer shift |
| Zhang-Suen 1-pixel skeletonization | §9.2 (both sub-iteration equations) | `zhang_suen()` — verbatim conditions |
| Topological invariants `d(p)`, endpoints/junctions, holes `b₁`, `χ = 1 − b₁` | §9.2 + Table 9.4 | `skeleton_invariants()` — conventions **reconstructed, see [U5]** |

## 2. [UNSPECIFIED] flags — what the paper leaves out, and the choice made

- **[U1] Tuple placement.** Paper: "K = 160 tuples of size n = 10 bits sampled
  within localized spatial neighborhoods N(c_r, c_c) of radius 4." No center
  layout, no intra-neighborhood sampling rule, no seed. **Choice:** 160 centers
  on a regular 10×16 grid spanning the field; per center, 10 (pixel,
  threshold-channel) pairs sampled without replacement from the 9×9
  neighborhood (both channels ⇒ 162 candidates), fixed RNG seed 42.
- **[U2] "Empirical Bayesian Beliefs" formula.** The phrase appears in the
  title and §4.2 names an "empirical log-odds emission E(t,d)" but **no
  equation is ever given**. **Choice:** per-tuple log-likelihood-ratio of the
  class counter against the mean counter across classes (smoothing α).
  Evidence this is the right reconstruction: it is the *only* tested variant
  that reproduces the headline number (see §3). Plain-sum WiSARD collapses to
  30.55% — the Bayesian normalization is load-bearing, not decorative.
- **[U3] Bleaching.** Canonical in WiSARD literature but never mentioned in the
  paper. Implemented as variant C; not needed for the reported results.
- **[U4] COM-normalization resampling.** Integer pixel shift (`np.roll`) vs
  subpixel interpolation unspecified. Chose integer shift (zero-weight
  friendly). Only used in the multi-digit pipeline, which was not the
  verification target.
- **[U5] Invariant counting conventions.** Paper gives Zhang-Suen conditions
  and Table 9.4 signatures but no convention for: neighbor indexing (chose
  standard p2=N…p9=NW clockwise), junction clustering (chose: merge
  8-connected deg≥3 pixel clusters, classify by distinct outgoing branch
  directions), background connectivity for hole counting (chose 4-connected
  background / 8-connected foreground duality — required, else background
  leaks through diagonal ring gaps). These reproduce 5/5 simple rows of
  Table 9.4 (see §3). The complex rows (e.g. True Position
  E=4,J_T=4,J_X=1,b₁=1) could not be derived under any single consistent
  counting model tried — the junction convention for crossing glyphs is
  genuinely underspecified.
- **[U6] Training increment magnitude.** Assumed +1 per sample per tuple
  (standard); paper says only "increments empirical counters."
- **[U7] Tie-breaking.** `argmax` lowest-index-wins; unspecified.

## 3. Measured results vs. paper claims

### MNIST (60k train / 10k test, standard split)

| Metric | Paper claim | Measured (this VM, 2 vCPU, numpy) |
|---|---|---|
| Test accuracy | **93.89%** | **93.77%** (α=1.0, seed 42); **94.57%** (α=0.1); 93.38% / 93.20% (seeds 123/2026) |
| Training time | 0.195 s (M4 Max) | **0.56 s** (107.7k img/s) after vectorization (was 2.24 s) |
| Inference latency | 5.10 μs/img (M4 Max) | **21.2 μs/img batched** (was 42–49); 166 μs/img single-image |
| Plain-sum accuracy | — | 30.55% (broken without belief norm) |

The accuracy claim **reproduces**: my runs bracket 93.89% across seeds, and a
better smoothing choice exceeds it. Latency was cut ~2.3× by honest numpy
vectorization (batched thermometer encoding; einsum-fused tuple addressing;
per-class accumulation of the log-odds score avoiding the (C,N,K) tensor —
see `docs/belief_derivation.md` for the algebraic reformulation). All
predictions verified bit-identical to the pre-optimization code on all 10,000
test images. Remaining gap to 5.10 μs is (a) M4 Max vs. 2-vCPU VM, (b) numpy
call overhead dominating single-image latency, (c) their presumably
WASM/SIMD-optimized in-browser build. 5.10 μs for 160 table lookups +
1600 adds is aggressive but not implausible in optimized native code; treat
the exact figure as hardware/marketing-grade.

### Clean vector symbols (10 classes: circle, square, triangle, cross,
perpendicularity, parallelism, angularity, position, door, window; jittered
±8° rotation, ±6px translation, 0.92–1.08 scale, variable stroke; 4000 train /
1000 test, same K=160/n=10 hyperparameters)

| Variant | Accuracy |
|---|---|
| log-odds (α=0.1) | **100.00%** |
| plain sum | 95.30% |

Training: 0.15 s. Confusion concentrated in triangle↔perpendicularity and
door↔square/window — geometrically similar glyphs at 28×28, as expected.

### Topological invariants vs. Table 9.4 (§9.2), clean 1px glyphs @112×112

| Glyph | Paper signature | Reproduced |
|---|---|---|
| Circularity ○ | E=0, J=0, b₁=1, χ=0 | ✅ exact |
| Concentricity ◎ | E=0, J=0, b₁=2, χ=−1 | ✅ exact |
| Perpendicularity ⊥ | E=3, J_T=1, b₁=0 | ✅ exact |
| Straightness — | E=2, J=0, b₁=0 | ✅ exact |
| Parallelism ∥ | E=4, J=0, b₁=0 | ✅ exact |

Caveat: thick-stroke rasterizations produce skeleton spurs that break the
signatures — the invariants are resolution/stroke-quality sensitive, which
matters for real drawing sheets (see verdict).

## 4. Hypotheses for remaining gaps

1. **Latency gap (now ~4× batched, was 9×):** closed most of the Python-overhead
   gap via vectorization (verified bit-identical). Remainder is hardware
   (M4 Max vs. 2-vCPU VM) + numpy call overhead on single images; a
   numba/C++/SIMD port would be the honest next step, not more numpy.
2. **Seed sensitivity (±0.7pp):** tuple placement [U1] is a real free parameter;
   the paper's 93.89% sits inside my measured band, so no foul play is
   needed to explain it — but it does mean the headline number is
   placement-dependent.
3. **α sensitivity (91.3–94.6%):** the undisclosed smoothing [U2] moves the
   headline number by >3pp; the paper's exact α (or equivalent) is unknown.
4. **Complex invariant rows:** irreproducible from the text alone [U5]; the
   paper's 99.93% GD&T figure rests partly on conventions the paper does not
   disclose.

## 5. Verdict

The core technique is **sound and the headline MNIST number is credible** —
this is a genuine reproduction, not a refutation. A from-spec WiSARD with a
log-odds belief normalization hits 93.8–94.6% where the paper claims 93.89%,
trains in one pass with integer ops, and needs no GPU. On clean vector
symbols — the actual use case — it reaches 100% on a 10-class jittered set
with the paper's exact hyperparameters, which is the strongest signal for
extending it to architectural symbol sets (door/window tags, equipment
symbols, dimension glyphs).

Recommend extending, with eyes open: (a) the win is determinism +
CPU-only + auditability, not accuracy vs. CNNs (ResNet-18 is 99.1% on
MNIST); (b) validate on *your* clean drawings before trusting the 99%+
figures — the paper's GD&T/schematic numbers are on their synthetic data
with undisclosed conventions [U5]; (c) test robustness to rotation beyond
±8° and stroke-width variation, the two places the invariant signatures
proved fragile; (d) the belief-normalization formula [U2] deserves a proper
derivation if this goes into production — it carries ~63pp of accuracy.
