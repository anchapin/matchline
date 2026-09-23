# open-issues.md — genuinely open issues

Currently open technical questions. Resolved issues are documented in
`docs/plans/designs/` with CFG identifiers.

---

## 1. IFC Tier 1: space attachment

**Location**: `ifc_import.py` + `docs/ifc_import.md`

Tier 0 recovers wall geometry, openings, and dimensions **without** `IfcRelSpaceBoundary`. Openings are NOT attached to spaces in Tier 0. Space assignment (Tier 1) requires geometric adjacency inference — an open question.

**Status**: Open. Tier 0 is implemented and tested.

**Workaround**: Use the drawing import path for room-linked takeoffs; use IFC only for geometry and envelope.
