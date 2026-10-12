# Branch deletion manifest (#515)

Generated 2026-10-06 by the deletion pass on issue #515, against `origin/develop`
at `a4e63ee` (HEAD at time of run).

## Scope

22 published (`origin/*`) branches deleted. Every entry below is recoverable with
`git push origin <sha>:refs/heads/<branch>` — the SHA is the tip, recorded before
deletion. None are local; all were remote-only.

## Safety gate applied per branch (per #515)

1. `git worktree list` — none of the 22 are checked out in any worktree.
2. `git diff develop...branch` (three-dot) **not** used as the safety check — it
   measures from the merge base, so squash-landed work still shows every line as
   an addition. `feat/detector-robustness-split-499` is the worked example: 665
   lines of "missing" work, all four files byte-identical to develop.
3. `git cherry` **not** used — the repo squash-merges, so it reports every branch
   unique. Same example reports both commits as `+`.
4. Per branch: paths touched vs merge-base → blob SHA vs develop's **current** tip
   → measure what fraction of the branch's added lines are present in develop's
   current file. A branch is safe to delete only if 100% coverage **and** zero
   untaken deletions (develop's current file lacks no line the branch added, and
   still has no line the branch removed).

The script that produced the tier table is `scripts/triage_branches.py`. The
re-derivation is in `.handoff-archive/branch-triage-515.md`.

## Deleted (22 branches)

| branch | tip | files |
|---|---|---|
| `detector-deps-pin` | `7fc50b4` | 1 |
| `docs/roadmap-atria-walkout` | `473f986` | 1 |
| `fix/186-path-traversal` | `ef5ae2b` | 1 |
| `fix/258-safe-xml-docs` | `b576868` | 1 |
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

## Recovery

Per branch:

```bash
git push origin <tip-sha>:refs/heads/<branch-name>
```

For example, to restore `fix/186-path-traversal`:

```bash
git push origin ef5ae2b:refs/heads/fix/186-path-traversal
```

The local repo will pick up the new ref on the next `git fetch origin`. The
`scripts/triage_branches.py` run is idempotent: re-running after restoration
should produce the same tier table, and a future re-derivation will see the
restored branch on origin.

## What was NOT deleted, and why

- **`docs/500-eca-ab-results`** (local, unmerged) — the active A/B matrix
  branch for #500. Not in the audit scope of #515; not in Tier A.
- **Tier B branches (61) — coverage < 100%** — every line of the branch's
  additions is not in develop. Some are churn (rewritten files), some are
  high-coverage-with-deletions, one (`backup/uncommitted-2026-09-29`, 78
  untaken deletions, 2 paths absent from develop) needs eyes first. See
  `.handoff-archive/branch-triage-515.md` for the full Tier B breakdown.
- **Tier C (local unmerged besides `docs/500-eca-ab-results`)** — none: the
  local repo has only `develop` and the active A/B branch.
- **`origin/develop` and `origin/HEAD`** — never deleted.

## Methodological notes

The "100% coverage" definition is a content-hash check, not a three-dot diff:

```text
for each path P in branch.touched:
  branch_blob = rev-parse <branch>:P
  dev_blob    = rev-parse <origin/develop>:P
  if branch_blob == dev_blob: P is identical — definitely safe
  else:
    dev_lines = set(get-file origin/develop:P)
    for each "+line" in git diff <merge-base> <branch> -- P:
      if "+line" in dev_lines: counted as present
      else: counted as missing
```

The branch is safe iff the missing count is zero. Deletions are checked the
same way: a deletion is "untaken" if develop's current file still contains the
deleted line. A branch with 100% added coverage AND any untaken deletion is
**not** safe — develop kept the deletion's target, so the branch's intent
contradicts develop's.

A subtle point: a branch can score 100% with files that have been substantively
rewritten. Example: `worker/519-gitignore-tracked` differs on 26 files in
develop, but develop's `.gitignore` is byte-identical to the branch's (verified
md5) and the 23 files the branch `git rm --cached` are absent from develop's
tree. That is the right kind of "100%" — the branch's substantive content
landed. A wrong kind of "100%" would be a branch that added lines that happen
to appear in develop's rewritten file by coincidence, in a different context.
The 22 above were spot-checked and the safety is by content, not by string
overlap.
