# Phase 3: IFC Round-Trip

**Goal**: BuildingModel round-trips through IFC — import via `ifc_import.py`, export via `ifc_export.py` — with full fidelity for zones, HVAC, lighting, and envelope. Cross-sheet window deduplication is implemented.

**Depends on**: Phase 1 (orchestration)

**Requirements**: IFC-01, IFC-02, IFC-03, IFC-04

**Success Criteria**:
1. `matchline ifc-import foo.ifc --out model.json` produces a `BuildingModel` with walls, openings, and material layers (Tier 0) — verified by existing test
2. `matchline ifc-export model.json out.ifc` writes an IFC4 file containing all `Space`, `Zone`, `SpaceLighting`, and `SpaceHVAC` data
3. An IFC round-trip (import → export → import) preserves zone-to-space many-to-many membership and lighting watt totals within 1% tolerance
4. Two `link_elevations` runs on two separate elevations of the same facade produce exactly one `SpaceOpening` per window (not two)

**Plans**: 2 plans in 2 waves

**Plan list:**
- [ ] 03-ifc-01-PLAN.md — `ifc_export.py` + `matchline ifc-export` CLI + BuildingModel→BEMModel adapter (IFC-01, IFC-02 basic)
- [ ] 03-ifc-02-PLAN.md — Zone + SpaceLighting IFC4 export + cross-sheet window dedup + round-trip test (IFC-02 extended, IFC-03, IFC-04)

---

## Key Context

### What's already built
- `ifc_import.py` (Tier 0): imports IFC → BuildingModel with spaces, walls, openings, materials, opportunistic zones. Tests pass in `tests/test_ifc_import.py`.
- `bem_export.write_ifc4`: takes `BEMModel` (simplified: spaces + ring + openings), writes IFC4. Does NOT handle zones, lighting, HVAC.
- `elevation_windows.merge_elevation_observations`: deduplicates windows within a single `link_elevations` call.

### What's missing
1. `ifc_export.py` — does not exist. Needs to accept `BuildingModel` (not `BEMModel`) and emit IFC4.
2. Zone IFC export — `IfcZone` + `IfcRelAssignsToGroup` for many-to-many zone membership.
3. SpaceLighting IFC export — `IfcLightFixture` for each space with lighting.
4. Cross-sheet window dedup across *separate* `link_elevations` runs.

### IFC-02 scope
`SpaceHVAC` (terminal units, diffusers, sensors) is inventory data that maps to `IfcDuctSegment`, `IfcFlowTerminal`, etc. Phase 3 exports zones (HVAC zoning concept) but does NOT fully implement SpaceHVAC as IFC entities — that is a Phase 5 concern. IFC-02 in Phase 3 means: zones fully exported, lighting (total_w) exported as IfcLightFixture.

### Blocker resolved
- ~~`bem_export.write_ifc4` only accepts `BEMModel`, not full `BuildingModel`~~ — resolved: adapter in `ifc_export.py` converts BuildingModel → BEMModel, then extends with zones/lighting via post-processing.
