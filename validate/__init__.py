"""Validation package.

Public API:
    run_checks(model, ...) -> ValidationReport
    export_gate(report) -> bool
    validate_bem_conservation(bem) -> list[CheckResult]
    CheckResult, ValidationReport  # from validate.types
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .types import CheckResult, ValidationReport

if TYPE_CHECKING:
    from building_model import BuildingModel

try:
    from geometry_simplify import footprint_from_regions

    _HAS_SIMPLIFY = True
except Exception:
    _HAS_SIMPLIFY = False

try:
    from datasets_adapter import polygon_area_px2
except Exception:
    polygon_area_px2 = None

# ---------------------------------------------------------------------------
# Import check functions from submodules (before _Ctx to satisfy E402)
# ---------------------------------------------------------------------------

from .conservation import (
    _check_area_conservation,
    _check_bem_area_conservation,
    _check_bem_volume_conservation,
    _check_envelope_area_matches_perimeter,
    _check_space_area_matches_polygon,
    _check_space_volume_matches_area_height,
    _check_volume_conservation,
    validate_bem_conservation,
)
from .export import (
    _check_assignment_uniqueness,
    _check_elevation_placement_consistency,
    _check_provenance_complete,
    _check_review_queue_acknowledged,
    _check_review_queue_sound,
    _check_revision_log_present,
    _check_space_id_hygiene,
    _check_window_double_link,
    _check_window_tag_coverage,
    _check_zone_nonempty,
    _check_zone_space_referential,
)
from .gbxml import (
    _check_gbxml_opening_refs,
    _check_gbxml_spaces,
    _check_gbxml_wall_areas,
    _check_ifc_counts,
)
from .invariants import (
    _check_facade_opening_closure,
    _check_fixture_schedule_join,
    _check_lpd_bounds,
    _check_lpd_unit_consistency,
    _check_no_negative_areas,
    _check_opening_schedule_join,
    _check_sill_head_sanity,
    _check_simplify_budget,
    _check_takeoff_counts_reconcile,
)

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
    sres: object = None
    gbxml_path: object = None
    ifc_path: object = None
    level_of: dict = field(default_factory=dict)
    footprint_area: dict = field(default_factory=dict)
    wall_height: dict = field(default_factory=dict)


def _build_ctx(model: BuildingModel, **kw) -> _Ctx:
    ctx = _Ctx(model=model, **kw)
    if ctx.sres is None:
        ctx.sres = getattr(model, "_sres", None)
    if ctx.gbxml_path is None:
        ctx.gbxml_path = getattr(model, "_gbxml_path", None)
    if ctx.ifc_path is None:
        ctx.ifc_path = getattr(model, "_ifc_path", None)
    for sid, sp in model.spaces.items():
        ctx.level_of.setdefault(sp.level_id, []).append(sp)
    for lvl in model.levels:
        ctx.wall_height[lvl.id] = lvl.wall_height_m
    if _HAS_SIMPLIFY:
        for lid, spaces in ctx.level_of.items():
            ring = footprint_from_regions([sp.polygon_m for sp in spaces])
            ctx.footprint_area[lid] = polygon_area_px2(ring) if (ring and polygon_area_px2) else 0.0
    return ctx


def _rel_err(actual: float, expected: float) -> float:
    if expected == 0:
        return 0.0 if actual == 0 else float("inf")
    return abs(actual - expected) / abs(expected)


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

BATTERY = [
    _check_space_area_matches_polygon,
    _check_area_conservation,
    _check_space_volume_matches_area_height,
    _check_volume_conservation,
    _check_envelope_area_matches_perimeter,
    _check_simplify_budget,
    _check_facade_opening_closure,
    _check_takeoff_counts_reconcile,
    _check_fixture_schedule_join,
    _check_opening_schedule_join,
    _check_no_negative_areas,
    _check_lpd_bounds,
    _check_lpd_unit_consistency,
    _check_sill_head_sanity,
    _check_assignment_uniqueness,
    _check_zone_nonempty,
    _check_zone_space_referential,
    _check_space_id_hygiene,
    _check_elevation_placement_consistency,
    _check_window_tag_coverage,
    _check_window_double_link,
    _check_provenance_complete,
    _check_review_queue_sound,
    _check_review_queue_acknowledged,
    _check_revision_log_present,
    _check_gbxml_spaces,
    _check_gbxml_opening_refs,
    _check_gbxml_wall_areas,
    _check_ifc_counts,
]

N_CHECKS = len(BATTERY)


def run_checks(
    model,
    gbxml_path=None,
    ifc_path=None,
    sres=None,
    tol_area: float = 0.03,
    tol_volume: float = 0.03,
    tol_envelope: float = 0.01,
    lpd_warn_max: float = 25.0,
    opening_eps: float = 0.005,
    min_review_confidence: float | None = None,
) -> ValidationReport:
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
    report = ValidationReport(building_name=getattr(model, "name", "unknown") or "(unnamed)")
    for check in BATTERY:
        try:
            result = check(ctx)
            if isinstance(result, CheckResult):
                report.results.append(result)
            elif isinstance(result, dict):
                report.results.append(CheckResult(**result))
            else:
                report.results.append(
                    CheckResult(
                        check.__name__,
                        check.__name__,
                        "error",
                        f"check returned unexpected type {type(result).__name__}",
                    )
                )
        except Exception as e:
            report.results.append(
                CheckResult(
                    check.__name__,
                    check.__name__,
                    "error",
                    f"check itself raised {type(e).__name__}: {e}",
                )
            )
    report.model = model
    return report


def export_gate(report: ValidationReport, model: BuildingModel | None = None) -> bool:
    """May this model be exported to gbXML/IFC? Errors block; warnings don't.

    Re-checks the review queue after auto-triage has run to ensure low-confidence
    results that were not auto-resolved are caught.
    """
    if not report.ok:
        return False
    check_model = model if model is not None else getattr(report, "model", None)
    if check_model is not None:
        unacknowledged = [
            item
            for item in check_model.review_queue
            if item.needs_review
            and not item.acknowledged
            and item.status not in ("confirmed", "rejected")
        ]
        if unacknowledged:
            return False
    return True


__all__ = [
    "run_checks",
    "export_gate",
    "N_CHECKS",
    "validate_bem_conservation",
    "_check_bem_area_conservation",
    "_check_bem_volume_conservation",
    "CheckResult",
    "ValidationReport",
    "BATTERY",
]
