# CI Gates

This document describes the automated gates that run on every PR and push.

## Test Markers and Randomized Order

Registered markers (pyproject `[tool.pytest.ini_options]`, #1192): `browser` drives a real browser and runs as its own CI step, outside the default suite; `openstudio` marks modules that require the OpenStudio extra (CI installs it, the module importor-skips without it; deselect locally with `-m "not openstudio"`); `slow` is reserved for long-running suites the caller excludes explicitly. Applying a marker to a module is a deliberate edit, not an accident.

pytest-randomly (test extra) is active in CI, so test order and the `random` seeds vary per run: a test that only passes in the order it happens to be written fails on some runs. The seed is printed in the pytest header; reproduce a failure with `--randomly-seed=<N>`, or opt a local run out with `-p no:randomly`.

## Test Count Regression Gate

**Purpose:** Prevent unintended test deletions or additions that could indicate broken tests or accidental removal of test coverage.

**Location:** `.github/workflows/ci.yml` — `Check test count` step

**Expected test count:** pinned as `EXPECTED_TEST_COUNT` in the `Check test count` step (see below)

The `demo-smoke` job installs the package non-editable the way a fresh user would, requires every CLI subcommand (read from `cli.py`) to answer `--help`, then runs `matchline demo --no-open` end to end, failing if it takes five minutes or more or the trust and review reports are missing (see #753, #1076). The `wheel-smoke` job builds the wheel and fails when any top-level module, or any `.py` file under a package declared in `pyproject.toml`, is missing from the archive (see #1035, #1076). The `repo-map` job regenerates `docs/repo-map.mmd` and `docs/repo-map.md` with `scripts/generate_repo_map.py` (stdlib-only) and fails on any diff, so the generated module map cannot go stale (see #1096). The `python-matrix` job (#1163) runs the same install-import-demo path on Linux for Python 3.10-3.13 on every PR and push, and the `platform-matrix` job does it on macOS arm64 and Windows x86_64 (Python 3.12) on every push to develop/main, dispatches, and PRs labeled `platform` (docs/platform_support.md).

**Behavior:**
- CI runs `pytest --collect-only` to count tests
- If the count differs from `EXPECTED_TEST_COUNT` (3799; +3 pset-exterior lining guard + unclassified-envelope tests, #1226; +2 geometry-first window-area truth tests, #1220; +3 wall-axis/Qto length-fallback tests, #1219; +2 mm-unit kernel-verts file-units tests, #1217; +1 bodyless-wall direct-fill regression test, #1214; +4 direct-fill window/door enumeration tests, #1206; +2 wall-length body-geometry fallback tests, #1205; +3 rect-profile / face-set space footprint tests, #1204; +2 closet area-cap tests, #1209; +6 mapped-item/CSG body unwrap tests, #1203; +7 independent benchmark truth tests, #1165; +1 API reference doc sync test, #1167), the step fails
- A drift of ±1 or more **requires investigation before merging**

**What to do if you legitimately need to change the test count:**

1. **Removing tests legitimately** (e.g., removing obsolete or redundant tests):
   - Update `EXPECTED_TEST_COUNT` in `.github/workflows/ci.yml` to the new count
   - Include a comment explaining why the count changed
   - This is acceptable if the tests were truly redundant or the feature they tested was removed

2. **Adding tests** (most common case):
   - Update `EXPECTED_TEST_COUNT` to the new count
   - This is generally fine, but ensure new tests provide meaningful coverage

3. **Investigating unexpected drift:**
   - Check recent commits for test changes
   - Ensure no tests were accidentally deleted during rebase/merge
   - Verify all test files are properly included

## Toolchain

Dev and CI environments are locked with `uv` (#1189): `uv.lock` at the repo root pins the resolved set for Python 3.10-3.13, `.python-version` pins local development to 3.12, and `[dependency-groups]` in `pyproject.toml` carries the CI tool pins (`dev`: ruff, mypy; `audit`: pip-audit). The merge-gating jobs (`quality`, `pip-audit`, `review-page-browser`, `detector-lint`) install with `astral-sh/setup-uv` and run everything through `uv sync --frozen` + `uv run --no-sync`, so a green run and its pip-audit result are reproducible day to day. The wheel, demo, python-matrix and platform-matrix jobs deliberately keep pip: they test the fresh-pip-user path, not the dev toolchain. Runtime dependencies in `pyproject.toml` stay ranged for consumers.

Locally: `uv sync --frozen --extra test --extra openstudio`, then `uv run --no-sync python -m pytest tests/ -q` (3752 collected; expected per `ci.yml`).

## Other CI Gates

- **Lint:** `uv run --no-sync ruff check .`
- **Format:** `uv run --no-sync ruff format --check .`
- **Pytest:** `uv run --no-sync python -m pytest tests/ -q`
- **Type checking:** `mypy` on a deliberately small module baseline (pyproject `[tool.mypy]`); imported repo modules are resolved but not checked, and the list grows module by module only intentionally (#1138). CI pins `mypy==2.4.0`; match it locally when widening the list.
- **Coverage ratchet (#1191):** the pytest run measures the shipped surface (`--cov`, config in pyproject `[tool.coverage]`), posts the percentage plus per-module lines to the job summary, and fails the job when coverage drops below `fail_under = 85.0` (baseline 85.6%, measured on develop b3e204d, 2026-10-19; set slightly below the baseline per the #1137 note so run-to-run wobble does not red CI). Raising the floor is a deliberate, reviewed edit; never let a commit lower it. `coverage.xml` is uploaded from the quality job as the `coverage-xml` artifact on every run, including failures, so per-run coverage is inspectable from the run page.
- **Dependency security:** `pip-audit --strict` against the locked resolved runtime dependency set (job `pip-audit`, via the `audit` dependency-group). Fails on known CVEs in runtime deps; test-only tooling is out of its scope by design (#1139). A finding that cannot be fixed yet gets an `--ignore <id>` pin with the reason and the upgrade that clears it.
- **Python pin:** all workflows (ci.yml jobs, release.yml, archcad-smoke.yml) run the same interpreter (3.12). A Python version change moves every workflow together; the archcad-smoke pin is not an independent knob (#1140).

All gates must be green before merging to `develop`.

## Ruff version updates

`ruff` is pinned to one exact version in three places that must agree: the `rev` in `.pre-commit-config.yaml`, the `ruff==X.Y.Z` entry in `[dependency-groups]` (`pyproject.toml`, resolved through `uv.lock`), and the `uvx ruff@X.Y.Z` spec in the `detector-lint` job. Pre-commit requires `rev` to be an immutable tag, so a minor-version pin (`v0.16`) or a range is not possible there; a range in CI alone would let CI and local hooks drift apart.

Renovate (`renovate.json`) opens one grouped `ruff` PR against `develop` every Monday morning that bumps all three together: the pre-commit `rev` and the pyproject pin natively, the workflow `uvx ruff@` spec via the custom regex manager. Patch releases arrive within a week, and CI on that PR proves the new version still passes before it merges. Renovate is scoped to ruff only; every other dependency is disabled in its config.

Renovate runs as a GitHub App, so it must be installed on the repository (https://github.com/apps/renovate) for the config to take effect.

## Dependency hygiene gate

The `dependency hygiene` job (#1190) runs deptry over the repository and fails when an imported package is not declared in `pyproject.toml` (the #1070 bug class: `matchline review` died on an undeclared import while demo-smoke stayed green). Config lives in `[tool.deptry]` in `pyproject.toml`: `known_first_party` covers the single-file helper modules outside the shipped package (detector/ and scripts/ tooling import each other directly), `DEP001` ignores `pymupdf` (function-level lazy import on the AEC-bench rasterization path, declaring it is a maintainer call), and `DEP002`/`DEP004` ignores cover pytest plugin tooling, the OpenStudio CLI extra, and dev-environment imports inside the tooling scripts. New imports in shipped code fail the PR until they are declared.

## Release pipeline

Publishing to PyPI is tag-driven (`release.yml`): pushing a `vX.Y.Z` tag whose name matches `pyproject.toml`'s version builds the sdist and wheel with `uv build`, runs `twine check` from the locked `release` dependency-group, smoke-tests the wheel with a fresh non-editable install (`matchline --help`, `pip check`), and publishes with `uv publish --trusted-publishing automatic` via PyPI trusted publishing (OIDC; no token secret lives in the repo). The `pypi` GitHub environment gates the publish job; `workflow_dispatch` dry-runs build+smoke without publishing.

One-time maintainer setup on pypi.org: add a pending/trusted publisher for `anchapin/matchline`, workflow `release.yml`, environment `pypi` (the environment is created in the GitHub repo automatically on first run). After that, cutting a release is: merge the version bump and release notes, then push the tag.
