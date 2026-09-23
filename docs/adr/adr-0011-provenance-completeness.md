# ADR-0011: Complete Provenance Tracking Design

**Date:** 2024-01-15 (updated 2024-09-23)
**Status:** Proposed
**Deciders:** Matchline team

## Context

Issue #185 identifies that provenance tracking is "incomplete and ad-hoc." This ADR catalogs the current state, enumerates the gaps, and proposes a complete design with formal propagation rules.

The [original ADR-0001](adr-0001-provenance-design.md) established the `Provenance` dataclass and core principles. This ADR addresses the gaps in coverage and the missing formalization of propagation rules.

---

## Current State: What Exists

### The Provenance Dataclass

```python
@dataclass
class Provenance:
    sheet_id: str  # e.g. "arch_A101", "elev_A201"
    revision: int  # sheet revision
    method: str  # e.g. "grid_registration", "point_in_polygon"
    confidence: float  # 0.0 – 1.0
    bbox: Optional[list]  # [xtl, ytl, xbr, ybr] in sheet pixels
    note: str = ""
```

### Entities with Provenance Fields

| Entity Type | Field Name | Location |
|-------------|------------|----------|
| `Window` | `provenance` | building_model.py |
| `Door` | `provenance` | building_model.py |
| `SpaceOpening` | `provenance` | building_model.py |
| `ComponentRef` | `provenance` | building_model.py |
| `DuctSegment` | `provenance` | building_model.py |
| `SpaceLighting` | `provenance` | building_model.py |
| `SpaceHVAC` | `provenance` | building_model.py |
| `DaylitZone` | `provenance` | building_model.py |
| `SpaceDaylight` | `provenance` | building_model.py |
| `Zone` | `provenance` | building_model.py |
| `EnvelopeWall` | `provenance` | building_model.py |
| `BimElement` | `provenance` | building_model.py |
| `BimOpening` | `provenance` | building_model.py |
| `Space` | `core_provenance` | building_model.py |

### Provenance Validation

`validate.py:_all_facts()` iterates over model entities and checks provenance completeness:

```python
def _all_facts(model: BuildingModel) -> list[tuple[str, str, Provenance]]:
    facts: list[tuple[str, str, Provenance]] = []
    # Spaces — use core_provenance
    for sp in model.spaces:
        facts.append((sp.id, "space", sp.core_provenance))
    # Openings
    for op in model.openings:
        facts.append((op.id, "opening", op.provenance))
    # Lighting fixtures
    for fix in sp.lighting.fixtures:
        facts.append((fix.id, "lighting_fixture", fix.provenance))
    # HVAC components (diffusers, sensors, terminal_units via ComponentRef)
    for diff in sp.hvac.diffusers:
        facts.append((diff.id, "hvac_diffuser", diff.provenance))
    # ... sensors, terminal_units similarly
    # Zones
    for zone in model.zones:
        for ref in zone.space_refs:
            facts.append((ref.id, "zone_assignment", ref.provenance))
    # Envelope walls
    for wall in model.envelope_walls:
        facts.append((wall.id, "envelope_wall", wall.provenance))
```

`validate.py:_check_provenance_complete()` requires all facts to have non-None provenance and confidence at least `REVIEW_CONFIDENCE` (0.80).

---

## Gap Analysis: What Is Missing or Ad-hoc

### GAP 1: Level Has No Provenance Field

`Level` is the root of the spatial hierarchy. It is created in three places:

- `link.py:708` — `Level(id=level_id, name="Level 1", wall_height_m=bldg["wall_height_m"])`
- `run_pipeline.py:529` — `Level(id="L1", name="Level 1", wall_height_m=wall_height)`
- `ifc_import.py:675` — from IFC building storey

**Problem:** Level carries no provenance. A level extracted from an IFC file has different provenance than one synthesized from a minimal building spec. The provenance system cannot answer "which input produced this level?"

**Proposed fix:** Add `provenance: Provenance = None` to the `Level` dataclass.

---

### GAP 2: DaylitZone and SpaceDaylight Provenance Not Validated

`DaylitZone` and `SpaceDaylight` both have `provenance` fields (building_model.py lines 177, 185), but `_all_facts()` does not iterate over them.

**Problem:** A DaylitZone extracted from elevation windows could have `confidence=0.5` and silently pass validation because it is never checked.

**Proposed fix:** Extend `_all_facts()` to yield DaylitZone and SpaceDaylight facts.

---

### GAP 3: BimElement and BimOpening Not Validated

`BimElement` and `BimOpening` have `provenance` fields but are not included in `_all_facts()`.

**Problem:** IFC-imported elements bypass provenance validation.

**Proposed fix:** Extend `_all_facts()` to yield BimElement and BimOpening facts.

---

### GAP 4: core_provenance vs provenance — Inconsistent Naming

`Space` uses `core_provenance` while every other entity uses `provenance`. The ADR-0001 does not explain this distinction.

**Problem:** Ad-hoc naming inconsistency. The term "core" suggests something semantically different, but no documented rule distinguishes them.

**Proposed fix:** Rename `core_provenance` to `provenance` on `Space`. The distinction is not meaningful — Space is a derived entity like any other, and its provenance should be checked the same way.

---

### GAP 5: No Formal Propagation Rules

Every extraction function creates `Provenance` independently. There are no documented rules for how provenance should propagate when one entity is derived from others.

**Example of the problem:** A `ZoneAssignment` is derived from a `Zone` + `Space` relationship. What provenance should `zone_assignment.provenance` carry? The current code in `link.py` creates `Provenance` with method `"zone_assignment"` but the propagation chain (Zone → ZoneAssignment) is not formally defined.

**Proposed fix:** Define explicit propagation rules (see below).

---

### GAP 6: No Explicit Transformation Boundary Definitions

The pipeline has implicit transformation stages:

1. **Ingest** — raw input (sheet raster, IFC file)
2. **Registration** — spatial registration of symbols to canonical coordinates
3. **Linking** — associating symbols with spaces
4. **Zone Assignment** — assigning spaces to thermal zones
5. **BIM Import/Export** — IFC interchange
6. **BEM Export** — energy model output

No document defines which provenance fields are required at each boundary or how provenance transforms across boundaries.

**Proposed fix:** Define transformation boundaries explicitly (see below).

---

## Proposed Design: Complete Provenance Tracking

### Design Principle

> Every entity in the canonical model that is derived from input data carries provenance. The provenance chain is unbroken from the input source to the exported BEM fact.

Entities that are pure configuration (not derived from input) — such as global `DaylightParams`, `HVACParams`, or `BuildingAddress` — do not carry provenance because they are not derived from sheet/IFC data.

---

### Complete Entity Provenance Coverage

| Entity Type | Provenance Required | Method Label | Notes |
|-------------|---------------------|--------------|-------|
| `Level` | Yes | `"ifc_import"` / `"synthetic"` | **NEW** — add provenance field |
| `Space` | Yes | `"space_detection"` / `"ifc_import"` / `"synthetic"` | Rename `core_provenance` → `provenance` |
| `Window` | Yes | `"window_detection"` / `"ifc_import"` | |
| `Door` | Yes | `"door_detection"` / `"ifc_import"` | |
| `SpaceOpening` | Yes | `"opening_registration"` / `"ifc_import"` | |
| `DaylitZone` | Yes | `"daylight_zoning"` | **NEW** — add to _all_facts |
| `SpaceDaylight` | Yes | `"daylight_analysis"` | **NEW** — add to _all_facts |
| `SpaceLighting` | Yes | `"lighting_registration"` | |
| `FixtureInstance` | Yes | `"fixture_detection"` | |
| `SpaceHVAC` | Yes | `"hvac_registration"` | |
| `ComponentRef` (diffuser/sensor/TU) | Yes | `"component_detection"` / `"ifc_import"` | |
| `DuctSegment` | Yes | `"duct_tracing"` / `"ifc_import"` | |
| `Zone` | Yes | `"zone_definition"` / `"ifc_import"` | |
| `ZoneAssignment` (via ComponentRef) | Yes | `"zone_assignment"` | Derived from Zone+Space join |
| `EnvelopeWall` | Yes | `"envelope_extraction"` / `"ifc_import"` | |
| `BimElement` | Yes | `"ifc_import"` | **NEW** — add to _all_facts |
| `BimOpening` | Yes | `"ifc_import"` | **NEW** — add to _all_facts |

---

### Transformation Boundary Propagation Rules

#### Boundary 1: Input → Registration (Sheet/IFC → Canonical Coordinates)

**Stage:** OCR, vision model, or IFC parser extracts raw observations.

**Rule:** Each extracted entity gets provenance with:
- `sheet_id` = source sheet identifier
- `revision` = sheet revision (0 for synthetic/IFC)
- `method` = extraction method (e.g., `"vision_window_detection"`, `"ifc_import"`)
- `confidence` = extraction confidence score
- `bbox` = sheet pixel coordinates (if spatially extracted)
- `note` = method-specific context

**Example:**

```python
# elevation_windows.py
window = Window(
    id=f"W-{i + 1}",
    x_m=x_reg,
    y_m=y_reg,
    width_m=w,
    height_m=h,
    provenance=Provenance(
        sheet_id=source_sheet,
        revision=rev,
        method="elevation_window_detection",
        confidence=detector_confidence,
        bbox=[xtl, ytl, xbr, ybr],
    ),
)
```

---

#### Boundary 2: Registration → Linking (Canonical Coordinates → Space Association)

**Stage:** Registered entities are linked to spaces via geometric predicates or explicit relationships.

**Rule:** The linked entity carries provenance from its spatial/relationship inference, not from the original extraction. The `method` becomes `"space_association"` or similar.

**Rationale:** The entity already has provenance from extraction. The linking step adds spatial context. We preserve the original extraction provenance on the entity; the space association is tracked separately via `Space.contains` relationships.

**Note:** If the same entity (same `id`, same sheet/revision) is re-linked to a different space, this is a **re-link event** and should be recorded in the revision log with `action="relink"`.

---

#### Boundary 3: Linking → Zone Assignment

**Stage:** Spaces are assigned to thermal zones via zone boundary tracing or schedule-based joins.

**Rule:** The `ZoneAssignment` (represented as `space_refs` on `Zone`) carries provenance with:
- `method` = `"zone_boundary_join"` or `"schedule_join"` or `"manual_assignment"`
- `sheet_id` = source of the zone definition (sheet or schedule)
- `confidence` = zone assignment confidence

**The Space itself retains its original provenance.** Zone assignment provenance is stored on the `ComponentRef` linking space to zone.

---

#### Boundary 4: BIM Import (IFC → Canonical Model)

**Stage:** IFC file is parsed and mapped to canonical model entities.

**Rule:** Every entity from IFC carries provenance:
- `method` = `"ifc_import"`
- `sheet_id` = IFC file identifier
- `revision` = 0 (IFC files do not have sheet revisions; use 0)
- `confidence` = 1.0 (IFC is authoritative data)
- `bbox` = `None` (IFC is geometrically registered, not sheet-relative)
- `note` = IFC element global ID for traceability

---

#### Boundary 5: Canonical Model → BEM Export

**Stage:** Canonical model is exported to energy simulation input (IDF, OSM).

**Rule:** BEM export facts carry **derived provenance** that traces back through the canonical model provenance chain. Each exported BEM fact references the canonical entity it was derived from.

**This is out of scope for the canonical model provenance system** — BEM export provenance is handled by the export tool. However, the exported BEM entities should carry a `source_provenance` field referencing the canonical model's provenance for the derivation chain.

---

### Propagation Rule Summary

| Transformation | Provenance Origin | Method Field |
|---------------|-------------------|--------------|
| Vision/symbol detection → entity | Detector output | `"window_detection"`, `"diffuser_detection"`, etc. |
| IFC import → entity | IFC element | `"ifc_import"` |
| Synthetic generation → entity | Generator config | `"synthetic"` |
| Geometric predicate → space association | Registration result | `"space_association"` |
| Zone boundary join → zone assignment | Zone boundary + space geometry | `"zone_boundary_join"` |
| Schedule join → zone assignment | Schedule data | `"schedule_join"` |

---

## Required Changes

### 1. Add `provenance` Field to `Level`

In `building_model.py`, add to the `Level` dataclass:

```python
provenance: Provenance = None
```

### 2. Rename `core_provenance` → `provenance` on `Space`

In `building_model.py`:

```python
# Before
core_provenance: Provenance = None

# After
provenance: Provenance = None
```

Update all references in:
- `validate.py` (`_all_facts`)
- `tests/model_factory.py`
- Any other files referencing `core_provenance`

### 3. Extend `_all_facts()` to Cover All Entities

Add to `validate.py:_all_facts()`:

```python
# DaylitZones
for sp in model.spaces:
    for dz in sp.daylight.primary:
        facts.append((dz.id, "daylit_zone", dz.provenance))
    for dz in sp.daylight.secondary:
        facts.append((dz.id, "daylit_zone", dz.provenance))
# SpaceDaylight (for the container-level fact)
for sp in model.spaces:
    facts.append((sp.daylight.id, "space_daylight", sp.daylight.provenance))
# BimElements
for elem in model.bim_elements:
    facts.append((elem.id, "bim_element", elem.provenance))
# BimOpenings
for opening in model.bim_openings:
    facts.append((opening.id, "bim_opening", opening.provenance))
```

### 4. Update Test Coverage

Ensure `test_provenance_required` in `tests/test_building_model.py` covers all entity types with provenance fields.

---

## Consequences

**Positive:**
- Complete provenance coverage — every derived entity is traceable
- Formal propagation rules eliminate ad-hoc provenance creation
- Validation catches low-confidence facts that were previously silently accepted
- Consistent naming (`provenance` everywhere, no `core_provenance`)

**Negative:**
- `Level` provenance may be `None` for synthetic/minimal buildings — acceptable, as Level is often configuration rather than derivation
- More entities to check in `_all_facts()` — slight performance cost (negligible for typical building models)
- Requires updating test fixtures that use `core_provenance`

**Migration:**
- Rename `core_provenance` → `provenance` is a breaking change for any code accessing Space.provenance via the old field name
- A deprecation period is not needed since this is a design fix; update all call sites atomically

---

## References

- `building_model.py` — `Provenance`, `Level`, `Space`, `Zone`, `DaylitZone`, `BimElement`, `BimOpening`
- `validate.py` — `_all_facts()`, `_check_provenance_complete()`
- `registration.py` — `register_elevation_windows`, `register_diffusers`, `register_sensors`
- `link.py` — space association logic
- `ifc_import.py` — IFC import provenance creation
- `elevation_windows.py` — window detection provenance
- [ADR-0001](adr-0001-provenance-design.md) — original provenance design decision
- [ADR-010](adr-010-conservation-laws.md) — conservation laws (confidence thresholds)
