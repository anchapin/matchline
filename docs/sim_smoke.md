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
| `eui_out_of_band` | site EUI outside the band below | 0 (1 with `--strict`) |
| `ok` | completed, no severe errors, EUI in band | 0 |

Unmet occupied heating and cooling hours are reported and noted when over
300 h (ASHRAE 90.1 Appendix G, G3.1.2.3); they never change the status.

## EUI band and its source

`sim/reference_eui.json` holds one reference per building type, template and
climate zone: the site EUI of openstudio-standards' own DOE prototype
(`sim/prototype.rb`), simulated with the same weather and tools.

| Reference | Site EUI | Tools | Weather |
|---|---|---|---|
| SmallOffice, 90.1-2019, 5A | 322.15 MJ/m2 (511.16 m2) | OpenStudio 3.11.0+241b8abb4d, openstudio-standards 0.8.5, EnergyPlus 25.2.0 | Chicago O'Hare TMY3 (sha256 in the file) |

The band is 0.5x to 2.0x that EUI. It is our smoke-level choice, not a
standard: the exported model has its own geometry, glazing and zoning, so only
a gross miss should leave it. A miss is reported, not auto-fixed.

Refresh a reference with
`python sim_smoke.py --reference-only --epw <weather> --out-dir <dir> --building-type SmallOffice --template 90.1-2019 --climate-zone 5A`
and copy the printed EUI, tools and date into the file.

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
occupied hours 0 heating / 1.3 cooling, site EUI 836.2 MJ/m2, 2.6x the
prototype, so `eui_out_of_band`. Natural-gas heating is 528.6 MJ/m2 against the
prototype's 21.2; tracked in its own issue.
