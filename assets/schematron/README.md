# BuildingSync use-case Schematron rules

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
