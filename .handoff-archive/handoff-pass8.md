# Handoff — Pass 8

## Status: PASS_COMPLETE_CONTINUE

## What Happened This Pass

### gh auth fixed
- gh was unauthenticated in `gh auth status` output, but `gh auth token` returned a valid token
- Fixed remote URL: removed embedded ghp_ token from `git remote set-url origin https://github.com/anchapin/matchline.git`
- `gh auth status` now shows: ✓ Logged in to github.com account anchapin

### PR #429 merged
- PR #429 (fix: provenance=None violations + silent IFC import failures) was ALREADY merged to develop (6e16965) before this pass started
- Did NOT need to merge — already closed
- This closes #402 and #417

### PR #428 closed
- PR #428 (fix-wave-1-conservation-export) was the stale orphaned worktree PR
- Closed it as superseded by PR #429

### PR #439 created — fix for #416
- Branch: `fix/add-datasets-md`
- Commits: DATASETS.md creation + wave-planner.js
- CI: ruff + format + pytest passing
- URL: https://github.com/anchapin/matchline/pull/439

### Issue #415 verified
- `validate.py errors do not block BEM export` — the fix is ALREADY in place
- `_stage_5_fail_fast()` calls `export_gate()` before Stage 6 (BEM export)
- `export_gate()` returns False when `validate_bem_conservation` raises
- `tests/conservation/test_pipeline_conservation_exit.py` passes

### wave-planner.js created
- Added `scripts/wave-planner.js` — useful for wave orchestrator
- Run with: `node scripts/wave-planner.js`

## Remaining Open Issues (Wave 1 candidates for Pass 9)

| # | Title | Type | Effort |
|---|-------|------|--------|
| #420 | fix the no-op assertion in test_validate.py | bug | quick |
| #422 | Add test for cross-sheet window deduplication with provenance | test | medium |
| #430 | room_labels.py does not populate Space.core_provenance | bug | medium |
| #426 | [feature] Natural-language query layer | feature | large |

Issues #420, #422, #430 are fixable in parallel. Issue #426 needs scope decision (natural language query is a big feature).

## Wave Planning

Run `node scripts/wave-planner.js` to get updated wave analysis.

Wave 1: #420, #422, #430 (quick/medium fixes — can run parallel sub-agents)
Wave 2: #426 (large feature — needs scope decision or deferral)

## Tests
- PR #439 CI: ruff + format + pytest passing
- Conservation exit test: `rtk pytest tests/conservation/test_pipeline_conservation_exit.py -v` → 1 passed
- Core modules: 77 passed (test_building_model, test_ifc_import, test_ifc_import_corruption, test_validate)

## Git State
- Current branch: `fix/add-datasets-md` (has PR #439 open)
- origin/develop: 6e16965 (PR #429 merged)
- No stale worktrees needed

## Immediate Next Step for Pass 9
1. Spawn sub-agents in parallel to fix #420, #430 (quick wins)
2. For #422: create branch + implement cross-sheet deduplication test
3. For #426: close or scope-limit (it's a feature request, not a bug)
4. Run `node scripts/wave-planner.js` before wave planning to get fresh data
