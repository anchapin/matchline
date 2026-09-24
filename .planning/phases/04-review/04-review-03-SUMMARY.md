# 04-review-03-SUMMARY — Classifier training pipeline

**Executed:** 2026-09-22
**Plan:** `04-review-03-PLAN.md`
**Status:** ✅ Complete

## What was built

### `review_classifier/corpus/`
- `README.md` — corpus format documentation
- `route_to_review.jsonl` — 30 seed synthetic examples
- `schedule_match.jsonl` — 30 seed synthetic examples
- `extraction_type.jsonl` — 30 seed synthetic examples
- `collect.py` — `load_corpus()`, `save_example()`, `export_from_model()`

### `review_classifier/train.py`
- `train_task()` — trains a per-task `TypedDecider` on synthetic + corpus
- `main()` — CLI: `--task` (or `all`), `--synthetic-n`, `--out-dir`, `--seed`
- Per-task model files: `trained_model_{task}.pkl`
- Reports 5-fold CV accuracy + corpus-only accuracy (RVIEW-04 metric)
- RVIEW-04 acceptance: corpus-only accuracy ≥0.70

### Per-task models trained

| Task | Combined n | CV Accuracy | Corpus Accuracy | Target |
|------|-----------|-------------|----------------|--------|
| route_to_review | 330 | 0.989 ± 0.009 | 1.000 | ≥0.70 ✅ |
| schedule_match | 330 | 0.962 ± 0.013 | 0.933 | ≥0.70 ✅ |
| extraction_type | 330 | 1.000 ± 0.000 | 0.867 | ≥0.70 ✅ |

## Acceptance criteria verified

| Criterion | Status |
|---|---|
| Corpus directory with seed examples exists | ✅ |
| `load_corpus()` / `save_example()` work | ✅ |
| `export_from_model()` exports confirmed/rejected items | ✅ |
| `python -m review_classifier.train --task all` saves per-task models | ✅ |
| Saved models load and `decide()` works | ✅ |
| RVIEW-04 corpus accuracy ≥0.70 on seed corpus | ✅ |
| `run_review.py` shows classifier suggestions inline | ✅ |
| No lint errors | ✅ |
