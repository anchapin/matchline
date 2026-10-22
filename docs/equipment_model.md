# The unified equipment model (#1237)

Every piece of equipment on the mechanical schedules becomes one
`Equipment` object: tag and type from the schedule row, capacities and
airflow as the schedule states them (Btu/h and m3/s, `None` when the row
gives no value with units), `served_by` naming the central unit that feeds
a terminal box (from #747's box-to-unit schedule links), and `serves`
listing the HVAC zones the piece serves, each relationship carrying its
source. `model.equipment` holds them all.

## Sources, in the order they are trusted

| Source | Meaning |
| --- | --- |
| `plan_terminal_tag` | the zone's terminal unit on the plan carries the schedule tag (a duct-traced terminal, or the plan placement of a terminal tag, which is how real-set zones are born) |
| `plan_tag_placement` | the schedule tag is placed on the plan inside a space the zone serves, so the zone is fed through that room |
| `schedule_box_link` | the zone's terminal box names this central unit in a schedule column; the plan ties the box to the zone, the schedule ties the box to the unit |

A zone served by no known air handler, or by more than one, keeps no
reference and goes to review (the #746 rule, kind `air_loop`, target kind
`hvac_zone`). A box-airflow sum above the supply fan airflow stays normal
diversity and is never a review item. A self-serving terminal (a fan coil)
resolves the zone to itself: the serving relationship is real and lands on
the fan coil's `serves`, but no central air handler is named, so the zone's
`air_loop` stays empty exactly as the gbXML writer expects.

## Resolution

`equipment.build_equipment(model, equipment)` does the work:

1. One `Equipment` per scheduled tag (first row wins on a repeated
   normalized tag; the id is `eq-` plus the normalized tag).
2. Zone claims are collected from the terminal tag and the plan
   placements inside the zone's rooms.
3. One distinct serving unit resolves the zone (`air_loop` set when the
   unit is central); two or more leave the zone unreferenced and flagged.
4. Zones with no claim are flagged per reason: no terminal unit traced, a
   scheduled box with no unit link, or a terminal tag that is not on the
   schedules.

Flags deduplicate on re-runs: a zone already queued for the same reason is
not queued twice.

## Exports

The model is export-agnostic. `air_loops.build_air_loops` calls
`build_equipment` first and derives the gbXML `AirLoop`s from the central
units (#746, option 3); zone `air_loop` references flow from the same
resolution. The BuildingSync writer (#1238) maps `Equipment` objects
directly, capacities and serving references included.

## Files

- `equipment.py`: builder (`build_equipment`)
- `building_model.py`: `Equipment`, `ServingRef`, `BuildingModel.equipment`
- `air_loops.py`: gbXML assembly on top (`build_air_loops`)
- `tests/test_equipment.py`: fixtures per source family, unresolved cases, round trip
