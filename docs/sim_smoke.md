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

The band is the weighted (FINALWT) 5th to 70th percentile of site EUI
(MFBTU / SQFT) in the EIA 2018 CBECS public use microdata, for the buildings
matching each reference in `sim/reference_eui.json`. `sim/cbecs_band.py`
recomputes it from the CSV (downloaded and checked by sha256, never committed;
listed in `license_ledger.json` as public-domain U.S. government data).

| Reference | CBECS filter | Records (buildings) | p5 / p50 / p70 MJ/m2 (band p5-p70) |
|---|---|---|---|
| SmallOffice, 90.1-2019, 5A | Office, 1,001-10,000 ft2, climate "Cool" (5A/5B/5C) | 65 (173,182) | 211.0 / 603.8 / 803.7 |

The climate group comes from EIA's own grouping of ASHRAE 169-2021 zones
for the 2018 CBECS ([CBECS maps, "Definitions of CBECS climate zones"](https://www.eia.gov/consumption/commercial/maps.php)):
Cold or very cold = 6A, 6B, 7, 8; Cool = 5A, 5B, 5C; Mixed mild = 4A, 4B, 4C;
Warm = 3A, 3B, 3C; Hot or very hot = 1A, 1B, 2A, 2B. `cbecs_band.pubclim_for`
encodes it and a test keeps each reference's filter on it.

CBECS is the existing stock, mostly built before 90.1-2019, so the top is
capped at p70: a new-code model using more energy than most existing buildings
of its type points at an export or template error. Using CBECS percentiles as
targets follows ANSI/ASHRAE/IES Standard 100, whose EUI targets are the CBECS
2012 25th percentile by building type and climate zone. The bottom is p5, not
p10, because openstudio-standards' SmallOffice 90.1-2019 5A prototype
(322.15 MJ/m2, `sim/prototype.rb`, same tools and weather) lands near p9; a
test keeps the prototype inside its own band. The ratio to the prototype is still
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
occupied hours 0 heating / 1.3 cooling, site EUI 836.2 MJ/m2: about CBECS
p73, above the p70 cap, so `eui_out_of_band`, and 2.6x the 90.1-2019 prototype.
Natural-gas heating is 528.6 MJ/m2 against the prototype's 21.2; tracked in
its own issue (#768).

## After #768 (2026-10-07)

Two cited defaults replace the placeholders: exterior walls and roof with no
named assembly take the Appendix G baseline classes (G3.1-5(b): steel-framed
wall U 0.3123, IEAD roof U 0.1817 W/m2K, 90.1-2019 Table 5.5-5), and
`sim/typical.rb` sets infiltration to the DOE prototype rate per above-grade
exterior wall area, 0.2016 cfm/ft2 (0.001024 m3/s-m2, PNNL-18898). Seed 101:
site EUI 671.6 MJ/m2 (`ok`, inside 211.0-803.7), 2.08x the prototype; gas
heating 378.3 MJ/m2; unmet 0 heating / 0.7 cooling h.
