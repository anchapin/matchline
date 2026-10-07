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

**Status**: Open. Tracked by [#663](https://github.com/anchapin/matchline/issues/663) (multi-sheet provenance schema) and [#664](https://github.com/anchapin/matchline/issues/664) (cross-level grouping + validation), which supersede [#654](https://github.com/anchapin/matchline/issues/654). Design record: [`designs/cfg-01-cross-sheet-window-deduplication.md`](../designs/cfg-01-cross-sheet-window-deduplication.md).

---

## P1 — IFC Tier 1 space attachment

**Description**: `ifc_import.py` Tier 0 recovers openings without attaching them to spaces. Tier 1 (geometric adjacency inference) is unimplemented.

**Location**: `ifc_import.py`

**Proposed**: Point-in-polygon + wall adjacency to assign openings to spaces. Requires `building_model.py` space polygon lookup.

**Status**: Open. Tracked by [#665](https://github.com/anchapin/matchline/issues/665) (along-wall direction design, HITL) and [#666](https://github.com/anchapin/matchline/issues/666) (attach + observe + guard), which supersede [#655](https://github.com/anchapin/matchline/issues/655).

---

## P2 — Pre-commit pin drift

**Description**: `.pre-commit-config.yaml` pins `ruff` to `v0.16.10` (Renovate keeps it current, #680). Ruff releases frequently; pinned versions can fall behind security patches.

**Location**: `.pre-commit-config.yaml`

**Proposed**: Pin to minor version (`0.16`) or use `ruff>=0.16,<0.17` range syntax if pre-commit supports it. Add Renovatebot.

**Status**: Open. Tracked by [#656](https://github.com/anchapin/matchline/issues/656).

---

## P2 — `E501` suppressions in `pyproject.toml`

**Description**: `E501` (line length) is suppressed project-wide because "geometry literals read better unbroken." This is a project norm, but the suppression is broad — any line can grow arbitrarily long.

**Location**: `pyproject.toml`

**Proposed**: Set a reasonable `line-length` (100 is current) and only suppress `E501` on multi-line geometry literals that genuinely benefit from it, using `# noqa: E501` inline.

**Status**: Open. Tracked by [#657](https://github.com/anchapin/matchline/issues/657).

---

## Closed

Resolved debt stays listed here so the tracker reads as a living backlog. The design record for each lives in `docs/plans/designs/`.

### CFG-02 — Thick-stroke skeleton pre-processing

**Status**: Completed (2026-09-23, Phase 6). Design record: [`designs/cfg-02-thick-stroke-skeleton.md`](../designs/cfg-02-thick-stroke-skeleton.md).

### CFG-03 — Complex GD&T invariant rows

**Status**: Completed (2026-09-23, Phase 6). Design record: [`designs/cfg-03-complex-gdt-invariant-rows.md`](../designs/cfg-03-complex-gdt-invariant-rows.md).
