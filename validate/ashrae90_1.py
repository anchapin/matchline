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

from .types import CheckResult

if TYPE_CHECKING:
    from validate import _Ctx

# ---------------------------------------------------------------------------
# ASHRAE 90.1-2019 Table 5.5.4 — Maximum Assembly U-Factor (Btu/h·ft²·°F)
# Climate zones: 1A, 1B, 2A, 2B, 3A, 3B, 3C, 4A, 4B, 4C, 5A, 5B, 5C, 6A, 6B, 7, 8
# Building types: Residential, Office, Retail, School, Hotel, etc.
# ---------------------------------------------------------------------------

# Table 5.5.4 — Mass Walls
# Key: (building_type, climate_zone) -> max_u (Btu/h·ft²·°F)
_WALL_U_MAX_BTU: dict[tuple[str, str], float] = {
    # Warm climates (1-3): no insulation required for mass walls
    ("*", "1A"): 0.58,
    ("*", "1B"): 0.58,
    ("*", "2A"): 0.58,
    ("*", "2B"): 0.58,
    ("*", "3A"): 0.58,
    ("*", "3B"): 0.58,
    ("*", "3C"): 0.58,
    # Temperate (4-5)
    ("*", "4A"): 0.40,
    ("*", "4B"): 0.40,
    ("*", "4C"): 0.40,
    ("*", "5A"): 0.40,
    ("*", "5B"): 0.40,
    ("*", "5C"): 0.40,
    # Cold (6-8)
    ("*", "6A"): 0.35,
    ("*", "6B"): 0.35,
    ("*", "7"): 0.35,
    ("*", "8"): 0.35,
}

# Table 5.5.4 — Roofs (continuous insulation path)
# climate_zone -> max_u (Btu/h·ft²·°F)
_ROOF_U_MAX_BTU: dict[str, float] = {
    "1A": 0.360,
    "1B": 0.360,
    "2A": 0.360,
    "2B": 0.360,
    "3A": 0.360,
    "3B": 0.360,
    "3C": 0.360,
    "4A": 0.282,
    "4B": 0.282,
    "4C": 0.282,
    "5A": 0.282,
    "5B": 0.282,
    "5C": 0.282,
    "6A": 0.248,
    "6B": 0.248,
    "7": 0.248,
    "8": 0.248,
}

# Table 5.5.4 — Windows (vertical glazing, fenestration)
_WINDOW_U_MAX_BTU: dict[str, float] = {
    "1A": 1.22,
    "1B": 1.22,
    "2A": 1.22,
    "2B": 1.22,
    "3A": 0.55,
    "3B": 0.55,
    "3C": 0.55,
    "4A": 0.40,
    "4B": 0.40,
    "4C": 0.40,
    "5A": 0.40,
    "5B": 0.40,
    "5C": 0.40,
    "6A": 0.35,
    "6B": 0.35,
    "7": 0.35,
    "8": 0.35,
}

_WINDOW_SHGC_MAX: dict[str, float] = {
    "1A": 0.25,
    "1B": 0.25,
    "2A": 0.25,
    "2B": 0.25,
    "3A": 0.25,
    "3B": 0.25,
    "3C": 0.60,
    "4A": 0.40,
    "4B": 0.40,
    "4C": 0.60,
    "5A": 0.40,
    "5B": 0.40,
    "5C": 0.60,
    "6A": 0.40,
    "6B": 0.40,
    "7": 0.40,
    "8": 0.40,
}

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

# Conversion: W/m²·K -> Btu/h·ft²·°F
_W_PER_M2K_TO_BTU = 0.17611


def _resolve_wall_u(building_type: str, climate_zone: str) -> float:
    key = (building_type, climate_zone)
    if key in _WALL_U_MAX_BTU:
        return _WALL_U_MAX_BTU[key]
    wildcard = ("*", climate_zone)
    if wildcard in _WALL_U_MAX_BTU:
        return _WALL_U_MAX_BTU[wildcard]
    return 0.40  # conservative fallback


# ---------------------------------------------------------------------------
# Check functions — each takes ctx and returns CheckResult
# ---------------------------------------------------------------------------


def _check_wall_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5.4 — Mass wall assembly U-factor.

    Requires EnvelopeWall.u_factor (Btu/h·ft²·°F) on the model.
    Skips walls that do not have u_factor set.
    """
    model: BuildingModel = ctx.model
    climate_zone = getattr(model, "climate_zone", "5A")
    building_type = getattr(model, "building_type", "other")

    u_max = _resolve_wall_u(building_type, climate_zone)
    violations: list[str] = []
    entity_ids: list[str] = []

    for wall in getattr(model, "envelope", []):
        u_actual = getattr(wall, "u_factor", None)
        if u_actual is None:
            continue
        entity_ids.append(wall.id)
        if u_actual > u_max:
            violations.append(f"{wall.id}: {u_actual:.3f} > {u_max:.3f} Btu/h·ft²·°F")

    if not entity_ids:
        return CheckResult(
            "ashrae_wall_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Wall U-factor",
            "skip",
            "No walls with u_factor set on model",
        )

    if violations:
        return CheckResult(
            "ashrae_wall_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Wall U-factor",
            "error",
            f"Wall U-factor exceeds maximum: {'; '.join(violations)}",
            entities=entity_ids,
            expected=u_max,
        )
    return CheckResult(
        "ashrae_wall_u_factor",
        "ASHRAE 90.1-2019 Table 5.5.4 — Wall U-factor",
        "pass",
        f"All {len(entity_ids)} walls comply (max {u_max:.3f} Btu/h·ft²·°F)",
        entities=entity_ids,
    )


def _check_roof_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5.4 — Roof assembly U-factor.

    Requires model.roof_u_factor or model.roofs[i].u_factor.
    """
    model: BuildingModel = ctx.model
    climate_zone = getattr(model, "climate_zone", "5A")

    u_max = _ROOF_U_MAX_BTU.get(climate_zone, 0.282)

    u_actual = getattr(model, "roof_u_factor", None)
    if u_actual is not None:
        if u_actual > u_max:
            return CheckResult(
                "ashrae_roof_u_factor",
                "ASHRAE 90.1-2019 Table 5.5.4 — Roof U-factor",
                "error",
                f"Roof u_factor={u_actual:.3f} > max={u_max:.3f} Btu/h·ft²·°F",
                expected=u_max,
                actual=u_actual,
            )
        return CheckResult(
            "ashrae_roof_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Roof U-factor",
            "pass",
            f"Roof complies (max {u_max:.3f} Btu/h·ft²·°F)",
        )

    roofs = getattr(model, "roofs", [])
    if not roofs:
        return CheckResult(
            "ashrae_roof_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Roof U-factor",
            "skip",
            "No roof_u_factor or roofs list on model",
        )

    violations, entity_ids = [], []
    for roof in roofs:
        u = getattr(roof, "u_factor", None)
        if u is None:
            continue
        rid = getattr(roof, "id", "?")
        entity_ids.append(rid)
        if u > u_max:
            violations.append(f"{rid}: {u:.3f} > {u_max:.3f}")

    if violations:
        return CheckResult(
            "ashrae_roof_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Roof U-factor",
            "error",
            f"Roof U-factor exceeds maximum: {'; '.join(violations)}",
            entities=entity_ids,
            expected=u_max,
        )
    return CheckResult(
        "ashrae_roof_u_factor",
        "ASHRAE 90.1-2019 Table 5.5.4 — Roof U-factor",
        "pass",
        f"All {len(entity_ids)} roofs comply (max {u_max:.3f} Btu/h·ft²·°F)",
        entities=entity_ids,
    )


def _check_window_u_factor(ctx: _Ctx) -> CheckResult:
    """ASHRAE 90.1-2019 Table 5.5.4 — Window U-factor and SHGC.

    Requires model.windows list with u_factor and shgc attributes.
    """
    model: BuildingModel = ctx.model
    climate_zone = getattr(model, "climate_zone", "5A")

    u_max = _WINDOW_U_MAX_BTU.get(climate_zone, 0.40)
    shgc_max = _WINDOW_SHGC_MAX.get(climate_zone, 0.40)

    windows = getattr(model, "windows", [])
    if not windows:
        return CheckResult(
            "ashrae_window_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Window U-factor & SHGC",
            "skip",
            "No windows list found on model",
        )

    u_violations, shgc_violations, entity_ids = [], [], []
    for win in windows:
        u = getattr(win, "u_factor", None)
        shgc = getattr(win, "shgc", None)
        wid = getattr(win, "id", "?")
        entity_ids.append(wid)
        if u is not None and u > u_max:
            u_violations.append(f"{wid}: U={u:.3f} > {u_max:.3f}")
        if shgc is not None and shgc > shgc_max:
            shgc_violations.append(f"{wid}: SHGC={shgc:.2f} > {shgc_max:.2f}")

    if u_violations or shgc_violations:
        msg_parts = []
        if u_violations:
            msg_parts.append(f"U-factor: {'; '.join(u_violations)}")
        if shgc_violations:
            msg_parts.append(f"SHGC: {'; '.join(shgc_violations)}")
        return CheckResult(
            "ashrae_window_u_factor",
            "ASHRAE 90.1-2019 Table 5.5.4 — Window U-factor & SHGC",
            "error",
            "Window violations: " + "; ".join(msg_parts),
            entities=entity_ids,
            expected=u_max,
        )
    return CheckResult(
        "ashrae_window_u_factor",
        "ASHRAE 90.1-2019 Table 5.5.4 — Window U-factor & SHGC",
        "pass",
        f"All {len(windows)} windows comply (max U={u_max:.3f}, SHGC={shgc_max:.2f})",
        entities=entity_ids,
    )


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
