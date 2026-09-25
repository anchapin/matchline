"""Conservation law checks.
import math

Provenance on every extracted fact.
Conservation laws: errors block export.
"""

from __future__ import annotations

from bem_export import _shoelace
from datasets_adapter import polygon_area_px2
from validate import _HAS_SIMPLIFY
from validate.types import CheckResult, _rel_err

try:
    from geometry_simplify import footprint_from_regions, simplify_ring
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
        return CheckResult(
            "bem_area_conservation",
            "BEM area conservation",
            "error",
            "ring area is zero or negative",
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


def _check_bem_volume_conservation(bem: BEMModel, tol_volume: float) -> CheckResult:
    """BEM space volumes consistent with expected volumes?

    For BEMModel, space volumes are stored in BEMSpace.volume_m3 and
    the total is computed from wall_height_m * ring_area.
    """
    mismatches: list[str] = []
    total_space_vol = sum(sp.volume_m3 for sp in bem.spaces)
    # Compute expected volume from ring area and wall height
    ring_area = abs(_shoelace(bem.ring_m)) if bem.ring_m else 0.0
    expected_vol = ring_area * bem.wall_height_m if ring_area > 0 and bem.wall_height_m else 0.0
    if expected_vol > 0:
        vol_delta_pct = abs(total_space_vol - expected_vol) / expected_vol * 100
        if vol_delta_pct > tol_volume * 100:
            mismatches.append(
                f"total vol={total_space_vol:.1f} vs "
                f"ring×height={expected_vol:.1f} ({vol_delta_pct:.2f}% delta)"
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
        "all space volumes consistent",
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


