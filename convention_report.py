"""Convention report: the measured biases of an export, in one place.

ROADMAP cross-cutting note: items 1 and 4 both create measured
overestimation, so every export carries one auditable report instead of
scattering the numbers across validation messages:

- ``volume_bias``: interior-face vs exterior-face air volume (item 1), taken
  verbatim from the ``convention_bias`` check so the two can never disagree.
- ``non_room_area``: floor area held by shafts, closets, elevator/stair
  cores and unclassified spaces (item 4), per level and per type. Closets and
  shafts are merged by space_merge where the rule settles it; what is left
  stays its own space and the table says how much area that is, so loads and
  LPD can be read against rooms only.
- ``area_budget``: how far envelope simplification moved the envelope area
  against its budget.

The report only measures. A number that cannot be measured is reported as
unavailable with the reason, never as zero.
"""

from __future__ import annotations

SCHEMA = "matchline.convention_report/1"
NON_ROOM_TYPES = ("shaft", "closet", "elevator_core", "unassigned")


def _check(report, check_id):
    for r in getattr(report, "results", []) or []:
        if r.check_id == check_id:
            return r
    return None


def _volume_bias(report) -> dict:
    r = _check(report, "convention_bias")
    if r is None:
        return {"available": False, "reason": "convention_bias check was not run"}
    if r.severity == "skip" or not isinstance(r.actual, dict):
        return {"available": False, "reason": r.message}
    payload = r.actual
    interior = float(r.expected or 0.0)
    exterior = sum(lv["exterior_volume_m3"] for lv in payload["levels"].values())
    return {
        "available": True,
        "status": r.severity,
        "message": r.message,
        "thickness_m": payload["thickness_m"],
        "thickness_source": payload["thickness_source"],
        "convention": payload["convention"],
        "interior_volume_m3": interior,
        "exterior_volume_m3": exterior,
        "delta_m3": exterior - interior,
        "delta_pct": (exterior - interior) / interior * 100.0 if interior else None,
        "levels": payload["levels"],
    }


def _non_room_area(model) -> dict:
    spaces = list(model.spaces.values())
    total = sum(abs(s.area_m2 or 0.0) for s in spaces)
    levels: dict = {}
    by_type: dict = {}
    rows = []
    missing_area = []
    for s in sorted(spaces, key=lambda s: s.id):
        t = getattr(s, "poly_type", "room") or "room"
        if t == "room":
            continue
        if s.area_m2 is None:
            missing_area.append(s.id)
        a = abs(s.area_m2 or 0.0)
        lv = levels.setdefault(s.level_id, {})
        lv[t] = lv.get(t, 0.0) + a
        by_type[t] = by_type.get(t, 0.0) + a
        rows.append(
            {
                "space_id": s.id,
                "level_id": s.level_id,
                "poly_type": t,
                "area_m2": s.area_m2,
                "confidence": getattr(s, "poly_type_confidence", None),
            }
        )
    non_room = sum(by_type.values())
    return {
        "total_floor_area_m2": total,
        "non_room_area_m2": non_room,
        "non_room_share_pct": non_room / total * 100.0 if total else None,
        "by_type_m2": by_type,
        "by_level_m2": levels,
        "spaces": rows,
        "spaces_without_area": missing_area,
        "treatment": "closets merge into the room their door opens onto; shafts into the room with the largest share of their wall area; spaces the rule cannot settle are kept and flagged for review",
    }


def _area_budget(sres) -> dict:
    if sres is None:
        return {"available": False, "reason": "no simplification result"}
    delta = getattr(sres, "area_delta_pct", None)
    tol = getattr(sres, "tol", None)
    return {
        "available": delta is not None,
        "area_delta_pct": delta,
        "budget_pct": tol * 100.0 if tol is not None else None,
        "valid": bool(getattr(sres, "valid", True)),
        "within_budget": (
            abs(delta) <= tol * 100.0 + 1e-9 if delta is not None and tol is not None else None
        ),
    }


def build_convention_report(model, report=None, sres=None) -> dict:
    """Collect the export's measured biases into one JSON-ready dict.

    ``report`` is the ValidationReport from ``run_checks``; when omitted the
    battery is run here (with ``sres``) so the volume bias still comes from
    the same check.
    """
    if report is None:
        from validate import run_checks

        report = run_checks(model, sres=sres)
    return {
        "schema": SCHEMA,
        "building": model.name,
        "volume_bias": _volume_bias(report),
        "non_room_area": _non_room_area(model),
        "area_budget": _area_budget(sres),
    }
