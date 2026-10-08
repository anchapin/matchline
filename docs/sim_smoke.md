# Simulation smoke test (#752)

`sim_smoke.py` proves an exported gbXML simulates and gives a plausible
answer, not only that it imports (#628).

1. `sim/typical.rb` loads the gbXML into OpenStudio, sets the TMY weather and
   its design days (the `.ddy` and `.stat` must sit beside the `.epw`), then
   openstudio-standards adds the DOE prototype space types, loads, constructions
   for surfaces without one, and the inferred HVAC template, with a sizing run.
2. A full year of EnergyPlus runs on the result.
3. The harness reads `eplusout.err` and `eplusout.sql` and writes
   `sim_smoke_report.json`.

## Verdict

| Status | When | Exit |
|---|---|---|
| `fail` | EnergyPlus did not complete, any severe error, or no conditioned area | 1 |
| `eui_out_of_band` | site EUI outside the CBECS band below | 0 (1 with `--strict`) |
| `ok` | completed, no severe errors, EUI in band | 0 |

Unmet occupied heating and cooling hours are reported and noted when over
300 h (ASHRAE 90.1 Appendix G, G3.1.2.3); they never change the status.

## EUI band and its source

The band is measured, not chosen: the weighted (FINALWT) 5th to 95th
percentile of site EUI (MFBTU / SQFT) in the EIA 2018 CBECS public use
microdata, for the buildings matching each reference in
`sim/reference_eui.json`. `sim/cbecs_band.py` recomputes it from the CSV
(downloaded and checked by sha256, never committed; listed in
`license_ledger.json` as public-domain U.S. government data).

| Reference | CBECS filter | Records (buildings) | p5 / p50 / p95 MJ/m2 |
|---|---|---|---|
| SmallOffice, 90.1-2019, 5A | Office, 1,001-10,000 ft2, climate "Cool" (ASHRAE 169 zone 5) | 65 (173,182) | 211.0 / 603.8 / 1912.4 |

CBECS is the existing stock, mostly built before 90.1-2019, so a code-built
model sits in its low tail: openstudio-standards' SmallOffice 90.1-2019 5A
prototype (322.15 MJ/m2, `sim/prototype.rb`, same tools and weather) lands
near the 9th percentile. That is why the band is p5-p95, not p10-p90; a test
keeps the prototype inside its own band. The ratio to the prototype is still
reported as a note. The public file is masked and perturbed for disclosure
avoidance, so the percentiles differ slightly from EIA's published tables.

## Where it runs

`.github/workflows/sim-smoke.yml`: nightly at 07:00 UTC, on demand, and on
PRs to `develop` that touch the harness or the gbXML writers. OpenStudio
3.11.0 (the Ubuntu 24.04 `.deb`, ~400 MB) and the weather files (pinned to
EnergyPlus v25.2.0, checked by sha256) are cached. The job summary shows the
status, EUI against the band and unmet hours; the report and logs are uploaded.
The per-PR suite only tests the harness's parsing and verdict
(`tests/test_sim_smoke.py`).

## First result (2026-10-07)

Seed 101 (5A, 361.67 m2): EnergyPlus completed, 0 severe errors, unmet
occupied hours 0 heating / 1.3 cooling, site EUI 836.2 MJ/m2: about the 73rd
percentile of the CBECS band, so `ok`, but 2.6x the 90.1-2019 prototype.
Natural-gas heating is 528.6 MJ/m2 against the prototype's 21.2; tracked in
its own issue (#768), since a new 90.1-2019 design should not look like the
median existing building.
