"""Export validation checks.

Only run when a path is given.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from validate.types import MIN_CONFIDENCE_THRESHOLD, CheckResult, _rel_err

if TYPE_CHECKING:
    from validate import _Ctx


def _check_assignment_uniqueness(ctx: _Ctx) -> CheckResult:
    """Every fixture/diffuser/sensor lives in exactly one space."""
    seen = {}
    dupes = set()
    for sid, sp in ctx.model.spaces.items():
        for f in sp.lighting.fixtures:
            key = ("fixture", f.id)
            if key in seen:
                dupes.add(f.id)
            seen[key] = sid
        for d in sp.hvac.diffusers:
            key = ("diffuser", d.id)
            if key in seen:
                dupes.add(d.id)
            seen[key] = sid
        for s in sp.hvac.sensors:
            key = ("sensor", s.id)
            if key in seen:
                dupes.add(s.id)
            seen[key] = sid
    if dupes:
        return CheckResult(
            "assignment_uniqueness",
            "Component assignment uniqueness",
            "error",
            f"{len(dupes)} component(s) assigned to more than one space (double counting)",
            entities=sorted(dupes)[:20],
        )
    return CheckResult(
        "assignment_uniqueness",
        "Component assignment uniqueness",
        "pass",
        f"{len(seen)} component assignments, all unique",
    )


def _check_zone_nonempty(ctx: _Ctx) -> CheckResult:
    bad = [zid for zid, z in ctx.model.zones.items() if not z.diffusers or not z.space_ids]
    if bad:
        return CheckResult(
            "zone_nonempty",
            "Zones non-empty",
            "error",
            f"{len(bad)} zone(s) with no diffusers or no spaces -- a zone that serves nothing",
            entities=bad,
        )
    if not ctx.model.zones:
        return CheckResult(
            "zone_nonempty", "Zones non-empty", "warn", "model has no zones (mech plan not linked?)"
        )
    return CheckResult(
        "zone_nonempty",
        "Zones non-empty",
        "pass",
        f"{len(ctx.model.zones)} zones each serve >=1 diffuser and >=1 space",
    )


def _check_zone_space_referential(ctx: _Ctx) -> CheckResult:
    """zone.space_ids <-> space.zone_ids: both directions resolve."""
    bad = []
    for zid, z in ctx.model.zones.items():
        for s in z.space_ids:
            if s not in ctx.model.spaces:
                bad.append(f"zone {zid} -> missing space {s}")
    for sid, sp in ctx.model.spaces.items():
        for z in sp.hvac.zone_ids:
            if z not in ctx.model.zones:
                bad.append(f"space {sid} -> missing zone {z}")
    # symmetry: zone lists space but space doesn't list zone, and reverse
    for zid, z in ctx.model.zones.items():
        for s in z.space_ids:
            sp = ctx.model.spaces.get(s)
            if sp is not None and zid not in sp.hvac.zone_ids:
                bad.append(f"zone {zid} lists {s} but not reciprocated")
    for sid, sp in ctx.model.spaces.items():
        for z in sp.hvac.zone_ids:
            zn = ctx.model.zones.get(z)
            if zn is not None and sid not in zn.space_ids:
                bad.append(f"space {sid} lists {z} but not reciprocated")
    if bad:
        return CheckResult(
            "zone_space_referential",
            "Zone<->space referential integrity",
            "error",
            f"{len(bad)} dangling/asymmetric zone-space link(s): {bad[0]}",
            entities=bad[:10],
        )
    return CheckResult(
        "zone_space_referential",
        "Zone<->space referential integrity",
        "pass",
        "all zone<->space links resolve both ways",
    )


def _check_space_id_hygiene(ctx: _Ctx) -> CheckResult:
    """Space ids unique (dict) and shaped like '{level}-{number}'."""
    bad = [sid for sid in ctx.model.spaces if "-" not in sid or not sid.split("-", 1)[0]]
    if not ctx.model.spaces:
        return CheckResult(
            "space_id_hygiene", "Space id hygiene", "error", "model contains no spaces"
        )
    if bad:
        return CheckResult(
            "space_id_hygiene",
            "Space id hygiene",
            "error",
            f"{len(bad)} space(s) with malformed ids",
            entities=bad[:20],
        )
    nums = [sp.number for sp in ctx.model.spaces.values() if sp.number]
    dupes = sorted({n for n in nums if nums.count(n) > 1})
    if dupes:
        return CheckResult(
            "space_id_hygiene",
            "Space id hygiene",
            "warn",
            f"duplicate room numbers on one level: {dupes} "
            f"(ids still unique, but numbers are the "
            f"human key -- flag for review)",
            entities=dupes,
        )
    return CheckResult(
        "space_id_hygiene",
        "Space id hygiene",
        "pass",
        f"{len(ctx.model.spaces)} spaces, ids well-formed",
    )


def _check_elevation_placement_consistency(ctx: _Ctx) -> CheckResult:
    """Openings with exact placement: head == sill+height, s_center in
    interval, area == w x h. Duck-typed: works whether or not the
    elevation_windows stretch-goal module attached exact positions."""
    with_pos = [
        o
        for sp in ctx.model.spaces.values()
        for o in sp.openings
        if getattr(o, "s_center_m", None) is not None
    ]
    if not with_pos:
        return CheckResult(
            "elevation_placement_consistency",
            "Elevation placement consistency",
            "skip",
            "no openings carry exact along-wall positions (elevation instance detection not run)",
        )
    bad = []
    for o in with_pos:
        if o.sill_m is not None and o.head_m is not None and o.height_m is not None:
            if abs(o.head_m - (o.sill_m + o.height_m)) > 1e-3:
                bad.append((o.id, "head != sill+height"))
        iv = o.host_interval_m
        if iv and not (min(iv) - 1e-6 <= o.s_center_m <= max(iv) + 1e-6):
            bad.append((o.id, "s_center outside host interval"))
        if o.width_m and o.height_m and o.area_m2 is not None:
            if _rel_err(o.area_m2, o.width_m * o.height_m) > 1e-6:
                bad.append((o.id, "area != w x h"))
    if bad:
        return CheckResult(
            "elevation_placement_consistency",
            "Elevation placement consistency",
            "warn",
            f"{len(bad)} opening(s) with inconsistent exact placement: {bad[0]}",
            entities=[b[0] for b in bad[:20]],
        )
    return CheckResult(
        "elevation_placement_consistency",
        "Elevation placement consistency",
        "pass",
        f"{len(with_pos)} exactly-placed openings self-consistent",
    )


def _check_window_double_link(ctx: _Ctx) -> CheckResult:
    """Detect windows in the same space that share a tag and overlapping center.

    Two elevation runs of the same facade can produce duplicate SpaceOpening
    entries before `_dedupe_space_openings()` runs. This check catches them
    by looking for same-tag windows on the same facade whose s_center_m
    positions are within OPENING_DEDUP_TOL_M (0.15 m) of each other.
    """
    OPENING_DEDUP_TOL_M = 0.15
    bad = []
    for sid, sp in ctx.model.spaces.items():
        by_tag: dict = {}
        for o in sp.openings:
            if o.category != "window" or not o.tag:
                continue
            by_tag.setdefault((o.tag, o.host_facade), []).append(o)
        for (tag, facade), ops in by_tag.items():
            if len(ops) < 2:
                continue
            ops_sorted = sorted(ops, key=lambda x: x.s_center_m or 0.0)
            for i in range(len(ops_sorted) - 1):
                c1 = ops_sorted[i].s_center_m or 0.0
                c2 = ops_sorted[i + 1].s_center_m or 0.0
                if abs(c2 - c1) < OPENING_DEDUP_TOL_M:
                    bad.append((sid, tag, ops_sorted[i].id, ops_sorted[i + 1].id))
    if bad:
        sid, tag, id1, id2 = bad[0]
        return CheckResult(
            "window_double_link",
            "Window double-link detection",
            "error",
            f"space '{sid}': windows '{id1}' and '{id2}' share tag '{tag}' "
            f"and are within {OPENING_DEDUP_TOL_M} m center-distance — "
            f"possible double-link before dedupe",
            entities=[id1, id2],
        )
    n = sum(
        1
        for sp in ctx.model.spaces.values()
        for o in sp.openings
        if o.category == "window" and o.tag
    )
    if n == 0:
        return CheckResult(
            "window_double_link",
            "Window double-link detection",
            "skip",
            "no tagged windows linked",
        )
    return CheckResult(
        "window_double_link",
        "Window double-link detection",
        "pass",
        f"{n} tagged window(s): no double-links detected",
    )


def _check_window_tag_coverage(ctx: _Ctx) -> CheckResult:
    bad = [o.id for sp in ctx.model.spaces.values() for o in sp.openings if not o.tag]
    if bad:
        return CheckResult(
            "window_tag_coverage",
            "Window tag coverage",
            "error",
            f"{len(bad)} opening(s) with empty tag -- cannot join to the schedule",
            entities=bad[:20],
        )
    n = sum(1 for sp in ctx.model.spaces.values() for o in sp.openings)
    if n == 0:
        return CheckResult(
            "window_tag_coverage", "Window tag coverage", "skip", "no openings linked"
        )
    return CheckResult(
        "window_tag_coverage",
        "Window tag coverage",
        "pass",
        f"{n} openings all carry schedule tags",
    )


def _all_facts(ctx):
    """Yield (entity_id, provenance) for every fact that must carry one."""
    m = ctx.model
    for sid, sp in m.spaces.items():
        yield sid, sp.core_provenance
        for o in sp.openings:
            yield o.id, o.provenance
        for f in sp.lighting.fixtures:
            yield f.id, f.provenance
        for d in sp.hvac.diffusers:
            yield f"{sid}:{d.id}", d.provenance
        for s in sp.hvac.sensors:
            yield f"{sid}:{s.id}", s.provenance
        for t in sp.hvac.terminal_units:
            yield f"{sid}:{t.id}", t.provenance
    for zid, z in m.zones.items():
        yield zid, z.provenance
    for w in m.envelope:
        yield w.id, w.provenance


def _check_provenance_complete(ctx: _Ctx) -> CheckResult:
    all_facts = list(_all_facts(ctx))
    missing = [eid for eid, p in all_facts if p is None or not getattr(p, "sheet_id", "")]

    needs_review: dict = {}
    for eid, p in all_facts:
        if p is None:
            continue
        has_sufficient_provenance = all(
            getattr(p, attr, None) for attr in ("method", "revision", "sheet_id")
        )
        confidence = getattr(p, "confidence", 1.0)
        # A fact needs review if it has insufficient provenance AND low confidence
        needs_review[eid] = not has_sufficient_provenance and confidence < MIN_CONFIDENCE_THRESHOLD

    if missing:
        return CheckResult(
            "provenance_complete",
            "Provenance complete",
            "error",
            f"{len(missing)} fact(s) with no provenance -- unauditable numbers",
            entities=missing[:20],
            needs_review=needs_review,
        )
    n = sum(1 for _ in all_facts)
    return CheckResult(
        "provenance_complete",
        "Provenance complete",
        "pass",
        f"{n} facts, every one cites sheet/revision/method",
        needs_review=needs_review,
    )


def _check_review_queue_sound(ctx: _Ctx) -> CheckResult:
    bad = [
        i.id
        for i in ctx.model.review_queue
        if not i.kind
        or not i.description
        or i.provenance is None
        or i.status not in ("open", "confirmed", "rejected")
    ]
    n_open = sum(1 for i in ctx.model.review_queue if i.status == "open")
    if bad:
        return CheckResult(
            "review_queue_sound",
            "Review queue sound",
            "error",
            f"{len(bad)} malformed review item(s) -- the safety net has holes",
            entities=bad[:20],
        )
    return CheckResult(
        "review_queue_sound",
        "Review queue sound",
        "pass",
        f"{len(ctx.model.review_queue)} review item(s), all well-formed; "
        f"{n_open} still open (queued for humans, nothing dropped)",
    )


def _check_review_queue_acknowledged(ctx: _Ctx) -> CheckResult:
    unacknowledged = [
        i.id
        for i in ctx.model.review_queue
        if i.needs_review and not i.acknowledged and i.status not in ("confirmed", "rejected")
    ]
    if unacknowledged:
        return CheckResult(
            "review_queue_acknowledged",
            "Review queue acknowledged",
            "error",
            f"{len(unacknowledged)} unacknowledged review item(s) with "
            f"needs_review=True: must acknowledge before export",
            entities=unacknowledged[:20],
        )
    return CheckResult(
        "review_queue_acknowledged",
        "Review queue acknowledged",
        "pass",
        f"{len(ctx.model.review_queue)} review item(s): all needs_review "
        f"items have been acknowledged",
    )


def _check_revision_log_present(ctx: _Ctx) -> CheckResult:
    n = len(ctx.model.revision_log)
    if n == 0:
        return CheckResult(
            "revision_log_present",
            "Revision log present",
            "warn",
            "empty revision log -- model was not built by the ingest pipeline (hand-built?)",
        )
    kinds = {}
    for e in ctx.model.revision_log:
        kinds[e.action] = kinds.get(e.action, 0) + 1
    return CheckResult(
        "revision_log_present", "Revision log present", "pass", f"{n} revision events: {kinds}"
    )
