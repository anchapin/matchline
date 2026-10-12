---
phase: 03-ifc
plan: 01
type: execute
wave: 1
depends_on: []
files_modified: [ifc_export.py, cli.py]
autonomous: true
requirements: [IFC-01, IFC-02]
must_haves:
  truths:
    - "matchline ifc-export model.json out.ifc writes a valid IFC4 file"
    - "The IFC file contains IfcSpace, IfcWall, IfcWindow, IfcDoor entities"
    - "A BuildingModel with zones round-trips through ifc_export without losing zone membership"
  artifacts:
    - path: ifc_export.py
      provides: BuildingModel → IFC4 export with zone support
      min_lines: 80
    - path: cli.py
      provides: matchline ifc-export CLI subcommand
      contains: ifc-export
  key_links:
    - from: ifc_export.py
      to: bem_export.write_ifc4
      via: BuildingModel→BEMModel adapter then write_ifc4
    - from: cli.py
      to: ifc_export.py
      via: cmd_ifc_export function
---

<objective>
Build `ifc_export.py` as the BuildingModel→IFC4 exporter with `matchline ifc-export` CLI. This closes the IFC round-trip: existing `ifc_import.py` + new `ifc_export.py` enable `matchline ifc-import foo.ifc --out model.json` and `matchline ifc-export model.json out.ifc`. The adapter converts BuildingModel to BEMModel (which write_ifc4 already understands) and extends write_ifc4 to emit IfcZone + IfcRelAssignsToGroup.
</objective>

<context>
@bem_export.py — write_ifc4 signature, BEMModel structure, _wall_edges, _distribute_openings, _place_openings_on_wall
@building_model.py — BuildingModel, Space, Zone, SpaceLighting, SpaceHVAC, SpaceOpening
@ifc_import.py — import_ifc to understand the full BuildingModel contract
@cli.py — existing matchline subcommand patterns (cmd_ifc_import as reference)
@tests/test_ifc_import.py — fixture strategy: write_ifc4 then enrich with zones/lighting; round-trip test pattern
</context>

<interfaces>
From bem_export.py write_ifc4:
```python
def write_ifc4(model: BEMModel, path: str | Path, wall_thickness_m: float = 0.2) -> Path:
    # model: BEMModel(spaces: list[BEMSpace], openings: list[BEMOpeningUnit],
    #          ring_m, wall_height_m, building_name, ...)
```

BEMSpace has: sid, name, number, polygon_m, area_m2, volume_m3
BEMOpeningUnit has: category ("window"|"door"), tag, width_m, height_m

From building_model.py BuildingModel:
```python
class BuildingModel:
    name: str
    levels: List[Level]  # Level(id, name, elevation_z_m, wall_height_m)
    spaces: Dict[str, Space]  # Space.id = "{level}-{number}"
    zones: Dict[str, Zone]    # Zone.id, space_ids (List[str])
    envelope: List[EnvelopeWall]
    bim_elements: List[BimElement]
```

Space has: id, level_id, name, number, polygon_m, area_m2, volume_m2,
           openings: List[SpaceOpening],
           lighting: SpaceLighting,
           hvac: SpaceHVAC

Zone has: id, level_id, space_ids (List[str]), ...
SpaceLighting has: fixtures: List[FixtureInstance], total_w: float, ...
FixtureInstance has: id, tag, fixture_class, x_m, y_m, watts: Optional[float], ...
</interfaces>

<tasks>

<task type="auto">
  <name>Task 1: BuildingModel→BEMModel adapter</name>
  <files>ifc_export.py</files>
  <action>
Create `ifc_export.py` with:

1. `_bem_from_model(model: BuildingModel) -> BEMModel` adapter:
   - Map each Space (id like "L1-101") to a BEMSpace:
     - sid = space.id
     - name = space.label (e.g. "OPEN OFFICE 101")
     - number = space.number
     - polygon_m = space.polygon_m (canonical y-down → IFC needs y-north; write_ifc4 uses ring_m + _wall_edges which is y-north from the inside; confirm bem_export.model_from_takeoff flips y: polygon_px -> (x*s, -y*s) — so canonical is y-down, write_ifc4 ring is y-north. building_model.polygon_m is already canonical y-down. For IFC (y-north) pass [(x, -y) for x,y in polygon_m].
     - area_m2 = space.area_m2
     - volume_m3 = space.volume_m3 or (space.area_m2 * wall_height)
   - Flatten SpaceOpenings across all spaces into a BEMOpeningUnit list:
     - For each SpaceOpening: category, tag, width_m, height_m
     - Deduplicate by (tag, width_m, height_m) — one BEMOpeningUnit per unique opening spec
   - ring_m: build envelope ring from EnvelopeWall segments in canonical order
   - wall_height_m: from model.levels[0].wall_height_m or 3.0
   - building_name: model.name

2. `_export_ifc(model: BuildingModel, path: str | Path)`:
   - Call _bem_from_model to get bem
   - Call bem_export.write_ifc4(bem, path)

3. `export_ifc(model_path: str, out_path: str)`:
   - Load BuildingModel from JSON (BuildingModel.from_json(Path(model_path).read_text()))
   - Call _export_ifc

No new ifcopenshell calls — reuse write_ifc4. Keep the adapter clean so future extensions (zones, lighting) can be layered in.
  </action>
  <verify>
python -c "
from ifc_export import _bem_from_model
from building_model import BuildingModel, Space, Level, Provenance
model = BuildingModel(name='Test', levels=[Level(id='L1', wall_height_m=3.0)])
model.spaces['L1-101'] = Space(id='L1-101', level_id='L1', name='Office', number='101', polygon_m=[[0,0],[10,0],[10,8],[0,8]], area_m2=80.0)
bem = _bem_from_model(model)
assert bem.building_name == 'Test'
assert len(bem.spaces) == 1
assert bem.spaces[0].name == 'Office 101'
print('adapter OK')
"
  </verify>
  <done>BEMModel adapter passes basic assertion; ifc_export._export_ifc calls write_ifc4 without error</done>
</task>

<task type="auto">
  <name>Task 2: matchline ifc-export CLI subcommand</name>
  <files>cli.py</files>
  <action>
Add to cli.py:

1. `cmd_ifc_export` function:
```python
def cmd_ifc_export(args: argparse.Namespace) -> None:
    from ifc_export import export_ifc
    export_ifc(args.model, args.out)
    print(f"wrote {args.out}")
```

2. Subparser registration after the ifc-import subparser block (~line 177):
```python
p = sub.add_parser("ifc-export", help="Export BuildingModel JSON → IFC4 file")
p.add_argument("model", help="BuildingModel JSON file path")
p.add_argument("out", help="Output IFC4 file path")
p.set_defaults(func=cmd_ifc_export)
```

Also update the module docstring at top of cli.py to include `matchline ifc-export model.json out.ifc`.
</action>
  <verify>
matchline ifc-export --help prints the subcommand help
</verify>
  <done>matchline ifc-export model.json out.ifc runs without error and produces an IFC4 file</done>
</task>

</tasks>

<verification>
python -m pytest tests/test_ifc_import.py -x -q  # existing Tier 0 round-trip still passes
python -c "
from building_model import BuildingModel, Level, Space, Zone, Provenance
from ifc_export import _bem_from_model
# Smoke: adapter produces a BEMModel that write_ifc4 accepts
import bem_export, tempfile, os
model = BuildingModel(name='Smoke', levels=[Level(id='L1', wall_height_m=3.0)])
model.spaces['L1-1'] = Space(id='L1-1', level_id='L1', name='ROOM', number='1',
    polygon_m=[[0,0],[5,0],[5,4],[0,4]], area_m2=20.0)
bem = _bem_from_model(model)
with tempfile.NamedTemporaryFile(suffix='.ifc', delete=False) as tf:
    path = tf.name
try:
    bem_export.write_ifc4(bem, path)
    ok, errs = bem_export.validate_ifc4(path)
    assert ok, f'validate_ifc4 failed: {errs}'
    print('IFC4 smoke OK')
finally:
    os.unlink(path)
"
</verification>

<success_criteria>
1. `matchline ifc-export model.json out.ifc` completes with exit 0 and produces a file
2. `validate_ifc4(out.ifc)` returns (True, []) — structurally valid IFC4
3. `ifcopenshell.open(out.ifc).schema == "IFC4"`
4. Existing `test_ifc_import.py` tests still pass (Tier 0 round-trip unchanged)
</success_criteria>

<output>
After completion, create `.planning/phases/03-ifc/03-ifc-01-SUMMARY.md`
</output>
