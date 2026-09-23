# CFG-04: Provenance Tracking System

**Decision**: Establish a formal provenance model for all extracted BEM facts with structured provenance records carrying sheet, revision, method, and confidence fields.

**Status**: Design proposed — implementation incomplete

**Date**: 2026-09-23

**Source**: Issue #185 (ad-hoc provenance tracking), Issue #190 (missing design doc)

---

## Context

Every extracted fact in matchline must carry provenance to support auditing and confidence scoring. Currently, provenance is attached ad-hoc per module with no unified schema. This design formalizes the provenance model.

## Decision

### Provenance Record Schema

Every extracted fact carries a `Provenance` record:

```python
@dataclass
class Provenance:
    sheet_id: str  # Source drawing/sheet identifier
    revision: str  # Drawing revision (e.g. "A", "3")
    method: str  # Extraction method (e.g. "ocr", "symbol_detect", "ifc_import:tier0:opening")
    confidence: float  # 0.0–1.0 confidence in the extraction
    extraction_params: dict | None  # Optional: model version, threshold, etc.
```

### Method Naming Convention

Methods follow a hierarchical naming scheme:

- `ocr:<aspect>` — text extraction from drawings
- `symbol_detect:<model>:<aspect>` — YOLO-based symbol detection
- `geometry_inference:<rule>` — derived from geometry (e.g. area from polygon)
- `ifc_import:tier0:<aspect>` — direct IFC read (no inference)
- `ifc_import:tier1:<aspect>` — IFC + geometric inference

### Confidence Calibration

| Confidence | Meaning |
|---|---|
| 0.95–1.0 | Direct extraction, high certainty |
| 0.7–0.94 | Standard extraction with validation |
| 0.5–0.69 | Inference-based, review recommended |
| < 0.5 | Low confidence — must go to review queue |

### Low-Confidence Policy

Facts with confidence < 0.5 MUST NOT be silently accepted. They are routed to the review queue for human confirmation before inclusion in the exported BEM model.

## Implementation Gaps

The following are not yet implemented:

1. **Confidence propagation**: When combining facts from multiple sources (e.g. two elevation sheets), the confidence of the combined fact should be derived from source confidences (e.g. max, or product).
2. **Provenance on validated facts**: `validate.py` conservation law checks produce derived facts (e.g. computed area) that should also carry provenance.
3. **Provenance serialization**: The BEM JSON export format does not yet include provenance records in the serialized output.
4. **Provenance UI**: The review queue (`matchline review`) should display provenance alongside each fact for human decision-making.

## Proposed Implementation Plan

1. **Schema enforcement**: Add `Provenance` field to all dataclasses in `building_model.py`
2. **Confidence propagation**: Implement `combine_provenance()` utility in `provenance.py`
3. **Export serialization**: Update `bem_export.py` write functions to include provenance in JSON output
4. **Review integration**: Update `review.py` to display and act on provenance

## Related

- `docs/design-docs/core-beliefs.md` — core beliefs on auditability
- `docs/ifc_import.md` — provenance in IFC import
- `docs/ifc_export.md` — provenance in IFC export
