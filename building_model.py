"""Canonical building model: the cross-discipline linking layer.

Problem: every discipline draws the same building in its own coordinate
system with its own symbols, and nothing carries a shared ID except the
room number. This module defines the ONE canonical model everything
registers into:

  * The ARCHITECTURAL FLOOR PLAN is the reference frame. Rooms (name +
    number) are the primary key: ``Space.id = "{level}-{number}"``.
  * Lighting plans, mechanical plans, and elevations are parsed into
    sheet-local models, REGISTERED into arch-plan coordinates
    (registration.py), then linked: fixtures/sensors/diffusers land in
    spaces by point-in-polygon; elevation windows land on rooms via
    facade wall-run correspondence; duct-tracing zones attach by diffuser
    positions (no fragile cross-sheet id matching).
  * Zones are MANY-TO-MANY with spaces (an open office can span two
    zones). There is no zone tree.

Provenance: EVERY derived fact carries a Provenance record (sheet id,
sheet revision, extraction method, confidence, source bbox). Facts from a
newer revision of the same sheet SUPERSEDE older facts -- they are kept
in ``history``, never silently overwritten.

Coordinate frame (canonical): meters, y growing DOWNWARD (drawing frame,
matches every synth layout and sheet raster). BEM export flips y to
north-up; see bem_export.model_from_takeoff.

JSON: BuildingModel.to_json() / from_json() round-trip the whole model
(model_version included) so the canonical model is a versioned artifact
that diffs cleanly across drawing revisions.
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Literal, Optional, Union, get_args, get_origin, get_type_hints

from datasets_adapter import ScheduleEntry

MODEL_VERSION = "1.2"
# 1.0 -> 1.1: SpaceOpening gained ``source_provenance`` (1-2 per-sheet records,
# issue #663). 1.0 payloads are migrated on load by _migrate_model_dict.
# 1.1 -> 1.2: SpaceOpening gained ``adjacent_space_id`` (the space on the far
# side of an interior wall opening, issue #666). Older payloads load with it
# None, which is what the default already gives.

# Links below this confidence are flagged for human review, not silently
# accepted (review queue, not dropped).
REVIEW_CONFIDENCE = 0.80

# Auto-triage via the local review_classifier TypedDecider is ON by default.
# Low-confidence results are automatically routed to the review queue.
ENABLE_AUTO_TRIAGE = True


# ---------------------------------------------------------------------------
# Provenance + revisions
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    """Where one fact came from. Attached to EVERY derived fact."""

    sheet_id: str  # e.g. "arch_A101", "elev_A201"
    revision: int  # sheet revision this fact was extracted from
    method: str  # e.g. "grid_registration", "geometric_fallback",
    # "point_in_polygon", "duct_tracing", "schedule_join"
    confidence: float  # 0..1
    bbox: Optional[list] = None  # [xtl,ytl,xbr,ybr] in sheet px, if applicable
    note: str = ""


@dataclass
class SymbolLinkage:
    """Explicit link between a symbol instance and its schedule row.

    Represents a resolved or unresolved link from a symbol detection
    (e.g. window tag) to a schedule entry. The linkage graph is
    queryable: callers can traverse from symbol → schedule row to
    get dimensions, type, and other metadata for takeoffs.

    Attributes:
        symbol_id: Unique identifier for this symbol instance (detection).
        symbol_tag: The tag label from the symbol (e.g. "W1", "D1").
        category: Symbol category (e.g. "window", "door").
        schedule_entry: The matched schedule entry, or None if unlinked.
        confidence: Confidence score for the match (0..1).
        provenance: Provenance information (sheet, revision, method).
    """

    symbol_id: str
    symbol_tag: str
    category: str
    schedule_entry: "ScheduleEntry | None" = None
    confidence: float = 0.0
    provenance: Provenance = field(default_factory=lambda: Provenance("", 0, "", 0.0))


@dataclass
class RevisionEvent:
    """One entry in the model's revision log."""

    seq: int
    sheet_id: str
    revision: int
    action: str  # "ingest" | "relink" | "supersede"
    note: str = ""


# ---------------------------------------------------------------------------
# Component references (sheet-detected equipment, registered to canonical m)
# ---------------------------------------------------------------------------


@dataclass
class ComponentRef:
    """One detected equipment instance, in canonical meters."""

    id: str  # stable within its sheet, e.g. "VAV-1", "D3", "A-12"
    type: str  # "vav" | "ahu" | "diffuser" | "grille" | "sensor" |
    # "fixture" | "window"
    x_m: float
    y_m: float
    tag: str = ""  # schedule tag, e.g. "A", "VAV-1"
    provenance: Provenance | None = None
    history: List[Provenance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Space sub-records
# ---------------------------------------------------------------------------


@dataclass
class SpaceOpening:
    """One window/door on this space's walls (from an elevation), or a skylight on its roof."""

    id: str
    tag: str
    category: str  # "window" | "door" | "skylight"
    width_m: float
    height_m: float
    sill_m: Optional[float] = None
    head_m: Optional[float] = None  # sill + height; drives daylighting
    host_facade: str = ""  # e.g. "south"
    host_interval_m: Optional[list] = None  # [s0, s1] along facade (IFC: along host wall), m
    s_center_m: Optional[float] = None  # exact along-wall position
    area_m2: Optional[float] = None
    provenance: Provenance | None = None
    needs_review: bool = True
    history: List[Provenance] = field(default_factory=list)
    # Roof glazing (roadmap item 3). A skylight is category="skylight" with
    # host_facade="roof"; sill/head/host_interval do not apply. Tilt is from
    # horizontal (0 = flat roof, the current convention), azimuth is the
    # outward normal's compass bearing (degrees clockwise from north). Both are
    # None for wall openings and for skylights whose roof plane is unknown.
    tilt_deg: Optional[float] = None
    azimuth_deg: Optional[float] = None
    # Skylights only: plan centre [x, y] in the space polygon's frame (y-down
    # metres); width_m runs along x, height_m along y. None when the position
    # is unknown (drawing takeoff lines carry a count, not a position).
    plan_center_m: Optional[list] = None
    # Per-sheet observations of this ONE logical opening (issue #663). A
    # window that spans two levels is seen on two adjacent elevation sheets;
    # cross-level dedup (#664) collapses them into one SpaceOpening and keeps
    # both records here. 1 record for a single-sheet opening, at most 2.
    # ``provenance`` stays the primary record (== source_provenance[0] when
    # populated) so every existing reader keeps working unchanged.
    source_provenance: List[Provenance] = field(default_factory=list)
    # Interior wall openings only (#666): the space on the far side of the
    # wall. The opening is stored once, on the lower-id space, so a door is
    # never counted twice; None for exterior openings and skylights.
    adjacent_space_id: Optional[str] = None
    # The assembly this opening is built from (a key into
    # BuildingModel.constructions, #747). Empty means not yet known.
    construction_id: str = ""

    def add_source_provenance(self, prov: Provenance) -> None:
        """Record one more source sheet for this opening (max 2, distinct sheets).

        The first record also becomes ``provenance`` when none is set.
        Raises ValueError on a third record or a repeated (sheet, revision).
        """
        if self.provenance is not None and not self.source_provenance:
            self.source_provenance.append(self.provenance)
        key = (prov.sheet_id, prov.revision)
        if any((p.sheet_id, p.revision) == key for p in self.source_provenance):
            raise ValueError(
                f"opening {self.id}: sheet {prov.sheet_id} rev {prov.revision} already recorded"
            )
        if len(self.source_provenance) >= MAX_OPENING_SOURCES:
            raise ValueError(
                f"opening {self.id}: at most {MAX_OPENING_SOURCES} source provenance records"
            )
        self.source_provenance.append(prov)
        if self.provenance is None:
            self.provenance = prov

    @property
    def source_sheet_ids(self) -> List[str]:
        """Sheet ids this opening was observed on (primary first)."""
        if self.source_provenance:
            return [p.sheet_id for p in self.source_provenance]
        return [self.provenance.sheet_id] if self.provenance is not None else []


# A logical opening is observed on at most two adjacent elevation sheets.
MAX_OPENING_SOURCES = 2


@dataclass
class FixtureInstance:
    """One light fixture assigned to this space."""

    id: str
    tag: str
    fixture_class: str
    x_m: float
    y_m: float
    watts: Optional[float] = None
    provenance: Provenance | None = None


@dataclass
class SpaceLighting:
    fixtures: List[FixtureInstance] = field(default_factory=list)
    total_w: float = 0.0
    lpd_w_m2: Optional[float] = None
    lpd_w_ft2: Optional[float] = None
    provenance: Provenance | None = None
    unmatched_tags: List[str] = field(default_factory=list)


@dataclass
class SpaceUse:
    """Occupant, plug-load and schedule inputs for one space (#685).

    Filled from the drawings when they carry a value; otherwise
    ``space_use_defaults.apply_space_use_defaults`` fills the gaps from the
    DOE Commercial Prototype Building Models (ASHRAE 90.1-2019) and records
    that in ``provenance`` (method ``"doe_prototype_default"``).
    ``space_type`` is the key into ``space_use_defaults_data.SPACE_USE_DEFAULTS``.
    Schedule fields name a schedule in ``space_use_defaults_data.SCHEDULES``.
    """

    space_type: str = ""
    people_per_m2: Optional[float] = None
    equipment_w_m2: Optional[float] = None
    lighting_schedule: str = ""
    occupancy_schedule: str = ""
    equipment_schedule: str = ""
    # total heat per occupant (W/person) from the prototype activity
    # schedule (#693); None when neither drawings nor prototype give one
    activity_w_per_person: Optional[float] = None
    provenance: Provenance | None = None


@dataclass
class SpaceHVAC:
    zone_ids: List[str] = field(default_factory=list)  # many-to-many
    diffusers: List[ComponentRef] = field(default_factory=list)
    sensors: List[ComponentRef] = field(default_factory=list)
    terminal_units: List[ComponentRef] = field(default_factory=list)
    provenance: Provenance | None = None


# ---------------------------------------------------------------------------
# Daylighting: sidelighted zones per ASHRAE 90.1 (from exact window placement)
# ---------------------------------------------------------------------------


@dataclass
class DaylitZone:
    """One sidelighted area polygon inside a space, per ASHRAE 90.1.

    Geometry: from the host window's head height H and wall interval,
    primary extends inward by ~1xH, secondary by ~1xH..2xH, laterally
    window width + ~1xH each side, all clipped to the room polygon.
    Exact factors live in DaylightParams (elevation_windows.py); they
    vary by 90.1 version, so the standard + factors are recorded here.
    """

    id: str
    zone_class: str  # "primary" | "secondary" | "under_skylight"
    window_id: str  # the SpaceOpening this derives from
    polygon_m: List[list] = field(default_factory=list)
    area_m2: float = 0.0
    head_height_m: Optional[float] = None
    provenance: Provenance | None = None


@dataclass
class SpaceDaylight:
    primary: List[DaylitZone] = field(default_factory=list)
    secondary: List[DaylitZone] = field(default_factory=list)
    params_note: str = ""  # e.g. "90.1-2019 approx: P=1.0xH, S=2.0xH"
    provenance: Provenance | None = None
    # Daylight area under skylights (roadmap item 3, toplighting). One zone
    # per placed skylight; toplit_m2 is the area of their union within the
    # space, so overlapping zones are not double counted. Skylights with no
    # known plan position are named in unplaced_skylights, never guessed.
    toplit: List[DaylitZone] = field(default_factory=list)
    toplit_m2: float = 0.0
    unplaced_skylights: List[str] = field(default_factory=list)

    @property
    def primary_m2(self) -> float:
        return sum(z.area_m2 for z in self.primary)

    @property
    def secondary_m2(self) -> float:
        return sum(z.area_m2 for z in self.secondary)


# ---------------------------------------------------------------------------
# Space: the primary key of the whole model
# ---------------------------------------------------------------------------


@dataclass
class Space:
    id: str  # "{level}-{number}" or "{level}-UNLABELED-{k}"
    level_id: str
    name: str = ""  # human label, e.g. "OPEN OFFICE"
    number: str = ""  # room number, e.g. "101"
    polygon_m: List[list] = field(default_factory=list)  # [[x,y],...] y-down
    area_m2: Optional[float] = None
    volume_m3: Optional[float] = None
    openings: List[SpaceOpening] = field(default_factory=list)
    lighting: SpaceLighting = field(default_factory=SpaceLighting)
    hvac: SpaceHVAC = field(default_factory=SpaceHVAC)
    use: SpaceUse = field(default_factory=SpaceUse)  # #685 loads + schedules
    daylight: "SpaceDaylight" = field(default_factory=lambda: SpaceDaylight())
    core_provenance: Provenance | None = None  # polygon + name/number source
    label_confidence: float = 0.0
    poly_type: str = "room"  # "room" | "shaft" | "closet" | "elevator_core" | "unassigned"
    # polygon_classify confidence for poly_type; None when the space was not
    # classified (e.g. IFC import, where IfcSpace says what it is)
    poly_type_confidence: Optional[float] = None
    # roadmap item 6: area-weighted exterior wall U-value over this space's
    # envelope segments, sum(U_i * A_i) / sum(A_i), written by
    # constructions.apply_wall_u_rollup. None until the rollup runs or when
    # no segment of the space has a construction with a known U-value.
    wall_u_value_w_m2k: Optional[float] = None
    # Ids of closets/shafts folded into this space by space_merge (the user's
    # rule, 2026-10-04). Empty for a space that absorbed nothing.
    # finish floors laid on this space's structural slab (#584):
    # [{"global_id": str, "thickness_m": float}]; never floor area or a BEM surface
    floor_finishes: List[dict] = field(default_factory=list)
    # ceiling from IfcCovering CEILING (#583): underside above the floor, and
    # covering top to the underside of the slab above; None when not known
    ceiling_height_m: Optional[float] = None
    plenum_depth_m: Optional[float] = None
    # full floor-to-top height of the space as the source states it (IFC space
    # body extrusion or Qto_SpaceBaseQuantities.Height); a height past the next
    # storey marks an atrium (#640). None when not stated.
    height_m: Optional[float] = None
    merged_from: List[str] = field(default_factory=list)
    history: List[Provenance] = field(default_factory=list)

    @property
    def window_area_m2(self) -> float:
        return sum(o.area_m2 or 0.0 for o in self.openings if o.category == "window")

    @property
    def label(self) -> str:
        return f"{self.name} {self.number}".strip() or "(unlabeled)"


@dataclass
class Zone:
    """One HVAC zone. MANY-TO-MANY with spaces: zone.space_ids and
    Space.hvac.zone_ids are both lists; neither is a tree."""

    id: str
    level_id: str
    space_ids: List[str] = field(default_factory=list)
    terminal_unit: Optional[ComponentRef] = None
    diffusers: List[ComponentRef] = field(default_factory=list)
    sensors: List[ComponentRef] = field(default_factory=list)
    duct_length_m: Optional[float] = None
    provenance: Provenance | None = None
    history: List[Provenance] = field(default_factory=list)


@dataclass
class OpeningAttachmentSummary:
    """Counts of openings left unattached, broken down by failure reason.

    Produced during Tier 1 geometric opening attachment
    (ifc_import._attach_openings_to_spaces).
    """

    no_envelope_edge: int = 0  # no envelope edge endpoint matched wall placement
    ambiguous_tie: int = 0  # multiple same-length edges tied for wall position
    no_ref_direction: int = 0  # envelope and entity RefDirection both unavailable
    # wall direction known, but neither side of the opening lies in a space on
    # the wall's level (#666)
    outside_spaces: int = 0
    # NOT unattached: openings attached using the host wall's own RefDirection
    # because no envelope edge settled its direction (#666, confidence 0.85)
    ref_direction_fallback: int = 0
    # NOT unattached: openings attached by their centre whose edges reach
    # another room, flagged ``adjacency_ambiguous`` for review (#681)
    adjacency_ambiguous: int = 0

    @property
    def total(self) -> int:
        return (
            self.no_envelope_edge + self.ambiguous_tie + self.no_ref_direction + self.outside_spaces
        )

    def is_empty(self) -> bool:
        return self.total == 0

    def summary_line(self) -> str:
        """Human-readable one-line summary for import logs."""
        notes = []
        if self.ref_direction_fallback:
            notes.append(f"{self.ref_direction_fallback} attached via RefDirection")
        if self.adjacency_ambiguous:
            notes.append(f"{self.adjacency_ambiguous} spanning a room boundary")
        fb = f" ({'; '.join(notes)})" if notes else ""
        if self.is_empty():
            return f"0 openings unattached{fb}"
        parts = []
        if self.no_envelope_edge:
            parts.append(f"{self.no_envelope_edge} no envelope edge")
        if self.ambiguous_tie:
            parts.append(f"{self.ambiguous_tie} ambiguous tie")
        if self.no_ref_direction:
            parts.append(f"{self.no_ref_direction} no RefDirection")
        if self.outside_spaces:
            parts.append(f"{self.outside_spaces} outside every space")
        return f"{self.total} opening(s) unattached: {', '.join(parts)}{fb}"


@dataclass
class ShadingSurface:
    """One exterior projection that shades the envelope (roadmap item 5).

    Overhangs, fins and balconies matter to BEM as shading, not as envelope:
    they live here, outside ``BuildingModel.envelope``, so the envelope area
    budget and closure checks never count them.

    Placement is relative to the host wall segment, in its own frame:
    ``along_m`` runs from the segment's ``from_m`` end toward ``to_m``,
    ``z_m`` is height above the host level's floor, and ``depth_m`` is the
    projection outward from the exterior wall face, which sits ``offset_m``
    outside the segment's line. An overhang or balcony
    is horizontal (``width_m`` along the wall at height ``z_m``); a fin is
    vertical (at ``along_m``, from ``z_m`` up ``height_m``).
    """

    id: str
    kind: str  # "overhang" | "fin" | "balcony" | "other"
    host_wall_id: str = ""  # EnvelopeWall.id; empty = no host (suspicious)
    host_opening_id: str = ""  # SpaceOpening.id it shades, if any
    along_m: Optional[float] = None
    width_m: Optional[float] = None  # extent along the wall (overhang/balcony)
    z_m: Optional[float] = None
    depth_m: Optional[float] = None  # projection from the wall face
    height_m: Optional[float] = None  # vertical extent (fin)
    # gap from the host segment's line out to the plate's inner edge: half
    # the wall thickness when the segment is a wall centreline (IFC #579),
    # 0 when the segment is drawn on the exterior face
    offset_m: float = 0.0
    provenance: Provenance | None = None


@dataclass
class Construction:
    """One exterior wall assembly (roadmap item 6).

    Drawings rarely carry assembly data, so ``u_value_w_m2k`` is often
    unknown; a construction without one still identifies the wall type, and
    the rollup leaves its segments out of the weighted U rather than
    guessing a value.
    """

    id: str  # e.g. "W-1", as tagged on the wall-type legend
    name: str = ""
    u_value_w_m2k: Optional[float] = None  # assembly U-value, SI
    provenance: Provenance | None = None
    # glazing only (#747): solar heat gain coefficient and visible
    # transmittance, fractions
    shgc: Optional[float] = None
    vt: Optional[float] = None


@dataclass
class EnvelopeWall:
    """One exterior wall run (per facade segment). The detailed
    area-budgeted simplification lives in geometry_simplify.py; this is
    the canonical record it feeds."""

    id: str
    facade: str  # "south" | "north" | "east" | "west"
    from_m: List[float] = field(default_factory=list)  # [x, y] canonical m
    to_m: List[float] = field(default_factory=list)
    length_m: Optional[float] = None
    height_m: Optional[float] = None
    area_m2: Optional[float] = None
    provenance: Provenance | None = None
    # roadmap item 6: the assembly this segment is built from (a key into
    # BuildingModel.constructions) and the space it encloses. Empty means
    # not yet known; validation names such segments rather than guessing.
    construction_id: str = ""
    space_id: str = ""


@dataclass
class BimOpening:
    """One window/door hosted in a BIM element (IFC Tier 0).

    Tier 0 recovers the opening, its dimensions, and its host wall WITHOUT
    IfcRelSpaceBoundary -- so openings are NOT attached to spaces yet.
    Space attachment is Tier 1 (geometric adjacency inference).
    """

    id: str  # GlobalId of the IfcOpeningElement
    category: str  # "window" | "door" | "skylight" | "unknown"
    tag: str = ""
    width_m: Optional[float] = None  # skylight: plan extent along x
    height_m: Optional[float] = None  # skylight: plan extent along y
    sill_m: Optional[float] = None  # above host wall base
    s_center_m: Optional[float] = None  # along host wall from wall start
    host_global_id: str = ""  # the IfcWall / IfcSlab / host element
    fill_global_id: str = ""  # the IfcWindow / IfcDoor
    provenance: Provenance | None = None
    # Skylights only (roof-hosted): plan centre [x, y] in the canonical
    # y-down frame, used to attach the skylight to the space under it.
    plan_center_m: Optional[list] = None
    # Skylights only (#614): tilt/azimuth of the roof plane above the plan
    # centre, same convention as SpaceOpening; None when no plane is known.
    tilt_deg: Optional[float] = None
    azimuth_deg: Optional[float] = None
    # Doors only (#573): IfcDoor.OperationType as written (IFC4 occurrence or
    # its type; IFC2X3 IfcDoorStyle), and what it says without guessing.
    operation_type: Optional[str] = None
    leaf_count: Optional[int] = None  # 1 | 2; None for revolving/rolling/undefined
    hinge_side: Optional[str] = None  # "left" | "right" for swing doors only
    # Pset_DoorCommon.GlazingAreaFraction (0..1) and the glazed area it gives.
    glazing_area_fraction: Optional[float] = None
    glazed_area_m2: Optional[float] = None
    # Stated thermal values on the fill (#787): Pset_WindowCommon or
    # Pset_DoorCommon ThermalTransmittance (W/m2K) and, for glazing,
    # Pset_DoorWindowGlazingType SolarHeatGainTransmittance and
    # VisibleLightTransmittance. None when the file does not state them.
    u_value_w_m2k: Optional[float] = None
    shgc: Optional[float] = None
    vt: Optional[float] = None
    thermal_reference: str = ""  # the common pset's Reference, kept for traceability


@dataclass
class BimElement:
    """Raw BIM element inventory (IFC Tier 0).

    Walls are ALSO mirrored into ``BuildingModel.envelope`` for the BEM
    path; everything else lives here until Tier 1 assigns it a role.
    """

    global_id: str
    ifc_class: str  # "IfcWall", "IfcSlab", ...
    name: str = ""
    level_id: str = ""
    length_m: Optional[float] = None
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    thickness_m: Optional[float] = None  # geometry or material layers
    area_m2: Optional[float] = None
    volume_m3: Optional[float] = None
    material_layers: List[dict] = field(default_factory=list)
    # [{"material": str, "thickness_m": float}] -- the analytical
    # wall-thickness answer (roadmap item 1 on the BIM path)
    placement_m: Optional[list] = None  # [x, y, z], canonical frame
    openings: List["BimOpening"] = field(default_factory=list)
    provenance: Provenance | None = None
    # "" for an ordinary element; "lining" for an IfcWall that only lines or
    # hides inside another wall and was kept out of the envelope (#577)
    role: str = ""
    # "" when the file contains the element in its storey; "elevation" when
    # it had no storey containment and exactly one storey fitted its
    # placement height (#585)
    storey_method: str = ""


@dataclass
class Level:
    id: str  # "L1"
    name: str = ""
    elevation_z_m: float = 0.0
    wall_height_m: float = 3.0
    # #634: True/False only when a source states it (IFC
    # Pset_BuildingStoreyCommon.AboveGround); None = not stated, never guessed
    above_ground: Optional[bool] = None
    above_ground_source: str = ""


# ---------------------------------------------------------------------------
# Review queue: low-confidence links flagged for humans, never silently
# accepted (and never silently dropped).
# ---------------------------------------------------------------------------


@dataclass
class ReviewItem:
    """Low-confidence link flagged for human review.

    A ReviewItem is created when the pipeline produces a result with
    confidence below the auto-accept threshold, or when an invariant
    violation is detected that requires human judgment to resolve.
    Items are never silently dropped or silently accepted — every
    item enters the review queue and awaits explicit confirmation or
    rejection.

    Attributes:
        id: Unique identifier for this review item.
        kind: Category of the review item (e.g. ``"window_room_link"``,
            ``"fixture_assignment"``). Controls routing and triage logic.
        description: Human-readable description of the issue or anomaly.
        confidence: Extraction confidence of the underlying fact (0.0–1.0).
            Values >= 1.0 are reserved for fully confirmed facts and
            cannot be combined with ``needs_review=False``.
        provenance: Provenance record carrying sheet ID, revision, method,
            and source bounding box of the extracted fact.
        status: Current workflow status: ``"open"`` (default), ``"confirmed"``,
            or ``"rejected"``.
        needs_human: Estimated probability that human judgment is required
            to resolve this item (0.0–1.0). Set by the triage classifier.
        urgency: Urgency tier in the range 0–3. Higher values indicate
            items that should be resolved before export.
        auto_resolved: True when the item was resolved automatically by
            the pipeline without human input (e.g. disambiguation guardrails).
        resolution: Triage outcome: ``"accept"``, ``"drop"``, or ``"reassign"``.
            Empty string when no resolution has been set.
        needs_review: True when the item is awaiting human review.
            False when the item has been reviewed and closed.
        acknowledged: True when a human has explicitly acknowledged this item
            (distinct from resolution — acknowledgment indicates the human
            has seen the item even if no action was taken).
        target: The entity the item is about (#796): ``kind`` (``"opening"``,
            ``"space"``, ``"wall"``, ``"fixture"``, ...), ``id``, and for an
            item a review edit can apply to, the model ``field`` it corrects.
            ``original`` is filled in the first time a review edit changes the
            field, so revert restores it. Empty for items flagged before #796.

    Raises:
        ValueError: If ``needs_review`` is False but ``confidence >= 1.0``.
            A reviewed item cannot carry a fully-confirmed confidence score.
    """

    id: str
    kind: Literal[
        "fixture_assignment",
        "fixture_schedule",
        "diffuser_assignment",
        "sensor_assignment",
        "window_room_link",
        "space_no_geometry",
        "elevation_conflict",
        "window_reconciliation",
        "gd_complex_row",
        "lighting_extraction",
        "hvac_extraction",
        "room_label_extraction",
        "window_extraction",
        "elevation_extraction",
        "facade_takeoff",
        "opening_attachment",
        "adjacency_ambiguous",
        "facade_unclear",
        "space_merge",
        "unclaimed_wall_loop",
        "ceiling",
        "lining_u",
        "matchline_identity",
        "roof_plane",
        "interstory",
        "below_grade",
    ]
    description: str
    confidence: float
    provenance: Provenance | None = None
    status: str = "open"  # "open" | "confirmed" | "rejected"
    needs_human: float = 1.0  # P(needs human) — set by triage
    urgency: int = 1  # 0-3; set by triage
    auto_resolved: bool = False  # True if auto-resolved per guardrails
    resolution: str = ""  # "accept" | "drop" | "reassign" — set by triage
    needs_review: bool = True  # True = awaiting human review; False = reviewed
    acknowledged: bool = False  # True = human explicitly acknowledged this item
    target: Dict[str, Any] = field(default_factory=dict)  # #796: entity the item is about

    def __post_init__(self):
        if not self.needs_review and self.confidence >= 1.0:
            raise ValueError(
                f"ReviewItem '{self.id}' has confidence={self.confidence} but is marked "
                f"needs_review=False. A reviewed item cannot carry confidence=1.0 "
                f"(fully confirmed). Use confidence < 1.0 for known limitations."
            )


# ---------------------------------------------------------------------------
# BuildingModel
# ---------------------------------------------------------------------------


@dataclass
class SpaceAdjacency:
    """Two spaces sharing a border with no wall on it (#582).

    ``from_m``/``to_m`` run along the midline of the shared border in the
    canonical frame. A virtual border carries no wall area and no U-value.
    """

    space_a: str
    space_b: str
    level_id: str
    from_m: List[float] = field(default_factory=list)
    to_m: List[float] = field(default_factory=list)
    length_m: float = 0.0
    boundary: str = "virtual"
    virtual_element_id: str = ""  # IfcVirtualElement GlobalId when the file models it
    provenance: Provenance | None = None


@dataclass
class RoofPlane:
    """One planar roof facet (roadmap item 2, #613).

    ``vertices_m`` are [x, y, z] corners in the canonical frame: plan x east,
    plan y down the sheet (south), z metres up from the host level's floor.
    ``tilt_deg`` is from horizontal (0 = flat). ``azimuth_deg`` is the
    outward (upward-facing) normal's compass bearing, degrees clockwise from
    north, the same convention as skylights; None for a flat facet, where it
    has no meaning. ``area_m2`` is the true sloped area, not the plan area.

    ``BuildingModel.roof_planes`` empty means the flat roof at wall height that
    every export assumed before this existed.
    """

    id: str
    level_id: str = ""
    vertices_m: List[List[float]] = field(default_factory=list)
    tilt_deg: Optional[float] = None
    azimuth_deg: Optional[float] = None
    area_m2: Optional[float] = None
    host_global_id: str = ""  # IfcRoof / IfcSlab it came from, if any
    provenance: Provenance | None = None


@dataclass
class BuildingModel:
    """Canonical building model: cross-discipline linking layer.

    The BuildingModel is the single source of truth for all extracted,
    linked, and reconciled building data. It is the ONE model that
    everything registers into. Disciplines (architectural floor plan,
    lighting, mechanical, elevations) are parsed into sheet-local models,
    registered into arch-plan coordinates, then linked via the
    registration layer. The canonical model is what is validated,
    exported, and reviewed.

    The architectural floor plan is the reference frame. Rooms
    (name + number) are the primary key: ``Space.id = "{level}-{number}"``.
    Zones are many-to-many with spaces (an open office can span two zones).

    Provenance is tracked on every derived fact. Facts from a newer
    revision of the same sheet supersede older facts — they are kept in
    ``history``, never silently overwritten.

    Coordinate frame (canonical): metres, y growing downward (drawing frame).
    BEM export flips y to north-up.

    Attributes:
        name: Display name of the building or project.
        model_version: Schema version string for this model.
        auto_triage: Whether to run automatic triage on the review queue.
            None means triage has not been run yet.
        levels: All building levels (floors) in the model.
        spaces: All spaces (rooms) keyed by ``Space.id``.
        zones: All thermal zones keyed by zone ID.
        envelope: All envelope wall segments (walls, windows, doors).
        constructions: Exterior wall assemblies keyed by ``Construction.id``;
            ``EnvelopeWall.construction_id`` points here.
        roof_construction_id: ``Construction.id`` of the roof assembly, or
            "" when unknown (exports then use the generic roof).
        slab_construction_id: ``Construction.id`` of the ground slab, or ""
            when unknown (exports then use the generic slab on grade).
        shading: Exterior shading projections hosted on envelope walls;
            kept out of ``envelope`` so they never enter the area budget.
        roof_planes: Planar roof facets with tilt and azimuth (#613); empty
            means the flat roof at wall height.
        site_latitude_deg: Site latitude (deg, north positive) or None
            when unknown; never defaulted (#615).
        bim_elements: All BIM elements from IFC import.
        schedules: Lighting and other schedules as plain dicts, keyed by
            schedule tag. Revived from plain dict on model load.
        revision_log: Ordered log of all revisions applied to this model,
            including supersession events.
        review_queue: List of low-confidence items awaiting human review.
        symbol_linkages: Explicit symbol-to-schedule linkage graph. Each entry
            links a symbol instance (detection) to its schedule row, or marks
            it as unlinked. Takeoffs derive from this graph rather than
            parallel symbol and schedule lists.
        _rev_seq: Internal revision sequence counter.

    Args:
        name: Display name of the building or project.
        model_version: Schema version string for this model.
        auto_triage: Whether to run automatic triage on the review queue.
        levels: All building levels (floors) in the model.
        spaces: All spaces (rooms) keyed by ``Space.id``.
        zones: All thermal zones keyed by zone ID.
        envelope: All envelope wall segments (walls, windows, doors).
        bim_elements: All BIM elements from IFC import.
        schedules: Lighting and other schedules as plain dicts, keyed by
            schedule tag.
        revision_log: Ordered log of all revisions applied to this model.
        review_queue: List of low-confidence items awaiting human review.
    """

    name: str = ""
    model_version: str = MODEL_VERSION
    auto_triage: Optional[bool] = None
    levels: List[Level] = field(default_factory=list)
    spaces: Dict[str, Space] = field(default_factory=dict)
    zones: Dict[str, Zone] = field(default_factory=dict)
    envelope: List[EnvelopeWall] = field(default_factory=list)
    constructions: Dict[str, Construction] = field(default_factory=dict)
    # Construction.id of the roof assembly, when known (IFC import reads it
    # from the roof's ThermalTransmittance); "" -> generic roof on export
    roof_construction_id: str = ""
    # Construction.id of the ground slab (IFC import reads a BASESLAB's stated
    # ThermalTransmittance); "" -> generic slab on grade on export
    slab_construction_id: str = ""
    # ASHRAE climate zone (e.g. "4A") and 90.1 building category, as given by
    # the user (--climate-zone / --building-category, #747/#763); "" means not
    # given, and nothing downstream assumes one
    climate_zone: str = ""
    building_category: str = ""
    shading: List[ShadingSurface] = field(default_factory=list)
    space_adjacencies: List[SpaceAdjacency] = field(default_factory=list)
    # sloped roof facets (roadmap item 2, #613); empty -> flat roof at wall height
    roof_planes: List[RoofPlane] = field(default_factory=list)
    # roof planes as they were before simplification (#617); empty when the
    # roof was never simplified
    source_roof_planes: List[RoofPlane] = field(default_factory=list)
    # site latitude in decimal degrees, north positive (IfcSite RefLatitude, #615)
    site_latitude_deg: Optional[float] = None
    # ground surface triangles [[x, y, z] x 3], canonical frame, from the IFC
    # site terrain (#641); empty when the source has none (never a default grade)
    terrain: List[list] = field(default_factory=list)
    # overhangs, fins, balconies (roadmap item 5); never part of envelope
    # exterior wall assemblies keyed by Construction.id (roadmap item 6)
    bim_elements: List[BimElement] = field(default_factory=list)
    # raw BIM element inventory (IFC frontend, Tier 0+)
    schedules: Dict[str, dict] = field(default_factory=dict)
    # schedules: tag -> ScheduleEntry as plain dict (revived on load)
    revision_log: List[RevisionEvent] = field(default_factory=list)
    review_queue: List[ReviewItem] = field(default_factory=list)
    symbol_linkages: List[SymbolLinkage] = field(default_factory=list)
    opening_attachment_summary: OpeningAttachmentSummary = field(
        default_factory=OpeningAttachmentSummary
    )
    # counts of openings left unattached, broken down by failure reason
    _rev_seq: int = 0

    # -- revision log ----------------------------------------------------
    def log_revision(
        self, sheet_id: str, revision: int, action: str, note: str = ""
    ) -> RevisionEvent:
        self._rev_seq += 1
        ev = RevisionEvent(
            seq=self._rev_seq, sheet_id=sheet_id, revision=revision, action=action, note=note
        )
        self.revision_log.append(ev)
        return ev

    def supersede(self, old: Provenance, new: Provenance, history: List[Provenance]) -> None:
        """Record that `new` replaces `old`; the old fact stays in history."""
        history.append(old)
        self.log_revision(
            new.sheet_id,
            new.revision,
            "supersede",
            f"{old.method} r{old.revision} -> {new.method} r{new.revision}",
        )

    # -- lookups ----------------------------------------------------------
    def space_by_number(self, number: str, level_id: str = "L1") -> Optional[Space]:
        return self.spaces.get(f"{level_id}-{number}")

    def zones_of_space(self, space_id: str) -> List[Zone]:
        s = self.spaces.get(space_id)
        if not s:
            return []
        return [self.zones[z] for z in s.hvac.zone_ids if z in self.zones]

    def spaces_of_zone(self, zone_id: str) -> List[Space]:
        z = self.zones.get(zone_id)
        if not z:
            return []
        return [self.spaces[s] for s in z.space_ids if s in self.spaces]

    def flag_for_review(
        self,
        kind: Literal[
            "fixture_assignment",
            "fixture_schedule",
            "diffuser_assignment",
            "sensor_assignment",
            "window_room_link",
            "space_no_geometry",
            "elevation_conflict",
            "window_reconciliation",
            "gd_complex_row",
            "opening_attachment",
            "adjacency_ambiguous",
            "facade_unclear",
            "space_merge",
            "unclaimed_wall_loop",
            "ceiling",
            "lining_u",
            "matchline_identity",
            "roof_plane",
            "interstory",
            "below_grade",
        ],
        description: str,
        confidence: float,
        provenance: Provenance,
        target: Optional[Dict[str, Any]] = None,
    ) -> Optional[ReviewItem]:
        """Append a low-confidence extraction to the review queue.

        Call this whenever an extraction result falls below a reliability threshold
        and should not be silently accepted.  High-confidence items
        (``confidence >= REVIEW_CONFIDENCE``) are silently accepted and this
        method returns ``None`` without adding anything to :attr:`review_queue`.
        The item is assigned triage metadata (``needs_human``, ``urgency``,
        ``resolution``) and appended to :attr:`review_queue`.

        **Confidence thresholds that trigger review** are set by the **caller**, not
        by this method.  Typical thresholds used in the pipeline:

        * ``REVIEW_CONFIDENCE`` (default **0.80**) — used by linkers and extractors
          to flag results that are plausible but not confirmed.
        * Hard fallbacks at **0.4** — used when geometry or assignment is entirely
          absent (e.g. a diffuser falls in no space); these are always routed to
          review regardless of :data:`ENABLE_AUTO_TRIAGE`.

        **``kind`` values** — must be one of the :class:`ReviewItem` literal union:

        :``"fixture_assignment"``: a fixture or appliance could not be placed in a
            space.
        :``"fixture_schedule"``: the schedule/group assignment for a fixture is
            ambiguous or missing.
        :``"diffuser_assignment"``: an HVAC diffuser could not be assigned to a
            thermal zone.
        :``"sensor_assignment"``: a sensor falls in no space or is unassigned.
        :``"window_room_link"``: a window/door glazing unit could not be linked
            to a room.
        :``"space_no_geometry"``: a space has no usable geometry for envelope or
            internal load calculations.
        :``"elevation_conflict"``: conflicting information between elevation and plan
            views for the same element.
        :``"window_reconciliation"``: multiple elevation views give different
            placements for the same window.
        :``"gd_complex_row"``: a complex row in the geometry diary could not be
            parsed unambiguously.

        **Relationship to :data:`ENABLE_AUTO_TRIAGE`**: when
        ``model.auto_triage`` is not explicitly set on the model, the module-level
        ``ENABLE_AUTO_TRIAGE`` setting governs whether :meth:`_triage_item` is
        called.  If auto-triage is **disabled**, triage fields keep their safe
        defaults (``needs_human=1.0``, ``urgency=1``) and the item enters the
        queue as an unconditional human-review request.  If auto-triage is
        **enabled**, the triage classifier estimates ``needs_human`` and ``urgency``
        and may set ``auto_resolved=True`` for items that meet the safety
        guardrails, allowing them to be exported without blocking the pipeline.

        Parameters
        ----------
        kind:
            The category of review concern, used by reviewers to prioritise and
            route the queue.
        description:
            Human-readable explanation of why review is needed, referencing the
            specific element (e.g. component ID) and the failure mode.
        confidence:
            Extractor confidence in the result, on ``[0.0, 1.0]``.  Lower values
            indicate less certainty; values below the caller's threshold trigger
            this method.
        provenance:
            The :class:`Provenance` record (sheet, revision, method) that sourced
            this extraction, used for auditability.
        target:
            The entity the item is about, ``{"kind", "id"[, "field"]}`` (#796);
            see :attr:`ReviewItem.target`.

        Returns
        -------
        ReviewItem
            The created queue item, already appended to :attr:`review_queue`.
        """
        if confidence >= REVIEW_CONFIDENCE:
            return None
        rid = f"RVW-{len(self.review_queue) + 1:03d}"
        item = ReviewItem(
            id=rid,
            kind=kind,
            description=description,
            confidence=confidence,
            provenance=provenance,
            target=dict(target or {}),
        )
        if self.auto_triage if self.auto_triage is not None else ENABLE_AUTO_TRIAGE:
            self._triage_item(item)
        self.review_queue.append(item)
        return item

    def _triage_item(self, item: ReviewItem) -> None:
        """Run the triage classifier on ``item`` and populate triage fields.

        Silently skips if the triage classifier is unavailable (all fields keep
        their safe defaults: ``needs_human=1.0``, ``urgency=1``).
        This method never invents geometry — it only sets metadata on the flag.
        """
        try:
            from review_classifier.data import Example
            from review_classifier.triage import get_triage

            text = f"[{item.kind}] {item.description} (conf={item.confidence:.2f})"
            example = Example(
                task="route_to_review",
                text=text,
                numeric={"det_conf": item.confidence},
            )
            triage = get_triage()
            decision = triage.triage(example)
            item.needs_human = decision.needs_human
            item.urgency = decision.urgency
            item.auto_resolved = decision.auto_resolved
            item.resolution = decision.resolution
        except Exception as e:
            import warnings

            warnings.warn(f"Triage classifier unavailable for item {item.sheet_id}: {e}")

    # -- JSON --------------------------------------------------------------
    def to_dict(self) -> dict:
        from dataclasses import asdict

        d = asdict(self)
        d.pop("_rev_seq", None)
        return {"model_version": MODEL_VERSION, "model": d}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    @classmethod
    def from_dict(cls, d: dict) -> "BuildingModel":
        m = _migrate_model_dict(d.get("model_version", "1.0"), d["model"])
        obj = _revive(cls, m)
        obj._rev_seq = len(obj.revision_log)
        return obj

    @classmethod
    def from_json(cls, s: str) -> "BuildingModel":
        return cls.from_dict(json.loads(s))


def _migrate_model_dict(version: str, m: dict) -> dict:
    """Upgrade an older serialized model dict to the current MODEL_VERSION shape.

    1.0 -> 1.1: seed ``SpaceOpening.source_provenance`` with the single
    ``provenance`` record so single-sheet openings look the same whether
    they were built in memory or loaded from an old file. Pure: returns a
    new dict, never mutates the caller's payload.
    """
    if version == MODEL_VERSION:
        return m
    import copy

    m = copy.deepcopy(m)
    m["model_version"] = MODEL_VERSION
    for sp in (m.get("spaces") or {}).values():
        for op in sp.get("openings") or []:
            if not op.get("source_provenance") and op.get("provenance"):
                op["source_provenance"] = [op["provenance"]]
    return m


# ---------------------------------------------------------------------------
# Generic dataclass revival for from_dict (keeps every nested Provenance,
# ComponentRef, etc. as real dataclass instances, not raw dicts).
# ---------------------------------------------------------------------------


def _conv(t, v):
    if v is None:
        return None
    origin = get_origin(t)
    if origin in (list, List):
        (et,) = get_args(t)
        return [_conv(et, x) for x in v]
    if origin in (dict, Dict):
        _kt, vt = get_args(t)
        return {k: _conv(vt, x) for k, x in v.items()}
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(t) if a is not type(None)]
        return _conv(args[0], v) if args else v
    if isinstance(t, type) and is_dataclass(t):
        return _revive(t, v)
    return v


def _revive(cls, data: dict):
    hints = get_type_hints(cls, globals())
    init_kwargs = {}
    for f in fields(cls):
        if f.name.startswith("_") or f.name not in data:
            continue  # dataclass defaults apply
        init_kwargs[f.name] = _conv(hints[f.name], data[f.name])
    return cls(**init_kwargs)
