# ADR-0001: Provenance Design

**Date:** 2024-01-15
**Status:** Accepted
**Deciders:** Matchline team

## Context

Every fact in the canonical building model is derived from an input — an architectural floor plan, an elevation sheet, a mechanical plan, an IFC file, or a schedule. Derived facts include detected window geometries, registered diffusers, traced duct segments, zone assignments, and component tag lookups.

Without provenance, the model cannot answer:
- Which sheet revision produced a given fact?
- Was this extraction from a drawing parse, a geometric predicate, or a schedule join?
- Is this fact below the confidence threshold and should be reviewed before being accepted?

The **conservation law** for this project states: "Low-confidence results go to the review queue — nothing is silently accepted." Provenance is the mechanism that makes this law enforceable.

## Decision Drivers

- Every derived fact must carry its source location (sheet + revision) for auditability.
- Supersession: when a sheet is re-issued, new facts from the newer revision supersede older facts; superseded facts are preserved in `history`, never silently overwritten.
- Review routing: the review queue routes facts by confidence and extraction method; routing requires per-fact provenance.
- Debugging: when an extraction is wrong, the `bbox` field provides pixel coordinates on the source sheet for human verification.

## Decision

Every derived fact in the canonical model carries a `Provenance` record.

```python
@dataclass
class Provenance:
    sheet_id: str  # e.g. "arch_A101", "elev_A201"
    revision: int  # sheet revision this fact was extracted from
    method: str  # e.g. "grid_registration", "point_in_polygon",
    # "duct_tracing", "schedule_join", "symbol_detection"
    confidence: float  # 0.0 – 1.0
    bbox: Optional[list]  # [xtl, ytl, xbr, ybr] in sheet pixels
    note: str = ""  # free-text context
```

### Fields

| Field | Type | Purpose |
|---|---|---|
| `sheet_id` | `str` | Identifies the source sheet (e.g., `"arch_A101"`, `"mech_M201"`) |
| `revision` | `int` | Sheet revision number; facts from newer revisions supersede older ones |
| `method` | `str` | Extraction method; enables method-specific confidence calibration and review routing |
| `confidence` | `float` | 0.0–1.0; facts below `REVIEW_CONFIDENCE` (default 0.80) are flagged for review |
| `bbox` | `Optional[list]` | Bounding box `[xtl, ytl, xbr, ybr]` in sheet pixel coordinates; `None` for non-spatial extractions (e.g., schedule joins) |
| `note` | `str` | Free-text annotation; used for method-specific context (e.g., fallback reason) |

### How Facts Are Annotated

Extraction functions return `(value, provenance)` tuples or attach `Provenance` directly to model objects:

```python
# Pattern 1: tuple return
x_m, y_m, provenance = detect_window(sheet, bbox)

# Pattern 2: field attachment
component = Diffuser(id="D3", x_m=1.2, y_m=3.4, provenance=Provenance(...))
```

All model classes that hold derived data — `ComponentRef`, `WindowRef`, `DuctSegment`, `Space`, `Zone` — carry an optional `provenance: Provenance` field with a default of `None` (allowing plain data constructions to omit it).

### Provenance in the Revision Log

Every change to the model is recorded in `RevisionEvent`:

```python
@dataclass
class RevisionEvent:
    seq: int  # monotonically increasing sequence number
    sheet_id: str  # which sheet triggered this event
    revision: int  # revision of that sheet
    action: str  # "ingest" | "relink" | "supersede"
    note: str = ""
```

When a newer revision of a sheet is ingested:
1. New facts are computed with the new `revision` in their `Provenance`.
2. Facts from the same `sheet_id` with an older `revision` are moved to `history` with `action="supersede"`.
3. The revision log records `action="supersede"` with a reference to the superseded facts.

This preserves a complete, auditable history. No fact is ever silently overwritten.

### Review Queue Integration

`confidence < REVIEW_CONFIDENCE` (default 0.80) triggers `flag_for_review()`:

```python
if fact.provenance.confidence < REVIEW_CONFIDENCE:
    model.flag_for_review(fact, reason="low_confidence")
```

The review queue routes flagged facts by `method`, allowing method-specific thresholds (e.g., `symbol_detection` may have different baseline confidence than `grid_registration`).

### Design Rationale

**Why not sheet-level provenance only?**
Sheet-level provenance makes supersession coarse-grained: an entire component (e.g., all windows from `elev_A201`) would be invalidated on revision, even if only one window changed. Per-fact provenance enables surgical supersession and preserves history at the correct granularity.

**Why a dataclass rather than a dict?**
`Provenance` is a `dataclass` so it is serializable via `to_json()`/`from_json()`, sortable (for `history` ordering), and IDE-completion-friendly. The `is_dataclass` check in `building_model._recursive_from_dict` enables transparent revival from JSON.

**Why `bbox` in sheet pixels, not canonical meters?**
Sheet pixel coordinates are what the vision model returns. Converting to canonical meters requires registration, which may fail or be approximate. Storing the raw `bbox` alongside the derived fact ensures the original observation is always recoverable, regardless of downstream registration state.

**Why `method` as a free string, not an enum?**
New extraction methods are added during development (e.g., `"geometric_fallback"`, `"ifc_direct"`, `"ocr_schedule_join"`). A string field avoids the need to update an enum definition every time a new method is introduced. Tests and review routing pattern-match on method strings directly.

**Why `confidence` is a raw float, not discrete levels?**
Confidence scores from different extraction methods are not directly comparable (a vision model's output confidence and a geometric predicate's coverage ratio have different scales). Storing the raw float allows review routing logic to apply method-specific thresholds without forcing a premature normalization.

## Consequences

**Positive:**
- Full audit trail per fact; any derived value can be traced back to its source sheet, revision, and extraction method.
- Supersession works at fact granularity; `history` preserves the complete provenance chain.
- Review queue routing by `method` and `confidence` is straightforward to implement.
- `bbox` enables human verification by rendering the source region on the sheet raster.

**Negative:**
- Extraction functions must consistently attach `Provenance`; this is enforced by `test_provenance_required` in `tests/test_building_model.py`.
- Memory footprint per fact is higher than a provenance-free design, but facts are small and the `history` store is append-only (not queried in the hot path).

## References

- `building_model.py` — `Provenance`, `RevisionEvent`, `ComponentRef`, `REVIEW_CONFIDENCE`
- `registration.py` — `register_elevation_windows`, `register_diffusers`, `register_sensors` (all attach provenance)
- `tests/test_building_model.py` — provenance field enforcement
- `tests/test_review_queue_routing.py` — method-based review routing
