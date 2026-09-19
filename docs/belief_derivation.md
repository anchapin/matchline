# Belief normalization: derivation note

## What the paper says (and doesn't)

The Jesse-Vision paper repeatedly invokes "Empirical Bayesian Beliefs" /
"empirical log-odds emission E(t,d)" as the scoring rule, but **never states
the formula**. The prototype (`jesse.py::predict_logodds`) implements our
reconstruction:

```
E_c = Σ_k log( (RAM_c[k, a_k] + α) / (mean_{c'} RAM_{c'}[k, a_k] + α) )
```

where `RAM_c[k,a]` is the integer counter for class `c`, tuple `k`, address
`a`, `a_k` is the observed address at tuple `k`, and `α` is an additive
smoothing constant. This note grounds that formula in standard theory and
proposes a principled alternative.

## Derivation: it is a sum of pointwise mutual informations

Model each tuple's address as a categorical draw. The MLE of the emission
probabilities from the RAM tables is

```
P̂(a | c, k) = RAM_c[k, a] / N_c ,   N_c = # training samples of class c.
```

Naive Bayes across tuples (conditional-independence assumption, the standard
WiSARD/WNN generative reading):

```
log P(c | a_1..a_K) = Σ_k log P(a_k | c, k) + log P(c) + const.
```

Now rewrite the prototype's summand for **balanced classes** (N_c = N ∀c,
true of MNIST up to sampling noise):

```
(RAM_c[k,a] + α) / (mean_{c'} RAM_{c'}[k,a] + α)
      ≈  P̂(a | c, k) / P̂(a | k)
```

since `mean_{c'} RAM_{c'}[k,a] = N · (1/C) Σ_{c'} P̂(a|c',k) = N · P̂(a|k)`
for balanced classes. Therefore

```
E_c ≈ Σ_k log( P̂(a_k | c, k) / P̂(a_k | k) ) = Σ_k PMI(a_k ; c),
```

a sum of **pointwise mutual information** terms between each observed tuple
address and the class. For balanced classes,

```
argmax_c Σ_k PMI(a_k ; c)  =  argmax_c [ Σ_k log P(a_k|c) + log P(c) ]
```

because the two differ only by the class-independent term `Σ_k log P(a_k)`
and the constant prior `log P(c)`. **So the reconstructed formula is, for
balanced classes, exactly the Naive Bayes log-posterior decision rule**
(up to additive-smoothing details).

## Why the plain sum collapses (30.55%)

The canonical WiSARD score `Σ_k RAM_c[k, a_k]` measures *how much training
data of class c ever produced these addresses* — it is dominated by tuples
whose addresses are frequent in **every** class (e.g. all-background
neighborhoods in MNIST). The PMI form discounts precisely those
non-discriminative tuples: if `P̂(a|c,k) ≈ P̂(a|k)` the term is ~0. The
normalization is not a tweak; it is the entire discriminative mechanism.
(Verified: removing it drops MNIST from 93.77% to 30.55%.)

## Caveats

1. **Imbalanced classes**: the mean-of-counts denominator is only
   proportional to `P̂(a|k)` when classes are balanced. For imbalanced data
   the formula silently mis-weights the prior; a proper NB implementation
   should normalize per class (`RAM_c[k,a]/N_c`) and add `log P(c)`.
2. **α sensitivity**: measured MNIST accuracy swings 91.3–94.6% over the
   smoothing constant (best α=0.1: 94.57%). A method this load-bearing should
   not hinge on an untuned constant.

## Proposed alternative: mutual-information-weighted voting

Weight each tuple's vote by its informativeness, estimated once from the
RAM tables:

```
I_k = I(A_k ; C) = Σ_{a,c} P̂(a,c) log( P̂(a,c) / (P̂(a) P̂(c)) )
score_c = Σ_k I_k · log P̂(a_k | c, k)
```

Properties: tuples carrying no class information get weight ≈ 0
automatically (no α to tune); the weight is a proper information-theoretic
quantity, auditable per tuple ("tuple #87 mostly fires on door swings");
reduces to the PMI rule when all I_k are equal. Test plan: implement
`predict_mi_weighted`, compare accuracy and α-robustness against
`predict_logodds` on MNIST and on the AEC-bench door/window crops.
