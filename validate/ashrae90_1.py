"""ASHRAE 90.1-2019 compliance checking.

Checks building design against ASHRAE 90.1-2019 requirements:
- Table 5.5.4: Building envelope thermal properties (max U-factors, SHGC)
- Table 9.5.1: Lighting power density (LPD) limits by space type
- HVAC efficiency minimums

Every extracted fact carries sheet, revision, method, confidence so that
low-confidence results go to the review queue rather than failing silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from building_model import BuildingModel, Space, Zone
from construction_library import CATEGORIES, classify, climate_zone_number
from construction_library_data import CONSTRUCTION_LIBRARY

from .types import CheckResult

if TYPE_CHECKING:
    from validate import _Ctx

# ---------------------------------------------------------------------------
# ASHRAE 90.1-2019 Tables 5.5-0 to 5.5-8: envelope maximums by climate zone.
# The limits come from construction_library_data.py (#747), generated from
# openstudio-standards v0.8.6; there is no second hand-typed table here (#763).
# A climate zone is never assumed: with none on the model the checks skip.
# ---------------------------------------------------------------------------

# Table 9.5.1 — Lighting Power Density (W/ft²) maximums
_LPD_MAX_WFT2: dict[str, float] = {
    "office": 0.98,
    "classroom": 1.24,
    "lecture hall": 1.05,
    "laboratory": 1.43,
    "library reading room": 1.42,
    "library stack": 2.41,
    "restaurant": 0.95,
    "fast food": 1.48,
    "hotel lobby": 1.48,
    "hotel guest room": 0.95,
    "hotel corridor": 0.86,
    "hospital emergency": 1.85,
    "hospital patient room": 1.10,
    "retail": 1.63,
    "gymnasium": 1.05,
    "gymnasium exercise area": 0.86,
    "auditorium": 1.05,
    "conference": 1.43,
    "data center": 1.05,
    "server room": 1.05,
    "warehouse": 0.37,
    "parking garage": 0.24,
    "electrical room": 0.95,
    "mechanical room": 0.95,
    "corridor": 0.86,
    "toilet": 0.98,
    "stairwell": 0.86,
    "storage": 0.48,
    "other": 1.00,
}

# ASHRAE 90.1-2019 Tables 6.8.1-6.8.3 — HVAC efficiency minimums
# (Cooling EER / Heating COP by equipment type and size)
_HVAC_COOLING_EER_MIN: dict[tuple[str, str], float] = {
    ("residential_split", "<65kBtuh"): 12.0,
    ("residential_split", ">=65kBtuh"): 11.5,
    ("commercial_split", "<65kBtuh"): 11.5,
    ("commercial_split", ">=65kBtuh"): 11.0,
    ("vav", "<65kBtuh"): 11.5,
    ("vav", ">=65kBtuh"): 11.2,
    ("*", "*"): 11.0,
}

_HVAC_HEATING_COP_MIN: dict[tuple[str, str], float] = {
    ("heat_pump", "<65kBtuh"): 3.8,
    ("heat_pump", ">=65kBtuh"): 3.5,
    ("furnace", "*"): 0.80,
    ("boiler", "*"): 0.80,
    ("*", "*"): 3.2,
}

U_SI_PER_IP = 5.678263  # 1 Btu/h-ft2-F = 5.678263 W/m2-K


def _zone(model) -> tuple[str, str, str]:
    """(zone number, category, skip reason); the reason is "" when usable."""
    cz = (getattr(model, "climate_zone", "") or "").strip()
    if not cz:
        return "", "", "no climate zone on the model (--climate-zone); none is assumed"
    try:
        n = climate_zone_number(cz)
    except ValueError as e:
        return "", "", str(e)
    cat = getattr(model, "building_category", "") or "Nonresidential"
    if cat not in CATEGORIES:
        return "", "", f"unknown building category {cat!r}"
    return n, cat, ""


def _limit(cat: str, n: str, surface: str, ctype: str, key: str = "u_ip"):
    row = CONSTRUCTION_LIBRARY.get((cat, n, surface, ctype))
    return None if row is None else row[key]


def _loosest(cat: str, n: str, surface: str, types=None, key: str = "u_ip"):
    """Largest limit over the surface's classes: a value above it fails any class."""
    vals = [
        (r[key], k[3])
        for k, r in CONSTRUCTION_LIBRARY.items()
        if k[0] == cat
        and k[1] == n
        and k[2] == surface
        and r[key] is not None
        and (types is None or k[3] in types)
    ]
    return max(vals) if vals else (None, None)


def _construction_class(model, cid: str, surface: str):
    c = (getattr(model, "constructions", None) or {}).get(cid) if cid else None
    if c is None:
        return None, None
    ctype, _ = classify(f"{cid} {c.name}", surface)
    u = c.u_value_w_m2k / U_SI_PER_IP if c.u_value_w_m2k else None
    return ctype, u


def _judge(check_id, title, items, cat, n, surface, unit="Btu/h·ft²·°F", key="u_ip", types=None):
    """items: (id, value, class or None). Known class -> its own limit; unknown
    class -> the loosest class limit, which catches certain failures only, so a
    pass there is reported as unconfirmed (warn), never as compliant."""
    violations, unconfirmed, ids = [], [], []
    loose, loose_cls = _loosest(cat, n, surface, types, key)
    for iid, val, ctype in items:
        ids.append(iid)
        lim = _limit(cat, n, surface, ctype, key) if ctype else None
        if lim is None:
            if loose is None:
                continue
            if val > loose + 1e-9:
                violations.append(
                    f"{iid}: {val:.3f} > {loose:.3f} {unit} (class unknown; fails even "
                    f"the loosest class, {loose_cls})"
                )
            else:
                unconfirmed.append(iid)
        elif val > lim + 1e-9:
            violations.append(f"{iid} ({ctype}): {val:.3f} > {lim:.3f} {unit}")
    if violations:
        return CheckResult(check_id, title, "error", "; ".join(violations), entities=ids)
    if unconfirmed:
        return CheckResult(
            check_id,
            title,
            "warn",
            f"{len(unconfirmed)} item(s) have no construction class, so compliance is not "
            f"confirmed (below the loosest limit {loose:.3f} {unit}): "
            + ", ".join(unconfirmed[:10]),
            entities=ids,
        )
    return CheckResult(check_id, title, "pass", f"All {len(ids)} comply", entities=ids)


# ---------------------------------------------------------------------------
# Check functions — each takes ctx and returns CheckResult
# ---------------------------------------------------------------------------


def _check_wall_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5-N: exterior wall assembly U-factor.

    U comes from ``EnvelopeWall.u_factor`` (Btu/h·ft²·°F) when set, else from
    the wall's construction (W/m²·K). The limit is the one for the wall's own
    construction class (#747 classification).
    """
    cid_ = "ashrae_wall_u_factor"
    model: BuildingModel = ctx.model
    n, cat, why = _zone(model)
    title = f"ASHRAE 90.1-2019 Table 5.5-{n or 'N'}: Wall U-factor"
    if why:
        return CheckResult(cid_, title, "skip", why)
    items = []
    for wall in getattr(model, "envelope", []):
        cid = getattr(wall, "construction_id", "") or ""
        ctype, u_con = _construction_class(model, cid, "ExteriorWall")
        u = getattr(wall, "u_factor", None)
        u = u if u is not None else u_con
        if u is not None:
            items.append((wall.id, u, ctype))
    if not items:
        return CheckResult(cid_, title, "skip", "No walls with a U-factor on the model")
    return _judge(cid_, title, items, cat, n, "ExteriorWall")


def _check_roof_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5-N: roof assembly U-factor.

    U from ``model.roof_u_factor`` or ``model.roofs[i].u_factor`` (Btu/h·ft²·°F),
    else from ``roof_construction_id``. The class comes from the roof
    construction, or ``roofs[i].construction_type``.
    """
    cid_ = "ashrae_roof_u_factor"
    model: BuildingModel = ctx.model
    n, cat, why = _zone(model)
    title = f"ASHRAE 90.1-2019 Table 5.5-{n or 'N'}: Roof U-factor"
    if why:
        return CheckResult(cid_, title, "skip", why)
    rid = getattr(model, "roof_construction_id", "") or ""
    ctype, u_con = _construction_class(model, rid, "ExteriorRoof")
    items = []
    u = getattr(model, "roof_u_factor", None)
    if u is not None:
        items.append((rid or "roof", u, ctype))
    for roof in getattr(model, "roofs", None) or []:
        ru = getattr(roof, "u_factor", None)
        if ru is not None:
            items.append((getattr(roof, "id", "?"), ru, getattr(roof, "construction_type", None)))
    if not items and u_con is not None:
        items.append((rid, u_con, ctype))
    if not items:
        return CheckResult(cid_, title, "skip", "No roof U-factor on the model")
    return _judge(cid_, title, items, cat, n, "ExteriorRoof")


def _window_class(win):
    t = getattr(win, "construction_type", None)
    if t in ("Fixed", "Operable"):
        return t
    op = getattr(win, "operable", None)
    return None if op is None else ("Operable" if op else "Fixed")


def _check_window_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5-N: vertical fenestration U-factor and SHGC.

    Requires ``model.windows`` with ``u_factor`` / ``shgc``; ``operable`` or
    ``construction_type`` picks the Fixed or Operable row.
    """
    cid_ = "ashrae_window_u_factor"
    model: BuildingModel = ctx.model
    n, cat, why = _zone(model)
    title = f"ASHRAE 90.1-2019 Table 5.5-{n or 'N'}: Window U-factor & SHGC"
    if why:
        return CheckResult(cid_, title, "skip", why)
    windows = getattr(model, "windows", None) or []
    if not windows:
        return CheckResult(cid_, title, "skip", "No windows list found on model")
    kinds = ("Fixed", "Operable")
    u_items = [
        (getattr(w, "id", "?"), w.u_factor, _window_class(w))
        for w in windows
        if getattr(w, "u_factor", None) is not None
    ]
    s_items = [
        (getattr(w, "id", "?"), w.shgc, _window_class(w))
        for w in windows
        if getattr(w, "shgc", None) is not None
    ]
    ru = _judge(cid_, title, u_items, cat, n, "ExteriorWindow", types=kinds)
    rs = _judge(cid_, title, s_items, cat, n, "ExteriorWindow", unit="", key="shgc", types=kinds)
    rank = {"error": 2, "warn": 1, "pass": 0}
    sev = max(ru.severity, rs.severity, key=lambda v: rank.get(v, 0))
    ids = sorted({getattr(w, "id", "?") for w in windows})
    if sev == "pass":
        return CheckResult(cid_, title, "pass", f"All {len(windows)} windows comply", entities=ids)
    parts = []
    if ru.severity != "pass":
        parts.append(f"U-factor: {ru.message}")
    if rs.severity != "pass":
        parts.append(f"SHGC: {rs.message}")
    return CheckResult(cid_, title, sev, "Window " + "; ".join(parts), entities=ids)


def _check_lighting_power_density(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 9.5.1 — Lighting Power Density.

    Checks Space.lighting.lpd_w_m2 (or lpd_w_ft2) against the Table 9.5.1
    limit for each space's space_type.
    """
    model: BuildingModel = ctx.model
    spaces: dict[str, Space] = getattr(model, "spaces", {})

    violations: list[str] = []
    entity_ids: list[str] = []
    checked = 0

    for space_id, space in spaces.items():
        lighting = getattr(space, "lighting", None)
        if lighting is None:
            continue

        lpd_wm2 = getattr(lighting, "lpd_w_m2", None)
        if lpd_wm2 is None:
            lpd_wft2 = getattr(lighting, "lpd_w_ft2", None)
            if lpd_wft2 is not None:
                lpd_wm2 = lpd_wft2 * 10.764  # W/ft² -> W/m²
            else:
                continue

        checked += 1
        space_type = (getattr(space, "name", "") or "").lower().strip()
        lpd_max_wft2 = _LPD_MAX_WFT2.get(space_type, _LPD_MAX_WFT2["other"])
        lpd_max_wm2 = lpd_max_wft2 * 10.764

        entity_ids.append(space_id)
        if lpd_wm2 > lpd_max_wm2:
            violations.append(
                f"{space_id} ({space_type or 'other'}): "
                f"{lpd_wm2:.1f} > {lpd_max_wm2:.1f} W/m² "
                f"(limit={lpd_max_wft2:.2f} W/ft²)"
            )

    if not entity_ids:
        return CheckResult(
            "ashrae_lighting_power_density",
            "ASHRAE 90.1-2019 Table 9.5.1 — Lighting Power Density",
            "skip",
            "No spaces with lighting.lpd_w_m2 found on model",
        )

    if violations:
        msg = "; ".join(violations[:5])
        if len(violations) > 5:
            msg += f" ... (+{len(violations) - 5} more)"
        return CheckResult(
            "ashrae_lighting_power_density",
            "ASHRAE 90.1-2019 Table 9.5.1 — Lighting Power Density",
            "warn",
            f"LPD violations: {msg}",
            entities=entity_ids,
        )
    return CheckResult(
        "ashrae_lighting_power_density",
        "ASHRAE 90.1-2019 Table 9.5.1 — Lighting Power Density",
        "pass",
        f"All {checked} space(s) comply",
        entities=entity_ids,
    )


def _check_hvac_efficiency(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Tables 6.8.1-6.8.3 — HVAC efficiency.

    Checks Space.hvac cooling_eer and heating_cop against minimums.
    Iterates zones -> space_ids -> space.hvac.
    """
    model: BuildingModel = ctx.model
    zones: dict[str, Zone] = getattr(model, "zones", {})
    spaces: dict[str, Space] = getattr(model, "spaces", {})

    cool_violations, heat_violations, entity_ids = [], [], []
    checked = 0

    for zone_id, zone in zones.items():
        for space_id in zone.space_ids:
            space = spaces.get(space_id)
            if space is None:
                continue
            hvac = getattr(space, "hvac", None)
            if hvac is None:
                continue

            equip_type = getattr(hvac, "equipment_type", None) or "*"
            cooling_eer = getattr(hvac, "cooling_eer", None)
            heating_cop = getattr(hvac, "heating_cop", None)
            capacity_kbtu = getattr(hvac, "cooling_capacity_kbtu", None)
            size_key = ">=65kBtuh" if (capacity_kbtu and capacity_kbtu >= 65) else "<65kBtuh"

            checked += 1
            entity_ids.append(space_id)

            if cooling_eer is not None:
                eer_min = _HVAC_COOLING_EER_MIN.get(
                    (equip_type, size_key)
                ) or _HVAC_COOLING_EER_MIN.get(("*", "*"), 11.0)
                if cooling_eer < eer_min:
                    cool_violations.append(
                        f"{space_id}: EER={cooling_eer:.1f} < min={eer_min:.1f} "
                        f"(equip={equip_type}, {size_key})"
                    )

            if heating_cop is not None:
                cop_min = _HVAC_HEATING_COP_MIN.get(
                    (equip_type, size_key)
                ) or _HVAC_HEATING_COP_MIN.get(("*", "*"), 3.2)
                if heating_cop < cop_min:
                    heat_violations.append(
                        f"{space_id}: COP={heating_cop:.2f} < min={cop_min:.2f} "
                        f"(equip={equip_type})"
                    )

    if not entity_ids:
        return CheckResult(
            "ashrae_hvac_efficiency",
            "ASHRAE 90.1-2019 Tables 6.8.1-6.8.3 — HVAC efficiency",
            "skip",
            "No spaces with hvac found on model",
        )

    issues = cool_violations + heat_violations
    if issues:
        msg = "; ".join(issues[:5])
        if len(issues) > 5:
            msg += f" ... (+{len(issues) - 5} more)"
        return CheckResult(
            "ashrae_hvac_efficiency",
            "ASHRAE 90.1-2019 Tables 6.8.1-6.8.3 — HVAC efficiency",
            "error",
            f"HVAC violations: {msg}",
            entities=entity_ids,
        )
    return CheckResult(
        "ashrae_hvac_efficiency",
        "ASHRAE 90.1-2019 Tables 6.8.1-6.8.3 — HVAC efficiency",
        "pass",
        f"All {checked} space(s) comply",
        entities=entity_ids,
    )


def compliance_report(ctx: _Ctx) -> list[CheckResult]:
    """Run all ASHRAE 90.1 checks and return results."""
    checks = [
        _check_wall_u_factor,
        _check_roof_u_factor,
        _check_window_u_factor,
        _check_lighting_power_density,
        _check_hvac_efficiency,
    ]
    results: list[CheckResult] = []
    for check in checks:
        try:
            results.append(check(ctx))
        except Exception as e:
            results.append(
                CheckResult(
                    f"ashrae_{check.__name__}",
                    check.__name__,
                    "error",
                    f"Check raised {type(e).__name__}: {e}",
                )
            )
    return results


__all__ = [
    "_check_wall_u_factor",
    "_check_roof_u_factor",
    "_check_window_u_factor",
    "_check_lighting_power_density",
    "_check_hvac_efficiency",
    "compliance_report",
]
