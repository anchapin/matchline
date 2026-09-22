# IFC Export — `ifc_export.py`

Exports a canonical `BuildingModel` to **IFC4** format via the existing `write_ifc4` pathway. This is the second half of the IFC round-trip (`ifc_import.py` → `BuildingModel` → `ifc_export.py` → IFC4 file).

## Pipeline position

```
ifc_import  →  BuildingModel  →  _bem_from_model  →  write_ifc4  →  IFC4 file
```

`export_ifc(model_path, out_path)` loads a `BuildingModel` from JSON and writes IFC4. `_export_ifc(model, path)` accepts the model object directly.

## Coordinate frame conversion

The canonical building model uses **y-down** (drawing frame). IFC/gbXML use **y-north**.
`_bem_from_model` flips the y axis on all polygons before export:

```
(x, y_drawing)  →  (x, -y_drawing)  =  y_north
```

CCW winding for the envelope ring is also enforced (required by `write_ifc4` for outer boundary direction).

## Adapter: `BuildingModel` → `BEMModel`

`_bem_from_model` maps:

| BuildingModel | BEMModel | Notes |
|---|---|---|
| `Space.id` | `BEMSpace.sid` | Space identifier |
| `Space.label` | `BEMSpace.name` | e.g. "OPEN OFFICE 101" |
| `Space.number` | `BEMSpace.number` | Room number string |
| `Space.polygon_m` (y-flipped, CCW) | `BEMSpace.polygon_m` | Geometry |
| `Space.area_m2` | `BEMSpace.area_m2` | Floor area |
| `Space.volume_m3` or `area × wall_height` | `BEMSpace.volume_m3` | Volume |
| `Space.openings` (deduplicated) | `BEMOpeningUnit` list | By (tag, width, height) |
| `EnvelopeWall` segments | `BEMModel.ring_m` (CCW) | Built via `_build_ring` |
| `Zone.id` + `Zone.space_ids` | `BEMModel.zones` | Zone membership |

## Envelope ring construction

`_build_ring` assembles a closed polygon from `EnvelopeWall` segments:

1. Sort walls by facade order: south → east → north → west
2. Chain `from_m → to_m` for each segment, flipping y to y-north
3. Skip duplicate adjacent points

If no envelope is present, the ring falls back to the first space's polygon (y-flipped and CCW).

## Opening deduplication

Openings are flattened across all spaces then deduplicated by `(tag, width_m, height_m)`. The first occurrence's geometry is used; subsequent spaces sharing the same opening unit get a reference to it. This avoids duplicate `IfcOpeningElement` definitions.

## Wall height

Uses `model.levels[0].wall_height_m` if levels are defined, else defaults to 3.0 m.

## What `write_ifc4` handles

All IFC geometry is delegated to `bem_export.write_ifc4` (no ifcopenshell calls in this module). The BEMModel passed to `write_ifc4` contains:

- `BEMSpace` list with polygon, area, volume, name, number
- `BEMOpeningUnit` list for window/door insertions
- `ring_m` for the envelope polygon
- `zones` for space-to-zone membership

## Validation

`validate_ifc4()` in `bem_export` is called by the demo runner. It checks:
- Schema version is IFC4
- Wall `SweptSolid` geometry is present
- All spaces are aggregated under a `IfcBuildingStorey`
- Each opening voids a wall and is filled by an `IfcRelFillsElement`

## Round-trip fidelity

| Property | IFC import | IFC export | Round-trip |
|---|---|---|---|
| Space name/number | ✓ | ✓ | ✓ |
| Space polygon | ✓ (y-flipped) | ✓ (y-flipped) | ✓ |
| Space area | Computed from polygon | Stored | ≈ (within rounding) |
| Openings | ✓ (by tag+dimensions) | Deduplicated | ✓ |
| Zones | ✓ | ✓ | ✓ |
| Wall geometry | ✓ (envelope segments) | ✓ (ring) | Partial |

## Limitations (v1)

1. **Interior walls not modeled** — only the exterior envelope ring is exported; interior partitions are omitted from IFC.
2. **Single zone fallback** — when zones are absent, all spaces share "Zone 1"; true HVAC zoning requires zone input data.
3. **Wall height uniform** — all exterior walls share `model.levels[0].wall_height_m`; floor-to-floor variation is not modeled.
4. **Roof/slab not in IFC** — envelope ring only; no `IfcRoof` or `IfcSlab` elements in v1.
5. **Opening placement synthetic** — positions are computed from wall length and unit count; true positions require elevation-view parsing.
6. **No material assignments** — walls get no `IfcMaterialLayer`; placeholder constructions only.
