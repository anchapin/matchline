# bem_geometry.py — BEM dataclasses, geometry helpers, model factory
# Extracted from bem_export.py for single-responsibility compliance.
# bem_export.py now focuses purely on serialization and export format handling.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class BEMSpace:
    sid: str
    name: str  # e.g. "OPEN OFFICE 101"
    number: str
    polygon_m: list  # [(x, y), ...] CCW, x=east, y=north
    area_m2: float
    volume_m3: float
    lighting_w: float = 0.0  # total lighting power (watts), from SpaceLighting
    # area-weighted exterior wall U (roadmap item 6), from Space.wall_u_value_w_m2k;
    # None -> walls in this space use the generic construction
    wall_u_value_w_m2k: Optional[float] = None
    provenance: Optional["Provenance"] = None
    history: List["Provenance"] = field(default_factory=list)
    # matchline identity written to the IFC (#588): {"id", "method",
    # "confidence", "merged_from", "poly_type"}; None writes nothing
    identity: Optional[dict] = None
    # parent space id when this space is one piece of a room split at an
    # Appendix G block line (#638); empty for an unsplit room
    split_from: str = ""
    # storey this space sits on (#639); empty on single-storey models
    level_id: str = ""


@dataclass
class BEMOpeningUnit:
    """One physical opening instance (expanded from count x schedule)."""

    category: str  # "window" | "door" | "skylight"
    tag: str  # schedule tag, e.g. "A"
    width_m: float
    height_m: float
    provenance: Optional["Provenance"] = None
    history: List["Provenance"] = field(default_factory=list)
    # The space that owns the opening, when known (IFC export path from a
    # BuildingModel). Skylights stay over that room on the roof; wall
    # openings stay on that room's share of their facade. Empty means
    # "anywhere on the roof" / "any wall of the facade" (takeoff lines carry
    # no space).
    space_sid: str = ""
    # Wall openings: the facade the drawing/model put it on ("south", ...),
    # when known. Writers use it to keep the opening on that facade's walls;
    # empty means distribute by wall length as before.
    host_facade: str = ""
    # matchline identity written to the IFC (#588): {"id", "method",
    # "confidence"}; None writes nothing
    identity: Optional[dict] = None


@dataclass
class BEMShade:
    """One shading surface in absolute export coordinates (roadmap item 5).

    ``vertices`` are (x, y, z) in the BEM frame (x=east, y=north, z=up),
    already placed off the host wall's exterior face.
    """

    id: str  # ShadingSurface.id it came from
    kind: str  # "overhang" | "fin" | "balcony" | "other"
    host_wall_id: str
    vertices: list  # [(x, y, z), ...] planar quad
    # gap from the host segment line to the quad's inner edge, already in
    # ``vertices`` (ShadingSurface.offset_m), and the host's outward plan
    # normal; the IFC adapter uses them to seat the plate on the face of its
    # centred wall (#611). gbXML ignores both.
    offset_m: float = 0.0
    outward: Optional[tuple] = None


@dataclass
class BEMRoof:
    """One roof plane in the BEM frame (x east, y north, z above the floor).

    ``vertices`` is the 3-D outline of the top-of-roof face; ``tilt_deg``
    and ``azimuth_deg`` (compass, clockwise from north; None when flat)
    carry over from ``building_model.RoofPlane`` (#613, #618).
    """

    id: str
    vertices: list
    tilt_deg: float
    azimuth_deg: Optional[float] = None


@dataclass
class BEMLevel:
    """One storey for the multi-storey writers (#639).

    ``rings`` are the level's exterior wall loops in the BEM frame
    (x=east, y=north): the outer boundary counter-clockwise and any courtyard
    loops clockwise, so the right of travel is always outdoors.
    """

    id: str
    name: str
    elevation_m: float
    height_m: float
    rings: list = field(default_factory=list)
    wall_type: str = "ExteriorWall"  # "UndergroundWall" on a stated below-grade level
    above_ground: Optional[bool] = None  # as the source states it, else None


@dataclass
class BEMHorizontal:
    """A floor, ceiling or roof surface between or bounding storeys (#639).

    ``loops`` are hole-free polygons (counter-clockwise, x=east, y=north) at
    height ``z_m``; ``surface_type`` is the gbXML surfaceType.
    """

    id: str
    surface_type: str
    z_m: float
    lower_space_id: Optional[str]
    upper_space_id: Optional[str]
    loops: list = field(default_factory=list)


@dataclass
class BEMModel:
    building_name: str
    spaces: list  # BEMSpace
    openings: list  # BEMOpeningUnit, one per physical opening
    ring_m: list  # simplified envelope ring, CCW, x=east/y=north
    wall_height_m: float
    area_delta_pct: float  # envelope area preservation, from simplifier
    simplify_tolerance: float
    skipped_openings: list = field(default_factory=list)  # tags w/o dims
    notes: list = field(default_factory=list)
    zones: list = field(default_factory=list)  # list of (zone_id, [space_ids])
    # zone_id -> [(diffuser_id, tag, x_m, y_m)] in the same y-north frame as
    # ring_m; written as IfcAirTerminal DIFFUSER grouped into the IfcZone.
    zone_terminals: dict = field(default_factory=dict)
    shades: list = field(default_factory=list)  # BEMShade (roadmap item 5)
    # facade of each ring edge i -> i+1 (parallel to ring_m), set by adapters
    # that know which frame their ring is in; empty when unknown
    ring_facades: list = field(default_factory=list)
    # roof assembly U (W/m2K) from BuildingModel.roof_construction_id;
    # None -> the generic roof construction
    roof_u_value_w_m2k: Optional[float] = None
    # ground slab U (W/m2K) from BuildingModel.slab_construction_id;
    # None -> the generic slab-on-grade construction
    slab_u_value_w_m2k: Optional[float] = None
    # BEMRoof planes (#618); empty -> flat roof at wall_height_m, as before
    roof_planes: list = field(default_factory=list)
    # Appendix G thermal zones (#638): [(block_id, [space_ids])]; empty ->
    # the writers' single default zone, as before. Kept apart from ``zones``,
    # which carries HVAC zones and their diffusers.
    thermal_zones: list = field(default_factory=list)
    # air walls between pieces of a split room (#638), thermal_zoning.AirWall
    air_walls: list = field(default_factory=list)
    # multi-storey (#639): BEMLevel per storey and BEMHorizontal floors,
    # ceilings and roofs; empty on single-storey models
    levels: list = field(default_factory=list)
    horizontals: list = field(default_factory=list)
    provenance: Optional["Provenance"] = None
    history: List["Provenance"] = field(default_factory=list)


def _shoelace(poly) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _ensure_ccw(ring):
    return list(reversed(ring)) if _shoelace(ring) < 0 else list(ring)


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def model_from_takeoff(
    takeoff, labeled, sres, wall_height_m: float = 3.0, building_name: str = "Jesse Building"
) -> BEMModel:
    """Assemble a unit-clean BEMModel from the pipeline output contracts."""
    s = takeoff.scale.m_per_px
    if not s:
        raise ValueError(
            "BEM export needs a drawing scale (m/px) for spaces and envelope; "
            "takeoff.scale.m_per_px is None."
        )
    notes = []

    # --- coordinate transform provenance ------------------------------------
    # All geometry (spaces + envelope ring) enters as drawing pixels
    # (x-right, y-DOWN) and is converted to canonical BEM meters
    # (x-east, y-NORTH, z-up) via:  x_m = x_px * s,  y_m = -y_px * s
    src_conf = max((sp.label_confidence for sp in labeled.spaces), default=1.0)
    coord_conf = min(1.0, src_conf)
    notes.append(
        f"Coordinate transform: drawing px -> m (scale={s:.4f} m/px), "
        f"y-down -> north-up flip applied to {len(labeled.spaces)} space(s) "
        f"and envelope ring ({len(sres.ring)} vertices, simplified "
        f"from {sres.original_count} original edges, "
        f"area_delta={sres.area_delta_pct:.2f}%). "
        f"Transform confidence={coord_conf:.2f} (geometrically exact, "
        f"bounded by source geometry confidence={src_conf:.2f})."
    )

    # --- spaces -----------------------------------------------------------
    spaces = []
    for i, sp in enumerate(labeled.spaces):
        poly_m = [(x * s, -y * s) for x, y in sp.polygon_px]
        poly_m = _ensure_ccw(poly_m)
        area = abs(_shoelace(poly_m))
        name = f"{sp.name} {sp.number}".strip() or f"SPACE-{i + 1:02d}"
        spaces.append(
            BEMSpace(
                sid=f"sp-{i + 1:03d}",
                name=name,
                number=sp.number,
                polygon_m=poly_m,
                area_m2=area,
                volume_m3=area * wall_height_m,
            )
        )
    if not spaces:
        raise ValueError("no labeled spaces to export")

    # --- envelope ring ----------------------------------------------------
    ring_m = _ensure_ccw([(x * s, -y * s) for x, y in sres.ring])
    if len(ring_m) < 3:
        raise ValueError("simplified envelope ring is degenerate")

    # --- openings: expand count x schedule dims ---------------------------
    openings, skipped = [], []
    for line in takeoff.lines:
        if line.width_m is None or line.height_m is None:
            skipped.append(
                {
                    "tag": line.tag,
                    "category": line.category,
                    "count": line.count,
                    "reason": "schedule dimensions missing",
                }
            )
            continue
        cat = line.category.lower()
        if cat not in ("window", "door", "skylight"):
            skipped.append(
                {
                    "tag": line.tag,
                    "category": line.category,
                    "count": line.count,
                    "reason": f"category '{line.category}' not window/door/skylight; not placed as opening",
                }
            )
            continue
        openings.extend(
            BEMOpeningUnit(cat, line.tag, line.width_m, line.height_m) for _ in range(line.count)
        )
    # deterministic order: windows then doors, sorted by tag
    openings.sort(key=lambda o: (o.category, o.tag))
    notes.append(
        f"{len(openings)} openings expanded from "
        f"{len(takeoff.lines)} schedule lines; "
        f"{len(skipped)} tags skipped (see skipped_openings)."
    )

    simplify_tolerance = sres.tol * 100.0
    # Validate simplify_tolerance against maximum threshold (same as tol_area = 3%)
    MAX_SIMPLIFY_TOL = 3.0
    if simplify_tolerance > MAX_SIMPLIFY_TOL:
        raise ValueError(
            f"simplify_tolerance={simplify_tolerance:.2f}% exceeds maximum "
            f"threshold {MAX_SIMPLIFY_TOL:.0f}% — geometry simplification "
            f"introduced too much distortion for reliable BEM export"
        )

    return BEMModel(
        building_name=building_name,
        spaces=spaces,
        openings=openings,
        ring_m=ring_m,
        wall_height_m=wall_height_m,
        area_delta_pct=sres.area_delta_pct,
        simplify_tolerance=simplify_tolerance,
        skipped_openings=skipped,
        notes=notes,
    )
