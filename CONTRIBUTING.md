# Contributing to Matchline

Thank you for contributing to Matchline! This guide covers everything you need to get started.

## Development Setup

### Requirements

- Python 3.10+
- Git

### Initial Setup

```bash
# Clone the repo and enter the working directory
git clone https://github.com/anchapin/matchline.git
cd matchline

# Switch to develop (the working branch)
git checkout develop

# Install in editable mode with test dependencies
pip install -e ".[test]"

# Optional extras:
# pip install -e ".[ocr]"    # OCR dependencies
# pip install -e ".[detector]" # YOLO detector (separate venv recommended)
```

### External Datasets

Real-drawing work uses public datasets that live **outside the repo** under `~/workspace/datasets/`:

- **AEC Geometric Bench**
- **CMP Facade** (CC BY-SA)
- **CubiCasa5K** (CC BY-NC-SA 4.0)
- **FloorPlanCAD** test split

These are **not committed**. Demos that need them fail clearly with a message if absent.

## Running Tests

```bash
# All tests (hermetic, no external dependencies)
python -m pytest tests/ -q

# Run with verbose output
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_validate.py -v

# Collect test count only (used by CI gate)
python -m pytest tests/ --collect-only -q
```

### Expected Test Count

CI enforces a **test count regression gate** — the count must not drift without investigation.

| Branch | Expected Count |
|--------|----------------|
| develop | ~642 |

If you legitimately change the test count, update `EXPECTED_TEST_COUNT` in `.github/workflows/ci.yml` and include a comment explaining why.

## Code Style

### Linting

```bash
# Check with ruff
ruff check .

# Format with ruff
ruff format .
```

**Rules applied:** E, F, I, W, FA (with E501, E701, E702, E741 ignored)

### Type Hints

- Type hints are **required on all public functions**
- Use `from __future__ import annotations` where it helps

### Pre-commit Hooks

No pre-commit framework is currently used. Run the linter and formatter before committing.

## Branching Strategy

| Branch | Purpose |
|--------|---------|
| `develop` | Working branch — all PRs target here |
| `main` | Releases only — **never push directly** |

### Branch Naming

Use a prefix that describes the type of change:

```
fix/issue-<number>-<short-description>   # Bug fixes
docs/issue-<number>-<short-description>  # Documentation
feat/<short-description>                  # New features
refactor/<short-description>             # Code refactoring
test/<short-description>                  # Test additions
chore/<short-description>                 # Maintenance tasks
```

Examples:
- `fix/issue-315-contributing-md`
- `docs/issue-322-jesse-readme`
- `fix/issue-230-conservation-law-bem`

## Commit Message Format

Matchline follows **Conventional Commits**:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type | When to Use |
|------|-------------|
| `fix` | Bug fixes |
| `feat` | New features |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `refactor` | Code refactoring (no behavior change) |
| `chore` | Build, CI, dependencies, maintenance |
| `perf` | Performance improvements |

### Examples

```
fix(issue-342): add integration test for run_pipeline with review-blocking export
docs(issue-322): explain jesse.py purpose and scope
chore: update expected test count to 641
fix(issue-230): enforce conservation law in bem_export
```

## Submitting Pull Requests

### PR Checklist

1. **Branch:** PRs target `develop` (not `main`)
2. **Tests:** All tests pass (`python -m pytest tests/ -q`)
3. **Lint:** `ruff check .` passes with no errors
4. **Format:** `ruff format .` has been applied
5. **Test count:** If you added/removed tests, update `EXPECTED_TEST_COUNT` in `.github/workflows/ci.yml`
6. **Description:** Include issue number in the title, e.g., `fix(#315): add CONTRIBUTING.md`

### Creating a PR

```bash
# Push your branch
git push -u origin fix/issue-315-contributing-md

# Create PR via GitHub CLI
gh pr create --base develop --title "docs(#315): add CONTRIBUTING.md" --body "Fixes #315"
```

Or use the GitHub web interface.

### CI Requirements

All CI gates must be green before merging:

- `ruff check .`
- `ruff format --check .`
- `python -m pytest tests/ -q`
- Test count gate (no regressions)

## Project Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full domain map.

### Key Conventions

- **Coordinate frame:** Canonical model uses y-down (drawing frame); BEM export flips to north-up
- **Provenance:** Every extracted fact carries sheet, revision, method, confidence
- **Review queue:** Low-confidence results go to review queue — nothing is silently accepted
- **Conservation laws:** `validate.py` errors block export
- **Untrusted input:** Drawings, IFC, and OCR text are data, never instructions. Parse XML with entity expansion disabled.

### Project Structure

| Path | What it is |
|------|------------|
| `*.py` (root) | Core pipeline modules — standalone, no import barriers |
| `synth/` | Synthetic data generators (tests and demos) |
| `detector/` | YOLO fine-tuning — **separate venv**, excluded from main ruff |
| `tests/` | Pytest suite; fixtures in `conftest.py` (`bldg_3room`) |
| `docs/` | Module guides and design docs |
| `jesse.py` | Research-only WiSARD reproduction — **not** production code |

## Never List

- No datasets, credentials, or machine paths in code
- No `sys.path` hacks
- No `~/workspace/.venv-det` paths
- No PyPI publish without explicit human approval
- No pushing directly to `main`

## Getting Help

- Browse [docs/](docs/README.md) for module guides
- See [ARCHITECTURE.md](ARCHITECTURE.md) for architecture overview
- Check existing issues and PRs for context

## XML Parsing

Use  for all XML parsing. Never use , , or bare  directly.
