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
