# CI Gates

This document describes the automated gates that run on every PR and push.

## Test Count Regression Gate

**Purpose:** Prevent unintended test deletions or additions that could indicate broken tests or accidental removal of test coverage.

**Location:** `.github/workflows/ci.yml` — `Check test count` step

**Expected test count:** ~324 tests

**Behavior:**
- CI runs `pytest --collect-only` to count tests
- If the count differs from `EXPECTED_TEST_COUNT` (324), the step fails
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
