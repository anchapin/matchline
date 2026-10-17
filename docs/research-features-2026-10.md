# Training/research features: ranked shortlist, 2026-10

Deliverable for [#1095](https://github.com/anchapin/matchline/issues/1095): the five
candidate angles ranked with rationale and cost, and the first experiment specced.
Ranked from the repo's actual state as of 2026-10-16, not from the candidate list's
order. Effort: **S** under a day, **M** 1-3 days, **L** a week or more.

## State of the data that decides the ranking

- The review feedback stream is effectively empty: the repo-root feedback log held no
  events (it was runtime noise riding feature commits, untracked in #1110), and the
  only real feedback file found holds 4 `failed_parse` events — no confirm/reject
  decisions have ever been recorded. The review classifier (review_classifier/) is
  trained on synthetic labels only, by design (REPORT.md).
- Every review item already carries `confidence` and `provenance`
  (review_report.py: Provenance with sheet/revision; sort order is open, urgency,
  confidence), and #1090 gave the viewer a provenance-method filter.
- detector/README.md records that seed-to-seed and run-to-run noise have **not been
  separated**, with a 3-repeat × fixed-epoch design noted as the fix (#516 lineage).
- The #748 benchmark results still wait on licensed real-building data; the synthetic
  fixture set is the only validated eval substrate today.

## The ranking

1. **Noise-floor discipline (#516 lineage) — do first.** Every other angle produces a
   delta somebody will want to judge, and right now no delta is judgeable: a 0.16
   mAP50 gap was nearly underpowered into an A/B conclusion once already. The fix is
   compute, not code: repeat the current best config at fixed epochs across seeds and
   record the spread. Cost: **S** of wall-clock (3× a single eval run), zero new code
   beyond a results row in the benchmark doc.
2. **Provenance-conditioned confidence calibration (#1090 follow-through) — do
   second.** The inputs already exist on every item; nothing needs a new capture
   pipeline. Per-method confidence histograms from the demo corpus immediately make
   triage cheaper (calibrate the sort order the viewer uses), and they are the
   baseline any later calibration model trains against. Cost: **S-M** (analysis +
   sort tweak; a real isotonic/regression calibration waits for review labels).
3. **Active learning from the review queue (#997/#1005) — highest ceiling, currently
   blocked.** The idea is right (confirm/reject history as weak labels plus a
   prioritized relabel queue), but there is no history: zero recorded decisions. The
   honest first step is instrumentation — make the review page write a
   matchline.feedback/1 confirm/reject event per decision — and the label value only
   accrues as the queue is actually used. Cost: **M** for the write path; payoff
   timeline measured in weeks of use, not in the PR itself.
4. **Synthetic-data expansion for underrepresented space types (#707) — deferred.**
   Expansion without a real-plan eval tunes the detector toward the fixture
   distribution precisely where we cannot see it. Revisit when the licensed
   real-building data lands (#748 dependency). Cost if forced now: **M-L**, plus
   re-baselining everything it touches.
5. **Retrain cadence policy — folded into #1.** A policy for when a retrain ships
   vs waits is premature before the noise floor exists (the 200-epoch line is a
   ceiling Alex set, not a measured answer); after #1 runs, the cadence rule is one
   paragraph in the benchmark doc. Cost: **S**, but only after #1.

## First experiment: measure the detector noise floor

- **Data source:** the existing synthetic benchmark corpus and fixed eval split, no
  new fixtures; the current best detector config, unchanged.
- **Design:** one config, 3 seeds, at the approved 200-epoch line (no config change
  between arms; the only variable is seed). If prior runs already logged seeds, fold
  their eval rows in rather than re-running.
- **Metric:** mAP50 (the metric the existing detector comparisons use). Report
  per-seed values, the mean, and the seed-to-seed spread; if a run-to-run repeat of
  one seed is cheap, add it to separate run noise from seed noise per the
  detector/README.md note.
- **Decision rule going forward:** a future delta counts only if it exceeds 2× the
  measured seed-to-seed SD at the same epoch count; this rule, not a new policy
  document, is angle 5's output.
- **Guardrails:** eval-only — no training-code changes, EXPECTED_TEST_COUNT and the
  wheel untouched; the 200-epoch line stays the cap (any longer run is Alex's call
  per the standing approval); results land in the benchmark doc with the seeds
  recorded so the baseline is reproducible.
- **Expected runtime:** three 200-epoch runs at the cost of the last approved
  retrain; locally scheduled as with prior retrains.
