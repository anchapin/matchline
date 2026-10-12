# 04-review-01-SUMMARY — Review CLI + list view

**Executed:** 2026-09-22
**Plan:** `04-review-01-PLAN.md`
**Status:** ✅ Complete

## What was built

### `run_review.py`
- `format_review_list(model_path, show_all=False, classifier_path=None)` — loads model, lists items
- `_confirm_item()` / `_reject_item()` — mutation helpers
- `_summarize_validation(report)` — prints validation summary
- `main(args)` — full CLI with list, confirm, reject

### `cli.py`
- `cmd_review()` — wires into `run_review.main()`
- `review` subparser — `model`, `--confirm`, `--reject`, `--show-all`

## Acceptance criteria verified

| Criterion | Status |
|---|---|
| `matchline review model.json` lists open items with kind, description, confidence, provenance | ✅ |
| `matchline review model.json --confirm <id>` confirms + re-validates | ✅ |
| `matchline review model.json --reject <id>` rejects + re-validates | ✅ |
| Confirmed/rejected items no longer appear in open list | ✅ |
| No lint errors (ruff) | ✅ |
