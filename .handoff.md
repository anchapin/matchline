# Session Handoff Checkpoint

**Timestamp:** 2026-09-28T17:06-04:00
**Branch:** `develop` @ `1793aff` — clean tree, **2 commits ahead of origin/develop, NOT pushed**
**Task:** #500 ECA A/B on the detector backbone (FloorYOLO) — 2 arms × 3 seeds ×
50 epochs on GPU, **3/6 rows landed**, run 4/6 training, ETA ≈19:35 EDT. #511
closed, #509 corrected, gap audit #514–#519 filed, #515 triaged.

## 1. Accomplished So Far

- **Resumed and verified the prior handoff against reality** — accurate to the
  minute (2/6 rows, run 3/6 at epoch 35, driver PID 828948, GPU 99%). No drift.
- **Refused to archive the handoff mid-matrix** and refreshed it instead. The
  resume procedure says remove it once the first action is done, but the matrix
  runs ~2.5h more and the file carries the #500 reporting rules and two live
  hazards. Deleting it would cause the staleness it exists to prevent.
- **Two methodology errors corrected, both of which produce false "lost work":**
  1. **`git cherry` is unusable here.** It matches per-commit patches and this
     repo squash-merges, so squash-landed work *always* reads as unmerged.
     `feat/detector-robustness-split-499` — #515's own worked example, provably
     landed, all four files byte-identical — reports **both** commits as unique
     (`+`). Any triage trusting `git cherry` flags all 95 branches.
  2. **Pass 1 of the triage was a false alarm.** It flagged 86/95 branches that
     had merely fallen *behind* develop. The directional test
     (`branch_blob == merge_base_blob`) returned `stale=0` everywhere, proving
     the filter earned nothing. Replaced with added-line coverage.
  - Correct method: paths touched vs merge-base → blob-SHA vs develop's
    **current** tip → added-line coverage %.
- **#515 triaged, comment posted, nothing deleted.** 95 branches in 4 tiers:
  **100% covered 16** (landed) · **1–99% 56** · **0% 14** (review queue) ·
  **deletions-only 9**. Calibrated the metric on three branches instead of
  trusting it: `fix/issue-21` (97%) confirmed landed ✅; `fix/issue-345` (0%) is
  11 lines since restructured — churn ❌; **`roadmap-part-ii` (0%) added a
  "Part II — Future directions" eight-persona-review backlog that develop's
  `ROADMAP.md` does not contain — likely genuinely unrecovered** ⚠️.
  Coverage **ignores deletions**, so even the 16 "safe" branches are not proven
  superseded. Not a delete-list.
- **`/tmp/opencode/verify_patch` proven to hold no unique work** — its 5-file
  uncommitted delta is md5-identical to stash `3fedbcb` (`904111322ec3a54…`),
  verified by content hash rather than numstat, and the stash's base commit
  *is* the worktree HEAD `d2718ee`. Left in place for human confirmation;
  `git worktree remove` (no `--force`) is safe.
- **Foreman abstained** on choosing the wait-window task (0.48 < 0.70; its own top
  pick was "refresh and stop"). Human chose #515.

## 2. Modified Files

Working tree clean; everything committed.

- `.handoff.md` — rewritten (this file). Two commits this session, both
  touching **only** this file: `8b02cab`, `1793aff`. **No code was touched.**
- `.handoff-archive/handoff-515-triage.md` — copy of the superseded handoff
  (untracked addition; commit with the next change).

## 3. Current Verification State

- **Suite: NOT run this session.** Last verified **981 passed, 2 skipped, 1
  xfailed** (984 collected) at `0511072`, confirmed by CI run `36479760128`
  (`Check test count` passed). Both commits since are handoff-only markdown, so
  the result stands in substance — but it is formally unverified *for this
  session*. Run in the **foreground** if needed; a backgrounded run once left an
  empty `.out` file and reported nothing.
- **Lint:** not re-run; no Python was modified, so `ruff check .` /
  `ruff format --check .` cannot have regressed. Last known clean.
- **CI:** green at `0511072`. `8b02cab` and `1793aff` are markdown-only and
  **not yet pushed** — `develop` is +2 over `origin/develop`. Use
  `gh run watch <id> --exit-status --interval 20`; `ci-wait` misreports
  FAILED for in-progress and successful runs.
- **Matrix — 3/6 rows landed:**

  | arm | seed | epochs | mAP50 | mAP50_95 | train_box_loss |
  |---|---|---|---|---|---|
  | yolo11n_baseline | 0 | 50 | 0.96547 | 0.87505 | 0.43498 |
  | yolo11n_eca | 0 | 50 | 0.96850 | 0.87837 | 0.43414 |
  | yolo11n_baseline | 1 | 50 | 0.97139 | 0.88419 | 0.42740 |

  Driver order is **seed-major** (`baseline_s0, eca_s0, baseline_s1, eca_s1,
  baseline_s2, eca_s2`). Runs 1–3 took 53, 49, 55 min (~50 min/run). Run 4/6
  `yolo11n_eca_s1` started 17:04:40. Driver alive (PID 828948, 2h38m), GPU 99%.
  All six ≈ **19:35 EDT**.
  - **ROCm/AMD box — `nvidia-smi` does not exist.** Use `rocm-smi --showuse`.
- **Open issues:** #500 (blocked on matrix), #507, #508, #509, #510, #512, #514
  (also blocked on matrix), #515 (triaged; 14-branch 0% queue remains),
  #516–#519.

## 4. Immediate Next Step

**Do not restart or edit the matrix.** Check progress, not results.

```bash
column -s, -t ~/workspace/datasets/detector_runs/ab_summary.csv   # one row per FINISHED run
tail -3 ~/workspace/datasets/detector_runs/ab_driver.log
```

`ab_summary.csv` is created on first completion; before that `column` errors
rather than printing an empty table. **Three rows is correct now.**

**If the driver died, do NOT blindly re-invoke** (#514): the summary append is
not tied to the skip branch, so resuming **duplicates rows** for every
already-complete run and corrupts the #500 statistics. Either verify no
duplicate rows after resuming, or wait for #514's fix. The driver is resumable;
its summary is not idempotent.

⚠️ **Stale-CSV hazard:** `yolo11n_baseline_s2` and `yolo11n_eca_s2` still hold
1-epoch `results.csv` from pre-launch validation (mtime 14:22 / 14:23). The skip
guard requires ≥50 rows so they will be overwritten — **but never read their
mAP50 as matrix data.** This exact hazard produced the bad #509 evidence.

🚫 **Do not edit `detector/train.py`, `detector/configs/`, or
`detector/run_ab_eca.sh` until all six rows exist** — the driver re-invokes
`train.py` per run, so a mid-matrix edit silently changes arms 2–6. Blocks #508
and #514. `detector/README.md` is *not* in that list.

**Reporting rules for #500** (do not skip): no delta before all six rows exist.
Per-arm **mean ± spread across all runs**, never a best-seed number. Compare the
between-arm difference against the **within-arm** spread; overlapping ranges →
"no delta measurable at this budget", a legitimate and better result than a
confident wrong number. Do not close as "no effect" without a measured spread.
The seed-0 rows differ by **+0.003 mAP50** for ECA — that is **not** a result;
one seed per arm carries almost no information. Do **not** use #509's old
0.05485/0.21352 as a noise floor (confound: different epochs ⇒ different LR
schedules). `detector/` is excluded from CI, so no detector result is verified
upstream — say so rather than implying a green run covers it.

**Safe work while the matrix runs** (none touch the files it reads): continue
**#515's 14-branch 0% queue** — pure reading, start with `roadmap-part-ii`
(likely real unrecovered content). Then #517 catalog rows + a guard test · #519
untrack stray artifacts · #510 venv provenance. **#518 needs a human decision,
not code.** #514 blocked on the matrix; **#516 needs the GPU** — queue after
#500 is reported. One at a time; the main suite's CPU cost slows the matrix.

**Also pending a human call:** `git worktree remove /tmp/opencode/verify_patch`
is now proven safe (no unique work) but was deliberately not executed; and
`develop` has 2 unpushed commits.
