# HVAC sizing inputs from recorded zone-load values (#1292)

`hvac_sizing.py` is the downstream consumer of the zone-load-impacting
inputs `mech_schedule.py` records. It aggregates the stated airflow
slots into a sizing-*input* summary per piece of equipment.

`mech_schedule` flags every airflow value it reads with
`zone_load_impacting=True` (the slots in `mech_schedule.ZONE_LOAD_SLOTS`:
`airflow`, `airflow_min`, `airflow_max`, `outside_air_flow`). Until this
module, nothing consumed that flag. Ledger decision #1236 records the
rule this module enforces: *any downstream HVAC sizing consumes zone
loads first*.

## Two phases, in order

```python
from mech_schedule import parse_cobie_workbook
from hvac_sizing import collect_zone_load_inputs, size_from_zone_loads

result = parse_cobie_workbook("/path/clinic-design.xlsx")
inputs = collect_zone_load_inputs(result)  # phase 1: collect
sized = size_from_zone_loads(inputs)  # phase 2: size
```

**Phase 1 — collect.** `collect_zone_load_inputs(result)` walks the rows
and systems of a `MechScheduleResult` and keeps only the values flagged
`zone_load_impacting`. Every extracted fact keeps its provenance: the
sheet, row and cell the raw string came from, the raw text, and the
confidence the schedule reader recorded. Each input also carries the
equipment tag and the service area the schedule states for it.

**Phase 2 — size.** `size_from_zone_loads(inputs)` returns a
`SizingResult`: one `SizingSummary` per equipment with the stated totals
for each slot, plus the totals of the whole schedule and every issue
raised. Totals are keyed by `(field, unit)`.

## The ordering guard

Sizing refuses to run before the inputs are registered.
`size_from_zone_loads` raises `ZoneLoadInputsNotSet` when it is handed

- anything that is not a `ZoneLoadInputs` registry (e.g. a raw parse
  result that was never collected),
- a registry that was never registered (`registered=False`), or
- a registry that holds no zone-load-impacting inputs.

The error message names the rule it enforces ("consumes zone loads
first", ledger decision #1236). The guard is enforced in code, not only
documented: `collect_zone_load_inputs` is the only thing that sets
`registered=True`, so a hand-built or empty registry cannot be sized.

## What the summary is

Per equipment, for each zone-load slot:

- `airflow`, `airflow_min`, `airflow_max`, `outside_air_flow`;
- the total of the distinct stated values, in the unit the schedule
  stated (`CFM`, `L/s`, ...);
- the inputs that contributed, with provenance.

Totals are grouped by unit and never converted: a schedule that states
one fan in CFM and another in L/s yields two separate totals, never a
sum of two different units.

## What the summary is not

There is no load model here. The module aggregates the stated inputs and
stops.

## Limitations

- **Stated inputs only.** This module sums what the schedule states. It
  computes no load, no capacity, no air change rate and no sensible
  heat; it never selects or sizes equipment.
- **No unit conversion.** Totals are grouped by the stated unit. There
  is no CFM/L/s conversion anywhere in the module, so a summary can
  carry several units for the same slot.
- **No equipment physics.** There is no fan curve, no coil sizing, no
  diversity factor and no redundancy rule. The summary is an input
  summary for a downstream sizing step, not that step.
- **Stated rows only.** Only the airflow slots the schedule reader
  resolved are aggregated. A slot the reader could not resolve (an
  ambiguous family/unit combination) never reaches this module, and a
  value it nulled is surfaced as an issue rather than totalled.
- **Conflicts are surfaced, never resolved.** When one slot carries two
  different stated values for the same equipment, neither is totalled:
  the slot becomes a `SizingIssue` of kind `conflict`. When the same
  value is stated more than once, it is counted once and reported as a
  `duplicate` issue. Nothing is silently double-counted, and nothing is
  silently dropped.
- **Low confidence is not a size.** A value the schedule reader
  recorded at low confidence is aggregated like any other stated value;
  the confidence stays on the input so a reviewer can filter it. This
  module does not re-judge confidence.
- **No cross-equipment roll-up by service area.** The summary is per
  equipment tag. Totalling several pieces of equipment that serve the
  same area is a downstream decision, not this module's.
