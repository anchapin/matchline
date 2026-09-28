# Session Handoff Checkpoint
**Timestamp:** 2026-09-28T17:52-04:00
**Branch:** `develop` @ `4161c16` — clean tree, **+6 unpushed**, `0 behind origin/develop`
**Task:** #500 ECA A/B on the detector backbone (FloorYOLO) — 2 arms × 3 seeds × 50
epochs on GPU, **3/6 rows landed**; run 4/6 (`yolo11n_eca_s1`) at **epoch 47/50,
finishing now**. All six ≈ **19:35 EDT**. #515 0%-queue recovered this session.

## 1. Accomplished So Far
- **Resumed from the prior handoff and verified against reality** — 3/6 rows,
  driver PID 828948 alive, GPU 99%, no drift. (Prior file said +2 commits;
  a 3rd had landed. Read the state, don't trust the file.)
- **Worked #515's 0%-coverage queue by hand; recovered 2 branches of real lost work:**
  - `roadmap-part-ii` → 84-line "Part II — Future directions" backlog (7 themed
    sections, eight-persona review) **absent from develop's `ROADMAP.md`**
    (confirmed: none of its 7 headings exist on develop). Ported as doc append.
    `ROADMAP.md` diverged +102/−115 vs develop, so **manual section copy, not a
    cherry-pick.**
  - `fix/issue-343-auto-triage-coverage` → `tests/test_run_pipeline_auto_triage.py`
    (88 lines) covers `run_pipeline._run_auto_triage()`, which **exists in
    develop (`run_pipeline.py:77`) with zero test coverage**. 5/5 pass.
  - Other 0% branches are **churn**: `fix/issue-314`, `fix/issue-20` target a
    `validate.py` develop split into the `validate/` package (superseded);
    `fix/issue-351`, `fix/issue-80` are superseded one-liners.
- **Found and fixed a bug in my own re-derivation before trusting it.**
  `git diff <base> -- <path>` (one commit-ish) diffs base against the
  **working tree**, not the branch tip — it scored nearly everything covered and
  put `roadmap-part-ii` at a false 100%. Corrected to
  `git diff <base> <branch> -- <path>`; the script then reproduced the prior
  session's **100%-covered tier exactly (16 branches)** and total-with-diff (95),
  confirming the original methodology. **Verdict posted to #515.**
- **Fixed CI's `Check test count` gate**: the recovered test moved collection
  984→989; bumped `EXPECTED_TEST_COUNT` and verified against `--collect-only`.
- Prior handoff archived to `.handoff-archive/handoff-515-queue-recovered.md`.

## 2. Modified Files
All committed; working tree clean.
- `ROADMAP.md`: +84 lines (Part II backlog appended, nothing removed).
- `tests/test_run_pipeline_auto_triage.py`: new, 88 lines (+5 tests).
- `.github/workflows/ci.yml`: `EXPECTED_TEST_COUNT` 984→989 + rationale comment.
- `.handoff.md`: this file.
- `.handoff-archive/handoff-515-queue-recovered.md`: prior handoff (untracked —
  scratch history, intentionally not committed; matches prior sessions' pattern).

Commits: `0ef8c98` (recoveries) · `4917ee9` (CI gate) · `4161c16` (handoff),
on top of `662f60a`/`1793aff`/`8b02cab` (handoff-markdown only).

## 3. Current Verification State
- **Linter / Tests: PASS.** `986 passed, 2 skipped, 1 xfailed` (989 collected);
  `ruff check .` + `ruff format --check .` clean. +5 vs the 981/984 baseline,
  exactly the recovered tests. The 14 `PytestUnraisableExceptionWarning`
  `KeyError`s are **pre-existing** (library `file.__del__` ResourceWarning),
  verified identical on a stashed tree.
- **CI: unverified for `0ef8c98`/`4917ee9`** — the first code commits since
  `0511072` (green, 981 passed, run `36479760128`). 6 commits unpushed.
- **Matrix — 3/6 rows:**

  | arm | seed | epochs | mAP50 | mAP50_95 | train_box_loss |
  |---|---|---|---|---|---|
  | yolo11n_baseline | 0 | 50 | 0.96547 | 0.87505 | 0.43498 |
  | yolo11n_eca | 0 | 50 | 0.96850 | 0.87837 | 0.43414 |
  | yolo11n_baseline | 1 | 50 | 0.97139 | 0.88419 | 0.42740 |

  Run 4/6 `yolo11n_eca_s1` at **epoch 47/50** (started 17:04:40, ~50 min/run);
  driver PID 828948 alive. Runs 1–3 took 53/49/55 min.
- **ROCm/AMD box — `nvidia-smi` does not exist.** Use `rocm-smi --showuse`.
- **Open issues:** #500 (blocked on matrix), #507, #508, #509, #510, #512, #514
  (blocked on matrix), #515 (0%-queue recovered; the 16 @100% are next deletion
  candidates but still need a **deletions** check — coverage ignores removals),
  #516 (needs GPU, after #500), #517, #518 (**human decision, not code**), #519.

## 4. Immediate Next Step
**Do not restart or edit the matrix. Check progress, not results.** Row 4 lands
within minutes — confirm 4 rows and that run 5 (`yolo11n_baseline_s2`) started:
```bash
column -s, -t ~/workspace/datasets/detector_runs/ab_summary.csv  # row per FINISHED run
tail -3 ~/workspace/datasets/detector_runs/ab_driver.log
```
`ab_summary.csv` is created on first completion; before that `column` errors
rather than printing an empty table. **Three rows was correct at 17:25; four is
correct now.** Stale `yolo11n_baseline_s2` / `yolo11n_eca_s2` `results.csv` hold
1-epoch pre-launch-validation data (the skip guard needs ≥50 rows, so they'll be
overwritten) — **never read their mAP50 as matrix data.** This hazard already
produced the bad #509 evidence.

⚠️ **If the driver died, do NOT blindly re-invoke** (#514): the summary append
is not tied to the skip branch, so resuming **duplicates rows** for every
complete run and corrupts the #500 statistics. The driver is resumable; its
summary is not idempotent. Verify no duplicate rows, or wait for #514's fix.

🚫 **Do not edit `detector/train.py`, `detector/configs/`, or
`detector/run_ab_eca.sh` until all six rows exist** — the driver re-invokes
`train.py` per run, so a mid-matrix edit silently changes later arms. Blocks #508
and #514. `detector/README.md` is *not* in that list.

**Then, in order:** (1) `git push` the 6 commits and confirm CI green at
`4161c16` — the test-count gate changed, so this is the one real CI risk;
(2) **Reporting rules for #500** (do not skip): no delta before all six rows
exist; per-arm **mean ± spread across all runs**, never a best-seed number;
compare between-arm difference against **within-arm** spread — overlapping
ranges → "no delta measurable at this budget", a legitimate and better result
than a confident wrong number. The seed-0 +0.003 mAP50 for ECA is **not** a
result (one seed per arm carries almost no information). Do **not** reuse #509's
old 0.05485/0.21352 as a noise floor (confound: different epochs ⇒ different LR
schedules). `detector/` is excluded from CI, so no detector result is verified
upstream — say so rather than implying a green run covers it.
(3) Safe work while it runs (none touch the matrix's files): #517 catalog rows +
guard test · #519 untrack stray artifacts · #510 venv provenance. One at a
time — the main suite's CPU cost slows the matrix.

**Pending human calls:** push the 6 commits; `git worktree remove
/tmp/opencode/verify_patch` (proven safe — no unique work — but deliberately not
executed); #518.
