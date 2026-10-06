"""Invariant checks.

Non-conservation checks.
Errors and warnings go to review queue if confidence < threshold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from validate.types import _HAS_SIMPLIFY, CheckResult, _rel_err

if TYPE_CHECKING:
    from validate import _Ctx

try:
    from geometry_simplify import simplify_ring
except Exception:
    simplify_ring = None

try:
    from geometry_simplify import footprint_from_regions
except Exception:
    footprint_from_regions = None

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
            if o.category == "skylight":
                continue  # roof glazing; skylight_within_roof owns it
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


def _check_hvac_zone_coverage(ctx: _Ctx) -> CheckResult:
    """HVAC conservation (#658): spaces with duct terminals are served by a zone.

    Three rules, read only from the canonical model:

    1. A space holding a duct terminal (diffuser or terminal unit) must be
       reachable from >=1 zone: one of its ``hvac.zone_ids`` resolves to a
       zone that lists the space back. Failing that is an **error**: the
       terminal's airflow lands in no zone, so it vanishes from the BEM.
    2. Among spaces carrying any HVAC evidence (diffusers, sensors or
       terminal units), the share with no resolved zone must stay at or
       below ``ctx.hvac_unzoned_warn_frac`` (default 5%); above it is a
       **warn**. Spaces with no HVAC evidence (storage, shafts) are not
       counted: an unzoned closet is normal.
    3. Each zone's diffuser ids must reconcile with the diffusers held by
       the spaces it serves (and vice versa); a mismatch is a **warn**.

    Skipped when the model has no zones (``zone_nonempty`` already warns
    that the mech plan is not linked).
    """
    cid, name = "hvac_zone_coverage", "HVAC zone coverage"
    m = ctx.model
    if not m.zones:
        return CheckResult(cid, name, "skip", "no zones in model (mech plan not linked)")

    def served_by(sid, sp):
        return [zid for zid in sp.hvac.zone_ids if zid in m.zones and sid in m.zones[zid].space_ids]

    hvac_spaces = {
        sid: sp
        for sid, sp in m.spaces.items()
        if sp.hvac.diffusers or sp.hvac.terminal_units or sp.hvac.sensors
    }
    unreachable = [
        sid
        for sid, sp in hvac_spaces.items()
        if (sp.hvac.diffusers or sp.hvac.terminal_units) and not served_by(sid, sp)
    ]
    if unreachable:
        return CheckResult(
            cid,
            name,
            "error",
            f"{len(unreachable)} space(s) hold duct terminals but no zone serves them: "
            f"{unreachable[0]}",
            entities=unreachable[:10],
        )

    unzoned = [sid for sid, sp in hvac_spaces.items() if not served_by(sid, sp)]
    frac = len(unzoned) / len(hvac_spaces) if hvac_spaces else 0.0
    if frac > ctx.hvac_unzoned_warn_frac:
        return CheckResult(
            cid,
            name,
            "warn",
            f"{len(unzoned)}/{len(hvac_spaces)} HVAC space(s) ({frac:.0%}) resolve to no zone "
            f"(threshold {ctx.hvac_unzoned_warn_frac:.0%})",
            entities=unzoned[:10],
        )

    drift = []
    for zid, z in m.zones.items():
        zone_d = {d.id for d in z.diffusers}
        space_d = {
            d.id
            for s in z.space_ids
            if s in m.spaces and zid in m.spaces[s].hvac.zone_ids
            for d in m.spaces[s].hvac.diffusers
        }
        missing_in_spaces = sorted(zone_d - space_d)
        # A space in several zones holds diffusers from all of them, so only
        # a space diffuser that belongs to NONE of its zones is drift.
        orphans = sorted(
            d.id
            for s in z.space_ids
            if s in m.spaces
            for d in m.spaces[s].hvac.diffusers
            if not any(
                d.id in {x.id for x in m.zones[o].diffusers}
                for o in m.spaces[s].hvac.zone_ids
                if o in m.zones
            )
        )
        if missing_in_spaces:
            drift.append(f"zone {zid}: diffuser(s) {missing_in_spaces} not in any served space")
        if orphans:
            drift.append(f"zone {zid}: space diffuser(s) {orphans} belong to none of its zones")
    if drift:
        return CheckResult(
            cid,
            name,
            "warn",
            f"{len(drift)} zone terminal count(s) do not reconcile with the duct trace: {drift[0]}",
            entities=drift[:10],
        )
    return CheckResult(
        cid,
        name,
        "pass",
        f"{len(hvac_spaces)} HVAC space(s) reachable from {len(m.zones)} zone(s); "
        "terminal counts reconcile",
    )


def _check_cross_level_dedup(ctx: _Ctx) -> CheckResult:
    """Opening conservation across levels (#664): one physical opening, one record.

    Read from the canonical model after linking:
    - error: two wall openings on adjacent levels still match as one physical
      opening (same facade/tag, centres within 5 cm, continuous vertical
      extent); dedup missed it and its glazing would be counted twice.
    - warn: on some level, the share of wall openings that are unplaced (no
      along-wall position, so dedup could not confirm them) or duplicated
      across spaces on that level exceeds ``ctx.cross_level_dedup_warn_frac``
      (default 1%).
    - skip: no wall openings.
    """
    from opening_identity import (
        group_key,
        is_wall_opening,
        level_table,
        opening_s,
        same_opening,
    )

    name = "Cross-level opening deduplication"
    levels = level_table(ctx.model)
    entries = [
        (sp.level_id, sp.id, op)
        for sp in ctx.model.spaces.values()
        for op in sp.openings
        if is_wall_opening(op)
    ]
    if not entries:
        return CheckResult("cross_level_dedup", name, "skip", "no wall openings linked")
    groups: dict = {}
    for e in entries:
        groups.setdefault(group_key(e[2]), []).append(e)
    cross, same_level_dup = [], set()
    for grp in groups.values():
        for i in range(len(grp)):
            li, si, oi = grp[i]
            for lj, sj, oj in grp[i + 1 :]:
                if si == sj or not same_opening(oi, li, oj, lj, levels):
                    continue
                if li != lj:
                    cross.append((oi.id, li, oj.id, lj))
                else:
                    same_level_dup.update({id(oi), id(oj)})
    if cross:
        a, la, b, lb = cross[0]
        return CheckResult(
            "cross_level_dedup",
            name,
            "error",
            f"{len(cross)} opening pair(s) on adjacent levels are one physical opening "
            f"but kept twice, e.g. '{a}' ({la}) and '{b}' ({lb}); glazing double-counted",
            entities=[a, b],
        )
    worst = None
    for lid in sorted({e[0] for e in entries}):
        ops = [e[2] for e in entries if e[0] == lid]
        bad = [o for o in ops if opening_s(o) is None or id(o) in same_level_dup]
        frac = len(bad) / len(ops)
        if worst is None or frac > worst[1]:
            worst = (lid, frac, bad, len(ops))
    lid, frac, bad, n = worst
    if frac > ctx.cross_level_dedup_warn_frac:
        return CheckResult(
            "cross_level_dedup",
            name,
            "warn",
            f"level {lid}: {len(bad)}/{n} wall openings ({frac:.0%}) unplaced or duplicated "
            f"across spaces (threshold {ctx.cross_level_dedup_warn_frac:.0%})",
            entities=[o.id for o in bad[:10]],
        )
    return CheckResult(
        "cross_level_dedup",
        name,
        "pass",
        f"{len(entries)} wall opening(s) on {len({e[0] for e in entries})} level(s): "
        "no cross-level duplicates",
    )


_IFC_WALL_CLASSES = ("IfcWall", "IfcWallStandardCase")


def _check_space_opening_attachment(ctx: _Ctx) -> CheckResult:
    """IFC Tier 1 guard (#666): every IFC wall opening reached a space.

    Per level, counts window/door openings hosted by IFC walls that no space
    holds (Tier 1 left them unattached: wall direction unknown, an ambiguous
    envelope tie, or no room on either side). Severity is warn, never error:
    an unattached opening is still in the takeoff, it just has no room yet.
    - warn: on some level, the unattached share exceeds
      ``ctx.opening_attachment_warn_frac`` (default 0: any unattached opening).
    - skip: no IFC wall openings (a drawing-derived model).
    """
    name = "IFC opening space attachment"
    per_level: dict = {}
    for el in ctx.model.bim_elements:
        if el.ifc_class not in _IFC_WALL_CLASSES:
            continue
        for bo in el.openings:
            if bo.category in ("window", "door"):
                per_level.setdefault(el.level_id or "?", []).append(bo.id)
    if not per_level:
        return CheckResult("space_opening_attachment", name, "skip", "no IFC wall openings")
    attached = {op.id for sp in ctx.model.spaces.values() for op in sp.openings}
    bad_levels, missing = [], []
    for lid in sorted(per_level):
        ids = per_level[lid]
        miss = [i for i in ids if i not in attached]
        if miss and len(miss) / len(ids) > ctx.opening_attachment_warn_frac:
            bad_levels.append(f"{lid}: {len(miss)}/{len(ids)} ({len(miss) / len(ids):.0%})")
            missing.extend(miss)
    summary = ctx.model.opening_attachment_summary
    total = sum(len(v) for v in per_level.values())
    if bad_levels:
        return CheckResult(
            "space_opening_attachment",
            name,
            "warn",
            "IFC wall openings attached to no space, "
            + "; ".join(bad_levels)
            + f" ({summary.summary_line()})",
            entities=missing[:20],
        )
    fb = summary.ref_direction_fallback
    return CheckResult(
        "space_opening_attachment",
        name,
        "pass",
        f"{total} IFC wall opening(s) on {len(per_level)} level(s) attached to spaces"
        + (f"; {fb} via wall RefDirection (confidence 0.85)" if fb else ""),
    )
