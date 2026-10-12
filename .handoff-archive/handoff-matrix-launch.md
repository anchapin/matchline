# Session Handoff Checkpoint

**Timestamp:** 2026-09-28T15:13-04:00
**Branch:** `develop` @ `e01b52b` — clean, in sync with `origin/develop`
**Task:** #500 ECA A/B on the detector backbone (FloorYOLO) — 2×3×50-epoch matrix running on GPU, run 1/6 at epoch 42/50. #506 closed this session; #507–#512 open.

## 1. Accomplished So Far

- **#506 closed** (`7c24841`, CI green run `36469908026`, 984 collected confirmed by
  the `Check test count` step). New `tests/test_detector_train_seed.py` — 8 tests,
  0.2s, no GPU / dataset / detector venv. The defect is pure argument forwarding,
  so `ultralytics` and `torch` are stubbed in `sys.modules` (`train.py` imports both
  lazily inside `main()`) and the kwargs reaching `model.train()` are inspected.
- **The location was the real finding.** Written first in `detector/tests/`, then
  moved to `tests/`: `detector/` is excluded from CI *and* ruff
  (`pyproject.toml:79`), so a detector-only test is unenforced — which is exactly
  how 28 detector tests passed identically before *and* after the seed fix. The
  defect needs no heavy dependency, so it now runs where CI enforces it. #507 (the
  other 28 torch tests) is unaffected.
- **Three guards a naive test misses.** (a) *seed 0 forwarded, not omitted* —
  Ultralytics' default is also `0`, so `kwargs.get("seed") == 0` passes on a
  train.py that forwards nothing; only key presence distinguishes them. (b)
  *distinct seeds forward distinctly* — a hardcoded forward satisfies any single
  seed; this is the #500 driver-output shape, caught at source instead of after
  four hours of GPU. (c) *defect injection on a **copy*** — the mutated train.py is
  written to `tmp_path` and the same assertion helper is pointed at it, asserting
  it raises. The real `train.py` is never edited; that obvious approach would have
  silently changed arms 2–6 of the running matrix.
- `detector/README.md`: seed contract recorded where #500 gets reported — **three
  identical rows in a summary mean a failed experiment, not three confirmations** —
  plus the #509 caveat that 3 seeds understate variance, so a within-arm spread is
  a floor, not a confidence interval. Stale "blocked on no dataset" status fixed.
- `EXPECTED_TEST_COUNT` 976 → **984** in `.github/workflows/ci.yml` (delta spelled
  out), `AGENTS.md`, `docs/ci.md` (×2), `docs/README.md`.
- Prior sessions: #513 (check-catalog doc reconciliation, +8 tests), #505+#504
  (wall↔envelope matching), seed-forwarding fix `e2292dd`, CubiCasa5K dataset
  built (5000 plans, CC BY-NC-SA, non-commercial), GPU unblocked (RX 6600 XT /
  ROCm 7.2.3, `torch==2.14.0+rocm7.2`, `detector/.venv-det-rocm`).

## 2. Modified Files

All committed; **working tree clean, no uncommitted diff** (`git status --short`
and `git diff --stat` both empty).

- `tests/test_detector_train_seed.py` — **new**, +185. 8 tests.
- `detector/README.md` — seed contract (#506) + corrected #500 status paragraph.
- `.github/workflows/ci.yml` — `EXPECTED_TEST_COUNT` 976 → 984.
- `AGENTS.md`, `docs/ci.md` (×2), `docs/README.md` — count 976 → 984.
- `.handoff-archive/handoff-seed-test.md` — previous handoff, archived.

## 3. Current Verification State

- **Suite:** `python3 -m pytest tests/ -q` → **981 passed, 2 skipped, 1 xfailed**
  (984 collected), 219 s. Run it in the **foreground** — a backgrounded run left
  an empty `.out` file and reported nothing.
- **Lint:** `ruff check .` PASS · `ruff format --check .` → 218 files formatted.
- **CI:** `success` on `7c24841` (`36469908026`). `e01b52b` is a markdown-only
  handoff commit; its run may still be in progress.
- **Tooling gotcha:** `ci-wait` reported `FAILED: Some checks failed after 0m 0s`
  for a run that was merely `in_progress`, and again after it succeeded. Use
  `gh run watch <id> --exit-status --interval 20` (blocks, reports correctly).
- **Matrix:** driver alive (`pgrep -c -f run_ab_eca.sh` → 2), `ab_summary.csv` has
  **0 rows**, `ab_driver.log` shows only `yolo11n_baseline_s0 : start 14:27:21`.
  Epochs done per run dir: `baseline_s0` 42, all five others 1 (stale — see below).
  Effective ~65.7 s/epoch (slower than 58.7 nominal; the pytest suite competed for
  CPU). Run 1 lands ~15:22; all six ≈ **19:20–19:30 EDT**.
- **Open issues:** #500 (blocked on the matrix), #507, #508, #509, #510, #511, #512.

## 4. Immediate Next Step

**Do not restart or edit the matrix.** Launched 14:27 EDT via
`nohup bash detector/run_ab_eca.sh 50`; ~49 min/run × 6. **Check progress, not
results** — 0 summary rows is correct at this point; the first lands ~15:22.

```bash
column -s, -t ~/workspace/datasets/detector_runs/ab_summary.csv   # one row per FINISHED run
tail -5 ~/workspace/datasets/detector_runs/ab_driver.log
```

If the driver died, re-invoke the identical command — it is resumable and skips
completed runs.

⚠️ **Stale-CSV hazard:** `yolo11n_baseline_s{1,2}` and `yolo11n_eca_s{0,1,2}` still
hold 1-epoch `results.csv` from earlier validation. Reading epoch counts across all
six dirs shows five bogus "epoch 1" rows (three of them phantom). The resumability
check needs ≥50 rows so they get overwritten — **but never read their mAP50 as
matrix data.**

🚫 **Do not edit `detector/train.py`, `detector/configs/`, or
`detector/run_ab_eca.sh` until all six rows exist** — the driver re-invokes
`train.py` per run, so a mid-matrix edit silently changes arms 2–6. Blocks **#508**
only. `detector/README.md` is *not* in that list; it is safe and was just edited.

**Reporting rules for #500** (do not skip): no delta before all six rows exist.
Per-arm mean ± spread; compare the between-arm difference against the within-arm
spread. Overlapping ranges → "no delta measurable at this budget", a legitimate
result. Do not quote the better arm's best seed. Do not close as "no effect"
without a measured spread — #509: ROCm is not bit-reproducible at fixed seed (same
config, same seed: 0.05485 vs 0.21352), so 3 seeds understate total variance.
`detector/` is excluded from CI: no detector result is verified upstream, so say so
rather than implying a green run covers it.

**Safe work while the matrix runs** (none touch the files it reads): #507 CI
coverage for the 28 torch-dependent detector tests · #509 ROCm reproducibility note
· #510 ROCm venv provenance · #511 superseded-branch triage (~10 min, cheapest) ·
#512 unattached-opening observability (most valuable). Only one at a time — the
CPU cost of the main suite slows the matrix.
