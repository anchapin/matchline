# Mechanical schedule parsing and system-type classification (#974)

`mech_schedule.py` parses a real mechanical schedule into typed rows,
classifies a system type per row with provenance, routes low-confidence
rows to the review queue, and emits first-class `MechSystem` entities
linked to rooms.

## Source and license

The reference dataset is buildingSMART's Medical-Dental Clinic design
workbook (`2013-10-09-Clinic-Design-v14-WORKING-01.xlsx`, COBie 2.4
Spreadsheet format), from the buildingSMART Community-Sample-Test-Files
repository, licensed CC BY 4.0 with attribution: BSI (2020)
"Medical-Dental Test Files," buildingSMART International, plus the
repository URL. The clinic drawing set carries no schedule PDFs; the
COBie workbook is the project's real schedule deliverable. The workbook
is never committed: pass its path in, and tests skip without
`MATCHLINE_CLINIC_COBIE` set to its location.

- `Component` sheet: equipment rows (name, type, service area,
  description). The workbook leaves `TagNumber` as `n/a` and embeds the
  tag in the name (`AC Unit Type 1 AC-1`), so the tag is parsed from the
  trailing `PREFIX-digits` token.
- `Type` sheet: category (OmniClass string) and description. Capacities
  live here as stated (`63300000 J` for the AHU, `633-703 kW` for the
  chiller) and are recorded as stated, never converted; a stated range
  keeps `value=None` with the raw string.
- `Space` sheet: the room list (`1A01`-style numbers) the service areas
  are matched against, so `MechSystem.rooms` is self-contained.

## Classification rules

The workbook's OmniClass category column is unreliable (`Duplex
Receptacle` sits under a `23-` HVAC category, mailbox types under
another), so the category is carried as provenance and never used to
classify. A row's system type comes from two independent signals:

1. the tag prefix parsed from the component name (`AC`, `ACCU`, `AHU`,
   `EF`, `VAV`, `RTU`, `FCU`, `ERV`, `HP`, `P`, `B`, `CH`), and
2. type-name and description text (fan coil, chiller, boiler, AHU, VAV,
   supply/return diffuser, exhaust, fan, pump, ...).

Tag and text agreement is high confidence; a single signal is medium;
conflicting evidence is low confidence and routes the row to the review
queue (kind `mech_schedule`) with `system_type=None` until a human
confirms it. Components with no positive evidence at all (receptacles,
doors, sprinklers, light fixtures) are excluded from the schedule and
counted in the result stats with a sample of excluded type names.

## Output

`parse_cobie_workbook(path)` returns a `MechScheduleResult`:

- `rows`: `MechScheduleRow` per schedule row (tag, type, system type,
  confidence, capacity as stated, service area, provenance with source
  path, sheet, row and type-row numbers);
- `systems`: `MechSystem` for scheduled equipment (everything except
  air terminals; diffusers stay rows because the drawing path links
  them to rooms via hvac_trace). Each system carries its rooms
  (`[{"space": "1B21", "matched": true}]`) matched against the
  workbook's `Space` sheet;
- `review_items`: one `ReviewItem` per low-confidence row;
- `stats`: totals, per-type and per-confidence counts, excluded count.

`link_to_model(systems, model)` binds each system's service area to
model rooms by room number or name and returns `{tag: [space ids]}`;
a service area no room carries stays unmatched, never guessed. The
follow-on MEP slice consumes `MechSystem` for air-loop building (#746:
each scheduled AHU/RTU becomes one gbXML AirLoop).

## What this slice does not cover

- Drawing-sheet (PDF) equipment schedules: the parser reads the COBie
  workbook; a real M-sheet route (e.g. the isnrv set's equipment
  schedule) is a candidate follow-up.
- Air-loop assembly and zone binding: the follow-on MEP slice.
- Parsing `clinic-handover.xlsx` (the O&M workbook): the design
  workbook is the schedule source of record for equipment.

## Scoring schedule extraction (grouped vs flat, #1112)

`scripts/schedule_score.py` scores predicted schedule rows against truth
rows both ways, so a row-shuffle error can no longer pass as correct:

- **grouped** (whole-row credit): a truth row scores only when the
  prediction carries the same equipment tag and every compared field
  (`system_type`, `service_area`, `capacity` value+unit) agrees.
- **flat** (field-level credit, KIEval-style): over the same tag-matched
  pairs each field is credited independently, so a crossed row still
  earns partial credit. The gap between the two scores is the
  row-integrity error the flat number hides; `crossed` rows are counted.

Identity is the exact tag (`mech_schedule.parse_tag`); duplicate or
missing tags are reported, never guessed. Feed it
`mech_schedule.MechScheduleRow` objects via `mech_row_to_dict`, or plain
`{"tag", "fields"}` dicts from fixtures. The benchmark harness (#748)
has no schedule-row path yet (its buildings are IFC-based); this scorer
is what that path reports when it lands.
