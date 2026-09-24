# BIM Import/Export Strategy

**Status**: Proposed

**Date**: 2026-09-23

**Source**: Issue #191

---

## Overview

BIM import/export enables round-tripping between matchline's canonical `BuildingModel` and industry-standard BIM formats (IFC4, gbXML). This document outlines the strategy for both import and export pipelines.

## IFC Import (`ifc_import.py`)

### Tier 0 — Direct IFC Read

Tier 0 reads IFC entities directly without geometric inference:

- **Scope**: Walls (`IfcWall`), Openings (`IfcOpeningElement`), Spaces (`IfcSpace`), Storeys (`IfcBuildingStorey`)
- **Provenance**: `method="ifc_import:tier0:<aspect>"`
- **Confidence**: 1.0 (direct read)
- **Attachment**: Openings are NOT attached to spaces at Tier 0

### Tier 1 — Geometric Inference

Tier 1 infers relationships from geometry:

- **Opening attachment**: If an opening's bounding box overlaps a wall's face within tolerance, attach it to the nearest space
- **Space boundaries**: Derive space boundaries from wall adjacencies
- **Provenance**: `method="ifc_import:tier1:<aspect>"`
- **Confidence**: 0.7–0.95 (depends on geometric inference confidence)

### Tier 2 — Multi-Sheet Fusion (Future)

When multiple IFC files or drawing sheets are available for the same building, Tier 2 fuses facts with conflict resolution and provenance tracking.

## IFC Export (`ifc_export.py`)

### Write Pipeline

```
BuildingModel → _bem_from_model() → write_ifc4() → IFC4 file
```

- **Output format**: IFC4 (ISO 16739-1:2013)
- **Validation**: XSD schema validation via `lxml` (when XSD available)
- **Fallback**: `lxml`-only parse when XSD not available
- **Provenance**: Original extraction method preserved through round-trip

### Supported Entities

| Entity Type | Support Level |
|---|---|
| IfcProject | Full |
| IfcSite | Full |
| IfcBuilding | Full |
| IfcBuildingStorey | Full |
| IfcSpace | Full |
| IfcWall | Full |
| IfcWallStandardCase | Full |
| IfcOpeningElement | Full |
| IfcDoor | Full |
| IfcWindow | Full |
| IfcZone | Planned |
| IfcSpaceZone | Planned |

### Limitations

- Only `IfcWall` and `IfcOpeningElement` currently support geometric representation
- Classification systems (e.g. OmniClass, UniFormat) not yet serialized
- Material properties not yet serialized

## gbXML Import/Export (`bem_export.py`)

### gbXML Smoke Check

When XSD validation is unavailable, a lightweight well-formedness check (`_gbxml_smoke_check`) validates gbXML using a safe XML parser with XXE protections enabled.

### Export Path Security

All export paths are validated via `_validate_out_path()` to prevent path traversal attacks.

## Supported Format Combinations

| Import | Export | Round-Trip |
|---|---|---|
| IFC4 | IFC4 | Yes (Tier 0) |
| IFC4 | gbXML | Yes (Tier 0) |
| Drawing OCR | IFC4 | Planned |
| Drawing OCR | gbXML | Planned |

## Roadmap

- [ ] Tier 1 geometric inference completion
- [ ] IFC4 classification serialization
- [ ] IFC4 material property serialization
- [ ] IFC2x3 support
- [ ] gbXML validation XSD updates
