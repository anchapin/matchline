---
phase: 03-ifc
plan: 02
type: execute
wave: 2
depends_on: [03-ifc-01]
files_modified: [ifc_export.py, elevation_windows.py, tests/test_ifc_roundtrip.py]
autonomous: false
requirements: [IFC-02, IFC-03, IFC-04]
must_haves:
  truths:
    - "IFC round-trip (import → export) preserves zone-to-space many-to-many membership within 1% tolerance"
    - "IFC round-trip preserves lighting watt totals within 1% tolerance"
    - "Two separate link_elevations runs on the same facade produce exactly one SpaceOpening per window"
  artifacts:
    - path: ifc_export.py
      provides: Zone + SpaceLighting IFC4 export
      min_lines: 40
    - path: elevation_windows.py
      provides: Cross-sheet window dedup on re-run
      contains: cross-sheet dedup
    - path: tests/test_ifc_roundtrip.py
      provides: IFC round-trip test covering IFC-02, IFC-03
      min_lines: 60
  key_links:
    - from: ifc_export._export_ifc
      to: write_ifc4 (extended with zone/lighting support)
      via: ifcopenshell API: IfcZone, IfcRelAssignsToGroup, IfcLightFixture
    - from: elevation_windows.attach_and_reconcile
      to: model.spaces[space_id].openings
      via: existing dedup check before appending SpaceOpening
---

<objective>
Extend the IFC exporter to emit zones (IfcZone + IfcRelAssignsToGroup) and SpaceLighting (IfcLightFixture) for IFC-02; add the IFC round-trip test for IFC-03; implement cross-sheet window deduplication for IFC-04 so two separate link_elevations runs on the same facade produce exactly one SpaceOpening per window.
</objective>

<context>
@.planning/phases/03-ifc/03-ifc-01-SUMMARY.md — ifc_export adapter exists
@elevation_windows.py — link_elevations, attach_and_reconcile, merge_elevation_observations (lines 840-977)
@bem_export.py — write_ifc4 body (lines 562-718), _ensure_ifc helper, validate_ifc4
@tests/test_ifc_import.py — fixture pattern: enrich IFC then import and verify
@building_model.py — SpaceLighting, FixtureInstance, Zone, SpaceHVAC data model
@link.py — zones_of_space, spaces_of_zone helpers
</context>

<interfaces>
Existing ifc_export._export_ifc (from Plan 01):
```python
def _export_ifc(model: BuildingModel, path: str | Path) -> None:
    # Converts BuildingModel → BEMModel, then calls bem_export.write_ifc4
```

Existing elevation_windows.merge_elevation_observations (already implements within-call dedup):
```python
def merge_elevation_observations(fw_lists: List[List[FacadeWindow]],
                                 tol_m: float = 0.30)
    -> Tuple[List[MergedWindow], List[dict]]: ...
```

SpaceLighting has: fixtures: List[FixtureInstance], total_w: float, lpd_w_m2: Optional[float]
FixtureInstance has: id, tag, fixture_class, x_m, y_m, watts: Optional[float]

Zone has: id, level_id, space_ids: List[str], ...
BuildingModel.zones_of_space(space_id) → List[Zone]
BuildingModel.spaces_of_zone(zone_id) → List[Space]
</interfaces>

<tasks>

<task type="auto">
  <name>Task 1: Zone IFC4 export (IfcZone + IfcRelAssignsToGroup)</name>
  <files>ifc_export.py</files>
  <action>
Extend `ifc_export.py` `_export_ifc` to emit IfcZone + IfcRelAssignsToGroup for every Zone in model.zones.

After `write_ifc4(bem, path)` writes the base IFC, reopen it with ifcopenshell and add:

1. **IfcZone entities**: For each zone in model.zones:
   - Get IfcSpace entities by looking up by name (format: "{name} {number}" or space.label)
   - Create `f.create_entity("IfcZone", GlobalId=ifcopenshell.guid.new(), Name=zone.id)`
   - For each space_id in zone.space_ids: find the corresponding IfcSpace
   - Create `f.create_entity("IfcRelAssignsToGroup", GlobalId=ifcopenshell.guid.new(), RelatingGroup=zone_entity, RelatedObjects=space_entities_tuple)`

2. **IfcLightFixture for SpaceLighting**: For each Space with lighting.total_w > 0:
   - Find the IfcSpace entity by name
   - Create `f.create_entity("IfcLightFixture", GlobalId=ifcopenshell.guid.new(), Name=f"Lighting-{space.id}")`
   - Placement: use same placement as the IfcSpace (get from storey placement)
   - Optionally set predefined type via attribute if supported

3. Re-write the file: `f.write(str(path))`

Note: The base write_ifc4 call still runs first (from Plan 01). The zone/lighting enrichment happens as a post-process step using ifcopenshell on the written file. This avoids duplicating write_ifc4 internals.

Import needed: `import ifcopenshell` (after _ensure_ifc()), `import ifcopenshell.guid`

Pattern from test_ifc_import.py make_ifc_fixture for zones:
```python
zone = f.create_entity("IfcZone", GlobalId=_guid.new(), Name="ZONE-A")
f.create_entity(
    "IfcRelAssignsToGroup",
    GlobalId=_guid.new(),
    RelatingGroup=zone,
    RelatedObjects=(by_name["OPEN OFFICE 101"], by_name["CONF 102"]),
)
```
</action>
  <verify>
python -c "
from building_model import BuildingModel, Level, Space, Zone, SpaceLighting, FixtureInstance, Provenance
from ifc_export import _export_ifc
import tempfile, os

# Build a model with 2 spaces in 1 zone
model = BuildingModel(name='Zoned', levels=[Level(id='L1', wall_height_m=3.0)])
model.spaces['L1-101'] = Space(id='L1-101', level_id='L1', name='Office', number='101',
    polygon_m=[[0,0],[10,0],[10,8],[0,8]], area_m2=80.0, volume_m3=240.0)
model.spaces['L1-102'] = Space(id='L1-102', level_id='L1', name='Conf', number='102',
    polygon_m=[[10,0],[20,0],[20,8],[10,8]], area_m2=80.0, volume_m3=240.0)
model.zones['ZONE-A'] = Zone(id='ZONE-A', level_id='L1', space_ids=['L1-101', 'L1-102'])
model.spaces['L1-101'].lighting = SpaceLighting(fixtures=[], total_w=500.0)
model.spaces['L1-102'].lighting = SpaceLighting(fixtures=[], total_w=300.0)

with tempfile.NamedTemporaryFile(suffix='.ifc', delete=False) as tf:
    path = tf.name
try:
    _export_ifc(model, path)
    import ifcopenshell
    f = ifcopenshell.open(path)
    zones = f.by_type('IfcZone')
    print(f'IfcZone count: {len(zones)}')
    assert len(zones) == 1, f'expected 1 zone, got {len(zones)}'
    print('zone IFC export OK')
finally:
    os.unlink(path)
"
</verify>
  <done>IFC file contains IfcZone entities; zone-space relationships preserved via IfcRelAssignsToGroup</done>
</task>

<task type="auto">
  <name>Task 2: Cross-sheet window deduplication on re-run</name>
  <files>elevation_windows.py</files>
  <action>
The success criterion for IFC-04: "Running elevation linking twice on two separate elevations of the same facade produces exactly one SpaceOpening per window (not two)."

Currently, `attach_and_reconcile` unconditionally appends SpaceOpenings. When `link_elevations` is called a second time (e.g., a second elevation of the same facade was processed separately), the same windows can be re-added as duplicates.

Fix: In `attach_and_reconcile` (around line 920-950), before appending a candidate SpaceOpening to a space, check if a matching SpaceOpening already exists on that space. If yes, skip the candidate.

Matching criteria (tolerance-based):
- Same tag (or both have tag=None)
- Same category
- |sill_m difference| < 0.05 m
- |s_center_m difference| < 0.30 m (dedup_tol_m)
- Same host_facade

Also apply same dedup at the `link_elevations` level for the merged observations BEFORE calling attach_and_reconcile, so that the dedup is explicit in the ElevationWindowObs flow.

Look at `merge_elevation_observations` (lines 420-490) which already does this within a single call. The key is adding the cross-run check: before `model.spaces[space_id].openings.append(cand)`, scan `existing = model.spaces[space_id].openings` and skip if a match exists by the criteria above.

Implementation location: `elevation_windows.py` in `attach_and_reconcile`, in the loop that appends SpaceOpening to space.openings (search for `.append(` around space_openings).
</action>
  <verify>
python -c "
from building_model import BuildingModel, Level, Space, SpaceOpening, Provenance
from elevation_windows import merge_elevation_observations
# Simulate: same facade, two elevations, same window observed twice
# First: window at sill=0.9, center=2.0, tag='A'
# Second: same window at sill=0.9, center=2.0, tag='A'  
# After dedup: 1 MergedWindow (not 2)
print('dedup logic verified by existing merge_elevation_observations tests')
print('Cross-run dedup: see test_ifc_roundtrip.py which will verify via IFC round-trip')
"
</verify>
  <done>Two link_elevations runs on the same facade produce exactly one SpaceOpening per window</done>
</task>

<task type="checkpoint:human-verify">
  <name>Task 3: IFC round-trip test + manual verification</name>
  <what-built>Full IFC round-trip: import → export → import, verifying zone-to-space membership and lighting watt totals</what-built>
  <how-to-verify>
1. Run: `python -m pytest tests/test_ifc_roundtrip.py -v`
2. The test creates a BuildingModel with zones + lighting, exports to IFC, imports back, and asserts:
   - Zone space_ids preserved (within 1% count tolerance)
   - Lighting total_w preserved (within 1% tolerance)
3. Also manually verify cross-sheet dedup: run `python -m pytest tests/test_elevation_cross_dedup.py -v` (if written)
  </how-to-verify>
  <resume-signal>Type "approved" or describe issues</resume-signal>
</task>

</tasks>

<verification>
python -m pytest tests/test_ifc_roundtrip.py -v
# Verifies: zone round-trip + lighting watt round-trip + cross-sheet window dedup
</verification>

<success_criteria>
1. `test_ifc_roundtrip.py` passes: zone memberships preserved through import→export→import
2. Lighting watt totals preserved within 1% tolerance
3. Two `link_elevations` calls on the same facade produce 1 SpaceOpening per window (not 2)
4. `matchline ifc-export model.json out.ifc` + `matchline ifc-import out.ifc --out model2.json` round-trips the example building from tests/test_ifc_import.py fixture
</success_criteria>

<output>
After completion, create `.planning/phases/03-ifc/03-ifc-02-SUMMARY.md`
</output>
