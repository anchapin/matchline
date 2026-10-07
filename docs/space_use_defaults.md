# Space-use defaults (#685)

Lighting power density, occupant density, plug-load density and the
lighting, occupancy and equipment schedules for each room, used only where
the drawings give no value.

## Source

**DOE Commercial Prototype Building Models, ASHRAE 90.1-2019 edition**, as
encoded in NREL openstudio-standards `v0.8.6`:

- space types: `ashrae_90_1_2019.spc_typ.json`
- schedules: `ashrae_90_1.schedules.json`

`space_use_defaults_data.py` is generated from those two files by
`scripts/build_space_use_defaults.py` (pinned tag, no hand edits). Every row
stores the prototype row it came from as `source_building_type /
source_space_type`, with the IP values verbatim and SI values converted at
1 m² = 10.7639 ft². Schedules are hourly fractions for weekday, Saturday and
Sunday/holiday.

## How defaults are applied

`space_use_defaults.apply_space_use_defaults(model)`:

1. Gives each room a `space_type` from its name (keyword rules in
   `space_use_defaults._RULES`, first match wins). A `space_type` already on
   `Space.use` is kept. Closets are `storage`; shafts and elevator cores get
   no loads. A name that matches nothing gets `office_whole_building` (the
   large-office whole-building average) at confidence 0.4 and is listed in
   the summary's `fallback` for review; a matched name gets 0.7.
2. Fills `lighting.lpd_w_m2`, `use.people_per_m2`, `use.equipment_w_m2` and
   the three schedule names **only where the model has no value**. A
   drawing-derived LPD or counted fixture watts is never overwritten.
3. Records a Provenance on each filled block: method
   `doe_prototype_default`, note naming the edition and source row.

## In the exports (#691)

`bem_loads.py` writes the loads in both gbXML writers (single- and
multi-storey) and the IFC4 writer:

- gbXML `Space`: `Description` naming the source (the DOE prototype row, or
  `drawing (method, sheet)`), `PeopleNumber` (`NumberOfPeople`, density ×
  area), `LightPowerPerArea` (counted fixture watts ÷ area, else the LPD) and
  `EquipPowerPerArea`, both W/m².
- gbXML schedules: each referenced schedule once, as `Schedule` →
  `YearSchedule` → `WeekSchedule` (`Weekday`, `Sat`, `Sun`, `Holiday`) →
  `DaySchedule` with 24 hourly fractions; spaces point at them with
  `lightScheduleIdRef`, `peopleScheduleIdRef` and `equipmentScheduleIdRef`.
  A schedule name with no profile in the table (one taken from a drawing) is
  left unreferenced rather than guessed.
- IFC4: `Pset_SpaceLighting.LightingPower`, `Pset_SpaceOccupancyRequirements`
  (`OccupancyNumber`, `AreaPerOccupant`) and `Pset_SpaceThermalLoad`
  (`Lighting`, `People`, `EquipmentSensible` in W).
- People heat gain (#693): gbXML `PeopleHeatGain` in `WattPerPerson`,
  `heatGainType="Total"`, from the row's prototype occupancy activity
  schedule (`activity_schedule`, copied into `activity_w_per_person`). Every
  mapped row's activity schedule is constant in the prototypes (120 W/person
  for the office, retail and school rows, 131.85 W/person for warehouse); the
  generator stops if one is not. The data-center row has no activity
  schedule, so its rooms get no heat gain. A drawing value is never
  overwritten.

IDF output does not carry loads yet.

## Table

| space_type | Prototype row | LPD (W/ft²) | People / 1000 ft² | Equipment (W/ft²) |
|---|---|---|---|---|
| `open_office` | Office / OpenOffice | 0.61 | 5.25 | 0.71 |
| `closed_office` | Office / ClosedOffice | 0.74 | 4.75 | 0.64 |
| `conference` | Office / Conference | 0.97 | 50.0 | 0.37 |
| `break_room` | Office / BreakRoom | 0.59 | 50.0 | 4.46 |
| `classroom` | Office / Classroom | 0.71 | 35.0 | 0.929 |
| `dining` | Office / Dining | 0.43 | 10.0 | 1.0 |
| `corridor` | Office / Corridor | 0.41 | 1.0 | 0.16 |
| `lobby` | Office / Lobby | 0.84 | 10.0 | 0.07 |
| `elevator_lobby` | Office / Elevator Lobby | 0.65 | 10.0 | 0.07 |
| `restroom` | Office / Restroom | 0.63 | 10.0 | 0.07 |
| `storage` | Office / Storage | 0.38 | 0.0 | 0.0 |
| `stair` | Office / Stair | 0.49 | 0.0 | 0.0 |
| `mechanical_electrical` | Office / Elec/MechRoom | 0.43 | 0.0 | 0.27 |
| `it_room` | Office / IT_Room | 0.74 | 5.0 | 1.56 |
| `print_room` | Office / PrintRoom | 0.74 | 10.0 | 2.79 |
| `vending` | Office / Vending | 0.41 | 1.0 | 3.85 |
| `data_center` | Office / OfficeLarge Data Center | 0.64 | None | 20.0 |
| `retail` | Retail / Retail | 1.05 | 15.0 | 0.3 |
| `kitchen` | SecondarySchool / Kitchen | 1.09 | 15.23 | 20.65 |
| `gym` | SecondarySchool / Gym | 0.9 | 30.0 | 0.46 |
| `auditorium` | SecondarySchool / Auditorium | 0.61 | 150.0 | 0.46 |
| `library` | SecondarySchool / Library | 0.83 | 10.0 | 0.93 |
| `warehouse` | Warehouse / Bulk | 0.33 | 0.0 | 0.2377 |
| `office_whole_building` | Office / WholeBuilding - Lg Office | 0.64 | 5.0 | 0.7497 |

## Limitations

- One schedule family per row (mostly the large-office prototype), not per
  building type; building-type selection is not inferred.
- Room-name rules are English keyword matches; anything unmatched is
  flagged rather than guessed at a specific type.
