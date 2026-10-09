# CI Gates

This document describes the automated gates that run on every PR and push.

## Test Count Regression Gate

**Purpose:** Prevent unintended test deletions or additions that could indicate broken tests or accidental removal of test coverage.

**Location:** `.github/workflows/ci.yml` — `Check test count` step

**Expected test count:** pinned as `EXPECTED_TEST_COUNT` in the `Check test count` step (see below)

**Behavior:**
- CI runs `pytest --collect-only` to count tests
- If the count differs from `EXPECTED_TEST_COUNT` (2586), the step fails
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

All gates must be green before merging to `develop`.

## Ruff version updates

`ruff` is pinned to one exact version in two places that must agree: the `rev` in `.pre-commit-config.yaml` and the `ruff==X.Y.Z` install in `.github/workflows/ci.yml` (both jobs). Pre-commit requires `rev` to be an immutable tag, so a minor-version pin (`v0.16`) or a range is not possible there; a range in CI alone would let CI and local hooks drift apart.

Instead, Renovate (`renovate.json`) opens one grouped `ruff` PR against `develop` every Monday morning that bumps all three pins together. Patch releases arrive within a week, and CI on that PR proves the new version still passes before it merges. Renovate is scoped to ruff only; every other dependency is disabled in its config.

Renovate runs as a GitHub App, so it must be installed on the repository (https://github.com/apps/renovate) for the config to take effect.

