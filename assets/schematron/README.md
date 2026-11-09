# BuildingSync use-case Schematron rules

Two rule sets live here and they are **not** the same thing:

1. `L000_Audit-1.0.0.sch` — an upstream artifact, fetched verbatim.
2. `matchline_policy-1.0.0.sch` — matchline's own repo policy, written
   and maintained here.

## Upstream artifact (never edited)

Committed ASHRAE 211 schematron rule files, fetched verbatim from the
upstream use-case library so the CI gate asserts against the same rule
set auditors download. Byte-identical to upstream: these are third-party
artifacts, never edited or regenerated locally (same policy as
assets/schemas).

- `L000_Audit-1.0.0.sch`: the "Preliminary Energy-Use Analysis" use case
  (the lowest ASHRAE 211 audit level). Requires the building facts, a
  Measured scenario with SiteEnergyUseIntensity + EnergyCostIndex, a
  Benchmark scenario, and the report's ASHRAEAuditLevel.

Provenance: BuildingSync/use-cases repo, `ASHRAE_211/schematron/`
(schemaVersion metadata says 1.0.0-2.7.0; the asserted element names all
bind against 2.6.0 documents, which is what matchline writes and the DOE
Audit Template speaks). SHA-256 of the fetched file:
62112eba446286ab6ae9da73e8e47634168319a8bc308ca568f10be3c1d03134.
Source license: BSD-3-Clause (BuildingSync artifacts).

Loaded by `bem.buildingsync_export.validate_buildingsync_use_case`; the
CI gate (`tests/test_buildingsync_use_case.py`) runs it over the example
export so a writer change that breaks L000 validity fails before merge.
`tests/test_buildingsync_policy.py` guards the SHA-256 above, so the
upstream file cannot be edited by accident.

## Repo policy rules (maintained here)

- `matchline_policy-1.0.0.sch`: matchline's own business rules for the
  BuildingSync files the writer produces (issue #1293). This is a first
  party file: edit it when the policy changes, unlike the upstream file
  above, which is frozen.

  Rules (the id is quoted in every failure message, so a report names the
  rule that was broken):

  | Rule | Policy |
  | --- | --- |
  | `ML-POL-001` | A sized HVAC system must be traceable to a zone-load-impacting input that appears earlier in the document: an earlier `ThermalZone` linked by `DeliveryID`, carrying a space floor area or `matchline:zone_flow_per_area_m3s_per_m2`. |
  | `ML-POL-002` | A fan that states an airflow (`matchline:fan_airflow_lps`) must be linked through `LinkedSystemID` to an HVAC system that states a sizing capacity. |
  | `ML-POL-003` | A fenestration thermal value (`matchline:u_value_w_m2k`) must name its source construction in the element id (`FenestrationSystemType-<construction id>`), so the value is traceable to drawing data or to an ASHRAE 90.1-2019 Table 5.5 library construction (`t55-*`). |
  | `ML-POL-004` | Every envelope element (wall, roof, foundation, fenestration) must state the thermal value it stands for, in the unit-bearing `matchline:*_u_value_w_m2k` form. |

  Every rule is written against the element and attribute names the writer
  actually emits; read `bem/buildingsync_export.py` before changing one.

  Loaded by `bem.buildingsync_export.validate_buildingsync_policy` and run
  by the CI gate `tests/test_buildingsync_policy.py`, which also commits a
  sample-violation fixture
  (`tests/fixtures/buildingsync_policy_violation.xml`) proving the rules
  fire: the fixture trips `ML-POL-001`, `ML-POL-002` and `ML-POL-003`,
  while the committed example export and a writer export stay clean.
