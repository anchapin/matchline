# PLANS.md — planning conventions

## Plan structure

Plans live under `docs/plans/` and are **gitignored** (they are local working notes, not committed documentation).

```
docs/plans/
  designs/           ← permanent design references
  work/             ← execution plans (Status: Active / Completed)
```

Use the `Status` header to track lifecycle: `Status: Draft` → `Status: Active` → `Status: Completed` (or `Status: Superseded`).

## Plan template

```markdown
# {NNN}-{short-name}

**Status**: Draft | Active | Completed | Superseded

## Context
What is the problem or opportunity?

## Goals
What must the solution achieve?

## Non-goals
What is explicitly out of scope?

## Approach
How does this work? Include key design decisions.

## Open questions
What is not yet decided?

## Verification
How will we know this is done?

## Notes
Discovery notes, tradeoffs considered, alternative approaches.
```

## 3-digit prefix convention

Plans use a 3-digit zero-padded sequential prefix per folder. Create the next number by inspecting existing files.

## When to create a plan

Create a `work/` plan when:
- The task spans more than one module or more than one session
- The approach is not already covered by an existing doc
- A human decision is needed on the approach before implementation proceeds

**Do not** create a plan for:
- Bug fixes with a clear root cause and a single-module fix
- Incremental additions to existing well-documented modules
- Research tasks that end in a report (write the report instead)

## Status tracking

Update `Status` in the plan header as work progresses. A plan is `Completed` when:
- Implementation is merged and verified by CI
- Docs are updated
- Tech debt is captured in `tech-debt-tracker.md` if any was introduced

## Active work tracking

Active execution plans are listed in `docs/plans/work/` with `Status: Active`. Completed plans are moved to `Status: Completed` (not deleted — they serve as decision records).
