# tech-debt-tracker.md — known technical debt

Known debt with priority, rationale, and proposed resolution.

---

## Priority legend

- **P0**: blocks release
- **P1**: degrades accuracy or auditability in production paths
- **P2**: quality-of-life; no customer impact
- **P3**: nice-to-have; deprioritized

---

## P1 — Cross-sheet window deduplication

**Description**: Two elevations of the same facade produce duplicate `SpaceOpening` entries (one per elevation run). No deduplication across runs.

**Location**: `link.py` (`build_model`)

**Proposed**: Post-processing dedup pass using window tag or geometric proximity (≤5cm overlap on same facade → merge).

**Status**: Open.

---

## P1 — IFC Tier 1 space attachment

**Description**: `ifc_import.py` Tier 0 recovers openings without attaching them to spaces. Tier 1 (geometric adjacency inference) is unimplemented.

**Location**: `ifc_import.py`

**Proposed**: Point-in-polygon + wall adjacency to assign openings to spaces. Requires `building_model.py` space polygon lookup.

**Status**: Open.

---

## P2 — Pre-commit pin drift

**Description**: `.pre-commit-config.yaml` pins `ruff` to `v0.16.8`. Ruff releases frequently; pinned versions can fall behind security patches.

**Location**: `.pre-commit-config.yaml`

**Proposed**: Pin to minor version (`0.16`) or use `ruff>=0.16,<0.17` range syntax if pre-commit supports it. Add Renovatebot.

**Status**: Open.

---

## P2 — `E501` suppressions in `pyproject.toml`

**Description**: `E501` (line length) is suppressed project-wide because "geometry literals read better unbroken." This is a project norm, but the suppression is broad — any line can grow arbitrarily long.

**Location**: `pyproject.toml`

**Proposed**: Set a reasonable `line-length` (100 is current) and only suppress `E501` on multi-line geometry literals that genuinely benefit from it, using `# noqa: E501` inline.

**Status**: Open.
