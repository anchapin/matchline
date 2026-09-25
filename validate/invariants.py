"""Invariant checks.

Non-conservation checks.
Errors and warnings go to review queue if confidence < threshold.
"""

from __future__ import annotations

from validate.types import CheckResult, _rel_err

try:
    from geometry_simplify import footprint_from_regions, simplify_ring
except Exception:
    pass

FT2_PER_M2 = 10.7639


def _check_simplify_budget(ctx: _Ctx) -> CheckResult:
    sres = ctx.sres
    if sres is None:
        return CheckResult(
            "simplify_budget",
            "Simplification within area budget",
            "skip",
            "no SimplifyResult passed; pass sres= to check the 1-5% envelope area budget",
        )
    delta = abs(getattr(sres, "area_delta_pct", float("inf")))
    tol_pct = getattr(sres, "tol", 0.02) * 100.0
    if not getattr(sres, "valid", True):
        return CheckResult(
            "simplify_budget",
            "Simplification within area budget",
            "error",
            "simplifier marked its own result invalid",
        )
    if delta > tol_pct + 1e-9:
        return CheckResult(
            "simplify_budget",
            "Simplification within area budget",
            "error",
            f"envelope area changed {delta:.3f}% vs budget {tol_pct:.1f}%: "
            f"simplification breached its own area budget",
            expected=tol_pct,
            actual=delta,
        )
    if _HAS_SIMPLIFY:
        spaces_iter = (
            ctx.model.spaces.values() if hasattr(ctx.model.spaces, "values") else ctx.model.spaces
        )
        raw_polys = [
            sp.polygon_m for sp in spaces_iter if getattr(sp, "polygon_m", None) is not None
        ]
        if len(raw_polys) >= 1:
            ring = footprint_from_regions(raw_polys)
            if ring is not None:
                wall_height = (
                    getattr(ctx.model.levels[0], "wall_height_m", None)
                    if ctx.model.levels
                    else None
                )
                fresh = simplify_ring(ring, tol=sres.tol if sres else 0.02, wall_height=wall_height)
                sres_area = getattr(sres, "simplified_area", None)
                orig_area = getattr(sres, "original_area", None)
                if fresh.original_area and sres_area and orig_area and orig_area > 0:
                    if abs(fresh.original_area - orig_area) / orig_area * 100.0 > 0.1:
                        return CheckResult(
                            "simplify_budget",
                            "Simplification within area budget",
                            "error",
                            f"original area re-verification failed: "
                            f"fresh {fresh.original_area:.3f} m² vs "
                            f"reported {orig_area:.3f} m²",
                            expected=orig_area,
                            actual=fresh.original_area,
                        )
    return CheckResult(
        "simplify_budget",
        "Simplification within area budget",
        "pass",
        f"area delta {delta:.3f}% within {tol_pct:.1f}% budget",
    )


def _check_facade_opening_closure(ctx: _Ctx) -> CheckResult:
    """Per facade: SUM(opening areas) <= gross wall area; opaque >= 0."""
    gross = {}
    for w in ctx.model.envelope:
        gross[w.facade] = gross.get(w.facade, 0.0) + (w.area_m2 or 0.0)
    openings = {}
    for sid, sp in ctx.model.spaces.items():
        for o in sp.openings:
            if o.area_m2:
                openings[o.host_facade or "?"] = openings.get(o.host_facade or "?", 0.0) + o.area_m2
    bad = []
    for fac, oa in openings.items():
        g = gross.get(fac, 0.0)
        eps = max(ctx.opening_eps * g, 1e-6)
        if oa > g + eps:
            bad.append((fac, oa, g))
    if bad:
        fac, oa, g = bad[0]
        return CheckResult(
            "facade_opening_closure",
            "Facade opening closure",
            "error",
            f"facade '{fac}': openings sum to {oa:.2f} m^2 but gross wall "
            f"area is {g:.2f} m^2 -- opaque wall area would be negative",
            entities=[fac],
            expected=g,
            actual=oa,
        )
    opaque = {f: round(gross.get(f, 0.0) - openings.get(f, 0.0), 2) for f in gross}
    return CheckResult(
        "facade_opening_closure",
        "Facade opening closure",
        "pass",
        f"openings fit within gross wall area per facade; opaque m^2: {opaque}"
        if gross
        else "no envelope walls recorded",
    )


def _check_takeoff_counts_reconcile(ctx: _Ctx) -> CheckResult:
    """Per tag: count x schedule dims == SUM(opening areas).

    The two independent paths to "window area per tag" -- the count x dims
    rollup and the per-opening geometry records -- must agree.
    """
    sched = ctx.model.schedules  # tag -> dict with width_m/height_m
    by_tag = {}
    bad_tags = []
    for sid, sp in ctx.model.spaces.items():
        for o in sp.openings:
            if o.category != "window" or not o.tag:
                continue
            by_tag.setdefault(o.tag, []).append(o.area_m2 or 0.0)
    msgs = []
    for tag, areas in sorted(by_tag.items()):
        e = sched.get(tag, {})
        w, h = e.get("width_m"), e.get("height_m")
        if w is None or h is None:
            continue  # covered by opening_schedule_join
        exp = len(areas) * w * h
        got = sum(areas)
        if _rel_err(got, exp) > 1e-6:
            bad_tags.append(tag)
            msgs.append(f"'{tag}': {len(areas)} x {w}x{h} = {exp:.3f} vs recorded {got:.3f}")
    if bad_tags:
        return CheckResult(
            "takeoff_counts_reconcile",
            "Takeoff counts reconcile",
            "error",
            "count x schedule dims disagrees with recorded opening areas: " + "; ".join(msgs),
            entities=bad_tags,
        )
    return CheckResult(
        "takeoff_counts_reconcile",
        "Takeoff counts reconcile",
        "pass",
        f"{len(by_tag)} window tag(s): count x dims matches recorded areas",
    )


def _review_kinds(ctx, kind: str) -> bool:
    return any(i.kind == kind for i in ctx.model.review_queue)


def _check_fixture_schedule_join(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        for f in sp.lighting.fixtures:
            e = ctx.model.schedules.get(f.tag, {})
            if f.watts is None and e.get("watts") is None:
                bad.append(f.id)
    unflagged = [b for b in bad if not _review_kinds(ctx, "fixture_schedule")]
    if unflagged:
        return CheckResult(
            "fixture_schedule_join",
            "Fixture schedule join",
            "error",
            f"{len(unflagged)} fixture(s) with no schedule watts and no "
            f"review flag: tags would silently contribute 0 W",
            entities=unflagged[:20],
        )
    if bad:
        return CheckResult(
            "fixture_schedule_join",
            "Fixture schedule join",
            "warn",
            f"{len(bad)} fixture(s) missing schedule watts "
            f"but flagged for review (0 W, not silent)",
            entities=bad[:20],
        )
    return CheckResult(
        "fixture_schedule_join",
        "Fixture schedule join",
        "pass",
        "every fixture resolves to schedule watts",
    )


def _check_opening_schedule_join(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        for o in sp.openings:
            e = ctx.model.schedules.get(o.tag, {})
            if (o.width_m is None or o.height_m is None) and (
                e.get("width_m") is None or e.get("height_m") is None
            ):
                bad.append(o.id)
    unflagged = [b for b in bad if not _review_kinds(ctx, "window_room_link")]
    if unflagged:
        return CheckResult(
            "opening_schedule_join",
            "Opening schedule join",
            "error",
            f"{len(unflagged)} opening(s) with no schedule dimensions and no review flag",
            entities=unflagged[:20],
        )
    if bad:
        return CheckResult(
            "opening_schedule_join",
            "Opening schedule join",
            "warn",
            f"{len(bad)} opening(s) missing schedule dims but flagged for review",
            entities=bad[:20],
        )
    return CheckResult(
        "opening_schedule_join",
        "Opening schedule join",
        "pass",
        "every opening resolves to schedule dims",
    )


def _check_no_negative_areas(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        vals = [("area", sp.area_m2), ("volume", sp.volume_m3)]
        if any(v is not None and v < 0 for _, v in vals):
            bad.append(sid)
        for o in sp.openings:
            if (
                (o.area_m2 is not None and o.area_m2 < 0)
                or (o.width_m is not None and o.width_m <= 0)
                or (o.height_m is not None and o.height_m <= 0)
            ):
                bad.append(o.id)
    if bad:
        return CheckResult(
            "no_negative_areas",
            "No negative areas",
            "error",
            f"{len(bad)} entit(ies) with negative/zero area or dimensions",
            entities=bad[:20],
        )
    return CheckResult(
        "no_negative_areas", "No negative areas", "pass", "all areas and dimensions non-negative"
    )


def _check_lpd_bounds(ctx: _Ctx) -> CheckResult:
    """Per-space LPD within sane bounds.

    ASHRAE 90.1 space-type allowances cluster ~5-16 W/m^2; >25 W/m^2 is
    almost always a unit slip (ft^2 read as m^2 inflates LPD 10.76x) or
    double-counted fixtures. Zero with fixtures present is a join bug.
    """
    bad, zero = [], []
    for sid, sp in ctx.model.spaces.items():
        lpd = sp.lighting.lpd_w_m2
        if lpd is None:
            continue
        if lpd > ctx.lpd_warn_max:
            bad.append((sid, lpd))
        elif lpd <= 0 and sp.lighting.fixtures:
            zero.append(sid)
    if bad:
        sid, lpd = bad[0]
        return CheckResult(
            "lpd_bounds",
            "LPD plausibility",
            "warn",
            f"space {sid}: LPD {lpd:.1f} W/m^2 exceeds {ctx.lpd_warn_max} "
            f"W/m^2 -- smells like a ft^2/m^2 unit slip or double-counted "
            f"fixtures ({len(bad)} space(s) affected)",
            entities=[s for s, _ in bad],
            expected=f"<= {ctx.lpd_warn_max} W/m^2",
            actual=lpd,
        )
    if zero:
        return CheckResult(
            "lpd_bounds",
            "LPD plausibility",
            "warn",
            f"{len(zero)} space(s) have fixtures but LPD <= 0 (schedule join produced 0 W)",
            entities=zero,
        )
    return CheckResult(
        "lpd_bounds", "LPD plausibility", "pass", "all space LPDs within sane bounds"
    )


def _check_lpd_unit_consistency(ctx: _Ctx) -> CheckResult:
    """lpd_w_ft2 must equal lpd_w_m2 / 10.7639 -- internal unit hygiene."""
    bad = []
    for sid, sp in ctx.model.spaces.items():
        a, b = sp.lighting.lpd_w_m2, sp.lighting.lpd_w_ft2
        if a is None or b is None:
            continue
        if _rel_err(b, a / FT2_PER_M2) > 1e-6:
            bad.append(sid)
    if bad:
        return CheckResult(
            "lpd_unit_consistency",
            "LPD unit consistency",
            "error",
            f"{len(bad)} space(s): lpd_w_ft2 != "
            f"lpd_w_m2/10.7639 -- metric/imperial mixup in "
            f"the rollup",
            entities=bad,
        )
    return CheckResult(
        "lpd_unit_consistency",
        "LPD unit consistency",
        "pass",
        "W/m^2 <-> W/ft^2 conversions consistent",
    )


def _check_sill_head_sanity(ctx: _Ctx) -> CheckResult:
    bad = []
    for sid, sp in ctx.model.spaces.items():
        h = ctx.wall_height.get(sp.level_id, 0.0)
        for o in sp.openings:
            if o.sill_m is None or o.head_m is None:
                continue
            if o.sill_m < 0 or o.head_m <= o.sill_m or o.head_m > h + 0.01:
                bad.append(o.id)
    if bad:
        return CheckResult(
            "sill_head_sanity",
            "Sill/head sanity",
            "warn",
            f"{len(bad)} opening(s) with impossible "
            f"vertical placement (sill<0, head<=sill, or "
            f"head above wall height)",
            entities=bad[:20],
        )
    n = sum(1 for sp in ctx.model.spaces.values() for o in sp.openings if o.sill_m is not None)
    if n == 0:
        return CheckResult(
            "sill_head_sanity", "Sill/head sanity", "skip", "no openings carry sill/head heights"
        )
    return CheckResult(
        "sill_head_sanity",
        "Sill/head sanity",
        "pass",
        f"{n} opening(s) with sane vertical placement",
    )


