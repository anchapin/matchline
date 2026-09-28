# Session Handoff Checkpoint

**Timestamp:** 2026-09-28T14:57-04:00
**Branch:** `develop` @ `5b40fec` — clean, in sync with `origin/develop` (0/0)
**Task:** #500 ECA A/B on the detector backbone (FloorYOLO). 2×3×50-epoch matrix **running now**. #505/#504 closed earlier; #513 closed this session; #506–#512 open.

## 1. Accomplished So Far

**#513 closed** (`ff45543`, CI green on `5b40fec`). Filed as "three docs say 28, 17 stale
`validate.py` refs" — true but understated. Fixing it exposed that `docs/validation.md`,
the catalog the count test *claimed* to police, was itself wrong, and that test read no
documentation at all: `test_battery_size_documented` asserted a bare `N_CHECKS == 37`
while its name and comment promised a doc check.

Replaced with 5 tests that read the docs, both directions:
`test_battery_size_documented` (heading == `N_CHECKS`) · `test_every_check_is_documented`
(forward) · `test_no_phantom_checks_documented` (reverse) · `test_check_count_matches_orientation_docs`
(×5 docs) · `test_no_docs_reference_removed_validate_module`. Vocabulary comes from a real
`run_checks` report, not from `BATTERY`, so a listed-but-unwired check still registers.

Drift found beyond the report, each fixed and each **verified by defect injection**
(mutate → only the intended guard fails → restore):
- heading said `34`; `N_CHECKS` is 37 (34 in `BATTERY` + 3 in `CONSERVATION_BATTERY`)
- **4 checks ran undocumented**, incl. the whole second battery: `area_closure`,
  `volume_closure`, `envelope_closure`, plus `review_queue_acknowledged`,
  `window_double_link`, `ashrae_hvac_efficiency`, `gbxml_wall_areas`
- `ashrae_window_shgc` = **phantom id**. SHGC *is* evaluated inside
  `_check_window_u_factor` via `ashrae90_1.window_envelope()` → folded into that
  check, not deleted. The claim was real, the id was not.
- Limitations named `check_area_balance`/`check_volume_balance` — never existed; the
  ids are `area_conservation`/`volume_conservation`

23 stale `validate.py` → `validate/` across 16 live docs, incl. 2 contributor
instructions that told people to edit a file that isn't there. `CHANGELOG.md`,
`docs/adr/`, `.planning/`, `.handoff-archive/` left as point-in-time records —
deliberate, stated in the commit.

**Prior context:** #505+#504 fixed (wall↔envelope matching by position, not length;
`s_center_m` projection off by half a wall — hit *every* wall). CubiCasa5K dataset
built (5000 plans, CC BY-NC-SA, non-commercial). GPU unblocked (RX 6600 XT / ROCm
7.2.3, `torch==2.14.0+rocm7.2`, separate `detector/.venv-det-rocm`; `.venv-det` left
on its CPU pin). Seed-forwarding bug fixed (`e2292dd`) — pre-fix, all 3 seeds returned
byte-identical metrics.

## 2. Modified Files

All committed; working tree clean.

- `tests/test_validate.py` — +110/−~15. 5 doc-reconciling tests replace the fake one.
- `docs/validation.md` — +52/−~20. count 34→37; 6 missing bullets added; phantom
  `ashrae_window_shgc` folded into `ashrae_window_u_factor`; `ashrae_hvac_efficiency`
  added; stale ids in Limitations fixed; "how to add a check" step 3 rewritten.
- `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `CONTRIBUTING.md`, `QUALITY-SCORE.md`,
  `ROADMAP.md`, `docs/README.md`, `docs/CODE-REVIEW.md`, `docs/pipeline.md`,
  `docs/run_review.md`, `docs/ifc_import.md`, `docs/geometry_simplify.md`,
  `docs/design-docs/core-beliefs.md`, `docs/plans/designs/cfg-01-*`, `cfg-04-*`,
  `.github/ISSUE_TEMPLATE/feature_request.md` — `validate.py`→`validate/`
- `README.md`, `ARCHITECTURE.md`, `docs/README.md`, `docs/pipeline.md`,
  `QUALITY-SCORE.md` — 28-check → 37-check
- `.github/workflows/ci.yml`, `AGENTS.md`, `docs/ci.md` (×2), `docs/README.md` —
  `EXPECTED_TEST_COUNT` 968 → **976**, with the +8 delta spelled out in the ci.yml comment

## 3. Current Verification State

- **Suite:** `python3 -m pytest tests/ -q` → **973 passed, 2 skipped, 1 xfailed**
  (976 collected). ~155 s idle; 311 s while competing with the GPU job for CPU.
- **Lint:** `ruff check .` PASS · `ruff format --check .` → 217 files formatted.
- **CI:** `success` on `5b40fec`, incl. the `Check test count` step — independently
  confirms 976. Workflow uses `concurrency: cancel-in-progress`, so intermediate
  commits show `cancelled`; only the tip is meaningful.
- **Detector tests:** 28 pass via the CPU venv, but pass identically before and after
  the seed fix — they do **not** cover it (#506).
- **Tree:** clean, in sync with origin. Open: #500, #506–#512.

## 4. Immediate Next Step

**Do not restart or edit the matrix.** `nohup bash detector/run_ab_eca.sh 50`, launched
14:27 EDT, 2 arms × 3 seeds × 50 epochs, 58.7 s/epoch → ~49 min/run → **~4.9 h total**,
ETA ~19:20 EDT. At 14:57: run 1/6, `yolo11n_baseline_s0` epoch 26/50.

```bash
column -s, -t ~/workspace/datasets/detector_runs/ab_summary.csv   # one row per FINISHED run
tail -5 ~/workspace/datasets/detector_runs/ab_driver.log
```

**Check progress, not results.** 0 rows now; the first lands ~15:17. If the driver died,
re-invoke the identical command — it is resumable and skips completed runs.

⚠️ **Stale-CSV hazard:** `yolo11n_baseline_s{1,2}` and `yolo11n_eca_s{0,1,2}` still hold
1-epoch `results.csv` from the earlier validation. Reading epoch counts across all six
dirs shows three bogus "epoch 1" rows. The resumability check needs ≥50 rows so they get
overwritten — but never read their mAP50 as matrix data.

🚫 **Do not edit `detector/train.py`, `detector/configs/`, or `detector/run_ab_eca.sh`
until all six rows exist.** The driver re-invokes `train.py` per run, so a mid-matrix
edit silently changes arms 2–6. This blocks **#508** only.

**Reporting rules for #500** (do not skip): no delta before all six rows exist. Report
per-arm mean ± spread; compare the between-arm difference against the within-arm spread.
Overlapping ranges → "no delta measurable at this budget", which is a legitimate result.
Do not quote the better arm's best seed. Do not close as "no effect" without a measured
spread — per #509, ROCm is **not** bit-reproducible at fixed seed (same config, same seed:
0.05485 vs 0.21352), so 3 seeds understate total variance.

`detector/` is excluded from CI (`pyproject.toml:79`) — no detector result here is
verified upstream. Say so rather than implying a green run covers it.

## 5. Safe Work While the Matrix Runs

#506 seed test gap · #507 detector CI coverage · #509 ROCm reproducibility note ·
#510 ROCm venv provenance · #511 superseded-branch triage · #512 unattached-opening
observability. None touch the files the matrix reads.
