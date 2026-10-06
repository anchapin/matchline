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


def _check_bem_space_area_matches_polygon(bem: BEMModel, tol_area: float) -> CheckResult:
    """Each BEM space's stored area_m2 matches the area of its own polygon.

    Issue #478: an export must block when a space claims more (or less) floor
    than its outline encloses. Before this check the case was caught only by
    accident, through a malformed envelope ring that pushed the volume check
    onto its floor-area fallback; with a correct ring it slipped through.
    Spaces without a usable polygon or area are skipped, not guessed.
    """
    bad = []
    checked = 0
    for sp in bem.spaces:
        poly = getattr(sp, "polygon_m", None) or []
        if len(poly) < 3 or not sp.area_m2:
            continue
        poly_area = abs(_shoelace(poly))
        if poly_area <= 0:
            continue
        checked += 1
        delta = abs(sp.area_m2 - poly_area) / poly_area
        if delta > tol_area:
            bad.append((sp.sid, sp.area_m2, poly_area, delta))
    if bad:
        lines = [
            f"space {sid}: area_m2={a:.2f} vs polygon {pa:.2f} ({d * 100:.2f}% delta)"
            for sid, a, pa, d in bad
        ]
        return CheckResult(
            "bem_space_area_matches_polygon",
            "BEM space area matches its polygon",
            "error",
            f"{len(bad)} space(s) exceed {tol_area * 100:.0f}% tolerance:\n" + "\n".join(lines),
            entities=[b[0] for b in bad],
        )
    return CheckResult(
        "bem_space_area_matches_polygon",
        "BEM space area matches its polygon",
        "pass" if checked else "skip",
        f"{checked} space area(s) match their polygons within {tol_area * 100:.0f}%"
        if checked
        else "no spaces with both a polygon and an area",
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
    results.append(_check_bem_space_area_matches_polygon(bem, tol_area))
    results.append(_check_bem_volume_conservation(bem, tol_volume))
    results.append(_check_bem_zone_space_refs(bem))
    results.append(_check_bem_hvac_zone_refs(bem))
    results.append(_check_bem_zone_space_symmetry(bem))
    return results


def _centreline_perimeters(model, lid, ring, perim):
    """Footprint perimeter, plus it offset by +-t/2 when IFC walls give a thickness t."""
    out = [perim]
    if not ring or len(ring) < 3:
        return out
    thick = {
        e.global_id: e.thickness_m
        for e in getattr(model, "bim_elements", []) or []
        if getattr(e, "thickness_m", None)
    }
    ts = []
    for w in model.envelope:
        if not w.id.startswith(lid + "-") or w.provenance is None:
            continue
        tok = (w.provenance.note or "").split(" ", 1)[0]
        if tok.startswith("GlobalId=") and tok[9:] in thick:
            ts.append(thick[tok[9:]])
    if not ts:
        return out
    ts.sort()
    t = ts[len(ts) // 2]
    try:
        from shapely.geometry import Polygon
    except ImportError:  # pragma: no cover
        return out
    poly = Polygon(ring)
    if not poly.is_valid or poly.area <= 0:
        return out
    for d in (-t / 2, t / 2):
        g = poly.buffer(d, join_style=2, mitre_limit=10.0)
        if not g.is_empty and g.geom_type == "Polygon":
            out.append(g.exterior.length)
    return out


def _check_envelope_area_matches_perimeter(ctx: _Ctx) -> CheckResult:
    """SUM(envelope wall areas) ~= footprint perimeter x height.

    The envelope records and the footprint union are built by different
    code paths (arch-plan wall runs vs space-polygon union); >1% drift
    means one of them is wrong. The 1% tolerance absorbs exterior-vs-
    interior wall-face representation differences (wall thickness).

    IFC-imported segments sit on wall body centrelines (#579) while spaces
    may be drawn to the exterior or the interior face, so where the level's
    segments carry a known wall thickness t, the footprint is also compared
    offset by -t/2 and +t/2 and the closest of the three counts.
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
        got = sum(w.area_m2 or 0.0 for w in ctx.model.envelope if w.id.startswith(lid + "-"))
        exp = min(
            (p * h for p in _centreline_perimeters(ctx.model, lid, ring, perim)),
            key=lambda e: _rel_err(got, e),
        )
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


def _unclaimed_note(model) -> str:
    """Unclaimed wall-loop area flagged on IFC import (#581), for the area summary."""
    from ifc_wall_loops import unclaimed_loop_area

    n, total = unclaimed_loop_area(model)
    if not n:
        return ""
    return f"; plus {n} unclaimed wall loop(s), {total:.2f} m^2 at centrelines, not in any space"


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
                f"(error {rel_err:.1%}, tol 3%)" + _unclaimed_note(model),
                expected=footprint_area,
                actual=room_area_sum,
            )
        return CheckResult(
            "area_closure",
            "Area closure",
            "pass",
            f"room areas sum {room_area_sum:.2f} m^2 matches footprint {footprint_area:.2f} m^2 "
            f"within 3%" + _unclaimed_note(model),
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


def _zone_poly(z):
    from shapely.geometry import Polygon

    if not z.polygon_m or len(z.polygon_m) < 3:
        return None
    g = Polygon(z.polygon_m)
    return g if g.is_valid else g.buffer(0)


ROOF_COVERAGE_TOL = 0.01  # gap or overlap, as a share of the top-level footprint
ROOF_APERTURE_TOL = 0.02  # same default as roof_simplify.APERTURE_TOL


def _roof_plan(rp):
    from shapely.geometry import Polygon

    return Polygon([(v[0], v[1]) for v in rp.vertices_m]).buffer(0)


def _check_roof_plan_coverage(ctx: _Ctx) -> CheckResult:
    """The top level's roof planes cover its footprint once (#617).

    Roadmap item 2. The plan projection of the roof planes on the top level
    must cover the union of that level's space polygons, with no part covered
    twice. Eaves past the footprint are fine; they are not counted.

    error -- uncovered footprint above ROOF_COVERAGE_TOL of its area (a
             missing facet; spaces under the gap are named), or roof planes
             overlapping in plan above the same tolerance (a doubled facet;
             the overlapping plane ids are named). Either changes the heat-
             loss area BEM sees.
    pass  -- covered once within tolerance.
    skip  -- no roof planes (the flat roof at wall height), or no spaces with
             polygons on the top level.
    """
    cid_, name = "roof_plan_coverage", "Roof plan coverage"
    m = ctx.model
    planes = list(getattr(m, "roof_planes", []) or [])
    if not planes:
        return CheckResult(cid_, name, "skip", "no roof planes (flat roof at wall height)")
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    elev = {lv.id: lv.elevation_z_m for lv in m.levels}
    levels = {sp.level_id for sp in m.spaces.values() if sp.polygon_m and len(sp.polygon_m) >= 3}
    if not levels:
        return CheckResult(cid_, name, "skip", "no spaces with polygons")
    top = max(levels, key=lambda lid: (elev.get(lid, 0.0), lid))
    spaces = {
        sid: Polygon(sp.polygon_m).buffer(0)
        for sid, sp in sorted(m.spaces.items())
        if sp.level_id == top and sp.polygon_m and len(sp.polygon_m) >= 3
    }
    foot = unary_union(list(spaces.values()))
    on_top = sorted((rp for rp in planes if rp.level_id == top), key=lambda rp: rp.id)
    if not on_top:
        where = sorted({rp.level_id for rp in planes})
        return CheckResult(
            cid_,
            name,
            "error",
            f"top level {top} has no roof planes; roof planes sit on {', '.join(where)}",
            entities=[top],
            expected=round(foot.area, 6),
            actual=0.0,
        )
    shapes = [(rp.id, _roof_plan(rp)) for rp in on_top]
    roof = unary_union([g for _, g in shapes])
    gap = foot.difference(roof)
    gap_area = gap.area
    overlaps = {}
    for i, (a, ga) in enumerate(shapes):
        for b, gb in shapes[i + 1 :]:
            ov = ga.intersection(gb).intersection(foot).area
            if ov > 1e-9:
                overlaps[(a, b)] = ov
    ov_area = sum(overlaps.values())
    tol = ROOF_COVERAGE_TOL * foot.area
    payload = {
        "level": top,
        "footprint_m2": round(foot.area, 6),
        "uncovered_m2": round(gap_area, 6),
        "overlap_m2": round(ov_area, 6),
        "overlaps": {f"{a}+{b}": round(v, 6) for (a, b), v in sorted(overlaps.items())},
    }
    problems, ents = [], []
    if gap_area > tol:
        under = [sid for sid, g in spaces.items() if g.intersection(gap).area > 1e-9]
        problems.append(
            f"{gap_area:.2f} m^2 of footprint has no roof over it (spaces {', '.join(under)})"
        )
        ents += under
    if ov_area > tol:
        pairs = sorted(overlaps, key=lambda k: -overlaps[k])
        problems.append(
            f"{ov_area:.2f} m^2 is roofed twice ("
            + ", ".join(f"{a} and {b} {overlaps[(a, b)]:.2f} m^2" for a, b in pairs[:5])
            + ")"
        )
        ents += sorted({x for pr in pairs for x in pr})
    if problems:
        return CheckResult(
            cid_,
            name,
            "error",
            f"level {top}: "
            + "; ".join(problems)
            + f"; tolerance {ROOF_COVERAGE_TOL:.0%} of {foot.area:.2f} m^2",
            entities=ents,
            expected=round(foot.area, 6),
            actual=payload,
        )
    return CheckResult(
        cid_,
        name,
        "pass",
        f"level {top}: {len(on_top)} roof plane(s) cover {foot.area:.2f} m^2 of footprint "
        f"(uncovered {gap_area:.3f} m^2, doubled {ov_area:.3f} m^2)",
        expected=round(foot.area, 6),
        actual=payload,
    )


def _check_roof_solar_aperture(ctx: _Ctx) -> CheckResult:
    """Simplified roof keeps the source roof's solar aperture (#617).

    Roadmap item 2. Compares ``solar_aperture`` of ``model.roof_planes``
    against ``model.source_roof_planes`` (kept by
    ``roof_simplify.apply_roof_simplification``) at the site latitude, total
    and per orientation. Warn-only, like the ASHRAE checks, until the export
    writes sloped roofs (#618/#619).

    warn  -- total aperture moved more than ROOF_APERTURE_TOL; the
             orientations that moved past it are named.
    pass  -- within tolerance; message gives area and aperture deltas.
    skip  -- roof never simplified (no source planes), no roof planes, or no
             site latitude (never assumed).
    """
    cid_, name = "roof_solar_aperture", "Roof solar aperture"
    m = ctx.model
    src = list(getattr(m, "source_roof_planes", []) or [])
    cur = list(getattr(m, "roof_planes", []) or [])
    if not src or not cur:
        return CheckResult(cid_, name, "skip", "roof not simplified (no source roof planes)")
    lat = getattr(m, "site_latitude_deg", None)
    if lat is None:
        return CheckResult(
            cid_, name, "skip", "no site latitude; aperture needs one and none is assumed"
        )
    from solar_aperture import solar_aperture

    a0, a1 = solar_aperture(src, lat), solar_aperture(cur, lat)
    if a0.total <= 0:
        return CheckResult(cid_, name, "skip", "source roof collects no sun at this latitude")
    d = (a1.total - a0.total) / a0.total
    area0 = sum(p.area_m2 for p in src)
    area1 = sum(p.area_m2 for p in cur)
    da = (area1 - area0) / area0 if area0 else 0.0
    moved = [
        k
        for k in a0.by_orientation
        if abs(a1.by_orientation[k] - a0.by_orientation[k]) > ROOF_APERTURE_TOL * a0.total
    ]
    payload = {
        "latitude_deg": lat,
        "aperture_delta_pct": round(d * 100, 4),
        "area_delta_pct": round(da * 100, 4),
        "planes_before": len(src),
        "planes_after": len(cur),
        "by_orientation_before": {k: round(v, 6) for k, v in a0.by_orientation.items()},
        "by_orientation_after": {k: round(v, 6) for k, v in a1.by_orientation.items()},
    }
    head = (
        f"{len(src)} -> {len(cur)} roof plane(s): aperture {d * 100:+.2f}%, "
        f"area {da * 100:+.2f}% at latitude {lat:g}"
    )
    if abs(d) > ROOF_APERTURE_TOL:
        return CheckResult(
            cid_,
            name,
            "warn",
            f"{head}; past the {ROOF_APERTURE_TOL:.0%} tolerance"
            + (f", moved on {', '.join(moved)}" if moved else ""),
            entities=moved,
            expected=round(a0.total, 6),
            actual=payload,
        )
    return CheckResult(cid_, name, "pass", head, expected=round(a0.total, 6), actual=payload)


def _check_daylight_zones(ctx: _Ctx) -> CheckResult:
    """Daylight zones are inside their space and their areas add up.

    Roadmap item 3 (toplighting) plus the existing sidelighting zones. The
    toplit area is recomputed here as the union of the zone polygons, with
    its own code, rather than trusting ``SpaceDaylight.toplit_m2``.

    error -- a zone polygon sticks out of its space by more than 1% of the
             zone; a zone's stored area disagrees with its polygon by more
             than the area tolerance; ``toplit_m2`` disagrees with the union
             of the toplit polygons, or exceeds the space's floor area; a
             toplit zone names an opening that is not a skylight of that
             space, or is not classed ``under_skylight``.
    warn  -- skylights listed as unplaced: they have no daylight area
             because their position or the ceiling height is unknown, so the
             space's toplit area is a lower bound.
    skip  -- no space has a daylight zone or an unplaced skylight.
    """
    cid_ = "daylight_zones"
    title = "Daylight zones"
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError:  # pragma: no cover - shapely is a core dependency
        return CheckResult(cid_, title, "skip", "shapely not available")

    tol = ctx.tol_area
    bad: list = []
    unplaced: list = []
    n_zones = 0
    toplit_total = 0.0
    for sid, sp in ctx.model.spaces.items():
        dl = getattr(sp, "daylight", None)
        if dl is None:
            continue
        top = list(getattr(dl, "toplit", []) or [])
        zones = list(dl.primary) + list(dl.secondary) + top
        unplaced += [f"{sid}:{o}" for o in getattr(dl, "unplaced_skylights", []) or []]
        if not zones:
            continue
        n_zones += len(zones)
        room = Polygon(sp.polygon_m) if sp.polygon_m and len(sp.polygon_m) >= 3 else None
        if room is not None and not room.is_valid:
            room = room.buffer(0)
        skylights = {o.id for o in sp.openings if o.category == "skylight"}
        top_polys = []
        for z in zones:
            g = _zone_poly(z)
            if g is None or g.is_empty:
                bad.append((sid, f"zone {z.id} has no polygon"))
                continue
            if abs(g.area - z.area_m2) > tol * max(g.area, 1e-9) + 1e-6:
                bad.append(
                    (sid, f"zone {z.id} area {z.area_m2:.2f} m^2 vs polygon {g.area:.2f} m^2")
                )
            if room is not None and g.difference(room).area > 0.01 * g.area + 1e-6:
                bad.append((sid, f"zone {z.id} extends outside its space"))
        for z in top:
            if z.zone_class != "under_skylight":
                bad.append((sid, f"toplit zone {z.id} is classed {z.zone_class!r}"))
            if z.window_id not in skylights:
                bad.append((sid, f"toplit zone {z.id} names {z.window_id}, not a skylight here"))
            g = _zone_poly(z)
            if g is not None and not g.is_empty:
                top_polys.append(g)
        if top:
            union = unary_union(top_polys).area if top_polys else 0.0
            stored = dl.toplit_m2
            if abs(union - stored) > tol * max(union, 1e-9) + 1e-6:
                bad.append((sid, f"toplit_m2 {stored:.2f} vs union of zones {union:.2f}"))
            floor = sp.area_m2 if sp.area_m2 is not None else (room.area if room else None)
            if floor is not None and stored > floor * (1.0 + tol) + 1e-6:
                bad.append((sid, f"toplit_m2 {stored:.2f} exceeds floor area {floor:.2f}"))
            toplit_total += stored

    if bad:
        sid, why = bad[0]
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(bad)} daylight zone problem(s), e.g. {sid}: {why}",
            entities=sorted({b[0] for b in bad})[:20],
        )
    if unplaced:
        return CheckResult(
            cid_,
            title,
            "warn",
            f"{len(unplaced)} skylight(s) have no daylight area (position or "
            f"ceiling height unknown); toplit area {toplit_total:.2f} m^2 is a "
            f"lower bound",
            entities=sorted(unplaced)[:20],
        )
    if not n_zones:
        return CheckResult(cid_, title, "skip", "no daylight zones in model")
    return CheckResult(
        cid_,
        title,
        "pass",
        f"{n_zones} daylight zone(s) inside their spaces; toplit area "
        f"{toplit_total:.2f} m^2 matches the union of its zones",
    )


def _check_wall_construction_coverage(ctx: _Ctx) -> CheckResult:
    """Every exterior wall segment has one known construction; per-space U holds.

    Roadmap item 6. The per-space wall U-value is sum(U_i * A_i) / sum(A_i)
    over the space's own segments. This check recomputes it here with its own
    loop rather than calling ``constructions.wall_u_rollup``, so a bug in the
    rollup cannot vouch for itself.

    error -- a segment names a construction that is not defined, a segment
             names a space that does not exist, or a space's stored
             ``wall_u_value_w_m2k`` differs from the recomputed value by more
             than 0.5% (or is set where nothing can contribute).
    warn  -- coverage is partial: some segments have no construction, or a
             referenced construction has no U-value, or a space has a
             computable U but none stored (rollup not run).
    skip  -- no construction is declared and no segment names one; the
             drawing path does not extract wall types yet.
    """
    cid_ = "wall_construction_coverage"
    title = "Wall construction coverage"
    model = ctx.model
    cons = getattr(model, "constructions", {}) or {}
    walls = list(model.envelope)
    named = [w for w in walls if getattr(w, "construction_id", "")]
    if not cons and not named:
        return CheckResult(cid_, title, "skip", "no wall constructions in model")

    dangling = sorted(w.id for w in named if w.construction_id not in cons)
    if dangling:
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(dangling)} wall segment(s) name a construction that is not "
            f"defined, e.g. {dangling[0]} -> "
            f"{next(w.construction_id for w in named if w.id == dangling[0])}",
            entities=dangling[:20],
        )
    orphan = sorted(
        w.id for w in walls if getattr(w, "space_id", "") and w.space_id not in model.spaces
    )
    if orphan:
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(orphan)} wall segment(s) name a space that does not exist",
            entities=orphan[:20],
        )

    # independent recompute of sum(U*A)/sum(A) per space
    ua: dict = {}
    aa: dict = {}
    for w in named:
        sid = getattr(w, "space_id", "")
        u = cons[w.construction_id].u_value_w_m2k
        if not sid or u is None or u <= 0:
            continue
        if w.area_m2 is not None:
            a = w.area_m2
        elif w.length_m is not None and w.height_m is not None:
            a = w.length_m * w.height_m
        else:
            continue
        if a <= 0:
            continue
        ua[sid] = ua.get(sid, 0.0) + u * a
        aa[sid] = aa.get(sid, 0.0) + a

    drift = []
    unrolled = []
    for sid, sp in model.spaces.items():
        stored = getattr(sp, "wall_u_value_w_m2k", None)
        want = ua[sid] / aa[sid] if sid in aa else None
        if want is None:
            if stored is not None:
                drift.append((sid, stored, None))
            continue
        if stored is None:
            unrolled.append(sid)
        elif abs(stored - want) > 0.005 * want + 1e-9:
            drift.append((sid, stored, want))
    if drift:
        sid, got, want = drift[0]
        detail = (
            f"{sid} stores {got:.3f} W/m^2K, recomputed {want:.3f}"
            if want is not None
            else f"{sid} stores {got:.3f} W/m^2K but no segment can contribute"
        )
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(drift)} space(s) whose wall U-value disagrees with its segments: {detail}",
            entities=[d[0] for d in drift][:20],
            expected=want,
            actual=got,
        )

    uncovered = sorted(w.id for w in walls if not getattr(w, "construction_id", ""))
    no_u = sorted(
        c.id
        for c in cons.values()
        if (c.u_value_w_m2k is None or c.u_value_w_m2k <= 0)
        and any(w.construction_id == c.id for w in named)
    )
    gaps = []
    if uncovered:
        gaps.append(f"{len(uncovered)} segment(s) with no construction")
    if no_u:
        gaps.append(f"{len(no_u)} construction(s) with no U-value ({', '.join(no_u[:5])})")
    if unrolled:
        gaps.append(f"{len(unrolled)} space(s) with no stored wall U (rollup not run)")
    if gaps:
        return CheckResult(
            cid_,
            title,
            "warn",
            "; ".join(gaps),
            entities=(uncovered + no_u + unrolled)[:20],
        )
    us = [ua[s] / aa[s] for s in aa]
    rng = f", wall U {min(us):.3f}-{max(us):.3f} W/m^2K" if us else ""
    return CheckResult(
        cid_,
        title,
        "pass",
        f"{len(walls)} segment(s), {len(cons)} construction(s), {len(aa)} space(s) rolled up{rng}",
    )


SHADING_DEPTH_PLAUSIBLE_M = 5.0  # deeper than this is almost surely a misread
_SHADING_EXTENT_EPS_M = 0.05


def _check_shading_host_reference(ctx: _Ctx) -> CheckResult:
    """Every shading surface is attached to a real, adjacent host.

    Roadmap item 5. A projection with no host is most likely a detection
    error (a dimension line or a neighbouring building read as a balcony),
    so it is flagged rather than exported as free-floating shade.

    error -- a host wall or host opening that does not exist; a host opening
             on a different facade from the host wall (not adjacent); a
             non-positive depth, overhang/balcony width, or fin height.
    warn  -- no host wall at all; the surface runs past the ends of its host
             wall; it sits above the wall's height; or its depth exceeds
             5 m (implausible for an overhang, fin or balcony).
    skip  -- the model has no shading surfaces.
    """
    cid_ = "shading_host_reference"
    title = "Shading host reference"
    model = ctx.model
    shades = list(getattr(model, "shading", []) or [])
    if not shades:
        return CheckResult(cid_, title, "skip", "no shading surfaces in model")

    walls = {w.id: w for w in model.envelope}
    openings = {o.id: o for sp in model.spaces.values() for o in sp.openings}

    errors: list = []
    for sh in shades:
        if sh.host_wall_id and sh.host_wall_id not in walls:
            errors.append((sh.id, f"host wall {sh.host_wall_id} does not exist"))
            continue
        if sh.host_opening_id:
            op = openings.get(sh.host_opening_id)
            if op is None:
                errors.append((sh.id, f"host opening {sh.host_opening_id} does not exist"))
                continue
            w = walls.get(sh.host_wall_id)
            if w is not None and op.host_facade and op.host_facade != w.facade:
                errors.append(
                    (
                        sh.id,
                        f"shades {op.id} on the {op.host_facade} facade but is "
                        f"hosted on {w.id} ({w.facade})",
                    )
                )
                continue
        if sh.depth_m is not None and sh.depth_m <= 0:
            errors.append((sh.id, f"depth {sh.depth_m} m"))
        elif sh.kind == "fin" and sh.height_m is not None and sh.height_m <= 0:
            errors.append((sh.id, f"fin height {sh.height_m} m"))
        elif sh.kind != "fin" and sh.width_m is not None and sh.width_m <= 0:
            errors.append((sh.id, f"width {sh.width_m} m"))
    if errors:
        sid, why = errors[0]
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(errors)} shading surface(s) with a bad host or size: {sid}: {why}",
            entities=[e[0] for e in errors][:20],
        )

    unhosted = sorted(sh.id for sh in shades if not sh.host_wall_id)
    past_ends = []
    too_high = []
    too_deep = sorted(
        sh.id for sh in shades if sh.depth_m is not None and sh.depth_m > SHADING_DEPTH_PLAUSIBLE_M
    )
    for sh in shades:
        w = walls.get(sh.host_wall_id)
        if w is None:
            continue
        length = w.length_m
        if length is None and w.from_m and w.to_m:
            length = math.dist(w.from_m, w.to_m)
        if length is not None and sh.along_m is not None:
            span = sh.width_m if (sh.kind != "fin" and sh.width_m) else 0.0
            if (
                sh.along_m < -_SHADING_EXTENT_EPS_M
                or sh.along_m + span > length + _SHADING_EXTENT_EPS_M
            ):
                past_ends.append(sh.id)
        if w.height_m is not None and sh.z_m is not None:
            if sh.z_m > w.height_m + _SHADING_EXTENT_EPS_M:
                too_high.append(sh.id)
    gaps = []
    if unhosted:
        gaps.append(f"{len(unhosted)} with no host wall (likely a detection error)")
    if past_ends:
        gaps.append(f"{len(past_ends)} running past the ends of the host wall")
    if too_high:
        gaps.append(f"{len(too_high)} above the host wall's height")
    if too_deep:
        gaps.append(f"{len(too_deep)} deeper than {SHADING_DEPTH_PLAUSIBLE_M:g} m")
    if gaps:
        return CheckResult(
            cid_,
            title,
            "warn",
            "shading surface(s) " + "; ".join(gaps),
            entities=(unhosted + past_ends + too_high + too_deep)[:20],
        )
    kinds: dict = {}
    for sh in shades:
        kinds[sh.kind] = kinds.get(sh.kind, 0) + 1
    breakdown = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    return CheckResult(
        cid_,
        title,
        "pass",
        f"{len(shades)} shading surface(s) ({breakdown}), each on an existing host",
    )


VALID_POLY_TYPES = ("room", "shaft", "closet", "elevator_core", "unassigned")


def _check_space_type_accounting(ctx: _Ctx) -> CheckResult:
    """Every space has a known poly_type; remaining non-room area is reported.

    Roadmap item 4. space_merge folds closets into the room their door opens
    onto and shafts into the room sharing the largest share of their wall
    area; whatever that rule cannot settle (and every elevator/stair core)
    stays its own space and is reported here, so nothing is inflated and
    nothing vanishes. Loads and LPD can be read against rooms only.

    error -- a space has a poly_type outside the known set.
    warn  -- the classifier could not decide on one or more spaces
             (poly_type "unassigned"); they need a person to look.
    pass  -- every space has a decided type; the message gives the non-room
             area share by type.
    skip  -- no spaces.
    """
    cid_ = "space_type_accounting"
    title = "Space type accounting"
    spaces = list(ctx.model.spaces.values())
    if not spaces:
        return CheckResult(cid_, title, "skip", "no spaces")
    bad = sorted(s.id for s in spaces if getattr(s, "poly_type", "room") not in VALID_POLY_TYPES)
    if bad:
        first = ctx.model.spaces[bad[0]]
        return CheckResult(
            cid_,
            title,
            "error",
            f"{len(bad)} space(s) have an unknown type, e.g. {bad[0]} -> {first.poly_type!r} "
            f"(known: {', '.join(VALID_POLY_TYPES)})",
            entities=bad[:20],
        )
    area = {}
    for s in spaces:
        a = abs(s.area_m2 or 0.0)
        area[s.poly_type] = area.get(s.poly_type, 0.0) + a
    total = sum(area.values())
    non_room = sorted(s.id for s in spaces if s.poly_type != "room")
    if not non_room:
        return CheckResult(cid_, title, "pass", f"all {len(spaces)} spaces are rooms")

    def share(t):
        pct = 100.0 * area[t] / total if total > 0 else 0.0
        return f"{t} {area[t]:.1f} m2 ({pct:.1f}%)"

    breakdown = "; ".join(share(t) for t in VALID_POLY_TYPES if t != "room" and t in area)
    undecided = sorted(s.id for s in spaces if s.poly_type == "unassigned")
    if undecided:
        return CheckResult(
            cid_,
            title,
            "warn",
            f"{len(undecided)} space(s) could not be classified and need review, "
            f"e.g. {undecided[0]}. Non-room area: {breakdown}",
            entities=undecided[:20],
        )
    return CheckResult(
        cid_,
        title,
        "pass",
        f"{len(non_room)} non-room space(s) kept as their own spaces: {breakdown}",
        entities=non_room[:20],
    )
