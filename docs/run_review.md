# `matchline review` — Review Queue Triage

Interactive review queue tool for confirming or rejecting extracted BEM facts that
need human verification.

```bash
matchline review model.json
matchline review model.json --show-all
matchline review model.json --confirm RVW-001
matchline review model.json --reject RVW-002 --enable-auto-triage
```

## Flags

`model`
: Path to a `BuildingModel` JSON file.

`--show-all`
: Also show confirmed and rejected items (default: shows open items only).

`--confirm ID`
: Confirm a review item. Marks it confirmed, then re-runs validation.

`--reject ID`
: Reject a review item. Marks it rejected, then re-runs validation.

`--enable-auto-triage`
: Enable the auto-triage classifier when loading the model.
  The `ENABLE_AUTO_TRIAGE=1` environment variable is also respected.

## Auto-Triage

When auto-triage is enabled (`--enable-auto-triage` or `ENABLE_AUTO_TRIAGE=1`),
each `ReviewItem` added to the review queue is scored by the `review_classifier`
triage models, populating:

- `needs_human` — Probability that the item needs human review (P-needs-human, 0–1)
- `urgency` — Urgency level (0–3)

This helps prioritize which review items to address first during interactive review.

## Environment Variables

`ENABLE_AUTO_TRIAGE=1`
: Enables the auto-triage classifier globally (in `matchline review` and in
  `matchline run` Stage 4b). When set, every `ReviewItem` added to the
  review queue is scored by the `review_classifier` triage models.

See [`building_model.md`](building_model.md) for the `ReviewItem` data model.
