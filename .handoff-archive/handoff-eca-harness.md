# Session Handoff Checkpoint

**Timestamp:** 2026-09-28T16:35:00-04:00
**Branch:** `develop` (clean, in sync with `origin/develop` @ `0f488e7`)
**Task:** Resume-from-handoff triage of the stale Pass 8 handoff, then clear the real backlog (#498, #500) and set up the detector venv.

## 1. Accomplished So Far

**Stale handoff retired.** `.handoff.md` (Pass 8) was obsolete — its wave-1 items (#420, #422, #430) were all closed 2026-09-25, and `develop` had advanced `6e16965 → 124b889`. Archived to `.handoff-archive/handoff-pass8.md` (committed `0f488e7`, pushed). Original was tracked in the repo, so it was archived rather than deleted.

**#498 — SALI-FP research → closed as reference-only.** Verdict routed through Foreman (0.96 confidence). The relation-record schema fails adoption on three counts: the paper never defines a relation taxonomy (`G = (ℛ, E_G)` is the whole thing; "relation types" appears zero times in the full text), it explicitly disclaims door-room adjacency — *"measures centerline coverage, not door-room adjacency"*, the exact dimension `bem_export.py` consumes — and relations are decoupled from geometry (*"referenced but not recomputed, so repaired geometry does not establish corrected topology"*), which is hostile to our provenance-coupled model where `validate.py` conservation errors block export. Separately, their simplification protocol selects for compactness (*"Fewest vertices; then bytes; then tolerance"*, Table B.3) while ours selects by a hard 2% **area budget** — opposed criteria, not merely different. Code release: confirmed none (all "released" hits are other papers' baselines). Salvaged the useful part → **#502**.

**#500 — ECA experiment: venv up, harness built, mAP delta NOT measured.** See blockers. Delivered on `feature/detector-eca-500` (commit `c42e48a`, PR **#503**, MERGEABLE).

**Three real bugs found and fixed along the way** (details in #500 comment and PR #503):
1. **Ultralytics' name-based weight loading is unsafe once layers are inserted.** ECA rows shift every later index → 23 of 28 positions misalign. `YOLO.load('yolo11n.pt')` reports "Transferred 52/503 items", which would have left the ECA arm training from scratch while the control arm started from COCO — measuring initialization, not attention, at ~12h of CPU. Fixed with `transfer_aligned` (skips inserted blocks, pairs the rest by forward order). Verified bit-identical to Ultralytics' own `load()` on an unmodified architecture (499/499 tensors) and confirmed both arms start from identical COCO weights (2,560,305 params, 98.8%; the 51 mismatched tensors are the `nc` 80→2 Detect head).
2. **The detector venv was not installable on any fresh machine.** `requirements-detector.txt` pinned `torch==2.14.0+cpu` (needs `--extra-index-url https://download.pytorch.org/whl/cpu`, never stated) and `torchvision==0.29.0+cpu.stub` — a hand-written NMS shim never published to any index. The "broken C++ extension" in the README was sandbox-specific: stock `torchvision==0.29.0+cpu` works here and `torchvision.ops.nms` returns correct results. From-scratch install now verified (exit 0).
3. **`configs/cubicasa.yaml` hardcoded `/home/hatch/workspace/...`**, a machine path violating the AGENTS.md never-list. Now resolved in `train.py` via `MATCHLINE_DATASETS` / `~` / relative, failing with the path it tried.

**#504 filed** — `tests/test_ifc_roundtrip.py::test_cross_sheet_dedup_via_roundtrip` is a flaky non-strict xfail (flips xpass/xfail 4/8 runs **on the unmodified tree**, verified by stashing). It caused the `1 xpassed` drift in this session's main-suite run; confirmed NOT caused by this work.

## 2. Modified Files

All changes are committed on `feature/detector-eca-500` (PR #503). **`develop` is clean — nothing uncommitted.**

- `detector/eca.py` *(new, 253 lines)*: ECA block (ECA-Net) with adaptive odd `k`; `register_eca()`; `transfer_aligned()` — the structure-aware loader.
- `detector/configs/yolo11n_eca.yaml` *(new)*: yolo11n backbone + 4 ECA rows at indices 3/6/9/12; head concat refs remapped. Measured: **+18 params (+0.0007%)**, GFLOPs +0.0001%, CPU fwd latency +3.0% at 640px.
- `detector/configs/yolo11n_baseline.yaml` *(new)*: control arm, yolo11n at `nc: 2`. **Required** — comparing against stock `yolo11n.yaml` (`nc: 80`) confounds ECA against Detect-head width.
- `detector/tests/test_eca.py` *(new, 28 tests)*: happy path, invariants, defect injection, plus the index-shift regression test. Lives in the detector venv, not `tests/`, because torch/ultralytics are an optional `detector` extra.
- `detector/train.py`: `--pretrained` flag, `_register_custom_modules`, `_load_pretrained` (aligned transfer), `_resolve_data_yaml` (MATCHLINE_DATASETS / `~` / relative).
- `detector/requirements-detector.txt`: `--extra-index-url` documented as required; `.stub` pin → stock `0.29.0+cpu`; pytest marked test-only.
- `detector/README.md`, `detector/QUICKSTART.md`: ECA A/B docs, retracted the stale torchvision note, limitations section.
- `detector/configs/cubicasa.yaml`: machine path removed.
- `.gitignore`: ignore auto-downloaded `/yolo11*.pt`.
- `.handoff-archive/handoff-pass8.md` *(renamed from `.handoff.md`)*, committed on `develop` as `0f488e7`.

## 3. Current Verification State

- **Linter:** `ruff check .` → All checks passed. `ruff format --check .` → 214 files already formatted. (`detector/` is excluded per `pyproject.toml:79`, so `eca.py`/`train.py`/`test_eca.py` are not ruff-checked — by project design, but worth knowing.)
- **Main suite:** `python3 -m pytest tests/ -q` → **935 passed, 2 skipped, 1 xfailed, 1 xpassed** (152s). The xpass is the pre-existing flaky xfail of #504, not a regression.
- **Detector tests:** `detector/.venv-det/bin/python -m pytest detector/tests -q` → **28 passed**.
- **Env smoke:** both A/B arms train end to end (2 epochs on a synthetic smoke dataset at `/tmp/opencode/ds_smoke`, weights saved) — proves plumbing, not accuracy.
- **PR #503 CI:** `Sourcery review` skipped, `detect empty commits` still **pending** at handoff time. Re-check before merging.
- **Detector venv:** present and working at `detector/.venv-det` (torch 2.14.0+cpu, torchvision 0.29.0+cpu, pytest).

## 4. Immediate Next Step

**Review and merge PR #503** — it is the only unmerged work, and nothing else can proceed without it. From `develop`:

```bash
gh pr checks 503          # confirm "detect empty commits" finished green
gh pr view 503            # read the diff
gh pr merge 503 --squash  # or --merge; then delete the branch
```

Then, in priority order:

1. **#500 measurement is blocked — needs a human decision, not more work.** Both prerequisites are unmet: no dataset at `~/workspace/datasets/` (CubiCasa5K absent), and the control run has never completed (README shows "Full training run … in progress, ~6h CPU" unchecked since 2026-09-19; the only recorded `mAP50=0.65` is a 3-epoch/400-image plumbing check, not a baseline). **Do not quote an mAP delta or close #500 as "no effect"** — nothing has been measured. Ask the user whether to (a) obtain CubiCasa5K and run the A/B, (b) close #500 as infrastructure-only, or (c) defer. When it does run, hold seed/split/epochs/init constant and use 3+ seeds — a single seed cannot support a ±1pt mAP50 claim.

2. **#502** (oblique-boundary + circulation-continuity fixtures for `tests/test_geometry_simplify.py`) is self-contained and unblocked — the best candidate for the next real implementation task. It has 8 tests today and zero such cases.

3. **Housekeeping, all destructive — do not do these without explicit user approval:**
   - 6 worktrees: `/home/alex/Projects/worktree-pr428` (closed PR #428 branch), `worktrees/issue-340`, `worktrees/wave15-dx-docs`, plus 3 prunable `/tmp` ones (`develop`, `issue-340`, `wt-328-check`).
   - 12 stashes dated 2026-09-23…27, all pre-existing (none from this session).

**Open items:** PR #503; issues #500, #502, #504.

**Note on `AGENTS.md`:** it states "~699 tests" but the real count is 935. The doc has drifted; worth a separate correction.
