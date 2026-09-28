# Stash archive

Dropped from `git stash` on 2026-09-28 during housekeeping. Every entry below is
recoverable with `git stash apply <sha>` — the commit object survives until git GC.
None of these were created by the session that archived them (all dated 2026-09-23…27).

| SHA | Date | Message | Diff stat |
|---|---|---|---|
| `c2fc94226a7a` | 2026-09-23 15:16 | On fix/188-validate-conservation-laws: wip-validate-conservation-laws | 7 files changed, 195 insertions(+), 15 deletions(-) |
| `a2e5dbaa2a08` | 2026-09-23 19:05 | WIP on develop: 41c145a fix(run_review): assert args.confirm/reject no | 1 file changed, 59 insertions(+), 59 deletions(-) |
| `355919948f86` | 2026-09-23 23:21 | WIP on develop: 7471114 ci: update expected test count to 527 for wave | 2 files changed, 78 insertions(+), 76 deletions(-) |
| `74f69eef5423` | 2026-09-24 02:12 | On wave6/issue-232: WIP refactor validate.py to validate/ package - BR | 1 file changed, 1437 deletions(-) |
| `700f8b375f69` | 2026-09-24 22:16 | WIP on develop: 7eae941 ci: update expected test count to 647 after wa | 6 files changed, 88 insertions(+), 6 deletions(-) |
| `39c2faafb445` | 2026-09-25 00:20 | WIP on issue-347-defect-injection: 0cafd32 test(pipeline): add defect- | 1 file changed, 1 insertion(+), 1 deletion(-) |
| `6a4141b4a6dc` | 2026-09-25 08:09 | WIP on develop: b201991 Merge pull request #394 from anchapin/fix/313- | 3 files changed, 27 insertions(+), 7 deletions(-) |
| `e7333292e13e` | 2026-09-25 14:09 | On fix/add-datasets-md: handoff | 1 file changed, 54 insertions(+), 69 deletions(-) |
| `ab314259bacb` | 2026-09-25 18:16 | WIP on develop: daec5e1 test: add regression tests for ReviewItem need | 6 files changed, 32 insertions(+), 78 deletions(-) |
| `942857e314cc` | 2026-09-25 19:52 | WIP on develop: 38269f3 docs: update stale test count references (#450 | 3 files changed, 98 insertions(+), 69 deletions(-) |
| `a68280c79cbf` | 2026-09-25 20:48 | WIP on develop: 977de9b feat: add revision diffing for zone-level incr | 2 files changed, 8 insertions(+), 71 deletions(-) |
| `ba50f3b65fe3` | 2026-09-27 10:21 | WIP on develop: 313c72b fix(bem): prevent overlapping openings and neg | 1 file changed, 6 insertions(+), 69 deletions(-) |

## Not dropped

- `74f69eef5423` — "WIP refactor validate.py to validate/ package - BROKEN (issue 232)", 1 file, -1437 lines. A self-described broken
  half-finished package refactor. Flagged for triage rather than silently discarded; see the note in the session summary.

## Triaged 2026-09-28 (session 2)

Both items previously deferred to triage are now resolved. Neither needed salvaging
beyond one fragment, and both were left on disk — nothing was deleted.

### `74f69eef5423` — SUPERSEDED, do not resurrect

The refactor it attempted **shipped**: `d48781c refactor: split validate.py into
validate/ package (closes #259)`. `validate/` is now a package with 9 modules
(`__init__.py`, `conservation.py`, `invariants.py`, `ashrae90_1.py`, `export.py`,
`gbxml.py`, `svg.py`, `types.py`), and `validate.N_CHECKS == 37`. Issue #232 closed
COMPLETED 2026-09-25. The stash's entire diff is the deletion of the flat
`validate.py` — which the completed refactor also did, by replacing it with the
package. Applying it now would delete nothing useful and break every
`from validate import ...` import in the suite. The "BROKEN" self-description is
accurate as of 2026-09-24 and simply no longer relevant.

### `wave15-dx-docs-uncommitted.patch` — ~95% SUPERSEDED, 1 fragment salvaged

122 insertions across 5 files, all documentation. The prior handoff recorded it as
"verified to apply cleanly against `d2718ee`" — true then, **false now**, and
`git apply --check` against current `develop` fails on 3 of 5 files:

| File | State | Why |
|---|---|---|
| `building_model.py` (+61) | superseded | `ReviewItem`, `BuildingModel` and `flag_for_review` all carry fuller docstrings in `develop` today |
| `jesse.py` (+6) | superseded | the `.. warning::` research-asset block is present in `develop` |
| `run_review.py` (+23) | superseded in substance | `develop` has a newer, differently-structured `Overview` docstring; hunks conflict |
| `validate.py` (+28) | **obsolete** | the file no longer exists — it is the `validate/` package (see above). `git apply` errors `does not exist in index` |
| `cli.py` (+17) | **salvaged** | applied by hand to `cli.py::cmd_validate` |

The one fragment worth keeping was `cmd_validate`'s docstring, which documents real
behaviour that was undocumented. Hand-applied with two corrections: the text now
says `validate.run_checks()` / `validate.export_gate` / `validate.N_CHECKS` instead
of `validate.py` (the package rename), and it points at `N_CHECKS` rather than
hardcoding the patch's "28+", which was true when written and is now 37.

The patch file itself is left in place as the record. The branch
`fix-wave15-dx-docs` still holds one unmerged commit, `d2718ee` ("DX: docstrings,
CONTRIBUTING clarification, docs/run_review.md, jesse.py warning"); the jesse.py
portion of its intent is in `develop` by another route, so it is likely superseded
too — but that was not verified commit-by-commit and is not asserted here.

