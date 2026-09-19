# Review-Queue Decision Classifier — Prototype Report

Weekend prototype of a Jev-style typed decision layer for Matchline's
extraction review queue (`building_model.py::flag_for_review`). Fully local
(scikit-learn, CPU), no external API, no data egress.

## What was built

`review_classifier/` — a small package with:

- **`data.py`** — synthetic labeled dataset generator (fixed seeds, reproducible).
  Three decision tasks modeled on realistic Matchline review items:
  1. `route_to_review` (yes/no): given detector confidence, OCR edit distance,
     schedule-match score, candidate count and takeoff area delta, should this
     extraction go to human review? Labels from a latent quality rule + noise,
     with a deliberate mix of easy cases and ambiguous boundary cases.
  2. `schedule_match` (yes/no): does this schedule row match this door/window
     tag? Features: tag string similarity, dimension agreement, category
     agreement, size deviation.
  3. `extraction_type` (4-way choice): door | window | room_label | fixture,
     from a textual description with class-specific vocabulary plus distractor
     phrases (25% of examples carry a misleading phrase from another class).
- **`features.py`** — TF-IDF (1–2 grams) over the text rendering +
  standardized numeric features, stacked into one sparse matrix.
- **`model.py`** — `TypedDecider`: logistic regression wrapped in
  `CalibratedClassifierCV` (sigmoid/Platt scaling), exposing Jev-style
  primitives: `noul(example) -> P(yes)`, `choice(example, options) ->
  {option: prob}`, and `decide(example)` returning the argmax label plus
  confidence, so callers can threshold and escalate to a human.
- **`evaluate.py`** — accuracy/F1, expected calibration error (ECE, 10 bins),
  and reliability diagrams.
- **`run_demo.py`** — `python3 -m review_classifier.run_demo`; trains one
  decider per task (80/20 stratified split), prints a results table, writes
  `reliability.png` and `metrics.json`.

## Results (4500 synthetic examples, 1200 train / 300 test per task)

| task | accuracy | F1 | ECE uncalibrated | ECE calibrated |
|---|---|---|---|---|
| route_to_review | 0.9933 | 0.9923 | 0.0094 | 0.0201 |
| schedule_match | 0.9467 | 0.9635 | 0.0137 | 0.0291 |
| extraction_type | 1.0000 | 1.0000 | 0.0587 | 0.0438 |

Reliability diagrams: `review_classifier/reliability.png`.

**Reading the calibration column honestly:** on the two binary tasks the raw
logistic regression was already nearly perfectly calibrated (ECE ~0.01), and
Platt rescaling on CV folds made it *slightly worse* (+0.01–0.015). On the
4-way task, where the raw model was overconfident (ECE 0.059), calibration
helped (ECE 0.044). The takeaway for production: calibration is a safety net,
not a guaranteed win — always measure ECE on your own data rather than
assuming the wrapper helps. That is exactly the discipline the real Jev
"calibrated decisions" claim still owes the public.

## How to run it

```bash
cd ~/workspace/jesse-proto          # branch: review-queue-classifier-proto
python3 -m review_classifier.run_demo --n-per-task 1500 --seed 20260919
pytest tests/test_review_classifier.py -q
```

Dependencies: scikit-learn, numpy, scipy, matplotlib (all present in the
system python3; nothing was installed).

## Limitations (explicit)

1. **Synthetic data only.** The label rules are invented; real extraction noise
   (OCR confusions, detector failure modes, messy schedules) will differ.
   Real calibration **must be re-measured on Monday's real drawings** before
   any threshold is trusted.
2. **Pipeline prototype, not a production model.** No integration with
   `building_model.py`'s review queue yet — the typed API (`noul`/`choice`/
   `decide`) is shaped for that wiring, but the wiring is future work.
3. **Text/numeric features only.** Like Jev, this layer never sees pixels; it
   decides on extracted observations, never on drawings directly.
4. Calibration was evaluated in-distribution; distribution shift (new drawing
   styles, new symbol sets) will degrade both accuracy and calibration.
