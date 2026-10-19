# CI Gates

This document describes the automated gates that run on every PR and push.

## Test Count Regression Gate

**Purpose:** Prevent unintended test deletions or additions that could indicate broken tests or accidental removal of test coverage.

**Location:** `.github/workflows/ci.yml` — `Check test count` step

**Expected test count:** pinned as `EXPECTED_TEST_COUNT` in the `Check test count` step (see below)

The `demo-smoke` job installs the package non-editable the way a fresh user would, requires every CLI subcommand (read from `cli.py`) to answer `--help`, then runs `matchline demo --no-open` end to end, failing if it takes five minutes or more or the trust and review reports are missing (see #753, #1076). The `wheel-smoke` job builds the wheel and fails when any top-level module, or any `.py` file under a package declared in `pyproject.toml`, is missing from the archive (see #1035, #1076). The `repo-map` job regenerates `docs/repo-map.mmd` and `docs/repo-map.md` with `scripts/generate_repo_map.py` (stdlib-only) and fails on any diff, so the generated module map cannot go stale (see #1096). The `python-matrix` job (#1163) runs the same install-import-demo path on Linux for Python 3.10-3.13 on every PR and push, and the `platform-matrix` job does it on macOS arm64 and Windows x86_64 (Python 3.12) on every push to develop/main, dispatches, and PRs labeled `platform` (docs/platform_support.md).

**Behavior:**
- CI runs `pytest --collect-only` to count tests
- If the count differs from `EXPECTED_TEST_COUNT` (3751; +1 API reference doc sync test, #1167), the step fails
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

## Other CI Gates

- **Lint:** `ruff check .`
- **Format:** `ruff format --check .`
- **Pytest:** `python -m pytest tests/ -q`
- **Type checking:** `mypy` on a deliberately small module baseline (pyproject `[tool.mypy]`); imported repo modules are resolved but not checked, and the list grows module by module only intentionally (#1138). CI pins `mypy==2.4.0`; match it locally when widening the list.
- **Coverage (informational):** the pytest run measures the shipped surface (`--cov`, config in pyproject `[tool.coverage]`) and posts the percentage plus per-module lines to the job summary. No gate yet (#1137): once the baseline is known, set `fail_under` slightly below it and raise only intentionally.
- **Dependency security:** `pip-audit --strict` against the resolved runtime dependency set (job `pip-audit`). Fails on known CVEs in runtime deps; test-only tooling is out of its scope by design (#1139). A finding that cannot be fixed yet gets an `--ignore <id>` pin with the reason and the upgrade that clears it.
- **Python pin:** all workflows (ci.yml jobs, release.yml, archcad-smoke.yml) run the same interpreter (3.12). A Python version change moves every workflow together; the archcad-smoke pin is not an independent knob (#1140).

All gates must be green before merging to `develop`.

## Ruff version updates

`ruff` is pinned to one exact version in two places that must agree: the `rev` in `.pre-commit-config.yaml` and the `ruff==X.Y.Z` install in `.github/workflows/ci.yml` (both jobs). Pre-commit requires `rev` to be an immutable tag, so a minor-version pin (`v0.16`) or a range is not possible there; a range in CI alone would let CI and local hooks drift apart.

Instead, Renovate (`renovate.json`) opens one grouped `ruff` PR against `develop` every Monday morning that bumps all three pins together. Patch releases arrive within a week, and CI on that PR proves the new version still passes before it merges. Renovate is scoped to ruff only; every other dependency is disabled in its config.

Renovate runs as a GitHub App, so it must be installed on the repository (https://github.com/apps/renovate) for the config to take effect.

## Release pipeline

Publishing to PyPI is tag-driven (`release.yml`): pushing a `vX.Y.Z` tag whose name matches `pyproject.toml`'s version builds the sdist and wheel, runs `twine check`, smoke-tests the wheel with a fresh non-editable install (`matchline --help`, `pip check`), and publishes via PyPI trusted publishing (OIDC; no token secret lives in the repo). The `pypi` GitHub environment gates the publish job.

One-time maintainer setup on pypi.org: add a pending/trusted publisher for `anchapin/matchline`, workflow `release.yml`, environment `pypi` (the environment is created in the GitHub repo automatically on first run). After that, cutting a release is: merge the version bump and release notes, then push the tag.
