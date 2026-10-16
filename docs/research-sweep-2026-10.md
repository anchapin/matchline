# Research sweep, 2026-10

Periodic arXiv sweep requested by [#1094](https://github.com/anchapin/matchline/issues/1094).
It builds on [docs/ecosystem_audit.md](ecosystem_audit.md) (which covered open-source
*repos* — SymPoint, BreakingCAD, P&ID symbol detection and their licenses) and
[docs/detector-robustness.md](detector-robustness.md) (whose scan-noise methodology
follows arXiv:2609.24565). This sweep covers papers only, mapped to the pipeline
slices the issue named. Nothing here was implemented; each entry says what to try.

Effort scale: **S** under a day, **M** 1-3 days, **L** a week or more.

## Floor-plan parsing and detection (walls, doors, windows, symbols)

**Enhancing Floor Plan Recognition: A Hybrid Mix-Transformer and U-Net Approach for
Precise Wall Segmentation** — Parashchuk et al., arXiv:2512.02413 (Dec 2025, v3 Apr 2026).
Relevance: thin-structure (double-line wall) segmentation with geometric fidelity,
exactly our wall-detector failure mode on scanned sheets.
What to try: compare its Mix-Transformer encoder against our current backbone on the
detector benchmark before considering any swap; its loss weighting for thin structures
is portable regardless. Effort: **M** (evaluation-only first pass).

**FloorplanVLM: A Vision-Language Model for Floorplan Vectorization** — arXiv:2602.06507 (2026).
Relevance: casts floorplan vectorization as sequence generation with reinforcement from
geometric-fidelity rewards, emitting a parametric vector graph (JSON) instead of a raster
mask; handles non-Manhattan geometry (slanted walls, curves) while keeping topology.
What to try: not a drop-in (it is a full VLM), but its reward design — connectivity and
geometric fidelity as the score — is a template for scoring our vectorization stage, and
its JSON-graph output shape matches our intermediate representation closely enough to
borrow schema ideas. Effort: **M** for the scoring scheme, **L** to adopt any model side.

**FGSSNet: Feature-Guided Semantic Segmentation of Real World Floorplans** — arXiv:2507.10343 (Jul 2025).
Relevance: a wall segmentation net with an auxiliary head that *classifies wall width*
from wall crops, feeding the width back as features. Our detector currently has no
notion of wall type/width, and width is the first input to construction-assembly
inference (#747) and the ASHRAE 90.1-2019 Table 5.5 fallbacks.
What to try: add a width/type head on our existing wall crops; even a coarse
(stud/partition vs structural) classification unblocks assembly inference. Effort: **M**.

**ArchCAD-400k: A Large-Scale CAD drawings Dataset and New Baseline for Panoptic Symbol
Spotting** — arXiv:2503.22346 (2025).
Relevance: 400k annotated CAD drawings for panoptic symbol spotting; the licensing-safest
path to more symbol-pretraining data than our synthetic sets (#712).
What to try: check the data license (CAD entities are vector primitives — a vector-domain
pretraining corpus, unlike our raster pipeline), then pretrain the symbol detector on it
and fine-tune on our synthetic pipeline. Effort: **L** (data licensing check first).

## PDF schedule parsing and HVAC symbol classification

**A Comparative Study of PDF Parsing Tools Across Diverse Document Categories** — arXiv:2410.09871 (v2 Apr 2025).
Relevance: 10 parsers (PyMuPDF, pdfplumber, camelot, Nougat, Table Transformer, ...)
benchmarked on tables/text/titles across categories with the same ground-truth method
we would need for mechanical schedules (#746).
What to try: replicate its harness on our schedule corpus (it is small: 10 tools, one
metric script) before touching schedule parser code; if the ranking holds for
equipment tables, it settles the "which parser" debate cheaply. Effort: **S**.

**Benchmarking Table Extraction from Heterogeneous Scientific PDF Documents** —
arXiv:2511.16134 (Nov 2025). Relevance: heterogeneous-table benchmark with matched
ground truth; mechanical schedules are precisely heterogeneous tables (merged headers,
multi-line cells, per-page repeats).
What to try: borrow its matching pipeline (aligning parser output to ground truth
tables) for our schedule accuracy metric, which currently has no published analogue. Effort: **M**.

**Benchmarking PDF Parsers on Table Extraction with LLM-based Semantic Evaluation** —
arXiv:2603.18652 (Mar 2026). Relevance: scores parser output with an LLM judge on
semantic equivalence rather than exact cell match — the right metric for schedules
where column headers vary by discipline ("GPM" vs "FLOW").
What to try: prototype the LLM-judge metric on our existing schedule fixtures as a
secondary score next to exact match. Effort: **S**.

**Fine-Tuning Vision-Language Model for Automated Engineering Drawing Information
Extraction** — Khan et al., arXiv:2411.03707 (Nov 2024).
Relevance: fine-tunes Florence-2 (a small open VLM) on only 400 expert-annotated
drawings for GD&T extraction — evidence that small-scale, domain-annotated fine-tunes
beat zero-shot VLMs on drawing sub-regions, which is our review-classifier scenario
(#1005) at low annotation cost.
What to try: a Florence-2-sized crop classifier for HVAC symbols (#744) trained on our
review-queue corrections (feedback.jsonl is exactly that annotation stream). Effort: **M**.

## ASHRAE 90.1 / Appendix G automation and construction-assembly inference

**Advancing Building Energy Modeling with Large Language Models: Exploration and Case
Studies** — arXiv:2402.09579 (Energy and Buildings, Feb 2024).
Relevance: LLMs writing OpenStudio Measures and diagnosing EnergyPlus errors — the
"automate the automation" layer above our BEM export (bem_export.md).
What to try: measure-writing is not our lane, but its error-diagnosis prompt patterns
(port failure taxonomies) are reusable for our validation suite output. Effort: **S**.

**Automatic building energy model development and debugging using large language models
agentic workflow** — Energy and Buildings 2024, doi:10.1016/j.enbuild.2024.115116
(OSTI 2480816; NREL iUnit case study). Relevance: a four-agent pipeline
(describe → IDF object extraction → generator suite → debugging agent) that turns a
building description into an error-free EnergyPlus model.
What to try: its debugging-agent pattern (run, parse eplusout.err, patch, re-run) maps
directly onto our validate/ package's invariant checks — a candidate CLI subcommand.
Effort: **M**.

**Tool-augmented vs. autonomous LLM agents for building energy model modifications:
A comparative study using NECB compliance models** — Building Simulation / ScienceDirect,
2026 (S2666546826002223). Relevance: directly compares tool-augmented agents (LLM
calling high-level HVAC-domain functions over IDF) against free-form agents, with
retrieval-augmented access to the *standard text* (NECB) — the same shape as our
Appendix G Table G3.4 rules.
What to try: their conclusion direction (tool-augmented wins on determinism) supports
our stated preference for rule tables over model-generated values; the paper's
standard-retrieval pattern is what a future `matchline compliance` agent would copy.
Effort: **M**.

**BEMEval-Doc2Schema: Benchmarking Large Language Models for Structured Data Extraction
in Building Energy Modeling** — Jia, Fu, Zhang, arXiv:2602.16926 (Feb 2026).
Relevance: the closest published analogue to our pipeline's core bet: extracting
structured data (HPXML-style schema) from building documentation with LLMs, with a
dedicated metric (Key-Value Overlap Rate) and zero-/few-shot comparisons across models.
What to try: adopt KVOR-style scoring for our schedule-extraction benchmark rows
(#748), and use their residential test cases as a foreign-data sanity set alongside our
synthetic fixtures. Effort: **S**.

## Geometry-aware ML, drawing understanding with LLMs, active learning from review

**Do We Still Need Humans in the Loop? Comparing Human and LLM Annotation in Active
Learning** — Hakimi et al., arXiv:2604.13899 (2026).
Relevance: LLM-in-the-loop active learning vs pure human; the practical finding is
that LLM pre-labels plus targeted human review beat either alone — exactly the shape
of our review queue (#997): the model's uncertain calls go to a person, and an LLM
could triage that queue first.
What to try: score review-queue items by model-vs-LLM agreement, route only the
disagreements to humans; measures queue size reduction on our existing feedback log.
Effort: **M**.

**Revisiting Active Learning under (Human) Label Variation** — arXiv:2507.02593 (Jul 2025).
Relevance: our review-queue labels are single-rater and contradictory corrections
happen; this gives a framework (label as distribution, annotator choice, instance
selection) for treating disagreement as signal rather than noise.
What to try: record reviewer identity in feedback.jsonl events (schema change only) so
the classifier can learn per-rater reliability later. Effort: **S**.

**Efficient Human-in-the-Loop Active Learning: A Novel Framework for Data Labeling in
AI Systems** — arXiv:2501.00277 (2025). Relevance: comparison-based querying ("is A
more like B or C?") reducing annotation cost versus multi-class labeling.
What to try: probably not for us — our review UI shows one item with context, and
reworking the UI for pairwise queries is UI work for a queue we want to shrink.
Recorded as considered-and-declined.

## Benchmark design for extraction tasks

**KIEval: Evaluation Metric for Document Key Information Extraction** — Khang et al.,
arXiv:2503.05488 (Mar 2025).
Relevance: scores *grouped* structure (a row belongs to its equipment) alongside flat
entity F1; our benchmark harness (#748) currently scores flat fields, which flatters
row-shuffling errors in schedules.
What to try: add a grouping-aware score to the harness alongside the existing field
metrics. Effort: **S**.

**ExtractBench: A Benchmark and Evaluation Methodology for Complex Structured
Extraction** — arXiv:2602.12247 (2026). Relevance: schema-driven evaluation with
field-type-sensitive matching and an LLM judge at a fixed pass threshold; the
annotation-effort accounting (8 hours per complex document) is a useful reality
check for our fixture budget.
What to try: its field-type matcher (exact IDs vs fuzzy text) for our schedule fields.
Effort: **S**.

**From Pixels to Pairs: A Comprehensive Benchmark of LLM-Driven Key-Value Extraction in
Noisy Document Settings** — arXiv:2609.17538 (2026).
Relevance: evaluates extraction under gold-text vs OCR-derived text with three
complementary metrics (Key Recall, Exact Match, Value F1); our detector-robustness
suite does the image-side equivalent (scan noise), but our schedule evaluation has no
noise-conditioned protocol.
What to try: run the schedule benchmark under degraded-PDF conditions mirroring
degrade.py, scored with KR/EM/VF1. Effort: **S**.

**UniKIE-Bench: Benchmarking Large Multimodal Models for Key Information Extraction in
Visual Documents** — arXiv:2602.07038 (Feb 2026). Relevance: end-to-end (image-in,
structured-out) KIE evaluation of LMMs; useful as a yardstick for how far behind an
end-to-end VLM approach is from our modular pipeline, not as a method to adopt.
Recorded as context.

## Top 3 actionable ideas (triaged into follow-up issues)

1. **Grouping-aware score in the benchmark harness** (#748 continuation, from KIEval):
   flat field F1 hides row-shuffling errors in mechanical schedules; a grouped metric
   matches a whole equipment row before crediting its fields. Effort **S-M**.
2. **Wall width/type auxiliary head** (from FGSSNet): predict coarse wall class per
   detected wall segment; feeds construction-assembly inference (#747) and the Table
   5.5 unlabeled-parameter fallbacks with evidence from the drawing itself. Effort **M**.
3. **Noise-conditioned schedule benchmark** (from 2609.17538 + 2603.18652): run the
   schedule extraction benchmark under degraded-PDF conditions with the
   Key-Recall / Exact-Match / Value-F1 protocol and an optional LLM-judge secondary
   score. Effort **S**.

Declined after reading: FloorplanVLM as a model replacement (L, and our vector stage is
deterministic by design); pairwise active-learning UI (2501.00277); UniKIE-Bench as a
method (context only); SymPoint-class panoptic spotting on vectors (already covered and
license-blocked in ecosystem_audit.md).
