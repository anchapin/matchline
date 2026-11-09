# Schedule confidence calibration (#1295)

The model records hand-set confidences for schedule-derived facts: **0.9**
for a plan terminal tag and for a schedule value whose unit names its slot
(`mech_schedule.VALUE_CONFIDENCE["high"]`), **0.8** for a schedule row read
as stated and for family/IP-default values
(`equipment.SCHEDULE_EQUIPMENT_CONFIDENCE`, `VALUE_CONFIDENCE["medium"]`),
and **0.4** for a value nulled by a same-slot conflict
(`VALUE_CONFIDENCE["low"]`). This note validates those numbers against the
#1240 schedule truth set instead of asserting them.

## Method

`scripts/schedule_bench.py` now emits a calibration block in its scoreboard:
every fact the extractor asserts about a schedule row is bucketed by the
confidence the model records for it and the claim kind, and scored against
the frozen truth (`scripts/schedule_score.calibration_table` /
`calibration_by_confidence`). Claims the extractor deliberately did not
assert (a nulled value, an unread unit) are counted as **abstentions** —
neither right nor wrong — and claims the truth states as a range are counted
**unverifiable**. Neither dilutes measured correctness.

## Measured table (bsi-clinic, #1240 truth set)

| confidence | claim kind | claims | correct | measured correctness | abstained | unverifiable |
|---|---|---|---|---|---|---|
| 0.90 | schedule_value_airflow | 27 | 27 | 1.000 | 0 | 0 |
| 0.80 | schedule_row_capacity | 3 | 3 | 1.000 | 264 | 0 |
| 0.80 | schedule_row_service_area | 410 | 410 | 1.000 | 0 | 0 |
| 0.80 | schedule_row_tag | 410 | 410 | 1.000 | 0 | 0 |
| 0.40 | schedule_value_airflow | 0 | 0 | n/a | 4 | 0 |

Not measurable against this truth set, and reported as such by the bench
rather than silently dropped:

- `serving:plan_terminal_tag` (0.90) — the frozen schedule truth carries no
  zone or terminal truth; the claim is read off the plan, not the schedules.
- `serving:schedule_box_link` (0.80) — the truth records no schedule column
  values, so a box-to-unit link cannot be scored against it.

Reproduction note: the clinic workbook is a local dataset (see DATASETS.md);
on a machine without it the bench reports the building as `missing`. The
table above is from a run against a local copy of the workbook, with truth
frozen by `scripts/schedule_truth.py` (openpyxl only, never the extractor).

## Verdict: values kept, with one caveat

- **0.9 (airflow values with explicit units) — justified.** 27/27 measured
  correct. If anything the value is conservative, but 27 claims is too
  small a sample to raise it on.
- **0.8 (schedule rows read as stated) — justified for tags and service
  areas** (410/410 each). **Capacity is the caveat:** only 3 of 267 stated
  capacities produced a scored claim; the other 264 abstained because the
  stated unit token could not be read as the reported unit (the Watt-family
  mismatch tracked in #1290). The 0.8 is therefore *not yet measured* for
  capacity — it is neither confirmed nor refuted — and must be re-measured
  once #1290's unit-token policy lands. Do not tune it before then: the
  abstention path is the extractor behaving correctly (asserting nothing it
  cannot read), and the calibration table now makes that visible.
- **0.4 (conflict-nulled values) — unmeasured by construction.** A nulled
  value asserts nothing, so it can only ever appear as an abstention (4
  airflow abstentions above). The number is a review-triage signal, not a
  correctness claim, and is kept on that basis.

## Limitations

- One building (bsi-clinic) and one truth set: the measured correctness
  values are that building's, and a second truth building could move them.
- The capacity confidence is unmeasured, not validated — 264 of 267 stated
  capacities abstain on unit tokens (#1290). Any capacity-confidence claim
  before #1290 lands is unsupported.
- Plan-derived confidences (terminal tag, box link) cannot be calibrated
  against a schedule truth set at all; they need a plan truth set.

## What changed in code

- `mech_schedule.VALUE_CONFIDENCE` names the qualitative→numeric mapping
  (`high` 0.9, `medium` 0.8, `low` 0.4) in one place.
- `equipment` confidences are named constants
  (`SCHEDULE_EQUIPMENT_CONFIDENCE`, `PLAN_TERMINAL_TAG_CONFIDENCE`,
  `SCHEDULE_BOX_LINK_CONFIDENCE`) with the same values as before — this
  change names and measures them; it does not move any of them.
- The bench calibration block runs on every scoreboard, so future drift
  between a hand-set confidence and measured correctness shows up in the
  bench output instead of being rediscovered by hand.
