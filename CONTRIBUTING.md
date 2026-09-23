# Contributing to Matchline

## Setup (cold clone → green tests)

```bash
git clone https://github.com/anchapin/matchline.git
cd matchline
git checkout develop        # develop is the working branch; main is releases only
python3 --version           # need >= 3.10
pip install -e ".[test]"    # editable install + pytest
python -m pytest tests/ -q  # ~55 tests, a few seconds, no network needed
```

Optional extras:

```bash
pip install -e ".[ocr]"       # rapidocr_onnxruntime, for room_labels OCR
pip install -e ".[detector]"  # torch + ultralytics, for detector/ (heavy;
                              # detector/ normally runs in its own venv,
                              # see detector/README.md)
```

External datasets (AEC Geometric Bench, CMP Facade, CubiCasa5K, FloorPlanCAD)
live under `~/workspace/datasets/` and are **not** committed. Demos that need
them (`matchline facade-takeoff`) fail with a clear message if the data is
absent.

## Everyday commands

```bash
matchline --help                 # unified CLI: one subcommand per demo script
matchline validate               # validation battery demo
python3 run_validation.py         # the old run_*.py scripts still work too
ruff check . && ruff format .     # lint + format (or: pre-commit run --all-files)
```

Set up the git hooks once: `pre-commit install`.

## Branch / PR conventions

- Work on feature branches off `develop`; open PRs against `develop`.
  **PRs are required — no direct pushes to `develop`**, by agents or by
  humans. This is the project's standing decision (2026-09-19).
- `main` is releases only — see `RELEASING.md`. Never merge feature work
  into `main` directly.
- CI must be green: install, `ruff check`, `ruff format --check`, pytest.
- Keep PRs focused; one concern per PR.
- Recommended: enable GitHub branch protection on `develop` (repo settings)
  requiring a PR and green CI before merge, so the rule is enforced by the
  platform and not just by convention. AI coding agents: see `AGENTS.md`
  for the full operating instructions, including the attribution trailer
  and verification requirements.

## Adding a module

A new pipeline module is done when it has all four:

1. **Implementation** — library code with no `print()` in library paths
   (prints belong in `main()` demos / the CLI layer).
2. **Tests** — in `tests/`; cover the happy path, an invariant, and at
   least one defect-injection case where it makes sense.
3. **Docs** — a page under `docs/` (one line in `docs/README.md`), with a
   limitations section. This project documents what *doesn't* work yet.
   **Naming convention**: doc pages are named to match their module. If a module
   is `geometry_simplify.py`, the doc is `docs/geometry_simplify.md`
   (not `geometry_simplification.md`). This keeps the mapping discoverable.
4. **Provenance** — every extracted fact that lands in `BuildingModel`
   carries sheet, revision, method, and confidence. Low-confidence results
   go to the review queue; nothing is silently accepted or silently dropped.

## Style

- `ruff check` / `ruff format` are the law; the pre-commit hook enforces them.
- Type hints on public functions; `from __future__ import annotations`
  where it helps.
- No `sys.path` hacks — the package is installed; imports resolve normally.
- No machine-specific paths in library code (no `~/workspace/...` fallbacks).
- Behavior-preserving refactors only unless the PR says otherwise.
