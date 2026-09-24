# Contributing to Matchline

## Branching Conventions

- `develop` is the working branch. All development happens here.
- `main` is reserved for releases only. **Never push directly to `main`.**
- Feature branches should be named `fix/<issue-number>-<short-description>` or `feat/<short-description>`.

## Development Environment Setup

### 1. Clone the repository and enter it

```bash
git clone <your-fork-url>
cd matchline
git checkout develop
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate  # on Windows: .venv\Scripts\activate
```

### 3. Install the package in editable mode with test dependencies

```bash
pip install -e ".[test]"
```

Optional extras:
- `[ocr]` — OCR support for room labels (`rapidocr_onnxruntime`)
- `[detector]` — YOLO detector support (`torch`, `ultralytics`); the `detector/` directory has its own venv — see `detector/README.md`

### 4. Verify the installation

```bash
python -m pytest tests/ -q
```

## Running the Test Suite

```bash
python -m pytest tests/ -q
```

The full suite runs in a few seconds and is hermetic (no external network or dataset dependencies required for the core tests).

## Linting and Formatting

We use `ruff` for both linting and formatting.

```bash
# Check for lint issues
ruff check .

# Check formatting
ruff format --check .
```

To auto-fix lint issues and format code:

```bash
ruff check . --fix
ruff format .
```

## Code Style

- Line length: 100 characters
- Target Python version: 3.10+
- Ruff lint rules enabled: `E`, `F`, `I`, `W`, `FA`
- Ruff ignores: `E501`, `E701`, `E702`, `E741`
- Use `from __future__ import annotations` where it helps with type hints
- Every extracted fact must carry provenance: sheet, revision, method, confidence

## Pull Request Conventions

- All PRs should target the `develop` branch (not `main`).
- PRs require all CI checks to pass before merging.
- Keep PRs focused and reasonably sized. If a change is large, discuss with maintainers first.
- Link the relevant GitHub issue in the PR description (e.g., "Fixes #212").

## Adding a Module

When adding a new module, you must provide:

1. **Implementation** in the appropriate `.py` file
2. **Tests** — happy path, invariant checks, and defect injection
3. **Documentation** — a `docs/<module>.md` page (with a limitations section)
4. **Provenance** — every extracted fact must carry sheet, revision, method, confidence

## Dataset Conventions

External datasets (AEC Geometric Bench, CMP Facade, CubiCasa5K, FloorPlanCAD) live under `~/workspace/datasets/` and are **not committed** to the repository. Demos that need them fail clearly if absent.

## Documentation

Module documentation lives in `docs/`. See `docs/README.md` for the full structure and writing guidelines.
