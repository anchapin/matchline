"""Scheduled central air handlers as gbXML air loops (#746, option 3).

Alex's 2026-10-11 decision on #746: each AHU/RTU on the mechanical schedules
becomes a gbXML ``AirLoop`` named by its tag, with a supply fan carrying the
scheduled design airflow and coils carrying the scheduled capacities, each
noting the schedule it came from. Each gbXML thermal ``Zone`` references the
air handler serving it (``AirLoopId``), found from the box-to-unit links on
the schedules (``zone_capacity.served_by_from_schedules``). A thermal zone
served by HVAC zones with no known air handler, or by more than one, gets no
reference and a review item.

The summed max airflow of an air handler's boxes may exceed its supply fan
airflow: zones peak at different times of day (diversity), so that is
reported as information only, never as a problem.

Two halves:

* ``build_air_loops`` (Stage 4, ``real_set``): ``BuildingModel.air_loops``
  from the schedule rows, and ``Zone.air_loop`` on each HVAC zone whose
  terminal unit is a linked box (or is the air handler itself).
* ``apply_air_loops`` (Stage 6): the BEM air loops and the thermal zone ->
  air loop map the gbXML writer emits.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from building_model import AirLoop, Provenance

REVIEW_CONF = 0.5
LPS_PER_M3S = 1000.0


def loop_id(tag: str) -> str:
    """gbXML id for an air handler tag (an XML NCName)."""
    return "airloop-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", tag.strip()).strip("-")


def build_air_loops(model, equipment: List[dict]) -> List[str]:
    """Build ``model.equipment`` (#1237) and fill ``model.air_loops`` plus
    each HVAC zone's ``air_loop`` from it.

    Returns report notes: the schedule link notes, one line per air handler
    (zones and boxes on it, box airflow sum against the supply fan airflow as
    information), and the zones left without an air handler. Zone serving
    resolution and review flags live in ``equipment.build_equipment``; this
    is the gbXML-facing assembly on top of it.
    """
    from zone_capacity import BOXES, CENTRAL

    from equipment import build_equipment

    eq_notes = build_equipment(model, equipment)
    loops: Dict[str, AirLoop] = {}
    for eq in model.equipment:
        if eq.kind not in CENTRAL:
            continue
        lp = AirLoop(tag=eq.tag, sheet=eq.sheet)
        lp.supply_airflow_m3s = round(eq.airflow_m3s, 6) if eq.airflow_m3s is not None else None
        lp.cooling_total_btuh = eq.cooling_total_btuh
        lp.cooling_sensible_btuh = eq.cooling_sensible_btuh
        lp.heating_btuh = eq.heating_btuh
        lp.sources = list(eq.sources)
        lp.box_tags = sorted(
            b.tag for b in model.equipment if b.kind in BOXES and b.served_by == eq.tag
        )
        flows = [
            b.airflow_m3s
            for b in model.equipment
            if b.kind in BOXES and b.tag in lp.box_tags
        ]
        if lp.box_tags and flows and all(f is not None for f in flows):
            lp.box_airflow_sum_m3s = round(sum(flows), 6)
        lp.provenance = Provenance(eq.sheet, 0, "pdf_schedule_air_loop", 0.8)
        loops[eq.tag] = lp
    if not loops:
        return []
    for zid in sorted(model.zones):
        t = model.zones[zid].air_loop
        if t in loops:
            loops[t].zone_ids.append(zid)
    model.air_loops = [loops[t] for t in sorted(loops)]
    out = [f"air loops: {n}" for n in eq_notes if not n.startswith("air loops: ")]
    for lp in model.air_loops:
        line = (
            f"air loops: {lp.tag} serves {len(lp.zone_ids)} HVAC zone(s), "
            f"{len(lp.box_tags)} box(es)"
        )
        if lp.box_airflow_sum_m3s is not None and lp.supply_airflow_m3s:
            r = lp.box_airflow_sum_m3s / lp.supply_airflow_m3s
            line += (
                f"; boxes sum {lp.box_airflow_sum_m3s:.3f} m3/s vs supply fan "
                f"{lp.supply_airflow_m3s:.3f} m3/s (ratio {r:.2f}"
                + (", diversity, expected)" if r > 1.0 else ")")
            )
        out.append(line)
    return out


def _bem_loops(model) -> List[dict]:
    out = []
    for lp in getattr(model, "air_loops", None) or []:
        q = lp.supply_airflow_m3s
        out.append(
            {
                "id": loop_id(lp.tag),
                "name": lp.tag,
                "system_type": "VariableAirVolume" if lp.box_tags else "",
                "fan_airflow_lps": round(q * LPS_PER_M3S, 3) if q else None,
                "cooling_total_btuh": lp.cooling_total_btuh,
                "cooling_sensible_btuh": lp.cooling_sensible_btuh,
                "heating_btuh": lp.heating_btuh,
                "sources": list(lp.sources),
            }
        )
    return out


def zone_air_loops(bem, hvac_zones: Sequence) -> Tuple[Dict[str, str], List[Tuple[str, List[str]]]]:
    """``({gbXML zone id: air handler tag}, [(gbXML zone id, [reasons])])``.

    A thermal zone takes the air handler of the HVAC zones serving it when
    they all name the same one. Spaces in it served by no HVAC zone don't
    block that: the thermal zone is one zone in the simulation either way.
    """
    from zone_airflow import gbxml_zones

    hvac = sorted(hvac_zones, key=lambda z: z.id)
    pieces: Dict[str, List[str]] = {}
    for s in bem.spaces:
        pieces.setdefault(getattr(s, "split_from", "") or s.sid, []).append(s.sid)
    foot = {z.id: {sid for p in z.space_ids for sid in pieces.get(p, [])} for z in hvac}

    values: Dict[str, str] = {}
    unsettled: List[Tuple[str, List[str]]] = []
    for gid, sids in gbxml_zones(bem):
        members = set(sids)
        serving = [z for z in hvac if foot[z.id] & members]
        reasons: List[str] = []
        if not serving:
            reasons.append("no HVAC zone serves it")
        else:
            none = [z.id for z in serving if not getattr(z, "air_loop", "")]
            if none:
                reasons.append("HVAC zones with no known air handler: " + ", ".join(none))
            units = sorted({z.air_loop for z in serving if getattr(z, "air_loop", "")})
            if len(units) > 1:
                reasons.append("served by more than one air handler: " + ", ".join(units))
            if not reasons:
                values[gid] = units[0]
        if reasons:
            unsettled.append((gid, reasons))
    return values, unsettled


def apply_air_loops(model, bem) -> List[str]:
    """Set ``bem.air_loops`` and ``bem.zone_air_loop``; flag each thermal zone
    left without an air handler.

    Nothing happens when the model has no scheduled air handlers. Returns the
    gbXML zone ids flagged for review. Safe to call twice: an item already in
    the queue for the same zone is not added again.
    """
    loops = _bem_loops(model)
    bem.air_loops = loops
    bem.zone_air_loop = {}
    if not loops:
        return []
    ids = {lp["name"]: lp["id"] for lp in loops}
    values, unsettled = zone_air_loops(bem, list((getattr(model, "zones", None) or {}).values()))
    bem.zone_air_loop = {gid: ids[t] for gid, t in values.items() if t in ids}
    have = {(i.kind, (i.target or {}).get("id")) for i in getattr(model, "review_queue", [])}
    flagged = []
    for gid, reasons in unsettled:
        flagged.append(gid)
        if ("air_loop", gid) in have:
            continue
        text = f"No air loop referenced by thermal zone {gid}: " + "; ".join(reasons)
        prov = Provenance(
            sheet_id="model", revision=0, method="air_loop", confidence=REVIEW_CONF, note=text
        )
        model.flag_for_review(
            "air_loop", text, REVIEW_CONF, prov, target={"kind": "zone", "id": gid}
        )
    return flagged
