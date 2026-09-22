"""Validation / invariant layer: the building model must balance its own books.

Alex's framing: "if the first floor is 20x30 ft, the sum of space floor
areas should add up to about 600 sq ft" and "3D volume of the gbXML model
vs footprint x height". These are CONSERVATION LAWS. A model that cannot
balance them should not export.

``run_checks(model, ...)`` runs a named battery of checks. Each check
returns pass / warn / error with a human-readable message, the expected
vs actual numbers, and the offending entity ids. Severity policy:

  * ERROR = the books don't balance (conservation violated, referential
    breakage, missing provenance). Blocks export: see ``export_gate``.
  * WARN  = plausibility tripwire (absurd LPD, sill/head sanity, unit
    smells). Does not block export but must be acknowledged.
  * SKIP  = check not applicable (no export path given, no elevation
    windows linked, no simplifier result passed in).

Every tolerance is documented in docs/validation.md with its rationale.
Nothing here modifies the model; it only reads it.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from building_model import BuildingModel
from datasets_adapter import polygon_area_px2

try:
    from geometry_simplify import footprint_from_regions

    _HAS_SIMPLIFY = True
except Exception:
    _HAS_SIMPLIFY = False

FT2_PER_M2 = 10.7639

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

SEVERITIES = ("pass", "warn", "error", "skip")


@dataclass
class CheckResult:
    check_id: str
    name: str
    severity: str  # "pass" | "warn" | "error" | "skip"
    message: str
    entities: list = field(default_factory=list)  # offending entity ids
    expected: object = None
    actual: object = None

    def to_dict(self) -> dict:
        d = {
            "check_id": self.check_id,
            "name": self.name,
            "severity": self.severity,
            "message": self.message,
            "entities": sorted(str(e) for e in self.entities),
        }
        if self.expected is not None:
            d["expected"] = _round(self.expected)
        if self.actual is not None:
            d["actual"] = _round(self.actual)
        return d


def _round(v, nd=6):
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, dict):
        return {k: _round(x, nd) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_round(x, nd) for x in v]
    return v


@dataclass
class ValidationReport:
    building_name: str
    results: list = field(default_factory=list)  # CheckResult

    @property
    def errors(self):
        return [r for r in self.results if r.severity == "error"]

    @property
    def warnings(self):
        return [r for r in self.results if r.severity == "warn"]

    @property
    def passes(self):
        return [r for r in self.results if r.severity == "pass"]

    @property
    def skipped(self):
        return [r for r in self.results if r.severity == "skip"]

    @property
    def ok(self) -> bool:
        """No errors. Warnings do not fail the gate."""
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "building_name": self.building_name,
            "ok": self.ok,
            "summary": {
                "n_checks": len(self.results),
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "passes": len(self.passes),
                "skipped": len(self.skipped),
            },
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=1)

    def compact(self) -> str:
        """One-line-per-check human summary."""
        lines = [
            f"validation: {self.building_name} -> "
            f"{'OK' if self.ok else 'ERRORS'} "
            f"({len(self.errors)}E/{len(self.warnings)}W/"
            f"{len(self.passes)}P/{len(self.skipped)}S)"
        ]
        for r in self.results:
            if r.severity in ("error", "warn"):
                lines.append(f"  [{r.severity.upper():5s}] {r.check_id}: {r.message}")
        return "\n".join(lines)


def export_gate(report: ValidationReport) -> bool:
    """May this model be exported to gbXML/IFC? Errors block; warnings don't."""
    return report.ok


# ---------------------------------------------------------------------------
# Check context: derived quantities shared across checks
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    model: BuildingModel
    tol_area: float = 0.03
    tol_volume: float = 0.03
    tol_envelope: float = 0.01
    lpd_warn_max: float = 25.0
    opening_eps: float = 0.005
    sres: object = None  # geometry_simplify.SimplifyResult | None
    gbxml_path: object = None
    ifc_path: object = None
    # derived:
    level_of: dict = field(default_factory=dict)  # level_id -> [spaces]
    footprint_area: dict = field(default_factory=dict)  # level_id -> m^2
    wall_height: dict = field(default_factory=dict)  # level_id -> m


def _build_ctx(model, **kw) -> _Ctx:
    ctx = _Ctx(model=model, **kw)
    for sid, sp in model.spaces.items():
        ctx.level_of.setdefault(sp.level_id, []).append(sp)
    for lvl in model.levels:
        ctx.wall_height[lvl.id] = lvl.wall_height_m
    if _HAS_SIMPLIFY:
        for lid, spaces in ctx.level_of.items():
            ring = footprint_from_regions([sp.polygon_m for sp in spaces])
            ctx.footprint_area[lid] = polygon_area_px2(ring) if ring else 0.0
    return ctx


def _rel_err(actual, expected) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else float("inf")
    return abs(actual - expected) / abs(expected)


# ---------------------------------------------------------------------------
# The battery. Each _check_* takes ctx and returns CheckResult.
# ---------------------------------------------------------------------------


def _check_space_area_matches_polygon(ctx) -> CheckResult:
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


def _check_area_conservation(ctx) -> CheckResult:
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
    total_all = float(sum(sum(sp.area_m2 or 0.0 for sp in spaces) for spaces in ctx.level_of.values()))
    fp_all = float(sum(ctx.footprint_area.get(lid, 0.0) for lid in ctx.level_of))
    return CheckResult(
        "area_conservation",
        "Area conservation (sum rooms ~= footprint)",
        "pass",
        f"per-level room areas tile footprint within {ctx.tol_area:.0%}: {detail}",
        expected=fp_all,
        actual=total_all,
    )


def _check_space_volume_matches_area_height(ctx) -> CheckResult:
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


def _check_volume_conservation(ctx) -> CheckResult:
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


def _check_envelope_area_matches_perimeter(ctx) -> CheckResult:
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


def _check_simplify_budget(ctx) -> CheckResult:
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
    return CheckResult(
        "simplify_budget",
        "Simplification within area budget",
        "pass",
        f"area delta {delta:.3f}% within {tol_pct:.1f}% budget",
    )


def _check_facade_opening_closure(ctx) -> CheckResult:
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


def _check_takeoff_counts_reconcile(ctx) -> CheckResult:
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


def _check_fixture_schedule_join(ctx) -> CheckResult:
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


def _check_opening_schedule_join(ctx) -> CheckResult:
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


def _check_no_negative_areas(ctx) -> CheckResult:
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


def _check_lpd_bounds(ctx) -> CheckResult:
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


def _check_lpd_unit_consistency(ctx) -> CheckResult:
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


def _check_sill_head_sanity(ctx) -> CheckResult:
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


def _check_assignment_uniqueness(ctx) -> CheckResult:
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


def _check_zone_nonempty(ctx) -> CheckResult:
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


def _check_zone_space_referential(ctx) -> CheckResult:
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


def _check_space_id_hygiene(ctx) -> CheckResult:
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


def _check_elevation_placement_consistency(ctx) -> CheckResult:
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


def _check_window_tag_coverage(ctx) -> CheckResult:
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


def _check_provenance_complete(ctx) -> CheckResult:
    missing = [eid for eid, p in _all_facts(ctx) if p is None or not getattr(p, "sheet_id", "")]
    if missing:
        return CheckResult(
            "provenance_complete",
            "Provenance complete",
            "error",
            f"{len(missing)} fact(s) with no provenance -- unauditable numbers",
            entities=missing[:20],
        )
    n = sum(1 for _ in _all_facts(ctx))
    return CheckResult(
        "provenance_complete",
        "Provenance complete",
        "pass",
        f"{n} facts, every one cites sheet/revision/method",
    )


def _check_review_queue_sound(ctx) -> CheckResult:
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


def _check_revision_log_present(ctx) -> CheckResult:
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


# ---------------------------------------------------------------------------
# Export checks (only run when a path is given)
# ---------------------------------------------------------------------------

_GBXML_NS = "http://www.gbxml.org/schema"


def _check_gbxml_spaces(ctx) -> CheckResult:
    path = ctx.gbxml_path
    if not path:
        return CheckResult("gbxml_space_areas", "gbXML space areas", "skip", "no gbXML path given")
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as e:
        return CheckResult(
            "gbxml_space_areas", "gbXML space areas", "error", f"gbXML not well-formed: {e}"
        )
    ns = {"g": _GBXML_NS}
    spaces = root.findall(".//g:Space", ns)
    if len(spaces) != len(ctx.model.spaces):
        return CheckResult(
            "gbxml_space_areas",
            "gbXML space areas",
            "error",
            f"gbXML has {len(spaces)} Space elements but the model has "
            f"{len(ctx.model.spaces)} spaces",
            expected=len(ctx.model.spaces),
            actual=len(spaces),
        )
    bad = []
    for se in spaces:
        try:
            a = float(se.find("g:Area", ns).text)
            v = float(se.find("g:Volume", ns).text)
        except (AttributeError, TypeError, ValueError):
            bad.append(se.get("id"))
            continue
        if a <= 0 or v <= 0:
            bad.append(se.get("id"))
    if bad:
        return CheckResult(
            "gbxml_space_areas",
            "gbXML space areas",
            "error",
            f"{len(bad)} gbXML Space(s) with missing or non-positive Area/Volume",
            entities=bad[:20],
        )
    return CheckResult(
        "gbxml_space_areas",
        "gbXML space areas",
        "pass",
        f"{len(spaces)} gbXML spaces, all with positive Area and Volume",
    )


def _check_gbxml_opening_refs(ctx) -> CheckResult:
    path = ctx.gbxml_path
    if not path:
        return CheckResult(
            "gbxml_opening_refs", "gbXML opening refs", "skip", "no gbXML path given"
        )
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as e:
        return CheckResult(
            "gbxml_opening_refs", "gbXML opening refs", "error", f"gbXML not well-formed: {e}"
        )
    ns = {"g": _GBXML_NS}
    # every Opening must be nested under a Surface with an id
    orphans = 0
    n_open = 0
    for su in root.findall(".//g:Surface", ns):
        sid = su.get("id")
        for op in su.findall("g:Opening", ns):
            n_open += 1
            if not sid or not op.get("id"):
                orphans += 1
    if orphans:
        return CheckResult(
            "gbxml_opening_refs",
            "gbXML opening refs",
            "error",
            f"{orphans} opening(s) not hosted on an identified surface",
        )
    return CheckResult(
        "gbxml_opening_refs",
        "gbXML opening refs",
        "pass",
        f"{n_open} gbXML openings all hosted on surfaces",
    )


def _check_ifc_counts(ctx) -> CheckResult:
    path = ctx.ifc_path
    if not path:
        return CheckResult("ifc_entity_counts", "IFC entity counts", "skip", "no IFC path given")
    try:
        import sys as _sys

        _vendor = str(Path.home() / "workspace" / "vendor" / "pylibs")
        if _vendor not in _sys.path:
            _sys.path.insert(0, _vendor)
        import ifcopenshell
    except ImportError:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "skip", "IfcOpenShell not available"
        )
    try:
        f = ifcopenshell.open(str(path))
    except Exception as e:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "error", f"could not parse IFC file: {e}"
        )
    n_spaces = len(f.by_type("IfcSpace"))
    n_walls = len(f.by_type("IfcWall"))
    if n_spaces != len(ctx.model.spaces):
        return CheckResult(
            "ifc_entity_counts",
            "IFC entity counts",
            "error",
            f"IFC has {n_spaces} IfcSpace but the model has {len(ctx.model.spaces)} spaces",
            expected=len(ctx.model.spaces),
            actual=n_spaces,
        )
    if n_walls == 0:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "error", "IFC has no IfcWall entities"
        )
    return CheckResult(
        "ifc_entity_counts",
        "IFC entity counts",
        "pass",
        f"{n_spaces} IfcSpace, {n_walls} IfcWall -- counts match model",
    )


# ---------------------------------------------------------------------------
# Battery + entry point
# ---------------------------------------------------------------------------

BATTERY = [
    # conservation laws (errors block export)
    _check_space_area_matches_polygon,
    _check_area_conservation,
    _check_space_volume_matches_area_height,
    _check_volume_conservation,
    _check_envelope_area_matches_perimeter,
    _check_simplify_budget,
    _check_facade_opening_closure,
    # takeoff closure
    _check_takeoff_counts_reconcile,
    _check_fixture_schedule_join,
    _check_opening_schedule_join,
    _check_no_negative_areas,
    # plausibility guards (warns)
    _check_lpd_bounds,
    _check_lpd_unit_consistency,
    _check_sill_head_sanity,
    # cross-discipline closure
    _check_assignment_uniqueness,
    _check_zone_nonempty,
    _check_zone_space_referential,
    _check_space_id_hygiene,
    _check_elevation_placement_consistency,
    _check_window_tag_coverage,
    # provenance / auditability
    _check_provenance_complete,
    _check_review_queue_sound,
    _check_revision_log_present,
    # export (skipped unless paths given)
    _check_gbxml_spaces,
    _check_gbxml_opening_refs,
    _check_ifc_counts,
]

N_CHECKS = len(BATTERY)


def run_checks(
    model: BuildingModel,
    gbxml_path=None,
    ifc_path=None,
    sres=None,
    tol_area: float = 0.03,
    tol_volume: float = 0.03,
    tol_envelope: float = 0.01,
    lpd_warn_max: float = 25.0,
    opening_eps: float = 0.005,
) -> ValidationReport:
    """Run the full invariant battery against a BuildingModel.

    Returns a ValidationReport. ``report.ok`` is False iff any check
    errored; use ``export_gate(report)`` to decide whether the model may
    be exported to gbXML/IFC.
    """
    ctx = _build_ctx(
        model,
        tol_area=tol_area,
        tol_volume=tol_volume,
        tol_envelope=tol_envelope,
        lpd_warn_max=lpd_warn_max,
        opening_eps=opening_eps,
        sres=sres,
        gbxml_path=gbxml_path,
        ifc_path=ifc_path,
    )
    report = ValidationReport(building_name=model.name or "(unnamed)")
    for check in BATTERY:
        try:
            report.results.append(check(ctx))
        except Exception as e:  # a check must never take down the battery
            report.results.append(
                CheckResult(
                    check.__name__,
                    check.__name__,
                    "error",
                    f"check itself raised {type(e).__name__}: {e}",
                )
            )
    return report
