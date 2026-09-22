# Phase 4: Review Queue UX

**Goal**: Human review of low-confidence links is surfaced, classified, and actionable — not buried in a JSON list.

**Depends on**: Phase 1 (orchestration)

**Requirements**: RVIEW-01, RVIEW-02, RVIEW-03, RVIEW-04

**Success Criteria**:
1. `matchline review --model model.json` prints each `ReviewItem` with its kind, description, confidence, and (if available) classifier suggestion
2. `matchline review --model model.json --confirm <id>` marks the item as confirmed and re-runs `validate.py`; the confirmed item no longer appears in the queue
3. `matchline review --model model.json --reject <id>` marks the item as rejected and re-runs `validate.py`
4. On a model with 20+ review items, the classifier suggestion matches the human decision in ≥70% of cases (measured over a curated set of real-drawing review items)

**Plans**: 3 plans in 2 waves

**Plan list:**
- [ ] 04-review-01-PLAN.md — Review CLI + list view with classifier inline (RVIEW-01, RVIEW-03)
- [ ] 04-review-02-PLAN.md — Confirm/reject mutations + revalidation (RVIEW-02)
- [ ] 04-review-03-PLAN.md — Classifier training data pipeline for real-drawing examples (RVIEW-04)

---

## Key Context

### What's already built
- `BuildingModel.review_queue: List[ReviewItem]` — already populated by pipeline stages via `model.flag_for_review()`
- `ReviewItem(id, kind, description, confidence, provenance, status)` — status is "open" | "confirmed" | "rejected"
- `validate._check_review_queue_sound()` — validates review queue items are well-formed
- `review_classifier/` module — `TypedDecider` with `noul`/`choice`/`decide` API, trained on synthetic seed data

### What's missing
1. `run_review.py` — review queue CLI tool with list/confirm/reject subcommands
2. Classifier integration — `TypedDecider` is not invoked when listing review items
3. Review item corpus — no curated real-drawing examples for classifier training (RVIEW-04)

### RVIEW-03 scope
The `TypedDecider` from `review_classifier/` is prototype-grade (synthetic data). RVIEW-03 means the classifier is INVOKED inline — not that it is perfect. Classifier predictions are shown as hints, not replacements for human judgment. RVIEW-04 improves the classifier with real data.

### Classifier tasks available
- `route_to_review`: Should this item go to review? (binary)
- `schedule_match`: Does this schedule row match the detected tag? (binary)
- `extraction_type`: Classify as door/window/room_label/fixture (4-way)

The classifier needs to be trained on a per-task basis. The review queue shows items of different `kind`; each kind may map to a different classifier task.
