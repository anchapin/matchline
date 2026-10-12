# Branch hygiene

This repo squash-merges. That has consequences for branch state hygiene that
do **not** apply to repos that rebase-merge or merge-commit. The accident
record for #515 / #511 is in `.handoff-archive/branch-triage-515.md` and
`.handoff-archive/stash-manifest.md`; the rules below are the policy the
triage established.

## 1. The merge model

Matchline's `main` and `develop` are squash-only. A branch's commits never
land as-is — they collapse into a single commit on the destination branch.
The branch's history is then unreachable from the destination and from any
later branch.

That has two consequences:

1. **Three-dot diffs lie.** `git diff develop...branch` measures from the
   *merge base*, so a branch whose work has already landed by a squash still
   shows every line as an addition. `feat/detector-robustness-split-499` is
   the worked example: 665 lines of "missing" work on a closed issue, all
   four files byte-identical to `develop`. Compare against `develop`'s
   *current tree*, or hash files, before calling anything lost.
2. **`git cherry` is unusable for "is this branch merged?"** It matches
   per-commit patches; a squash-merged branch's per-commit patches are not in
   `develop`. Every squash-merged branch reads as unmerged under
   `git cherry develop`. Use the script in §4 instead.

## 2. The deletion safety gate

Before deleting a branch, in order:

1. **`git worktree list`** — a branch checked out in a worktree is the #511
   near-miss. `git branch -D` will refuse; `git worktree remove --force`
   will destroy the only unstashed copy. This check can only run on the
   workstation, not from CI.
2. **Compare touched paths against `develop`'s current tip, not the merge
   base.** For each path the branch changed, compare the branch's blob SHA
   to `develop`'s blob SHA. A path is "covered" if `develop`'s current file
   contains every line the branch added and zero of the lines the branch
   removed. A branch is safe to delete only if every touched path is
   covered.
3. **Save the tip SHA to a manifest before pushing the deletion.** A branch's
   ref is the only thing pointing at its tip commit; once the ref is gone
   `git gc` will eventually reclaim the commit. The manifest is what makes
   deletion recoverable. The format used for #515 is in
   `.handoff-archive/branch-deletion-515.md`; every entry has a recovery
   command of the form `git push origin <sha>:refs/heads/<branch>`.

Coverage on its own is not enough. A branch can score 100% with files that
have been substantively rewritten; what matters is whether `develop` actually
contains the branch's content, not whether it happens to contain the same
lines by coincidence. Spot-check at least one touched path per branch by
hand before pushing.

## 3. The right unit of cleanup

#515 found 95 local unmerged branches, 51 of them on `origin`. The cleanup
cost is not linear: triage scales with branches, but the underlying
*invisibility* of dead refs is the problem. A branch-deletion policy at
merge time — a GitHub repo setting, not a per-quarter hygiene pass — is the
cheaper maintainer of this cleanup.

The setting in question is **Settings → General → Pull Requests → "Automatically
delete head branches."** When enabled, GitHub deletes the head ref the moment
the PR merges. There is no per-quarter triage to forget, no
state-vs-issue-tracker drift, and the #511 worktree trap is unreachable
because the branch is gone before anyone can check it out and forget to
unstash.

Enable it.

## 4. The triage script

`scripts/triage_branches.py` is the codified safety gate from §2. It does not
trust `git diff develop...branch` or `git cherry`. For each `origin/*` branch
not reachable from `develop`:

1. Finds paths touched vs the merge-base
2. Compares each path's blob SHA against `develop`'s *current* tip
3. Computes added-line coverage — fraction of the branch's added lines that
   exist in `develop`'s current file
4. Computes deletions-not-taken — lines the branch removed that `develop`
   still has
5. Outputs JSON; classify by hand

The script is idempotent. Re-run after every merge cycle. Branches that move
into Tier A as a result of a new merge become deletion candidates the next
time the manifest is updated.

## 5. When not to delete

- **Active work** — branches in flight for an open issue with a non-closed
  PR. `git worktree list` first.
- **Coverage < 100% with deletions** — develop kept lines the branch removed.
  The branch's intent contradicts develop's. Hand-check; usually a later
  refactor re-added them, but the safety gate treats this as unsafe.
- **Coverage < 100% with content** — the branch added lines develop does not
  have. Either genuinely unlanded or a churn/rebase artifact. Read the diff.
- **Anything named `backup/*`** — by name, these are defensive copies. The
  one on this list (`backup/uncommitted-2026-09-29`) was 78 untaken
  deletions and 2 paths absent from develop at the time of triage — the
  highest-value thing to look at before anything else is deleted.

## 6. The recovery path

`git push origin <sha>:refs/heads/<branch>` restores a deleted branch. The
local repo picks up the new ref on the next `git fetch origin`. The script
in §4 will then re-classify the restored branch into whatever tier it
belongs in.

This is why the manifest matters. The deletion is recoverable as long as
the SHA is recorded. A `git branch -D <wildcard>` with no manifest is not
recoverable.
