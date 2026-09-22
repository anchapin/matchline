---
phase: 05-realdata-ci
plan: "01"
type: execute
wave: 1
depends_on: []
files_modified:
  - .github/workflows/ci.yml
autonomous: true
requirements:
  - CI-01
must_haves:
  truths:
    - "Every PR runs ruff check + format check + pytest before merge"
    - "A failed CI check blocks the merge button"
  artifacts:
    - path: ".github/workflows/ci.yml"
      provides: "GitHub Actions CI pipeline"
      min_lines: 30
  key_links:
    - from: ".github/workflows/ci.yml"
      to: "ruff, ruff-format, pytest"
      via: "shell commands in job steps"
---

<objective>
Set up GitHub Actions CI pipeline that runs the full verification battery on every PR.

Purpose: Catch regressions before merge. Gate: no PR merges with failing CI.
Output: `.github/workflows/ci.yml`
</objective>

<execution_context>
@/home/alex/.agents/get-shit-done/workflows/execute-plan.md
</execution_context>

<context>
The project uses:
- `ruff check .` for linting (E, F, I, W; ignores E501, E701, E702, E741)
- `ruff format --check .` for formatting
- `python -m pytest tests/ -q` for tests

Per AGENTS.md: "Verify: `pip install -e ".[test]"`, then `python -m pytest tests/ -q`, `ruff check .`, `ruff format --check .` — all must be green."

The repo root has a `pyproject.toml` with ruff and pytest config. The `.[test]` extra installs test dependencies.
</context>

<tasks>

<task type="auto">
  <name>Create GitHub Actions CI workflow</name>
  <files>.github/workflows/ci.yml</files>
  <action>
Create `.github/workflows/ci.yml` with:
- Trigger: `push` to `main` and `develop` branches; `pull_request` targeting `main` and `develop`
- Job `quality` running on `ubuntu-latest`:
  1. Checkout with fetch-depth: 0
  2. Set up Python 3.11
  3. Install the package with `pip install -e ".[test]"`
  4. Run `ruff check .`
  5. Run `ruff format --check .`
  6. Run `python -m pytest tests/ -q`
- Job `quality` must pass for any subsequent jobs to run (no need for separate jobs since all run the same env)
- Add `concurrency` to cancel in-progress runs on the same branch/PR
- Add `permissions` block for security (contents: read, pull-requests: write for PR status)
</action>
  <verify>
    <automated>python -m pytest tests/ -q && ruff check . && ruff format --check .</automated>
  </verify>
  <done>`.github/workflows/ci.yml` exists, runs ruff + pytest on push/PR, and blocks merge on failure</done>
</task>

</tasks>

<verification>
The workflow file is syntactically valid YAML and the jobs reference correct commands from the project.
</verification>

<success_criteria>
- `.github/workflows/ci.yml` created with correct triggers
- CI runs `ruff check .` and fails if lint errors exist
- CI runs `ruff format --check .` and fails if formatting errors exist
- CI runs `python -m pytest tests/ -q` and fails if tests fail
</success_criteria>

<output>
After completion, create `.planning/phases/05-realdata-ci/05-01-SUMMARY.md`
</output>
