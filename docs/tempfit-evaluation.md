# TempFit template-fitting for symbol spotting: evaluation against Matchline's detector (#1256)

Issue #1256 asks whether **TempFit** (arXiv:2610.17776, *TempFit: Panoptic
Symbol Spotting via Template Fitting*) should replace or complement
Matchline's symbol detector in `detector/`. This document answers the issue's
four questions against the code in this repository and against the paper —
not in the abstract.

## How to read this document

- **Verified by reading Matchline's code**: every statement about Matchline
  cites the file and the line it came from. The grep commands that produce
  those citations are reproduced in [§10](#10-reproduction-grep-evidence).
- **Verified from the paper**: TempFit's claims are cited as *paper §x.y* /
  *paper Table n* (arXiv:2610.17776v1, submitted 13 Oct 2026, cs.GR). The
  project page is <https://tempfit-project.github.io/>.
- **Not verified**: no TempFit code, model weights, or datasets exist in this
  repository, and TempFit cannot be run here (see [§5](#5-constraints)). **This
  document therefore contains no benchmark numbers of our own** — no measured
  TempFit accuracy, no measured TempFit-vs-YOLO comparison, no timings. The
  only quantitative figures are (a) the ones the paper states, always cited to
  the paper, and (b) constants measured from Matchline's own source, always
  cited with file and line. Where the paper is silent, this document says
  **"not addressed in the source"** and does not speculate.

The same discipline as `docs/crab-evaluation.md` (#1257): an evaluation
document with an explicit claim ledger, no unverifiable numbers presented as
measurements, and a verdict that names its first concrete step.

### Where the issue's four questions are answered

| Issue #1256 asks | Answered in | Short answer |
|---|---|---|
| 1. Accuracy on door/window/fixture classes | [§4.1](#4-where-template-fitting-does-not-beat-the-current-yolo-detector) | **not answerable from the source** — the paper reports no door/window results and no box-regression baseline |
| 2. Handling of scale/rotation variants | [§3.1](#3-where-template-fitting-could-beat-the-current-yolo-detector), [§4.5](#4-where-template-fitting-does-not-beat-the-current-yolo-detector) | TempFit predicts pose explicitly (its strongest transferable claim); YOLO compensates with tiling and scale banks; TempFit's own length-sensitive decoding is a risk on 4800×7200 sheets |
| 3. Inference speed on large-format drawings | [§4.4](#4-where-template-fitting-does-not-beat-the-current-yolo-detector) | paper-relative only (279.0 ms/scene, hardware unstated); no Matchline-comparable ratio exists |
| 4. Training data requirements (templates vs annotated examples) | [§3.2](#32-one-example-classes-the-case-the-issue-names), [§4.6](#4-where-template-fitting-does-not-beat-the-current-yolo-detector) | a template library is cheap (one canonical template per family); the **training corpus is not** — the paper's own library was clustered from 103,959 annotated instances |

---

## 1. What TempFit is (from the paper)

TempFit reformulates **panoptic symbol spotting** on CAD drawings as a
**template fitting** problem: every foreground instance is produced by
retrieving a canonical template from a library `T` and predicting the 2D
transformation that aligns it to the drawing (*paper §1, §3.1*). The paper's
argument is that a prediction which is by construction an instantiation of a
valid template cannot suffer the *merge* and *fragmentation* errors that
plague primitive-wise and instance-wise segmentation methods, which assign
labels or masks to individual graphical primitives without constraining them
to be a spatially connected group (*paper §1, §2.1*).

The model (*paper §3.2*):

- **Input is vector CAD, not pixels.** "All graphical primitives are
  discretized into line segments via uniform sampling", each with a geometric
  feature vector (length, direction, midpoint, primitive centroid); the
  line-based representation is taken from VecFormer, and both drawing and
  templates are encoded by a shared PointTransformerV3 backbone
  (*paper §3.2, CAD and Template Encoding*).
- **Template library as explicit geometric memory.** Each template is mean-
  pooled into one token, forming `T_tpl`; templates are prepended to the
  decoder sequence, so geometry is not memorised in the weights (*paper §3.2*).
- **Background and foreground are handled separately.** Learnable
  category-specific background tokens predict one mask per background category
  in parallel; foreground instances are then generated **autoregressively**,
  one token at a time, each step retrieving a template (cosine similarity
  against the template prefix) and predicting translation, rotation and scale,
  each discretised into `K = 128` bins (*paper §3.2*).
- **Training** uses cross-entropy on template identity and on the discrete
  transform tokens, Huber regression on continuous attributes, BCE + Dice on
  background masks, and a Chamfer reconstruction loss against ground-truth
  instances (*paper §3.2, Loss Function*). 6 decoder layers, `d_model = 768`,
  500 epochs, AdamW, 8 × RTX-4090, per-GPU batch 4 × gradient-accumulation 4
  (*paper §4.2, Implementation Details*).
- **The template library is an input, not a by-product.** It is either
  projected from a 3D asset library (IndustryCAD: 76 prototypes) or
  auto-constructed by clustering the **training set** — on FloorPlanCAD, 462
  prototypes were extracted from 103,959 annotated training instances by greedy
  coverage under a rotation/scale-invariant shape-context similarity threshold
  at the top 1 % of the pairwise similarity distribution (*paper §4.1*).

Reported results (*paper §4.3–§4.6*): on FloorPlanCAD under a strict
one-to-one (Hungarian) protocol, PQ 80.1 vs 77.0 for SymPointV2 and 74.4 for
the paper's own detect-then-fit baseline DEIM+ICP; on the harder IndustryCAD,
PQ 67.0 vs 56.6 for DEIM+ICP; zero-shot on SESYD, PQ 24.67 vs 16.23 for
SymPointV2. The paper's own limitation analysis reports two failure modes:
instances whose nearest template is far away (Chamfer distance ≳ 0.07) are
recovered with sharply degraded IoU, and drawings with exceptionally many
instances break down along the autoregressive chain (IoU ≈ 0.28 at the first
decoding step, below 0.05 towards the end of an 80-instance sequence); it also
states plainly that "the token-by-token generation also limits inference speed
on large drawings" (*paper, Limitations and Future Work*).

**Availability**: the project page lists **Code: "Coming soon"** and **Data:
"Coming soon"** (<https://tempfit-project.github.io/>, read 2026-10-23). The
page is a SIGGRAPH Asia 2026 submission. Nothing is downloadable today.

---

## 2. What Matchline runs today (the baseline this evaluation compares against)

| What | Where | Note |
|---|---|---|
| 2-class symbol taxonomy (**door**, **window**) | `detector/classes.py:13-17` | per-dataset label maps at `detector/classes.py:25-67` |
| Fine-tune of a small YOLO | `detector/train.py:20-27` | default `yolo11n.pt`, `imgsz 640`, `batch 8` |
| Tiled inference for large sheets | `detector/sahi_infer.py:4-6`, `:20-38`, `:93-120` | 1024 px tiles, 20 % overlap, `conf 0.25`, `imgsz 1024`, CPU by default |
| Tile-merge NMS (IoU **or** containment) | `detector/sahi_infer.py:41-90` | the containment rule exists because a symbol straddling a tile edge yields a sliver *and* a full box |
| Zero-shot / cross-dataset eval | `detector/eval_zero_shot.py:26-73` | greedy IoU-0.5 matcher, per-class P/R, unmatched GT **and** unmatched predictions reported |
| AP50 / mAP50 (scratch path) | `detector/ap50.py:20-55` | all-point interpolated AP from the same greedy matches |
| Degradation robustness harness | `detector/eval_robustness.py`, `docs/detector-robustness.md` | scan-noise and thickened-stroke variants; P/R deltas vs clean |
| Measured detector quality (CubiCasa5K, 3 seeds × 50 epochs) | `detector/results/ab_2026-09-28/analysis.md:24-31` | mAP50 **0.96805 ± 0.00303**, mAP50-95 0.87951, P 0.93356, R 0.92748 |
| Run-to-run noise floor | `detector/results/noise_2026-10-02/analysis.md:13`, `:18` | run-to-run spread **0.00000** (byte-identical `results.csv`); seed-to-seed spread 0.00592 |
| Dataset status (FloorPlanCAD eval, AEC sheets, harness check, SAHI proof) | `detector/README.md:103-108` | FloorPlanCAD eval = 5,308 images, 12,697 doors + 1,952 windows; SAHI on a 7201×4801 sheet ≈ 30 s CPU |
| Backend swap point | `detection_provider.py:606-613` (`PROVIDERS`), `:155-186` (`YoloSahiProvider`) | the R2 interface: a backend is a config value, not a rewrite (`ROADMAP.md:311-318`) |
| Rule-based (training-free) alternatives already in production | `detection_provider.py:9-24` | `door_swing` (rule-based, `door_detect.py`), `vector_glazing` (from the wall reader) |
| One-example template path that already ships | `hvac_legend.py:207-233`, `hvac_trace.py:537`, `:541-570` | the firm's own symbol is cut from the sheet raster as a one-shot template and found by NCC at 0.80 |

What Matchline does **not** have today: any vector-primitive symbol model.
`ROADMAP.md:280-283` names VecFormer as "the leading learned candidate" for
the vector-native track, and `docs/r1_vector_spike.md:49-52` records the
decision to defer it: "VecFormer stays deferred … any future use enters
through the R2 DetectionProvider interface, not as a pipeline rewrite."
TempFit is *VecFormer plus a template memory* — i.e. the deferred candidate
with the addition the paper's contribution lies in.

---

## 3. Where template fitting could beat the current YOLO detector

These are **hypotheses with repo-side evidence**, not measurements. No
TempFit-vs-YOLO number exists anywhere (§1, §5).

**3.1 Scale and rotation handled by construction rather than by tiling.**
TempFit predicts translation, rotation and scale per instance as first-class
outputs (*paper §3.2, Transform*), so a rotated or rescaled symbol is the
*same* template under a different pose. The raster path instead compensates
with infrastructure: SAHI tiling at a fixed 1024 px window
(`detector/sahi_infer.py:20-38`) and, on the HVAC side, explicit scale and
turn banks — `TEMPLATE_SCALES = {"diffuser": (1.0, 0.87, 1.15)}`
(`hvac_trace.py:118`) and `TIGHT_TURNS = {"grille": (0, 1)}`
(`hvac_trace.py:105`). The repo has already measured how much the raster path
loses to scale: "The raster door sweep is scale-sensitive. tosv p3: 61 door
candidates at 1/8" scale, 24 at 1/4"" (`docs/r1_vector_spike.md:44-46`).
**A pose-parameterised template matcher is the natural fix for exactly that
failure mode**, and it is the mode where the paper's own evidence is
strongest (IndustryCAD scale spread 510× in bbox area, *paper §4.1*).

**3.2 One-example classes: the case the issue names.** The issue's premise is
that "TempFit fits a floor-plan template where one example is available".
That is **true of the template library and false of the training**: the paper
builds its FloorPlanCAD library by clustering 103,959 *annotated training
instances* (*paper §4.1*) and trains with per-primitive instance annotations
and a Chamfer loss against ground-truth instances (*paper §3.2*). TempFit
relocates annotation effort from "many boxes per class" to "one canonical
template per family **plus** a fully instance-annotated training corpus"; it
does not remove it. For the case where genuinely only one example exists,
Matchline already has the right mechanism and ships it: the legend block is
cut into one-shot templates and matched by NCC (`hvac_legend.py:207-233`,
`hvac_trace.py:537`). TempFit's marginal contribution there is *learned*
retrieval plus pose estimation and occlusion-aware fitting, not the idea of
using a single example.

**3.3 A finer label than the current 2-class taxonomy.** Matchline collapses
every door variant into one class (`detector/classes.py:25-36`) even though
the underlying data carries `single_door` / `double_door` / `sliding_door`
(`detector/classes.py:60-66`). A retrieved *template identity* is by
construction a per-variant label, so a template-fitting stage could recover
door subtype (and window subtype) without training a new detector — a real
gain, provided the downstream takeoff consumes it.

**3.4 Geometric grounding transfers better across drawing styles.** The
paper's zero-shot result (SESYD, no fine-tuning: PQ 24.67 vs 16.23 for
SymPointV2, *paper §4.6*) is the paper's evidence that "rather than directly
predicting instance labels from learned features … our model retrieves the
most geometrically similar template from the library and adapts it via an
explicit 2D transformation". This is the scenario Matchline actually cares
about for a new client: a firm's own symbol style with no training data. The
raster path's answer to that today is the legend-template route (above), not
the YOLO weights.

**3.5 Occlusion and adjacent-instance separation.** The paper's largest
margin over detect-then-fit is on overlapping instances (PQ 54.4 vs 26.9 in
the most-overlapped bin, *paper §4.4, Table 3*). Matchline's door/window
symbols are usually *not* adjacent like industrial equipment, so the size of
this advantage for Matchline is doubtful — but where symbols *are* adjacent
(a door symbol next to a window symbol in a wall run, a symbol touching a
tag), the paper's mechanism (fit a whole template, do not classify loose
strokes) is the right one.

---

## 4. Where template fitting does not beat the current YOLO detector

**4.1 There is no YOLO baseline in the paper at all.** Every baseline is a
CAD symbol-spotting model: PanCADNet, CADTransformer, GAT-CADNet (primitive-wise);
SymPoint, SymPointV2, CADSpotting, VecFormer (instance-wise); PrimitiveAnything
(shape abstraction) and DEIM+ICP (detect-then-fit) (*paper §4.2, Baselines*).
The paper therefore contains **zero** evidence about template fitting versus a
box-regression detector. The issue's request #1 ("accuracy on door/window/
fixture classes") cannot be answered from the paper: it reports panoptic PQ
over primitive masks for furniture/equipment categories and **no door or
window results**.

**4.2 The paper's headline gains are against failure modes YOLO does not
have.** The merge/fragmentation critique (*paper §1, §2.1*) targets methods
that assign labels to individual primitives and then group them heuristically.
YOLO predicts independent, per-object boxes; it cannot "merge primitives from
distant symbols into a single instance" in the first place. So the 80.1 vs
77.0 PQ margin buys Matchline nothing it does not already have.

**4.3 The metrics are not comparable, and no conversion exists.** The paper's
metric is panoptic PQ with a length-weighted IoU over *vector primitives*
(*paper §4.2, Evaluation Metrics*). Matchline's metrics are box-level:
mAP50 from `detector/ap50.py:20-55` and greedy IoU-0.5 P/R from
`detector/eval_zero_shot.py:26-73`. There is no published mapping from
primitive-mask PQ to box mAP50, so even a released TempFit could not be
scored on Matchline's scoreboard without a converter we would have to write
(and validate).

**4.4 Cost, against this repository's budget.** TempFit is 108.3 M parameters
at 279.0 ms per scene (*paper Table 1*); Matchline's YOLO11n baseline is
2,590,230 parameters and 55.1 ms forward at 640 px on CPU
(`detector/README.md:135-137`, ECA cost table). TempFit trains on 8 × RTX-4090 for
500 epochs (*paper §4.2*); this repository trains on a single AMD RX 6600 XT
(ROCm 7.2.3, `detector/README.md:25`, `:108`) at 25–50 epochs per run, with
5.08 h of GPU time for a full 2-arm × 3-seed × 50-epoch matrix
(`detector/results/ab_2026-09-28/analysis.md:8`). Reproducing the paper's
training recipe is out of budget by orders of magnitude; reproducing its
*inference* on a plan sheet would need a vector front-end Matchline does not
have (§5.2).

**4.5 The paper's own failure modes land on Matchline's large sheets.**
TempFit degrades on instances far from the library (Chamfer ≳ 0.07) and on
very instance-dense scenes (80-instance block: IoU 0.28 at the first decoding
step, < 0.05 at the end) (*paper, Limitations and Future Work*). Matchline's
sheets are exactly the dense case: "Sheets are 4800x7200+ px"
(`detector/sahi_infer.py:4`). The raster path handles density by *tiling* —
which bounds the number of instances per forward pass — while TempFit's
autoregressive chain is explicitly length-sensitive and the paper concedes the
latency cost of token-by-token generation on large drawings.

**4.6 Licence and data-availability friction on the training side.** The
FloorPlanCAD copy Matchline holds is the **test split only**
(`detector/convert_floorplancad.py:8-9`; `DATASETS.md:97-118`) and the
parquet schema is `image` + `objects {id, bbox, category, mask}` — **no line
primitives** (`detector/convert_floorplancad.py:4-6`). TempFit needs
per-primitive annotations and the original vector release; the local copy
cannot supply either. FloorPlanCAD is CC BY-NC 4.0
(`assets/license_ledger.json:54-59`), so weights trained on it stay
quarantined for commercial release — the same gate the R1 vector track already
flags (`ROADMAP.md:292-294`).

---

## 5. Constraints

### 5.1 Locally runnable (open/local models preferred; no third-party API)

**TempFit fails this constraint today, on three independent counts.**

1. **No artefact exists.** The project page lists Code and Data as "Coming
   soon" (<https://tempfit-project.github.io/>). There is no checkpoint, no
   inference script, no template library to download. Nothing about TempFit can
   be measured in this repository today.
2. **Compute profile mismatch.** The recipe needs 8 × RTX-4090 for 500 epochs
   (*paper §4.2*); the repo has one RX 6600 XT (`detector/README.md:25`).
   Even with a release, *training* TempFit here is out of budget.
3. **Input alphabet mismatch.** TempFit consumes vector primitives
   (*paper §3.2*); Matchline's ingest path renders sheets to raster
   (`ingest/pdf_ingest.py:536-548`) and the local FloorPlanCAD export carries
   no primitives (`detector/convert_floorplancad.py:4-6`). A vector front-end
   (pdfplumber-style primitive extraction) exists as a prototype only
   (`scripts/r1_vector_spike.py`, `docs/r1_vector_spike.md:28-32`).

Note the *positive* side of the constraint: TempFit, once released, is
self-hosted open weights — it satisfies "no third-party API for lab-sensitive
data" **better** than the API candidates the roadmap already priced out
(Kamai, from the Contractor tier at $800/mo; Kreo Auto Measure REST API —
`ROADMAP.md:326-336`). On this axis TempFit is the *preferred* kind of
candidate even though it is not runnable yet.

### 5.2 Integration cost

- **Backend swap is a config change in principle.** The R2
  `DetectionProvider` interface exists (`detection_provider.py:606-613`), and
  `ROADMAP.md:311-315` states the intent: "swapping backends must be a config
  change, not a rewrite". A `tempfit` provider would slot in beside
  `yolo_sahi` (`detection_provider.py:155-186`).
- **But it is not only a config change.** `DetectionProvider.detect` must
  return `datasets_adapter.Detection` rows — label, score, pixel bbox, source —
  and the existing adapter reads the YOLO JSON schema
  (`detection_provider.py:152-153`, `datasets_adapter.detections_from_yolo_json`).
  TempFit emits a **panoptic primitive mask, a template identity and a pose**,
  so a new adapter (mask → axis-aligned bbox, template id → class label, pose
  → provenance) has to be written and tested.
- **Plus the front-end and the library.** Vector primitive extraction, plus a
  template-authoring tool for the canonical library (the paper builds it by
  clustering 103,959 annotated instances, *paper §4.1* — a one-off pipeline
  Matchline would have to reimplement).
- **Realistic estimate**: this is a research project, not a provider swap.
  Compare the repo's own experience: the vector-native track (R1) is still a
  spike with the sheet-routing blocker open (`docs/r1_vector_spike.md:33-38`).

### 5.3 Cost of *not* acting

Low. The production path stays raster YOLO+SAHI, which is measured at
mAP50 0.968 on CubiCasa5K (`detector/results/ab_2026-09-28/analysis.md:28`),
with a zero run-to-run noise floor
(`detector/results/noise_2026-10-02/analysis.md:13`) and an eval harness that
can score any new backend (`detector/eval_zero_shot.py`) on data Matchline
already holds locally.

---

## 6. Plausible hybrid division of labour

The paper itself contains the shape of the workable hybrid: its
detect-then-fit baseline (**DEIM+ICP** — a detector proposes boxes, then
templates are retrieved and aligned by ICP inside each box) reaches PQ 74.4 on
FloorPlanCAD and 56.6 on IndustryCAD, i.e. it is a large part of the way to
TempFit's 80.1 / 67.0 while reusing an off-the-shelf detector
(*paper §4.2, Baselines*; §4.3, §4.4). That is precisely the architecture
Matchline can afford, because Matchline already owns the detector half.

| Job | Tool | Why |
|---|---|---|
| Door/window **boxes** for takeoff | **YOLO + SAHI** (unchanged) | measured, deterministic, CPU-friendly; the paper's advantage is on failure modes YOLO does not have (§4.2) |
| Door **subtype** and geometry-quality filter | **Template fit inside the YOLO box** (NCC/ICP over a canonical template bank) | gives a per-variant label the 2-class taxonomy cannot express (§3.3) and rejects boxes whose shape matches no template; implementable with the repo's existing NCC machinery (`hvac_trace.py`) |
| **One-example** classes (a firm's own glyph, HVAC terminal) | **Legend-cut templates** (`hvac_legend.py:207-233`), the path already shipped | TempFit's "one example" case is already covered without a model |
| Classes with many variants, occlusion, and a trainable corpus | **Learned template retrieval (TempFit-style)** — *only if and when code lands* | the paper's actual contribution; needs a vector front-end and an instance-annotated corpus (§5) |
| Walls / background regions | **Vector wall parser** (`plan_walls.py`) | TempFit's background branch duplicates a capability Matchline already has from vectors, and the paper's background gain (PQ_bg 65.4) is in a metric Matchline does not use |

The hybrid's ordering constraint: the **fit stage must not be allowed to
create detections**. A template fit inside a proposed box can refine, relabel
or reject a box, but must not add one — otherwise the pipeline loses the
"unmatched predictions are reported" honesty of
`detector/eval_zero_shot.py:6-7` and the low-confidence-review discipline
the repo enforces everywhere else.

---

## 7. Verdict

**Adopt partially — and the "partially" is the *idea*, not the model.**

- **Reject** replacing the YOLO detector with TempFit, now and for the
  foreseeable future: there is no code, no weights and no data to evaluate
  (*project page: "Coming soon"*); the paper contains no YOLO comparison
  (§4.1); the metrics do not convert (§4.3); the input alphabet and the
  compute budget do not match (§5); and the paper's headline gains are
  against failure modes a box detector does not have (§4.2).
- **Reject** the issue's premise that TempFit "could improve accuracy …
  for rare symbol variants with few training examples": the model still needs
  a fully instance-annotated training corpus, and the library itself was
  mined from 103,959 annotated instances in the paper's own setting
  (*paper §4.1*). What the premise *does* support is the much narrower claim
  that **a canonical-template library plus a pose fit is the right mechanism
  for one-example classes** — which Matchline already ships for HVAC.
- **Adopt (partially)** the template-fitting paradigm as an **optional,
  in-house, CPU-cheap second stage** behind the existing R2 provider
  interface, and keep the paper as the design reference for extending the
  legend-template route.

### First concrete step (one, cheap, local, no new data)

**Build a scale/rotation-parameterised template-fitting baseline for the
existing 2-class taxonomy and score it against the archived YOLO predictions
with the repo's own harness.**

1. Cut canonical door and window templates from the **already-local**
   FloorPlanCAD eval PNGs using the label files the converter already writes
   (`detector/convert_floorplancad.py:52-66`); keep the template bank small
   (one per variant) and record it as a repo artefact, with its licence class
   (FloorPlanCAD = noncommercial, `assets/license_ledger.json:54-59`).
2. Match with the repo's own NCC machinery plus a small scale/turn bank (the
   `TEMPLATE_SCALES` / `TIGHT_TURNS` pattern, `hvac_trace.py:105`, `:118`),
   optionally refined by ICP inside each candidate box — the paper's own
   detect-then-fit baseline (`paper §4.2`).
3. Score it with `detector/eval_zero_shot.py` (greedy IoU-0.5 P/R,
   `detector/eval_zero_shot.py:26-73`) **against the same images the YOLO
   model is scored on**, so the comparison is apples-to-apples on data
   Matchline already holds.
4. **Decision rule, fixed before the run**: adopt the fit stage for a class
   if it does not reduce P/R against the YOLO baseline on the clean set **and**
   gains on the scale/rotated subset (the failure mode
   `docs/r1_vector_spike.md:44-46` documents). Because the eval path is
   deterministic, the run-to-run floor is 0.00000
   (`detector/results/noise_2026-10-02/analysis.md:13`) — any difference
   beyond that floor is signal.

Cost: hours of CPU, no GPU, no new licences beyond the ledgered
FloorPlanCAD, no new annotation. It tests the one TempFit claim that
transfers to Matchline (pose-parameterised template fitting beats fixed-scale
box detection on scale/rotation variation) **without** needing TempFit.

**Secondary, zero-cost step**: keep this document and re-run the evaluation
when the project page flips "Coming soon" to a release. At that point the
three questions to answer are (i) does it ship an inference script and
weights, (ii) can its input be produced from a PDF (vector primitives), and
(iii) does it report a box-level or mask-level metric that maps onto
`detector/ap50.py`.

---

## 8. Claim ledger

| # | Claim | Source | Status |
|---|---|---|---|
| 1 | TempFit is an autoregressive transformer that retrieves a canonical template and predicts translation/rotation/scale per instance | paper §3.2 | verified from the paper text |
| 2 | TempFit's input is vector line primitives, encoded by a shared PointTransformerV3 backbone | paper §3.2 | verified from the paper text |
| 3 | FloorPlanCAD library = 462 prototypes clustered from 103,959 annotated training instances | paper §4.1 | verified from the paper text |
| 4 | Training = 500 epochs, 8 × RTX-4090, batch 4 × grad-accum 4, 6 decoder layers, d=768, K=128 bins | paper §4.2 | verified from the paper text |
| 5 | FloorPlanCAD PQ (Hungarian) 80.1 vs SymPointV2 77.0 vs DEIM+ICP 74.4; IndustryCAD 67.0 vs 56.6; SESYD zero-shot 24.67 vs 16.23 | paper Tables 1, 2, 6 | verified from the paper text |
| 6 | 108.3 M parameters, 279.0 ms inference | paper Table 1 | verified from the paper text; **the latency hardware and the scene unit are not stated in the text I could read** — treat the number as paper-relative, not as a Matchline-comparable latency |
| 7 | No YOLO / box-regression baseline appears in the paper | paper §4.2 | verified from the paper text |
| 8 | Failure modes: Chamfer ≳ 0.07 to nearest template → IoU drop; 80-instance block → IoU 0.28 → < 0.05; token-by-token generation limits speed on large drawings | paper, Limitations | verified from the paper text |
| 9 | Code and data are not released | <https://tempfit-project.github.io/> (read 2026-10-23) | verified (project page); **not** independently confirmed against any package index |
| 10 | Matchline's YOLO detector reaches mAP50 0.96805 ± 0.00303 on CubiCasa5K (3 seeds × 50 epochs) | `detector/results/ab_2026-09-28/analysis.md:28` | verified in-repo |
| 11 | Matchline's training run-to-run noise floor is 0.00000 (byte-identical results) | `detector/results/noise_2026-10-02/analysis.md:13`, `:18` | verified in-repo |
| 12 | Matchline's raster door sweep is scale-sensitive (61 vs 24 candidates at two paper scales) | `docs/r1_vector_spike.md:44-46` | verified in-repo (counts on unlabelled scratch sheets — counts, not scores) |
| 13 | Matchline already ships a one-example template route (legend cut → NCC at 0.80) | `hvac_legend.py:207-233`, `hvac_trace.py:537` | verified in-repo |
| 14 | Matchline's local FloorPlanCAD export has image + bbox/category/mask only, test split only | `detector/convert_floorplancad.py:4-9` | verified in-repo |
| 15 | FloorPlanCAD is CC BY-NC 4.0 → weights stay evaluation-only | `assets/license_ledger.json:54-59` | verified in-repo |
| 16 | Matchline's sheets are 4800×7200+ px; the raster path handles density by tiling | `detector/sahi_infer.py:4`, `:20-38` | verified in-repo |
| 17 | "TempFit could improve accuracy for rare variants with few training examples" | issue #1256 | **contradicted in part** — the model still needs an instance-annotated training corpus (claim 3) |
| 18 | Any measured TempFit-vs-YOLO accuracy number | — | **unverifiable** — not runnable here, and no such comparison exists in the paper |
| 19 | Whether TempFit would beat YOLO on Matchline's door/window classes | — | **unverifiable today**; this is what the first concrete step (§7) measures, with an in-house baseline rather than TempFit itself |

---

## 9. Limitations of this evaluation

- **No TempFit number in this document was produced here.** Every TempFit
  figure is the paper's own; the evaluation is a *reading* of the paper
  against Matchline's code, plus a cost and constraint analysis.
- **The comparison is architectural, not numerical.** PQ over primitive masks
  and box mAP50 are different metrics; §4.3 explains why no conversion is
  available. A reader wanting a head-to-head must run the first concrete
  step in §7 and then, if TempFit is released, re-run it against the same
  harness.
- **The paper's latency figure is not comparable to the repo's** (claim 6);
  the repo's own sheet-level number is "≈30 s CPU for a 7201×4801 AEC sheet"
  (`detector/README.md:106`), which is a whole-sheet figure including
  tiling, so the two cannot be divided into a speed ratio.
- **The scale/rotation argument in §3.1 is a hypothesis** built on the
  repo's measured raster scale-sensitivity, not on a TempFit measurement.
- **This evaluation says nothing about the background branch** of TempFit
  beyond noting that Matchline takes walls from vectors.

---

## 10. Reproduction (grep evidence)

Every Matchline citation above is reproducible with these commands from the
repository root:

```bash
# current detector approach: taxonomy, training defaults, tiling, merge, metrics
grep -n "CLASS_IDS = \|door\": 0\|window\": 1" detector/classes.py          # 13-16
grep -n "imgsz\|epochs\|model" detector/train.py | head -3                 # 20-22
grep -n "def tile_boxes\|def nms\|def infer_sheet\|tile=1024\|Sheets are" detector/sahi_infer.py   # 4, 20, 41, 93-94
grep -n "def average_precision\|def mean_average_precision" detector/ap50.py # 20, 58
grep -n "def iou\|greedy\|best_iou >= args.iou" detector/eval_zero_shot.py    # 26, 61-73
grep -n "mAP50 | 0.96805\|mAP50-95 | 0.87951\|Precision | 0.93356\|Recall | 0.92748" \
    detector/results/ab_2026-09-28/analysis.md                              # 28-31
grep -n "run-to-run at fixed seed\|byte-identical" detector/results/noise_2026-10-02/analysis.md  # 13, 18
grep -n "5,308 images\|~30s CPU\|RX 6600 XT" detector/README.md              # 25, 103, 106, 108

# backend swap point and the already-shipped template path
grep -n "PROVIDERS: Dict\|class YoloSahiProvider\|class DoorSwingProvider\|class VectorGlazingProvider" \
    detection_provider.py                                                    # 155, 606-613
grep -n "def legend_templates" hvac_legend.py                                # 207
grep -n "LEGEND_NCC_ACCEPT = \|TEMPLATE_SCALES = \|TIGHT_TURNS = " hvac_trace.py  # 105, 118, 537

# vector-native deferral and the raster scale-sensitivity finding
grep -n "VecFormer as the leading\|DetectionProvider interface\|0.929" ROADMAP.md            # 280, 290, 306, 315
grep -n "raster door sweep is scale-sensitive\|VecFormer stays deferred" docs/r1_vector_spike.md  # 44, 49

# data and licence facts
grep -n "The parquet has columns\|split only" detector/convert_floorplancad.py                 # 4, 8
grep -n "floorplancad" -A4 assets/license_ledger.json                                          # 54-59
```

TempFit's side of the evidence:

```bash
curl -sSL https://arxiv.org/abs/2610.17776          # abstract + v1 date (13 Oct 2026)
curl -sSL https://arxiv.org/html/2610.17776v1       # full text (sections cited above)
curl -sSL https://tempfit-project.github.io/         # project page: Code/Data "Coming soon"
```
