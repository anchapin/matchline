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
# NOTE: no `from __future__ import annotations` here -- from_dict revival
# resolves real type objects via typing.get_type_hints.

import json
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Dict, List, Optional, Union, get_args, get_origin, get_type_hints

MODEL_VERSION = "1.0"

# Links below this confidence are flagged for human review, not silently
# accepted (review queue, not dropped).
REVIEW_CONFIDENCE = 0.80

# Feature flag: enable auto-triage via the local review_classifier TypedDecider.
# When False (default), flag_for_review behaves as before (pure flagging).
ENABLE_AUTO_TRIAGE = False


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
    provenance: Provenance = None
    history: List[Provenance] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Space sub-records
# ---------------------------------------------------------------------------


@dataclass
class SpaceOpening:
    """One window/door on this space's walls (from an elevation)."""

    id: str
    tag: str
    category: str  # "window" | "door"
    width_m: float
    height_m: float
    sill_m: Optional[float] = None
    head_m: Optional[float] = None  # sill + height; drives daylighting
    host_facade: str = ""  # e.g. "south"
    host_interval_m: Optional[list] = None  # [s0, s1] along facade, meters
    s_center_m: Optional[float] = None  # exact along-wall position
    area_m2: Optional[float] = None
    provenance: Provenance = None
    needs_review: bool = False
    history: List[Provenance] = field(default_factory=list)


@dataclass
class FixtureInstance:
    """One light fixture assigned to this space."""

    id: str
    tag: str
    fixture_class: str
    x_m: float
    y_m: float
    watts: Optional[float] = None
    provenance: Provenance = None


@dataclass
class SpaceLighting:
    fixtures: List[FixtureInstance] = field(default_factory=list)
    total_w: float = 0.0
    lpd_w_m2: Optional[float] = None
    lpd_w_ft2: Optional[float] = None
    provenance: Provenance = None
    unmatched_tags: List[str] = field(default_factory=list)


@dataclass
class SpaceHVAC:
    zone_ids: List[str] = field(default_factory=list)  # many-to-many
    diffusers: List[ComponentRef] = field(default_factory=list)
    sensors: List[ComponentRef] = field(default_factory=list)
    terminal_units: List[ComponentRef] = field(default_factory=list)
    provenance: Provenance = None


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
    zone_class: str  # "primary" | "secondary"
    window_id: str  # the SpaceOpening this derives from
    polygon_m: List[list] = field(default_factory=list)
    area_m2: float = 0.0
    head_height_m: Optional[float] = None
    provenance: Provenance = None


@dataclass
class SpaceDaylight:
    primary: List[DaylitZone] = field(default_factory=list)
    secondary: List[DaylitZone] = field(default_factory=list)
    params_note: str = ""  # e.g. "90.1-2019 approx: P=1.0xH, S=2.0xH"
    provenance: Provenance = None

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
    daylight: "SpaceDaylight" = field(default_factory=lambda: SpaceDaylight())
    core_provenance: Provenance = None  # polygon + name/number source
    label_confidence: float = 0.0
    poly_type: str = "room"  # "room" | "shaft" | "closet" | "elevator_core" | "unassigned"
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
    provenance: Provenance = None
    history: List[Provenance] = field(default_factory=list)


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
    provenance: Provenance = None


@dataclass
class BimOpening:
    """One window/door hosted in a BIM element (IFC Tier 0).

    Tier 0 recovers the opening, its dimensions, and its host wall WITHOUT
    IfcRelSpaceBoundary -- so openings are NOT attached to spaces yet.
    Space attachment is Tier 1 (geometric adjacency inference).
    """

    id: str  # GlobalId of the IfcOpeningElement
    category: str  # "window" | "door" | "unknown"
    tag: str = ""
    width_m: Optional[float] = None
    height_m: Optional[float] = None
    sill_m: Optional[float] = None  # above host wall base
    s_center_m: Optional[float] = None  # along host wall from wall start
    host_global_id: str = ""  # the IfcWall / host element
    fill_global_id: str = ""  # the IfcWindow / IfcDoor
    provenance: Provenance = None


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
    provenance: Provenance = None


@dataclass
class Level:
    id: str  # "L1"
    name: str = ""
    elevation_z_m: float = 0.0
    wall_height_m: float = 3.0


# ---------------------------------------------------------------------------
# Review queue: low-confidence links flagged for humans, never silently
# accepted (and never silently dropped).
# ---------------------------------------------------------------------------


@dataclass
class ReviewItem:
    id: str
    kind: str  # e.g. "window_room_link", "fixture_assignment"
    description: str
    confidence: float
    provenance: Provenance = None
    status: str = "open"  # "open" | "confirmed" | "rejected"
    needs_human: float = 1.0  # P(needs human) — set by triage
    urgency: int = 1  # 0-3; set by triage
    auto_resolved: bool = False  # True if auto-resolved per guardrails
    resolution: str = ""  # "accept" | "drop" | "reassign" — set by triage
    needs_review: bool = True  # True = awaiting human review; False = reviewed

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
class BuildingModel:
    name: str = ""
    model_version: str = MODEL_VERSION
    auto_triage: Optional[bool] = None
    levels: List[Level] = field(default_factory=list)
    spaces: Dict[str, Space] = field(default_factory=dict)
    zones: Dict[str, Zone] = field(default_factory=dict)
    envelope: List[EnvelopeWall] = field(default_factory=list)
    bim_elements: List[BimElement] = field(default_factory=list)
    # raw BIM element inventory (IFC frontend, Tier 0+)
    schedules: Dict[str, dict] = field(default_factory=dict)
    # schedules: tag -> ScheduleEntry as plain dict (revived on load)
    revision_log: List[RevisionEvent] = field(default_factory=list)
    review_queue: List[ReviewItem] = field(default_factory=list)
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
        self, kind: str, description: str, confidence: float, provenance: Provenance
    ) -> ReviewItem:
        rid = f"RVW-{len(self.review_queue) + 1:03d}"
        item = ReviewItem(
            id=rid,
            kind=kind,
            description=description,
            confidence=confidence,
            provenance=provenance,
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
        except Exception:
            pass  # classifier unavailable — leave safe defaults

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
        m = d["model"]
        obj = _revive(cls, m)
        obj._rev_seq = len(obj.revision_log)
        return obj

    @classmethod
    def from_json(cls, s: str) -> "BuildingModel":
        return cls.from_dict(json.loads(s))


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
    if origin is Union:
        args = [a for a in get_args(t) if a is not type(None)]
        return _conv(args[0], v) if args else v
    if isinstance(t, type) and is_dataclass(t):
        return _revive(t, v)
    return v


def _revive(cls, data: dict):
    hints = get_type_hints(cls)
    init_kwargs = {}
    for f in fields(cls):
        if f.name.startswith("_") or f.name not in data:
            continue  # dataclass defaults apply
        init_kwargs[f.name] = _conv(hints[f.name], data[f.name])
    return cls(**init_kwargs)
