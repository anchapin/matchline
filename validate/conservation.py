"""Conservation law checks.

Provenance on every extracted fact.
Conservation laws: errors block export.
"""

from __future__ import annotations

import math

from bem_export import _shoelace

try:
    from datasets_adapter import polygon_area_px2
except Exception:
    polygon_area_px2 = None

from typing import TYPE_CHECKING

from validate.types import _HAS_SIMPLIFY, CheckResult

if TYPE_CHECKING:
    from validate import _Ctx

from bem_export import BEMModel


def _rel_err(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else float("inf")
    return abs(actual - expected) / abs(expected)


try:
    from geometry_simplify import footprint_from_regions, ring_perimeter
except Exception:
    pass


# ---------------------------------------------------------------------------
# The battery. Each _check_* takes ctx and returns CheckResult.
# ---------------------------------------------------------------------------


def _check_space_area_matches_polygon(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        if not sp.polygon_m or len(sp.polygon_m) < 3:
            bad.append(sid)
            continue
        if sp.area_m2 is None:
            bad.append(sid)
            continue
        poly_a = polygon_area_px2(sp.polygon_m)
        if _rel_err(sp.area_m2, poly_a) > 1e-3:
            bad.append(sid)
    if not ctx.model.spaces:
        return CheckResult(
            "space_area_matches_polygon",
            "Space area equals shoelace(polygon)",
            "error",
            "model has no spaces",
            entities=[],
        )
    if bad:
        return CheckResult(
            "space_area_matches_polygon",
            "Space area equals shoelace(polygon)",
            "warn",
            f"{len(bad)} space(s) whose area_m2 disagrees "
            f"with their polygon (>0.1%): drift between "
            f"recorded area and geometry",
            entities=bad,
        )
    return CheckResult(
        "space_area_matches_polygon",
        "Space area equals shoelace(polygon)",
        "pass",
        f"{len(ctx.model.spaces)} spaces consistent",
    )


def _check_area_conservation(ctx: _Ctx) -> CheckResult:
    """SUM(space areas) per level ~= footprint area. The 20x30 -> 600 check."""
    if not _HAS_SIMPLIFY:
        return CheckResult(
            "area_conservation",
            "Area conservation",
            "skip",
            "geometry_simplify unavailable; cannot union footprint",
        )
    worst = []
    for lid, spaces in ctx.level_of.items():
        fp = ctx.footprint_area.get(lid, 0.0)
        total = sum(sp.area_m2 or 0.0 for sp in spaces)
        err = _rel_err(total, fp)
        if err > ctx.tol_area:
            worst.append((lid, total, fp, err))
    if worst:
        lid, total, fp, err = worst[0]
        return CheckResult(
            "area_conservation",
            "Area conservation (sum rooms ~= footprint)",
            "error",
            f"level {lid}: sum of space areas {total:.2f} m^2 vs footprint "
            f"{fp:.2f} m^2 (rel err {err:.1%}, tol {ctx.tol_area:.0%}). "
            f"Rooms should tile the floor plate minus wall thickness.",
            entities=[lid],
            expected=fp,
            actual=total,
        )
    detail = {lid: round(float(ctx.footprint_area.get(lid, 0.0)), 2) for lid in ctx.level_of}
    total_all = float(
        sum(sum(sp.area_m2 or 0.0 for sp in spaces) for spaces in ctx.level_of.values())
    )
    fp_all = float(sum(ctx.footprint_area.get(lid, 0.0) for lid in ctx.level_of))
    return CheckResult(
        "area_conservation",
        "Area conservation (sum rooms ~= footprint)",
        "pass",
        f"per-level room areas tile footprint within {ctx.tol_area:.0%}: {detail}",
        expected=fp_all,
        actual=total_all,
    )


def _check_space_volume_matches_area_height(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        h = ctx.wall_height.get(sp.level_id)
        if sp.volume_m3 is None or sp.area_m2 is None or not h:
            bad.append(sid)
            continue
        if _rel_err(sp.volume_m3, sp.area_m2 * h) > 1e-3:
            bad.append(sid)
    if bad:
        return CheckResult(
            "space_volume_matches_area_height",
            "Space volume = area x height",
            "warn",
            f"{len(bad)} space(s) whose volume_m3 disagrees with area x wall height (>0.1%)",
            entities=bad,
        )
    return CheckResult(
        "space_volume_matches_area_height",
        "Space volume = area x height",
        "pass",
        f"{len(ctx.model.spaces)} spaces consistent",
    )


def _check_volume_conservation(ctx: _Ctx) -> CheckResult:
    """SUM(space volumes) ~= footprint x floor-to-floor height."""
    if not _HAS_SIMPLIFY:
        return CheckResult(
            "volume_conservation",
            "Volume conservation",
            "skip",
            "geometry_simplify unavailable; cannot union footprint",
        )
    worst = []
    for lid, spaces in ctx.level_of.items():
        h = ctx.wall_height.get(lid, 0.0)
        fp = ctx.footprint_area.get(lid, 0.0)
        total = sum(sp.volume_m3 or 0.0 for sp in spaces)
        err = _rel_err(total, fp * h)
        if err > ctx.tol_volume:
            worst.append((lid, total, fp * h, err))
    if worst:
        lid, total, exp, err = worst[0]
        return CheckResult(
            "volume_conservation",
            "Volume conservation",
            "error",
            f"level {lid}: sum of space volumes {total:.2f} m^3 vs "
            f"footprint x height {exp:.2f} m^3 (rel err {err:.1%}, tol "
            f"{ctx.tol_volume:.0%})",
            entities=[lid],
            expected=exp,
            actual=total,
        )
    total_all = sum(sum(sp.volume_m3 or 0.0 for sp in spaces) for spaces in ctx.level_of.values())
    exp_all = sum(
        ctx.footprint_area.get(lid, 0.0) * ctx.wall_height.get(lid, 0.0) for lid in ctx.level_of
    )
    return CheckResult(
        "volume_conservation",
        "Volume conservation",
        "pass",
        f"per-level space volumes match footprint x height within {ctx.tol_volume:.0%}",
        expected=exp_all,
        actual=total_all,
    )


def _check_bem_area_conservation(bem: BEMModel, tol_area: float) -> CheckResult:
    """BEM envelope area preservation within tolerance?

    The BEMModel's area_delta_pct is pre-computed during model_from_linked_model
    or model_from_takeoff as the percentage change in envelope area due to
    polygon simplification. This check validates that delta stays within tolerance.
    """
    # area_delta_pct is pre-computed by model_from_linked_model/model_from_takeoff
    area_delta = getattr(bem, "area_delta_pct", None)
    if area_delta is not None:
        if abs(area_delta) > tol_area * 100:
            return CheckResult(
                "bem_area_conservation",
                "BEM area conservation",
                "error",
                f"area_delta_pct={area_delta:.2f}% exceeds {tol_area * 100:.0f}% tolerance",
            )
        return CheckResult(
            "bem_area_conservation",
            "BEM area conservation",
            "pass",
            f"area_delta_pct={area_delta:.2f}% within {tol_area * 100:.0f}% tolerance",
        )
    # Fallback: compute delta from space areas and ring area
    space_total = sum(sp.area_m2 for sp in bem.spaces)
    ring_area = abs(_shoelace(bem.ring_m)) if bem.ring_m else 0.0
    if ring_area <= 0:
        # No envelope ring provided: validate floor-area coverage directly.
        if space_total <= 0:
            return CheckResult(
                "bem_area_conservation",
                "BEM area conservation",
                "error",
                "ring area is zero or negative and no spaces to validate",
            )
        return CheckResult(
            "bem_area_conservation",
            "BEM area conservation",
            "pass",
            f"floor-area total ({space_total:.1f}) used as envelope (ring unavailable)",
        )
    # ring covers <90% of floor area: treat as unavailable (the ring was
    # derived from a single space's polygon rather than the full envelope).
    if ring_area < 0.9 * space_total:
        return CheckResult(
            "bem_area_conservation",
            "BEM area conservation",
            "pass",
            f"ring area ({ring_area:.1f}) does not cover full floor "
            f"({space_total:.1f}); using floor-area fallback",
        )
    delta_pct = abs(space_total - ring_area) / ring_area * 100
    if delta_pct > tol_area * 100:
        return CheckResult(
            "bem_area_conservation",
            "BEM area conservation",
            "error",
            f"space total ({space_total:.1f}) differs from ring area "
            f"({ring_area:.1f}) by {delta_pct:.2f}%",
        )
    return CheckResult(
        "bem_area_conservation",
        "BEM area conservation",
        "pass",
        f"space total ({space_total:.1f}) matches ring area ({ring_area:.1f})",
    )


def _check_bem_zone_space_refs(bem: BEMModel) -> CheckResult:
    """Every zone.space_ids must reference an existing space in bem.spaces."""
    space_ids = {sp.sid for sp in bem.spaces}
    bad = []
    for zone in bem.zones:
        zone_id, zone_space_ids = zone
        for sid in zone_space_ids:
            if sid not in space_ids:
                bad.append((zone_id, sid))
    if bad:
        zone_id, sid = bad[0]
        return CheckResult(
            "bem_zone_space_refs",
            "BEM zone space references",
            "error",
            f"zone {zone_id} references non-existent space {sid}",
            entities=[zone_id],
        )
    return CheckResult(
        "bem_zone_space_refs",
        "BEM zone space references",
        "pass",
        f"all {len(bem.zones)} zones reference only valid spaces",
    )


def _check_bem_hvac_zone_refs(bem: BEMModel) -> CheckResult:
    """Every space.hvac.zone_ids must reference an existing zone in bem.zones."""
    zone_ids = {z[0] for z in bem.zones}
    bad = []
    for sp in bem.spaces:
        hvac = getattr(sp, "hvac", None)
        if hvac is None:
            continue
        for zid in hvac.zone_ids:
            if zid not in zone_ids:
                bad.append((sp.sid, zid))
    if bad:
        space_id, zid = bad[0]
        return CheckResult(
            "bem_hvac_zone_refs",
            "BEM HVAC zone references",
            "error",
            f"space {space_id} references non-existent zone {zid}",
            entities=[space_id],
        )
    return CheckResult(
        "bem_hvac_zone_refs",
        "BEM HVAC zone references",
        "pass",
        f"all {len(bem.spaces)} spaces reference only valid zones",
    )


def _check_bem_zone_space_symmetry(bem: BEMModel) -> CheckResult:
    """If zone includes space, the space must include that zone (bidirectional)."""
    bad = []
    for zone in bem.zones:
        zone_id, zone_space_ids = zone
        for sid in zone_space_ids:
            sp = next((s for s in bem.spaces if s.sid == sid), None)
            if sp is None:
                continue
            hvac = getattr(sp, "hvac", None)
            if hvac is None:
                continue
            if zone_id not in hvac.zone_ids:
                bad.append((zone_id, sid))
    if bad:
        zone_id, space_id = bad[0]
        return CheckResult(
            "bem_zone_space_symmetry",
            "BEM zone-space symmetry",
            "error",
            f"zone {zone_id} contains space {space_id} but space does not list zone",
            entities=[zone_id, space_id],
        )
    return CheckResult(
        "bem_zone_space_symmetry",
        "BEM zone-space symmetry",
        "pass",
        "all zone-space membership relations are bidirectional",
    )


def _check_bem_volume_conservation(bem: BEMModel, tol_volume: float) -> CheckResult:
    """BEM space volumes consistent with expected volumes?

    For BEMModel, space volumes are stored in BEMSpace.volume_m3 and
    the total is computed from wall_height_m * ring_area.

    If ring_m is empty or covers only a subset of the floor (e.g. it was
    derived from a single space's polygon), fall back to validating the
    total space-volume sum against the floor-area * wall-height. This is
    the "rings_unavailable, floor-area validated" path.
    """
    mismatches: list[str] = []
    total_space_vol = sum(sp.volume_m3 for sp in bem.spaces)
    floor_area = sum(sp.area_m2 for sp in bem.spaces)
    ring_area = abs(_shoelace(bem.ring_m)) if bem.ring_m else 0.0
    if ring_area <= 0 or ring_area < 0.9 * floor_area:
        # ring unavailable: use floor-area * wall_height as expected
        effective_area = floor_area
        area_source = "floor-area"
    else:
        effective_area = ring_area
        area_source = "ring"
    expected_vol = (
        effective_area * bem.wall_height_m if effective_area > 0 and bem.wall_height_m else 0.0
    )
    if expected_vol > 0:
        vol_delta_pct = abs(total_space_vol - expected_vol) / expected_vol * 100
        if vol_delta_pct > tol_volume * 100:
            mismatches.append(
                f"total vol={total_space_vol:.1f} vs "
                f"{area_source}×height={expected_vol:.1f} "
                f"({vol_delta_pct:.2f}% delta)"
            )
    if mismatches:
        return CheckResult(
            "bem_volume_conservation",
            "BEM volume conservation",
            "error",
            f"{len(mismatches)} volume(s) exceed {tol_volume * 100:.0f}% "
            "tolerance:\n" + "\n".join(mismatches),
        )
    return CheckResult(
        "bem_volume_conservation",
        "BEM volume conservation",
        "pass",
        f"all space volumes consistent (using {area_source})",
    )


def validate_bem_conservation(
    bem: BEMModel,
    tol_area: float = 0.03,
    tol_volume: float = 0.03,
) -> list[CheckResult]:
    """Run conservation law checks on a BEMModel.

    Returns a list of CheckResult objects. An empty list means no conservation
    checks were applicable. A failed CheckResult indicates a violation.

    Use at export time or after model_from_linked_model / model_from_takeoff
    to catch violations introduced by BEM transformations.
    """
    results: list[CheckResult] = []
    results.append(_check_bem_area_conservation(bem, tol_area))
    results.append(_check_bem_volume_conservation(bem, tol_volume))
    results.append(_check_bem_zone_space_refs(bem))
    results.append(_check_bem_hvac_zone_refs(bem))
    results.append(_check_bem_zone_space_symmetry(bem))
    return results


def _check_envelope_area_matches_perimeter(ctx: _Ctx) -> CheckResult:
    """SUM(envelope wall areas) ~= footprint perimeter x height.

    The envelope records and the footprint union are built by different
    code paths (arch-plan wall runs vs space-polygon union); >1% drift
    means one of them is wrong. The 1% tolerance absorbs exterior-vs-
    interior wall-face representation differences (wall thickness).
    """
    if not _HAS_SIMPLIFY:
        return CheckResult(
            "envelope_area_matches_perimeter",
            "Envelope area matches perimeter x height",
            "skip",
            "geometry_simplify unavailable",
        )
    bad = []
    for lid in ctx.level_of:
        ring = footprint_from_regions([sp.polygon_m for sp in ctx.level_of[lid]])
        perim = (
            sum(math.dist(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
            if ring
            else 0.0
        )
        h = ctx.wall_height.get(lid, 0.0)
        exp = perim * h
        got = sum(w.area_m2 or 0.0 for w in ctx.model.envelope if w.id.startswith(lid + "-"))
        if _rel_err(got, exp) > ctx.tol_envelope:
            bad.append((lid, got, exp))
    if bad:
        lid, got, exp = bad[0]
        return CheckResult(
            "envelope_area_matches_perimeter",
            "Envelope area matches perimeter x height",
            "error",
            f"level {lid}: envelope walls sum to {got:.2f} m^2 vs "
            f"footprint perimeter x height {exp:.2f} m^2 (tol "
            f"{ctx.tol_envelope:.0%})",
            entities=[lid],
            expected=exp,
            actual=got,
        )
    return CheckResult(
        "envelope_area_matches_perimeter",
        "Envelope area matches perimeter x height",
        "pass",
        f"envelope wall areas match perimeter x height within {ctx.tol_envelope:.0%}",
    )


def area_closure(model: BuildingModel) -> CheckResult:
    """Total floor area ~= sum of individual room areas.

    Conservation law: the gross floor area (union of space polygons) should
    equal the sum of each room's reported area, within tolerance.
    A mismatch indicates an error in either the space boundary definitions
    or the area calculations.
    """
    if not _HAS_SIMPLIFY:
        return CheckResult(
            "area_closure",
            "Area closure",
            "skip",
            "geometry_simplify unavailable",
        )
    try:
        all_polys = [sp.polygon_m for sp in model.spaces.values() if sp.polygon_m is not None]
        if not all_polys:
            return CheckResult(
                "area_closure",
                "Area closure",
                "skip",
                "no space polygons available",
            )
        union = footprint_from_regions(all_polys)
        ring = union if isinstance(union, list) else []
        footprint_area = abs(_shoelace(ring))
        room_area_sum = sum(sp.area_m2 or 0.0 for sp in model.spaces.values())
        rel_err = _rel_err(room_area_sum, footprint_area)
        if rel_err > 0.03:
            return CheckResult(
                "area_closure",
                "Area closure",
                "error",
                f"room areas sum to {room_area_sum:.2f} m^2 vs footprint {footprint_area:.2f} m^2 "
                f"(error {rel_err:.1%}, tol 3%)",
                expected=footprint_area,
                actual=room_area_sum,
            )
        return CheckResult(
            "area_closure",
            "Area closure",
            "pass",
            f"room areas sum {room_area_sum:.2f} m^2 matches footprint {footprint_area:.2f} m^2 "
            f"within 3%",
            expected=footprint_area,
            actual=room_area_sum,
        )
    except Exception as exc:
        return CheckResult(
            "area_closure",
            "Area closure",
            "error",
            f"could not compute area closure: {exc}",
        )


def volume_closure(model: BuildingModel) -> CheckResult:
    """Total building volume ~= sum of individual room volumes.

    Conservation law: the gross building volume (footprint area x characteristic
    height) should equal the sum of each room's reported volume, within tolerance.
    """
    if not _HAS_SIMPLIFY:
        return CheckResult(
            "volume_closure",
            "Volume closure",
            "skip",
            "geometry_simplify unavailable",
        )
    try:
        all_polys = [sp.polygon_m for sp in model.spaces.values() if sp.polygon_m is not None]
        if not all_polys:
            return CheckResult(
                "volume_closure",
                "Volume closure",
                "skip",
                "no space polygons available",
            )
        union = footprint_from_regions(all_polys)
        ring = union if isinstance(union, list) else []
        footprint_area = abs(_shoelace(ring))
        room_vol_sum = sum(sp.volume_m3 or 0.0 for sp in model.spaces.values())
        if not model.levels:
            return CheckResult(
                "volume_closure",
                "Volume closure",
                "skip",
                "no levels available",
            )
        avg_height = sum(l.wall_height_m or 3.0 for l in model.levels) / len(model.levels)
        expected_vol = footprint_area * avg_height
        rel_err = _rel_err(room_vol_sum, expected_vol)
        if rel_err > 0.05:
            return CheckResult(
                "volume_closure",
                "Volume closure",
                "error",
                f"room volumes sum to {room_vol_sum:.2f} m^3 vs expected {expected_vol:.2f} m^3 "
                f"(error {rel_err:.1%}, tol 5%)",
                expected=expected_vol,
                actual=room_vol_sum,
            )
        return CheckResult(
            "volume_closure",
            "Volume closure",
            "pass",
            f"room volumes sum {room_vol_sum:.2f} m^3 matches expected {expected_vol:.2f} m^3 "
            f"within 5%",
            expected=expected_vol,
            actual=room_vol_sum,
        )
    except Exception as exc:
        return CheckResult(
            "volume_closure",
            "Volume closure",
            "error",
            f"could not compute volume closure: {exc}",
        )


def envelope_closure(model: BuildingModel) -> CheckResult:
    """Total facade area ~= sum of solid wall areas (facade minus openings).

    Conservation law: the total facade area (sum of EnvelopeWall.area_m2) should
    equal the sum of solid wall areas (facade area minus window+door areas),
    within tolerance. This ensures the facade area is fully accounted for.
    """
    try:
        facade_area = sum(w.area_m2 or 0.0 for w in model.envelope)
        opening_area = 0.0
        for sp in model.spaces.values():
            if sp.openings:
                for op in sp.openings:
                    if op.area_m2:
                        opening_area += op.area_m2
        solid_wall_area = facade_area - opening_area
        rel_err = _rel_err(solid_wall_area, facade_area) if facade_area > 0 else float("inf")
        if rel_err > 0.05:
            return CheckResult(
                "envelope_closure",
                "Envelope closure",
                "error",
                f"solid wall area {solid_wall_area:.2f} m^2 vs facade {facade_area:.2f} m^2 "
                f"(error {rel_err:.1%}, tol 5%)",
                expected=facade_area,
                actual=solid_wall_area,
            )
        return CheckResult(
            "envelope_closure",
            "Envelope closure",
            "pass",
            f"solid wall area {solid_wall_area:.2f} m^2 matches facade {facade_area:.2f} m^2 "
            f"within 5%",
            expected=facade_area,
            actual=solid_wall_area,
        )
    except Exception as exc:
        return CheckResult(
            "envelope_closure",
            "Envelope closure",
            "error",
            f"could not compute envelope closure: {exc}",
        )


# ---------------------------------------------------------------------------
# Convention bias (roadmap item 1): measure the volume delta between the
# interior-face and exterior-face derivations rather than asserting it is zero.
# ---------------------------------------------------------------------------

# Plausible exterior wall thickness for a commercial building, in metres.
# Outside this band the number is far more likely a units slip (an inch or a
# foot value landing in a metre field) or a material-layer parse error than a
# real assembly, so the check reports it instead of quietly biasing a volume.
THICKNESS_PLAUSIBLE_M = (0.05, 1.20)

# Above this, the bias stops being a footnote on the export and becomes the
# headline. 10% of volume is roughly a 0.5 m wall on a 20 m x 30 m plate.
BIAS_WARN_FRACTION = 0.10


def _exterior_thickness_m(ctx: _Ctx) -> tuple[float | None, str, list]:
    """Best available exterior wall thickness, with its source and evidence.

    Priority is analytical first, per roadmap item 1: ``IfcMaterialLayerSet``
    gives true per-layer thickness on the BIM path, so no convention is needed,
    just extraction. ``thickness_m`` on its own is the drawing-path fallback
    (wall-thickness pairs or dimension annotations). Returns (None, reason, [])
    when neither exists, which is a skip and never a guessed default: a
    fabricated thickness would produce a fabricated bias number, which is worse
    than reporting that the input does not carry one.
    """
    walls = [
        e
        for e in getattr(ctx.model, "bim_elements", [])
        if str(getattr(e, "ifc_class", "")).lower().startswith("ifcwall")
    ]
    layered = []
    for w in walls:
        layers = getattr(w, "material_layers", None) or []
        total = sum(
            float(layer["thickness_m"])
            for layer in layers
            if isinstance(layer, dict) and layer.get("thickness_m") is not None
        )
        if total > 0:
            layered.append((w, total))
    if layered:
        vals = [t for _, t in layered]
        return (
            sum(vals) / len(vals),
            f"material_layers ({len(layered)} wall(s), analytical)",
            [w.global_id for w, _ in layered],
        )

    direct = [
        (w, float(w.thickness_m))
        for w in walls
        if getattr(w, "thickness_m", None) is not None and float(w.thickness_m) > 0
    ]
    if direct:
        vals = [t for _, t in direct]
        return (
            sum(vals) / len(vals),
            f"thickness_m ({len(direct)} wall(s), geometry)",
            [w.global_id for w, _ in direct],
        )

    return None, "no wall carries material_layers or thickness_m", []


def _check_convention_bias(ctx: _Ctx) -> CheckResult:
    """Report the interior-face vs exterior-face volume delta per level.

    Roadmap item 1 settles the architecture: thickness is kept internally and
    surfaces are derived per convention at export. That means a choice of
    convention biases the exported air volume in a KNOWN direction (spaces
    tile the interior, so an exterior-face derivation is always larger), and
    the project's answer everywhere is to measure the bias and attribute it to
    the convention that caused it rather than pretend it is zero.

    So this check is a measurement, not a conservation law. It is informational
    by design and does not block export: there is no "correct" value to fail
    against, and an export whose bias is honestly reported is exactly what the
    convention report wants. It warns in two cases only, both of which mean the
    NUMBER is untrustworthy rather than the building being unusual: a thickness
    outside a plausible band, and a bias large enough to dominate the export.

    Geometry: offsetting a simple closed ring outward by t grows its area by
    perimeter x t plus a corner term. The corner term is the Steiner term
    pi t^2 for a convex ring, which is what is used here; for a 0.2 m wall
    that whole term is 0.13 m^2, so on any real plate it is far below the 3%
    conservation tolerances while the perimeter x t term is the one that
    matters. Non-convex plates make it a slight overestimate of the corner
    contribution only. Documented rather than hidden, because the bias number
    is the deliverable.
    """
    check_id = "convention_bias"
    name = "Convention bias (interior vs exterior face)"

    if not _HAS_SIMPLIFY or polygon_area_px2 is None:
        return CheckResult(
            check_id, name, "skip", "geometry_simplify unavailable; cannot union footprint"
        )
    if not ctx.level_of:
        return CheckResult(check_id, name, "skip", "model has no spaces on any level")

    thickness, source, evidence = _exterior_thickness_m(ctx)
    if thickness is None:
        return CheckResult(
            check_id,
            name,
            "skip",
            f"no exterior wall thickness available ({source}); "
            f"volume bias is unmeasurable, not zero",
        )

    lo, hi = THICKNESS_PLAUSIBLE_M
    implausible = not (lo <= thickness <= hi)

    per_level = {}
    interior_total = exterior_total = 0.0
    for lid, spaces in ctx.level_of.items():
        h = ctx.wall_height.get(lid, 0.0)
        fp_area = ctx.footprint_area.get(lid, 0.0)
        ring = footprint_from_regions([sp.polygon_m for sp in spaces])
        perim = ring_perimeter(ring) if ring else 0.0
        if fp_area <= 0 or h <= 0:
            continue
        interior_v = fp_area * h
        exterior_area = fp_area + perim * thickness + math.pi * thickness**2
        exterior_v = exterior_area * h
        interior_total += interior_v
        exterior_total += exterior_v
        per_level[lid] = {
            "interior_volume_m3": interior_v,
            "exterior_volume_m3": exterior_v,
            "delta_m3": exterior_v - interior_v,
            "delta_pct": (exterior_v - interior_v) / interior_v * 100.0,
            "perimeter_m": perim,
            "wall_height_m": h,
        }

    if not per_level:
        return CheckResult(
            check_id, name, "skip", "no level has both a footprint area and a wall height"
        )

    delta = exterior_total - interior_total
    frac = delta / interior_total if interior_total else 0.0
    payload = {
        "thickness_m": thickness,
        "thickness_source": source,
        "convention": "interior_face (canonical); exterior_face derived",
        "levels": per_level,
    }
    worst = max(per_level, key=lambda lid: per_level[lid]["delta_pct"])
    headline = (
        f"exterior-face derivation is {delta:.2f} m^3 ({frac:.1%}) larger than "
        f"interior-face across {len(per_level)} level(s); worst level {worst} "
        f"at {per_level[worst]['delta_pct']:.1f}%; t={thickness:.3f} m from {source}"
    )

    if implausible:
        return CheckResult(
            check_id,
            name,
            "warn",
            f"wall thickness {thickness:.3f} m is outside the plausible band "
            f"{lo}-{hi} m, so the bias number is untrustworthy (likely a units "
            f"slip or a material-layer parse error). {headline}",
            entities=evidence,
            expected=interior_total,
            actual=payload,
        )
    if frac > BIAS_WARN_FRACTION:
        return CheckResult(
            check_id,
            name,
            "warn",
            f"convention bias exceeds {BIAS_WARN_FRACTION:.0%} of air volume: "
            f"{headline}. Report it with the export; do not average it away.",
            entities=sorted(per_level),
            expected=interior_total,
            actual=payload,
        )
    return CheckResult(
        check_id,
        name,
        "pass",
        headline,
        expected=interior_total,
        actual=payload,
    )


SKYLIGHT_ROOF_HOST = "roof"


def _skylight_area(o, schedules: dict):
    """Area of one skylight, or None when no dimensions resolve.

    Same resolution order as the rest of the battery: the opening's own
    area, then its own width x height, then the schedule entry for its tag.
    An unresolvable skylight is opening_schedule_join's to report; here it is
    left out of the sum and named, never counted as zero.
    """
    if o.area_m2 is not None:
        return o.area_m2
    if o.width_m is not None and o.height_m is not None:
        return o.width_m * o.height_m
    e = schedules.get(o.tag, {}) if o.tag else {}
    if e.get("width_m") is not None and e.get("height_m") is not None:
        return e["width_m"] * e["height_m"]
    return None


def _check_skylight_within_roof(ctx: _Ctx) -> CheckResult:
    """Per space: skylight area <= the roof area over that space.

    Roadmap item 3. The model has no roof entity yet; the gbXML exporter
    writes one flat roof over the footprint. This check makes that convention
    explicit instead of hiding it: the roof over a space on the top level is
    taken to be the space's own floor area (flat roof, no overhang), and
    spaces below the top level have no roof. Sloped roofs are roadmap item 2;
    when they land, the roof area here becomes the space's share of the roof
    planes and the flat-roof line goes away.

    error -- a space's skylights exceed its roof area. No convention makes
             that real: it is a dimension slip or a wrong host space.
    warn  -- a skylight hosted by a space below the top level. Under the
             flat-roof convention that space has no roof. It may be an atrium
             or light well (real, but the model cannot represent it yet) or a
             level assignment error; either way a person should look.
    skip  -- the model has no skylights.
    """
    sky = [
        (sid, sp, o)
        for sid, sp in ctx.model.spaces.items()
        for o in sp.openings
        if o.category == "skylight"
    ]
    if not sky:
        return CheckResult(
            "skylight_within_roof",
            "Skylight within roof",
            "skip",
            "no skylights in model",
        )

    levels = list(ctx.model.levels)
    if levels:
        top_z = max(lvl.elevation_z_m for lvl in levels)
        top_ids = {lvl.id for lvl in levels if lvl.elevation_z_m == top_z}
    elif len(ctx.level_of) == 1:
        top_ids = set(ctx.level_of)
    else:
        top_ids = None  # levels undeclared: cannot tell which carries the roof

    per_space: dict = {}
    unmeasured = []
    for sid, sp, o in sky:
        a = _skylight_area(o, ctx.model.schedules)
        if a is None:
            unmeasured.append(o.id)
            continue
        per_space[sid] = per_space.get(sid, 0.0) + a

    over = []
    for sid, sa in per_space.items():
        sp = ctx.model.spaces[sid]
        roof = sp.area_m2 if sp.area_m2 is not None else abs(_shoelace(sp.polygon_m))
        if sa > roof * (1.0 + ctx.opening_eps) + 1e-9:
            over.append((sid, sa, roof))
    if over:
        sid, sa, roof = over[0]
        return CheckResult(
            "skylight_within_roof",
            "Skylight within roof",
            "error",
            f"{len(over)} space(s) with more skylight than roof: {sid} has "
            f"{sa:.2f} m^2 of skylight under a {roof:.2f} m^2 flat roof",
            entities=[o[0] for o in over][:20],
            expected=roof,
            actual=sa,
        )

    tail = f"; {len(unmeasured)} skylight(s) without dimensions left out" if unmeasured else ""
    if top_ids is not None:
        below = sorted({o.id for sid, sp, o in sky if sp.level_id not in top_ids})
        if below:
            return CheckResult(
                "skylight_within_roof",
                "Skylight within roof",
                "warn",
                f"{len(below)} skylight(s) hosted below the top level, where the "
                f"flat-roof convention puts no roof (atrium, or a level "
                f"assignment error){tail}",
                entities=below[:20],
            )
    note = "" if top_ids is not None else "; levels undeclared, host level not checked"
    total = sum(per_space.values())
    return CheckResult(
        "skylight_within_roof",
        "Skylight within roof",
        "pass",
        f"{len(sky)} skylight(s), {total:.2f} m^2, each space within its "
        f"flat-roof area{note}{tail}",
    )
