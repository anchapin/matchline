## What

## Why

## Validation

- [ ] `python -m pytest tests/ -q` green
- [ ] `ruff check .` / `ruff format --check .` clean
- [ ] Docs updated (`docs/`, `docs/README.md`, README/CHANGELOG if user-facing)

## Provenance checklist (for pipeline changes)

- [ ] New extracted facts carry sheet, revision, method, confidence
- [ ] Low-confidence results go to the review queue, not silently dropped
- [ ] Limitations documented, not hidden

## Agent authorship

- [ ] Agent-generated? (yes/no — if yes, name the task/worker, e.g. `Generated-by: worker/<task>`)
- [ ] Verified: `pytest` result (paste count), `ruff check` / `ruff format --check` result
- [ ] Manual checks performed (describe, or n/a)
- [ ] CI run link (when the workflow is live)
