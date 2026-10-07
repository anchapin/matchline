# Releasing Matchline

`develop` is the working branch. `main` is reserved for releases and should
only ever receive release PRs.

## Checklist

1. `develop` is green: CI passes, `python -m pytest tests/ -q` is clean.
2. Update `CHANGELOG.md`: move items from `[Unreleased]` into a new
   `[X.Y.Z] - YYYY-MM-DD` section.
3. Bump `version` in `pyproject.toml`.
4. Check licenses: `pip wheel --no-deps --no-build-isolation -w dist .` then
   `python scripts/check_release_licenses.py dist/*.whl`. It must pass: nothing
   trained on non-commercial or copyleft data ships, and share-alike data ships
   with `NOTICE.md` (see `license_ledger.json`, docs/license_ledger.md).
5. Open a PR `develop` → `main` titled `Release vX.Y.Z`. Get review.
6. After merge, tag the release on `main`:
   `git tag -a vX.Y.Z -m "matchline vX.Y.Z" && git push origin vX.Y.Z`.
7. Immediately after tagging, on `develop`, start a new `[Unreleased]`
   section in `CHANGELOG.md`.

## Versioning

SemVer. While pre-1.0, minor bumps may include breaking changes to the
`BuildingModel` schema; patch bumps are fixes only.

## What "release" means here

A release is a tagged, changelogged snapshot of `main` that a downstream
user (e.g. an energy modeler) can `pip install` from the repo. There is no
PyPI publication yet; when there is, add the publish step here.
