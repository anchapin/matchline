# Tier B branch deletion manifest (#515, follow-up)

Generated 2026-10-06 against `origin/develop` at `2f00f73`.

## Scope

88 published (`origin/*`) branches deleted as the Tier B follow-up to #515.
Every entry below is recoverable with
`git push origin <sha>:refs/heads/<branch>`. None are local; all are
remote-only.

## Justification for bulk deletion (vs. the strict Tier A gate)

The Tier A safety gate in `docs/process/branch-hygiene.md` is the right rule
for "is this branch recoverable from a single deletion decision?" It checks
that develop's current tree contains every line the branch added, and zero of
the lines it removed. Tier A is the set of branches where that is exactly
true. Tier B is the set where at least one of those is false.

The user authorised the Tier B deletion on 2026-10-06 with the explicit
justification that **no issues and no PRs are open against any of these
branches** (verified: `gh issue list --state open` and
`gh pr list --state open` both returned empty). The deletion is therefore
not destroying in-flight work — it is closing out branches whose work has
already been resolved by other routes (squash, rename, or superseded by
later commits).

This is the use case the policy doc calls out in §3 ("the right unit of
cleanup"): a merge-time setting, not a per-quarter triage. This pass
performs the bulk cleanup once, and the repo setting will prevent the
recurrence.

## What Tier B looks like

The 88 branches split into three groups by coverage:

- **High-coverage-only (99.x% with zero untaken deletions) — 13 branches.**
  These are the closest to Tier A. They differ from Tier A by 1–3 lines per
  branch, almost always reworded doc strings or one-line refactors. They were
  excluded from the prior Tier A pass because the gate is binary: 100% or
  not. Re-derive after another merge cycle and several will likely join Tier A.
- **High-coverage-with-deletions — 7 branches.** `feat/walkout-basement`
  (16 untaken), `pr-83` (36), `fix/issue-77-ifc-complexity` (36),
  `issue-340` (13), `fix-issue-444-type-annotations` (42), `pr-83` (36),
  `fix/issue-72-pickle-security` (3), and others. Develop kept lines the
  branch removed; that is usually a later refactor re-adding them, not lost
  work, but the safety gate treats this as unsafe because the branch's
  intent contradicts develop's current state.
- **Low coverage, small diffs and the 0% churn set — 68 branches.** Mostly
  feature branches that landed by another route, plus a handful of
  0% coverage entries the prior triage hand-checked as churn
  (`fix/issue-19-pickle-rce`, `fix/issue-351`, `fix/issue-80-*`,
  `worker/526-ifcopenshell-pin`, `fix/issue-345`).

## Safety gate applied

1. **No open issues or PRs** — `gh issue list --state open` and
   `gh pr list --state open` both returned 0 results before the deletion.
   No branch is the head of an active PR; no branch carries work for an
   open issue.
2. **`git worktree list`** — no Tier B branch is checked out in any worktree
   (only `develop` is).
3. **No local copies** — every Tier B branch is remote-only; local refs
   contains only `develop` and `docs/500-eca-ab-results` (the active A/B
   matrix branch for #500, not in scope).
4. **Manifest written first** — this file is committed before any
   `git push origin :branch` runs. Every tip SHA is recorded so the
   deletion is recoverable with `git push origin <sha>:refs/heads/<name>`.

## Deleted (88 branches)

| branch | tip |
|---|---|
| `backup/uncommitted-2026-09-29` | `b6aa5a1` |
| `docs/190-191-156-plan-docs` | `32a1b58` |
| `e2e-pipeline-tests` | `bd13fe7` |
| `feat/atria` | `1c9db26` |
| `feat/appendix-g-levels` | `b6994c7` |
| `feat/appendix-g-zoning` | `04d93d5` |
| `feat/convention-report` | `a1d5328` |
| `feat/daylight-under-skylights` | `2316515` |
| `feat/daylight-zone-check` | `66976b1` |
| `feat/detector-robustness-split-499` | `aba6cba` |
| `feat/door-swing-detector` | `39c90a2` |
| `feat/ifc-door-semantics` | `c765237` |
| `feat/ifc-doors-closet-merge` | `a15fc32` |
| `feat/ifc-space-longname` | `af8e60a` |
| `feat/ifc-storey-elevation-fallback` | `431dc30` |
| `feat/ifc-unclaimed-wall-loops` | `5366f72` |
| `feat/ifc-wall-centreline-joins` | `a827953` |
| `feat/ifc-wall-linings` | `071786f` |
| `feat/ifc-wall-reference-line` | `fc19d01` |
| `feat/interstory-matching` | `d1d05be` |
| `feat/merge-closets-shafts` | `e06bc88` |
| `feat/openstudio-gate` | `ecb90a8` |
| `feat/plan-door-positions` | `5ba0db7` |
| `feat/real-doors-from-swings` | `68d4108` |
| `feat/space-type-accounting` | `e99a1d9` |
| `feat/synth-closets-shafts-doors` | `3b00c95` |
| `feat/synth-door-swing` | `819d5a9` |
| `feat/walkout-basement` | `0c9118e` |
| `fix-issue-308-run-checks-exceptions` | `fa05c9e` |
| `fix-issue-332-convexity-check` | `d51c7d9` |
| `fix-issue-405-review-bug` | `781ad7b` |
| `fix-issue-444-type-annotations` | `cf5e197` |
| `fix-issue-231-low-confidence-review` | `8e03449` |
| `fix-wave-1-conservation-export` | `7555c0f` |
| `fix-wave16-review-block` | `2bff927` |
| `fix/wave1-issues-496-495-494` | `eda8192` |
| `fix-wave16-roundtrip` | `07fbf76` |
| `fix/146-remove-pickle-deserialization` | `f811fc6` |
| `fix/146b-pickle-removal` | `2d3bb5f` |
| `fix/148-test-bem-export` | `6522861` |
| `fix/150-ifc-import-provenance` | `60f63ef` |
| `fix/150b-ifc-import-provenance` | `72060ed` |
| `fix/184-xxe-prevention` | `f0c51bf` |
| `fix/185-provenance-level` | `aa1e438` |
| `fix/202-geometry-simplify-defect-injection` | `e033117` |
| `fix/211-test-count-gate` | `d73c968` |
| `fix/234-defect-injection-space-area` | `ef18d86` |
| `fix/256-link-refactor` | `c4c0805` |
| `fix/257-convert-aec-coverage` | `7d99b74` |
| `fix/478-ifc-export-validation` | `4844ede` |
| `fix/add-datasets-md` | `8af79f9` |
| `fix/conservation-laws` | `315b69e` |
| `fix/identity-tiebreak` | `4cda146` |
| `fix/issue-15-xxe-hardening` | `c996d75` |
| `fix/issue-17-xxe-pytest` | `409713f` |
| `fix/issue-19-pickle-rce` | `85af410` |
| `fix/issue-20-syspath-injection` | `781ce49` |
| `fix/issue-21-write-gbxml-test` | `78dd86b` |
| `fix/issue-22-e2e-pytest` | `2e5f254` |
| `fix/issue-23-ifc-corruption-test` | `6b6fc52` |
| `fix/issue-27-coord-provenance` | `7bc60c4` |
| `fix/issue-270` | `64fab92` |
| `fix/issue-306-xfail-conservation` | `60da444` |
| `fix/issue-314-validate-type-hints` | `7aab2ee` |
| `fix/issue-316-review-queue-blocking` | `dbd464e` |
| `fix/issue-325-conservation-integration-tests` | `c46a49d` |
| `fix/issue-345` | `3c8d1b9` |
| `fix/issue-348-review-queue-pipeline-test` | `7330520` |
| `fix/issue-351` | `cfc2598` |
| `fix/issue-72-pickle-security` | `73bb40c` |
| `fix/issue-75-future-annotations` | `e40267b` |
| `fix/issue-76-link-registration-tests` | `2577a35` |
| `fix/issue-77-ifc-complexity` | `99dab0c` |
| `fix/issue-80-docs-polygon-classify` | `4811073` |
| `fix/provenance-none-and-ifc-silent-failures` | `81c7f3d` |
| `issue-340` | `384be8a` |
| `issue-340-conservation-test` | `da777b8` |
| `issue-347-defect-injection` | `0cafd32` |
| `pr-248-wave5` | `576b2d6` |
| `pr-83` | `1e7e4b4` |
| `quickstart-docs-readme` | `90baef7` |
| `review-queue-classifier-proto` | `72dc0e0` |
| `roadmap-data-flywheel` | `6ed2aff` |
| `roadmap-part-ii` | `13b7e1f` |
| `style/ruff-format-develop` | `be16655` |
| `wave5-69` | `f48c263` |
| `wave5-69-clean` | `290277e` |
| `worker/526-ifcopenshell-pin` | `f031b88` |

## Recovery

Per branch:

```bash
git push origin <tip-sha>:refs/heads/<branch-name>
```

For example, to restore `feat/atria`:

```bash
git push origin 1c9db26:refs/heads/feat/atria
```

The local repo picks up the new ref on the next `git fetch origin`. The
`scripts/triage_branches.py` script is idempotent — re-running after
restoration produces the same tier table.

## What was NOT deleted, and why

- **`origin/develop` and `origin/HEAD`** — never deleted.
- **`docs/500-eca-ab-results`** (local, unmerged) — the active A/B matrix
  branch for #500. Not in the audit scope of #515; not a remote ref.
