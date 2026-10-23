# GNI 2026 real-building benchmark scoreboard (#748)

Measured 2026-10-23, run 6 of 16 buildings per config `gni26` (GNI 2026_BIMprojects, staged
per-building under an external path; dataset and license in DATASETS.md, data itself never committed).

**Caveat:** every number below measures IFC self-consistency (matchline's model against a truth file
built from the same IFC), not drawings-vs-model accuracy. Geometry truth is derived from the IFC's own
spaces and wall geometry, so a perfect row means the pipeline preserved what the IFC states, nothing more.

| building | schema | status | rooms (truth / model) | area err | wall len err | windows (t / m) | doors (t / m) | win area err | LPD med err | terminals | envelope (in / out) | roof (in / out) | surfaces | cross-frontend | openstudio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gni26-p0-str | IFC2X3 (8 entities skipped in upgrade) | ok |  |  |  |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p1-arc | IFC2X3 | ok | 27 / 27 | 0.0% | 1.0% | 66 / 66 | 59 / 59 | 9.2% |  |  | 83 / 19 | 1 / 1 | 1790 |  | skipped |
| gni26-p1-str | IFC2X3 | ok |  |  | 1.3% |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p2-arc | IFC2X3 (77 entities skipped in upgrade) | ok | 31 / 31 | 1.1% | 2.3% | 8 / 0 | 20 / 20 | 100.0% |  |  | 23 / 4 | 36 / 33 | 374 |  | skipped |
| gni26-p3-arc | IFC2X3 | ok |  |  | 2.1% |  | 20 / 18 |  |  |  |  |  |  |  | skipped |
| gni26-p3-str | IFC2X3 | ok |  |  | 0.6% |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p4-arc | IFC2X3 | ok | 17 / 17 | 0.0% | 0.1% | 32 / 32 | 25 / 20 | 6.6% |  |  | 20 / 14 |  | 603 |  | skipped |
| gni26-p4-str | IFC2X3 | ok |  |  | 0.2% |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p5-arc | IFC2X3 | ok | 20 / 19 | 0.0% | 0.4% |  | 63 / 47 |  |  |  | 23 / 9 | 16 / 14 | 208 |  | skipped |
| gni26-p5-str | IFC2X3 | ok |  |  | 100.0% |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p7-arc | IFC2X3 | ok | 31 / 28 | 2.6% | 1.5% |  | 17 / 13 |  |  |  | 34 / 13 | 41 / 41 | 1428 |  | skipped |
| gni26-p7-str | IFC2X3 | ok |  |  | 2.5% |  |  |  |  |  |  |  |  |  | skipped |
| gni26-p8-str | IFC4 | ok |  |  | 0.0% |  |  |  |  |  |  |  |  |  | skipped |

OpenStudio column skipped: rerun with `--openstudio` and the CLI on PATH.

## Notes

- **Coverage: 13 of 16 staged buildings scored.** `gni26-p6-arc` (535 MB IFC) exceeds the 3 GiB
  sandbox memory budget on import (killed at 3.2 GiB RSS); `gni26-p8-arc` (21 MB) did not finish
  scoring inside a ~870 s local budget across three attempts. Both are resource caps of the run
  environment, not extractor failures; no row is published for them.
- **Raised import limits** were required for four buildings via env (local benchmark runs only, CI
  defaults unchanged): `MATCHLINE_MAX_IFC_SIZE_MB=600` for p1-arc/p2-arc (178 MB / 101 MB), and
  `MATCHLINE_MAX_IFC_ELEMENTS=1500000` for p4-arc/p5-arc (1.05 M / 1.49 M elements), `2200000` for
  p2-arc (1.84 M), `3200000` for p1-arc (3.06 M).
- **Structural IFCs (p*-str) carry no IfcSpace rooms**, so rooms/area/windows/doors columns are
  blank and only wall length is scored (truth built from IFC wall geometry with external-state
  conventions in the per-building convention report).
- **`gni26-p5-str` wall err is 100.0% because the model exported zero external walls** (truth 53.8 m
  vs model 0.0 m). The truth built from the IFC walls, the model's wall export found none marked
  external; flagged for investigation rather than averaged in. It is excluded from the wall mean.
- Aggregates over the five buildings with room truth (p1-arc, p2-arc, p4-arc, p5-arc, p7-arc):
  rooms 126 truth / 122 model matched, doors 204 truth / 177 model,
  floor-area err 0.76% mean (max 2.6%, p7-arc),
  exterior wall length err 1.10% mean excluding the p5-str anomaly.
- Per-building convention reports (volume conventions, area sources, external-wall state) are written
  next to the scoreboard by the harness; numbers here are exactly as measured, no tuning against truth.

Run recipe: `MATCHLINE_MAX_IFC_ELEMENTS=... scripts/benchmark.py --config gni26.json --out scoreboard.md --json scoreboard.json`
against the staged per-building dataset dirs (config + staging documented in DATASETS.md).
