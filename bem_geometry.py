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
    provenance: Optional["Provenance"] = None
    history: List["Provenance"] = field(default_factory=list)


@dataclass
class BEMOpeningUnit:
    """One physical opening instance (expanded from count x schedule)."""

    category: str  # "window" | "door"
    tag: str  # schedule tag, e.g. "A"
    width_m: float
    height_m: float
    provenance: Optional["Provenance"] = None
    history: List["Provenance"] = field(default_factory=list)


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
        if cat not in ("window", "door"):
            skipped.append(
                {
                    "tag": line.tag,
                    "category": line.category,
                    "count": line.count,
                    "reason": f"category '{line.category}' not window/door; not placed as opening",
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
