# Squash-merged-leftover branch deletion manifest (#515 final)

Generated 2026-10-06 against `origin/develop` at `17b5604`.

## Scope

42 published (`origin/*`) branches deleted as the third pass of #515. Every
entry is recoverable with `git push origin <sha>:refs/heads/<branch>`. None
are local; all are remote-only.

## Why these are different from Tier A and Tier B

The first two passes deleted branches that were **not** reachable from
`develop` via the merge base — branches whose work was either fully in
develop (Tier A) or partially missing (Tier B). This pass deletes a third
category:

- **Squash-merged leftovers** — branches whose head commit is reachable from
  `develop`. Their work landed (typically via merge commit or squash), and
  the head ref was not auto-deleted by GitHub because the repo's
  "Automatically delete head branches" setting is off.

`git merge-base --is-ancestor <branch> origin/develop` returns 0 for every
entry in this manifest. The strict Tier A gate (100% coverage, 0 untaken
deletions) does not apply to these because they are already ancestors of
develop — there is no merge base to diff against.

## Safety gate applied

1. **0 open issues, 0 open PRs** — `gh issue list --state open` and
   `gh pr list --state open` both returned 0 before the deletion. No branch
   is the head of an active PR; no branch carries work for an open issue.
2. **`git merge-base --is-ancestor <branch> origin/develop` returns 0** —
   the branch's work is in develop.
3. **`git worktree list`** — only `develop` is checked out; none of the 42
   are in any worktree.
4. **No local copies** — every entry is remote-only; local refs contain only
   `develop` and `docs/500-eca-ab-results` (active A/B matrix for #500).
5. **Manifest written first** — this file is committed before any
   `git push origin :branch` runs. Every tip SHA is recorded so the
   deletion is recoverable with `git push origin <sha>:refs/heads/<name>`.

## Deleted (42 branches)

| branch | tip |
|---|---|
| `fix-issue-475-post-export-validation` | `f6170f2` |
| `fix-issue-476-property-tests-vacuous` | `9648c95` |
| `fix-issue-477-polygon-classify-evidence` | `3aea267` |
| `fix/151-157-toctou-pickle` | `dc3e37f` |
| `fix/152-sha256-golden-integrity` | `34f3492` |
| `fix/153-cv2-lazy-import` | `2b80c1b` |
| `fix/154-remove-datasets-md-ref` | `17f3e8b` |
| `fix/158-canonical-cli` | `960671b` |
| `fix/159-agents-test-count` | `06af174` |
| `fix/268-test-invariants-coverage` | `6886dcc` |
| `fix/269-property-based-testing` | `961d493` |
| `fix/271-file-size-limits` | `95c1cfe` |
| `fix/276-docs-plans-index` | `0848e19` |
| `fix/277-docs-readme-coverage` | `e97c39e` |
| `fix/304-pipeline-validate-after-export` | `8c647f4` |
| `fix/313-ifc-roundtrip-test` | `319b4c3` |
| `fix/issue-10-review-queue-triage` | `7138994` |
| `fix/issue-103-validate-checks` | `9a41cfc` |
| `fix/issue-11-typed-decision-candidates` | `d8899a5` |
| `fix/issue-12-ci-workflow-consolidation` | `1d05479` |
| `fix/issue-121-pre-commit-fix` | `1c9d695` |
| `fix/issue-121-precommit-fut` | `a6ce436` |
| `fix/issue-123-defect-injection` | `a6ce436` |
| `fix/issue-29-provenance` | `304d189` |
| `fix/issue-30-cli-docs` | `71eea22` |
| `fix/issue-31-empty-plans` | `b7ed76b` |
| `fix/issue-32-pipeline-error-handling` | `d6ce3f1` |
| `fix/issue-320-ifc-infer-adjacency` | `25a2b23` |
| `fix/issue-33-auto-triage` | `7db287d` |
| `fix/issue-34-confidence-injection` | `8b1ca4c` |
| `fix/issue-343-auto-triage-coverage` | `35ddc19` |
| `fix/issue-343-auto-triage-coverage-v2` | `4825b2d` |
| `fix/issue-74-export-gate-test` | `214ffa6` |
| `fix/issue-9-laya-investigation` | `8e23add` |
| `issue/259-validate-refactor` | `5f1a5e6` |
| `roadmap-vector-native-detection` | `c15a2aa` |
| `wave10/330` | `7eae941` |
| `wave11/317-run-review-docstring` | `ccb589b` |
| `wave11/325-battery-conservation-laws` | `94baece` |
| `wave2/issue333` | `7eae941` |
| `wave3/241-ruff-fix` | `822d9d2` |
| `wave6/issue-230` | `3f8f3d5` |

## Recovery

Per branch:

```bash
git push origin <tip-sha>:refs/heads/<branch-name>
```

For example, to restore `fix/151-157-toctou-pickle`:

```bash
git push origin dc3e37f:refs/heads/fix/151-157-toctou-pickle
```

The local repo picks up the new ref on the next `git fetch origin`. The
`scripts/triage_branches.py` script excludes these from its output (since
they are reachable from develop), so a re-run after restoration will not
list them — recovery is a separate step, not a triage step.

## What was NOT deleted, and why

- **`origin/develop` and `origin/HEAD`** — never deleted.
- **`origin/main`** — releases only per the project AGENTS.md.
- **`docs/500-eca-ab-results`** (local, unmerged) — the active A/B matrix
  branch for #500. Not in the audit scope of #515; not a remote ref.

## Combined #515 cleanup totals

| pass | branches deleted | manifest |
|---|---|---|
| Tier A (strict 3-criterion gate) | 22 | `.handoff-archive/branch-deletion-515.md` |
| Tier B (bulk by user authorisation) | 88 | `.handoff-archive/branch-deletion-tierB-515.md` |
| Squash-merged leftovers (this pass) | 42 | `.handoff-archive/branch-deletion-leftovers-515.md` |
| **Total** | **152** | |

Pre-cleanup remote non-default branches: 154. Post-cleanup: 2
(`develop` and `main`). The 152 deleted branches are recoverable as long as
these three manifests are preserved.
