# Published-branch triage manifest (#515)

Generated 2026-09-30 by `scripts/triage_branches.py` against `origin/develop` at `2dfb191`.
Re-derived 2026-10-06 against `origin/develop` at `a4e63ee`; Tier A still 22
branches (one addition: `docs/roadmap-atria-walkout` at 473f986). Tier B
expanded from 61 to 88 as the manifest caught up with later merges.

## Tier B deletion performed 2026-10-06 (follow-up)

All 88 Tier B branches deleted from `origin` (none were local). Recovery SHAs
recorded in `.handoff-archive/branch-deletion-tierB-515.md`. The bulk
deletion was authorised by the user on the explicit justification that
**0 open issues, 0 open PRs** — `gh issue list --state open` and
`gh pr list --state open` both returned empty before the deletion. The strict
Tier A gate (100% coverage, 0 untaken deletions) excludes Tier B, so this is
a bulk deletion by user authorisation rather than the strict gate.

The 42 remaining remote branches (not in the original Tier A or Tier B) are
**squash-merged leftovers**: branches whose head commit is reachable from
`develop` (typically via merge commit, not squash). Their work is in develop;
their refs were not auto-deleted by GitHub because the repo's "Automatically
delete head branches" setting is off. They are tracked separately and not
in the audit scope of #515.

## Tier A deletion performed 2026-10-06

All 22 Tier A branches deleted from `origin` (none were local). Recovery SHAs
recorded in `.handoff-archive/branch-deletion-515.md`.


## Scope

**Published (`origin/*`) branches only.** #515 counted 147 local branches, 95
unmerged, of which 51 were said to be on origin. Measured from a fresh clone,
origin carries **126 non-default branches, 83 of them unmerged** into develop
(43 are already ancestors and need no triage). The local-only remainder is not
visible from a clone and stays with the workstation that holds it.

## Method

Per the correction recorded on #515: `git diff develop...branch` (three-dot)
measures from the merge base, so squash-landed work still shows every line as
an addition, and `git cherry` reports ~all branches unique because this repo
squash-merges. Neither is used here.

For each branch: take the paths it touched vs its merge-base, compare each
path's blob SHA against develop's **current** tip, and measure what fraction of
the branch's added lines are present in develop's current file.

**This pass also closes the metric gap the last one flagged.** Added-line
coverage ignores deletions, so a branch could score 100% while develop still
contained lines the branch removed. `deletions_not_taken` counts exactly those,
and a branch is only called clean when it is zero.

## Nothing here is deleted

This is a record, not an action. Every tip SHA is written down so any branch can
be restored after deletion (`git branch <name> <sha>`). Per #515, deletion needs
a `git worktree list` check first: a branch checked out in a worktree is the
#511 near-miss, and that check can only run on the workstation.


## Tier A: safe to delete (22 branches)

100% of added lines present in develop, and no deletions develop failed to take.

| branch | tip | files |
|---|---|---|
| `detector-deps-pin` | `7fc50b4` | 1 |
| `fix/186-path-traversal` | `ef5ae2b` | 1 |
| `fix/258-safe-xml-docs` | `b576868` | 1 |
| `fix/conservation-laws` | `315b69e` | 2 |
| `fix/issue-105-ifc-export` | `e671952` | 1 |
| `fix/issue-16-run-pipeline-pytest` | `d38cc8c` | 1 |
| `fix/issue-18-datasets-adapter-docs` | `e851a7e` | 1 |
| `fix/issue-24-polygon-ifc-docs` | `345a0a5` | 2 |
| `fix/issue-25-detector-contract` | `2200a3b` | 4 |
| `fix/issue-26-auto-triage-scope` | `a61a3e8` | 3 |
| `fix/issue-278` | `7d9acba` | 1 |
| `fix/issue-28-from-json` | `5a7542d` | 1 |
| `fix/issue-312-bem-roundtrip-battery` | `b0feb31` | 1 |
| `fix/issue-313-ifc-roundtrip-battery` | `cb2d8a3` | 2 |
| `fix/issue-342-pipeline-review-blocking-test` | `ffa41b8` | 1 |
| `fix/issue-78-review-classifier-docs` | `4b5703a` | 2 |
| `fix/issue-78-review-classifier-docs-v2` | `1ac3bbd` | 2 |
| `pr90` | `9a751f7` | 2 |
| `wave6-67` | `8f26ec8` | 1 |
| `worker/514-run-ab-eca-idempotent` | `53ec31b` | 1 |
| `worker/517-docs-readme-catalog` | `169b97f` | 1 |
| `worker/519-gitignore-tracked` | `d197b40` | 26 |

## Tier B: needs eyes (88 branches, deleted 2026-10-06)

Deleted in the Tier B follow-up pass; see the "Tier B deletion performed"
note above for details. The table below is preserved as the historical record
of what was triaged and recovered. Recovery: see
`.handoff-archive/branch-deletion-tierB-515.md`.

Re-derived 2026-10-06 against `a4e63ee`. Ordered by coverage, descending.
`del_untaken` = lines this branch removed that develop still has; `absent` =
touched paths that do not exist in develop at all (a restructure, or genuinely
unlanded work).

| branch | coverage | del_untaken | absent | tip |
|---|---|---|---|---|
| `feat/atria` | 99.8% | 0 | 0 | `1c9db26` |
| `feat/detector-robustness-split-499` | 99.8% | 0 | 0 | `aba6cba` |
| `review-queue-classifier-proto` | 99.8% | 0 | 0 | `72dc0e0` |
| `feat/appendix-g-levels` | 99.7% | 0 | 0 | `b6994c7` |
| `feat/real-doors-from-swings` | 99.7% | 0 | 0 | `68d4108` |
| `feat/daylight-under-skylights` | 99.6% | 0 | 0 | `2316515` |
| `feat/ifc-storey-elevation-fallback` | 99.5% | 1 | 0 | `431dc30` |
| `feat/ifc-door-semantics` | 99.4% | 0 | 0 | `c765237` |
| `fix/issue-316-review-queue-blocking` | 99.4% | 0 | 0 | `dbd464e` |
| `style/ruff-format-develop` | 99.3% | 0 | 0 | `be16655` |
| `feat/openstudio-gate` | 99.1% | 0 | 0 | `ecb90a8` |
| `feat/walkout-basement` | 99.1% | 16 | 0 | `0c9118e` |
| `fix/257-convert-aec-coverage` | 99.1% | 0 | 0 | `7d99b74` |
| `fix/identity-tiebreak` | 99.1% | 0 | 0 | `4cda146` |
| `fix/issue-76-link-registration-tests` | 99.1% | 0 | 0 | `2577a35` |
| `feat/ifc-wall-reference-line` | 98.7% | 2 | 0 | `fc19d01` |
| `fix/issue-325-conservation-integration-tests` | 98.7% | 0 | 0 | `c46a49d` |
| `issue-340` | 98.5% | 13 | 0 | `384be8a` |
| `roadmap-part-ii` | 98.5% | 0 | 0 | `13b7e1f` |
| `feat/appendix-g-zoning` | 98.4% | 0 | 0 | `04d93d5` |
| `feat/interstory-matching` | 97.9% | 0 | 0 | `d1d05be` |
| `feat/ifc-wall-linings` | 97.7% | 1 | 0 | `071786f` |
| `feat/convention-report` | 97.6% | 0 | 0 | `a1d5328` |
| `feat/ifc-wall-centreline-joins` | 97.6% | 0 | 0 | `a827953` |
| `fix/conservation-laws` | 97.6% | 0 | 0 | `315b69e` |
| `fix/issue-17-xxe-pytest` | 97.6% | 0 | 0 | `409713f` |
| `fix/issue-21-write-gbxml-test` | 97.2% | 0 | 0 | `78dd86b` |
| `fix/issue-306-xfail-conservation` | 97.0% | 0 | 0 | `60da444` |
| `feat/plan-door-positions` | 96.9% | 0 | 0 | `5ba0db7` |
| `e2e-pipeline-tests` | 96.7% | 0 | 0 | `bd13fe7` |
| `fix-issue-405-review-bug` | 96.6% | 0 | 0 | `781ad7b` |
| `feat/ifc-unclaimed-wall-loops` | 96.3% | 0 | 0 | `5366f72` |
| `feat/ifc-space-longname` | 95.8% | 0 | 0 | `af8e60a` |
| `feat/merge-closets-shafts` | 95.4% | 0 | 0 | `e06bc88` |
| `wave5-69` | 95.2% | 6 | 0 | `f48c263` |
| `fix/issue-23-ifc-corruption-test` | 94.5% | 0 | 0 | `6b6fc52` |
| `pr-248-wave5` | 94.1% | 0 | 0 | `576b2d6` |
| `feat/synth-closets-shafts-doors` | 93.8% | 0 | 0 | `3b00c95` |
| `feat/daylight-zone-check` | 93.6% | 0 | 0 | `66976b1` |
| `wave5-69-clean` | 93.5% | 0 | 0 | `290277e` |
| `fix-issue-231-low-confidence-review` | 91.7% | 0 | 0 | `8e03449` |
| `fix/148-test-bem-export` | 91.5% | 0 | 0 | `6522861` |
| `feat/space-type-accounting` | 91.2% | 0 | 0 | `e99a1d9` |
| `fix/issue-22-e2e-pytest` | 90.4% | 0 | 0 | `2e5f254` |
| `fix/234-defect-injection-space-area` | 90.2% | 0 | 0 | `ef18d86` |
| `fix/202-geometry-simplify-defect-injection` | 90.0% | 0 | 0 | `e033117` |
| `fix/issue-72-pickle-security` | 89.9% | 3 | 0 | `73bb40c` |
| `fix/issue-270` | 88.9% | 0 | 0 | `64fab92` |
| `issue-340-conservation-test` | 88.5% | 0 | 0 | `da777b8` |
| `feat/ifc-doors-closet-merge` | 87.4% | 0 | 0 | `a15fc32` |
| `fix/256-link-refactor` | 87.4% | 0 | 1 | `c4c0805` |
| `pr-83` | 87.4% | 36 | 0 | `1e7e4b4` |
| `fix/issue-77-ifc-complexity` | 87.0% | 36 | 0 | `99dab0c` |
| `fix-issue-308-run-checks-exceptions` | 86.5% | 0 | 0 | `fa05c9e` |
| `quickstart-docs-readme` | 85.7% | 0 | 0 | `90baef7` |
| `fix/wave1-issues-496-495-494` | 84.0% | 1 | 0 | `eda8192` |
| `fix-issue-444-type-annotations` | 83.3% | 42 | 30 | `cf5e197` |
| `fix/issue-348-review-queue-pipeline-test` | 82.8% | 0 | 1 | `7330520` |
| `fix/issue-75-future-annotations` | 72.7% | 0 | 4 | `e40267b` |
| `feat/synth-door-swing` | 71.3% | 1 | 0 | `819d5a9` |
| `fix/211-test-count-gate` | 70.7% | 0 | 0 | `d73c968` |
| `feat/door-swing-detector` | 68.9% | 0 | 0 | `39c90a2` |
| `docs/190-191-156-plan-docs` | 67.8% | 0 | 0 | `32a1b58` |
| `fix/184-xxe-prevention` | 66.7% | 0 | 0 | `f0c51bf` |
| `fix/add-datasets-md` | 60.1% | 1 | 2 | `8af79f9` |
| `fix/provenance-none-and-ifc-silent-failures` | 59.3% | 4 | 0 | `81c7f3d` |
| `fix/146b-pickle-removal` | 57.1% | 2 | 0 | `2d3bb5f` |
| `fix/146-remove-pickle-deserialization` | 47.1% | 2 | 0 | `f811fc6` |
| `fix/150-ifc-import-provenance` | 45.8% | 2 | 0 | `60f63ef` |
| `fix/150b-ifc-import-provenance` | 41.7% | 0 | 0 | `72060ed` |
| `fix-wave-1-conservation-export` | 38.8% | 0 | 0 | `7555c0f` |
| `fix-wave16-review-block` | 27.6% | 0 | 0 | `2bff927` |
| `fix/issue-15-xxe-hardening` | 25.0% | 0 | 0 | `c996d75` |
| `fix-wave16-roundtrip` | 18.2% | 2 | 0 | `07fbf76` |
| `fix/478-ifc-export-validation` | 13.8% | 1 | 0 | `4844ede` |
| `fix/issue-345` | 10.0% | 0 | 0 | `3c8d1b9` |
| `fix/issue-27-coord-provenance` | 8.3% | 0 | 0 | `7bc60c4` |
| `fix/185-provenance-level` | 6.7% | 9 | 2 | `aa1e438` |
| `issue-347-defect-injection` | 2.1% | 0 | 1 | `0cafd32` |
| `backup/uncommitted-2026-09-29` | 0.0% | 78 | 2 | `b6aa5a1` |
| `fix-issue-332-convexity-check` | 0.0% | 2 | 0 | `d51c7d9` |
| `fix/issue-19-pickle-rce` | 0.0% | 0 | 0 | `85af410` |
| `fix/issue-20-syspath-injection` | 0.0% | 6 | 1 | `781ce49` |
| `fix/issue-314-validate-type-hints` | 0.0% | 0 | 1 | `7aab2ee` |
| `fix/issue-351` | 0.0% | 0 | 0 | `cfc2598` |
| `fix/issue-80-docs-polygon-classify` | 0.0% | 0 | 0 | `4811073` |
| `roadmap-data-flywheel` | 0.0% | 0 | 0 | `6ed2aff` |
| `worker/526-ifcopenshell-pin` | 0.0% | 0 | 0 | `f031b88` |

## Reading Tier B

Three groups, and only the first is interesting:

- **`backup/uncommitted-2026-09-29` (0%, 78 untaken deletions, 2 paths absent
  from develop)** is the one branch on this list whose name says it is not a
  feature branch. It is the single highest-value thing to look at before
  anything is deleted. Hand-check, not delete on metrics.
- **High-coverage-with-deletions** (`feat/walkout-basement` 99.1%/16,
  `fix/issue-77-ifc-complexity` 87.0%/36, `pr-83` 87.4%/36, `issue-340` 98.5%/13,
  `fix-issue-444-type-annotations` 83.3%/42) are near-landed work where
  develop kept lines the branch removed. That is usually a later refactor
  re-adding them, not lost work, but the deletions are what the previous metric
  could not see.
- **High-coverage-only** (the 13 entries at 99.x% with zero untaken deletions)
  are the closest to safe. They differ from Tier A by 1–3 lines per branch,
  almost always reworded doc strings or one-line refactors. They were excluded
  from the deletion pass because the issue's safety gate is binary: 100% or
  not. Re-derive after another merge cycle and several will likely join Tier A.
- **Low coverage, small diffs** (`fix/issue-351`, `fix/issue-80-*`,
  `worker/526-ifcopenshell-pin`, `fix/issue-345`) match the churn verdicts
  already hand-checked on #515. `roadmap-part-ii` (98.5%) and
  `fix/issue-343-auto-triage-coverage` were recovered in `0ef8c98`, which is
  why the former now scores high rather than 1.2%.

## The policy question #515 raises

The issue asks whether a branch whose issue is closed and whose content is
byte-identical to develop has any reason to exist. If the answer is no, the fix
is a merge-time branch-deletion policy (GitHub repo setting: "Automatically
delete head branches"), not 83 deletions followed by the same drift next month.
That setting is the cheaper maintainer of this cleanup, and it is one checkbox.

