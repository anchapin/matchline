"""Scheduled zone airflow on the gbXML thermal zones (#746).

Alex's 2026-10-10 decision (option 1 on #746): each gbXML thermal ``Zone``
gets ``FlowPerArea`` = the summed scheduled max airflow of the HVAC zones
serving it over the thermal zone's floor area, but only where every space in
it is served by HVAC zones that serve nothing outside it. Anything else gets
no value and a review item naming why. The min airflow stays in the JSON
report (``BuildingModel.zones``), not in gbXML.

HVAC zones (``building_model.Zone``) serve BuildingModel space ids; a room
split at an Appendix G block line (#638) shows up as pieces whose
``split_from`` is that id, so an HVAC zone serving a split room covers every
piece. When the BEM model carries no thermal zones the writer's single
default zone (``zone-1``, every space) is the thermal zone.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from building_model import Provenance

UNIT = "CubicMPerSecPerSquareM"
REVIEW_CONF = 0.5


def gbxml_zones(bem) -> List[Tuple[str, List[str]]]:
    """(gbXML Zone id, BEM space ids) as the gbXML writer emits them."""
    tz = list(getattr(bem, "thermal_zones", None) or [])
    if tz:
        return [(f"zone-{zid}", list(sids)) for zid, sids in tz]
    return [("zone-1", [s.sid for s in bem.spaces])]


def flow_per_area(
    bem, hvac_zones: Sequence
) -> Tuple[Dict[str, float], List[Tuple[str, List[str]]]]:
    """``({gbXML zone id: m3/s per m2}, [(gbXML zone id, [reasons])])``.

    Nothing either way when no HVAC zone carries a scheduled max airflow:
    the set gave no airflow to put anywhere.
    """
    hvac = sorted(hvac_zones, key=lambda z: z.id)
    if not any(z.design_airflow_max_m3s is not None for z in hvac):
        return {}, []
    area = {s.sid: float(s.area_m2 or 0.0) for s in bem.spaces}
    pieces: Dict[str, List[str]] = {}
    for s in bem.spaces:
        pieces.setdefault(getattr(s, "split_from", "") or s.sid, []).append(s.sid)
    foot = {z.id: {sid for p in z.space_ids for sid in pieces.get(p, [])} for z in hvac}
    lost = {z.id: [p for p in z.space_ids if p not in pieces] for z in hvac}

    values: Dict[str, float] = {}
    splits: List[Tuple[str, List[str]]] = []
    for gid, sids in gbxml_zones(bem):
        members = set(sids)
        serving = [z for z in hvac if foot[z.id] & members]
        reasons: List[str] = []
        if not serving:
            reasons.append("no HVAC zone serves it")
        else:
            served = set().union(*(foot[z.id] for z in serving))
            unserved = sorted(members - served)
            if unserved:
                reasons.append("spaces served by no HVAC zone: " + ", ".join(unserved))
            outside = [z.id for z in serving if (foot[z.id] - members) or lost[z.id]]
            if outside:
                reasons.append("HVAC zones also serving spaces outside it: " + ", ".join(outside))
            noflow = [z.id for z in serving if z.design_airflow_max_m3s is None]
            if noflow:
                reasons.append("HVAC zones with no scheduled max airflow: " + ", ".join(noflow))
        floor = sum(area.get(s, 0.0) for s in members)
        if not reasons and floor <= 0:
            reasons.append("no floor area")
        if reasons:
            splits.append((gid, reasons))
            continue
        values[gid] = sum(z.design_airflow_max_m3s for z in serving) / floor
    return values, splits


def apply_zone_airflow(model, bem) -> List[str]:
    """Set ``bem.zone_flow_per_area`` and flag each unsettled thermal zone.

    Returns the gbXML zone ids flagged for review. Safe to call twice: an
    item already in the queue for the same zone is not added again.
    """
    values, splits = flow_per_area(bem, list((getattr(model, "zones", None) or {}).values()))
    bem.zone_flow_per_area = values
    have = {(i.kind, (i.target or {}).get("id")) for i in getattr(model, "review_queue", [])}
    flagged = []
    for gid, reasons in splits:
        flagged.append(gid)
        if ("zone_airflow", gid) in have:
            continue
        text = f"No scheduled airflow written on thermal zone {gid}: " + "; ".join(reasons)
        prov = Provenance(
            sheet_id="model", revision=0, method="zone_airflow", confidence=REVIEW_CONF, note=text
        )
        model.flag_for_review(
            "zone_airflow", text, REVIEW_CONF, prov, target={"kind": "zone", "id": gid}
        )
    return flagged
