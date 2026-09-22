# CODE-REVIEW.md — code review standards

## Every PR must

- [ ] Pass `pip install -e ".[test]"` in a fresh environment
- [ ] Pass `python -m pytest tests/ -q` in the current session (not reported from a prior run)
- [ ] Pass `ruff check .` with no new violations introduced
- [ ] Pass `ruff format --check .`
- [ ] Include or update tests covering: happy path, at least one invariant, and at least one defect-injection case where applicable
- [ ] Include or update `docs/` page if adding or changing a module
- [ ] Carry `Generated-by: <worker-name-or-task-id>` attribution in the commit body if agent-generated

## Severity levels

| Level | Label | Meaning |
|---|---|---|
| Blocker | `blocker` | CI red; conservation law violation; provenance missing on a new fact type; silently dropping a low-confidence result |
| Major | `major` | Wrong data flow direction; missing boundary check; `REVIEW_CONFIDENCE` bypassed; new dependency on undeclared package |
| Minor | `minor` | Style violation introduced; dead code; missing type hint on public function |
| Nit | `nit` | Comment spelling; ordering; whitespace |

## Domain-specific review focus

### Provenance and review queue
Every new fact type added to `BuildingModel` must have a `Provenance` field. If the extraction method is uncertain, it must go through the review queue — never silently accept a low-confidence link.

### Conservation laws (`validate.py`)
Do not weaken existing tolerances or remove existing checks without an explicit decision in `docs/design-docs/`. New invariants must be documented in `docs/validation.md`.

### Coordinate frames
Changes to `building_model.py` or `bem_export.py` coordinate handling must note whether they affect the y-down → north-up flip at export. Test with a building that has non-zero y-coordinates.

### Cross-sheet linking (`link.py`)
New discipline sheets added to `build_model` must follow the same registration → assignment → flagging pattern: register into arch coordinates, assign by position, flag unassigned items for review.

### IFC import (`ifc_import.py`)
Tier 0 (geometry + openings without space assignment) is the current contract. Do not claim Tier 1 space attachment is implemented if it is not.

### `detector/` changes
The detector track has its own venv and is excluded from the main ruff config. Changes there do not require main lint to pass — but must not break `detector/train.py` or its data pipeline.

## Anti-patterns to flag

- `print()` in library code (should be in CLI/main only)
- `sys.path` manipulation
- `~/workspace/...` paths in library code
- Magic constants not named or documented
- Silently accepting a confidence below `REVIEW_CONFIDENCE` without queuing
- Asserting conservation law tolerances without evidence

## When to request human review

- Any change to `building_model.py` core types or `Provenance` fields
- Any new module added to the pipeline
- Any change to `validate.py` invariants or tolerances
- Any change to coordinate frame handling
- Any IFC Tier 1 (space attachment) work

## Auto-approve

PRs that only touch the following may be merged without human review: test fixtures, documentation typo fixes, `CHANGELOG.md` entries, or purely cosmetic changes to comments/formatting.
