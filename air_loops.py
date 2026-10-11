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
    """Fill ``model.air_loops`` and each HVAC zone's ``air_loop``.

    Returns report notes: the schedule link notes, one line per air handler
    (zones and boxes on it, box airflow sum against the supply fan airflow as
    information), and HVAC zones whose box has no known air handler.
    """
    from datasets_adapter import normalize_tag
    from zone_capacity import BOXES, CENTRAL, _airflow_m3s, _cap, served_by_from_schedules

    central = {}
    for e in equipment or []:
        if e.get("tag") and e.get("kind") in CENTRAL:
            central.setdefault(normalize_tag(e["tag"]), e)
    if not central:
        return []
    links, notes = served_by_from_schedules(equipment)
    boxes = {
        normalize_tag(e["tag"]): e for e in equipment if e.get("tag") and e.get("kind") in BOXES
    }

    loops: Dict[str, AirLoop] = {}
    for t in sorted(central):
        e = central[t]
        sheet = e.get("sheet", "") or ""
        lp = AirLoop(tag=t, sheet=sheet)
        q = _airflow_m3s(e)
        if q is not None:
            lp.supply_airflow_m3s = round(q, 6)
            v = e.get("cfm_max") if e.get("cfm_max") is not None else e.get("cfm")
            lp.sources.append(f"{t} supply airflow {v:g} {e.get('airflow_unit')}")
        for duty in ("cooling_total", "cooling_sensible", "heating"):
            btuh, src = _cap(e, duty)
            if btuh is not None:
                setattr(lp, f"{duty}_btuh", round(btuh, 1))
                lp.sources.append(src)
            elif src:
                notes.append(f"air loops: {src}")
        lp.box_tags = sorted(b for b, u in links.items() if u == t)
        flows = [_airflow_m3s(boxes[b]) for b in lp.box_tags if b in boxes]
        if lp.box_tags and flows and all(f is not None for f in flows):
            lp.box_airflow_sum_m3s = round(sum(flows), 6)
        lp.provenance = Provenance(sheet, 0, "pdf_schedule_air_loop", 0.8)
        loops[t] = lp

    unlinked = []
    for zid in sorted(model.zones):
        z = model.zones[zid]
        tu = getattr(z, "terminal_unit", None)
        tag = normalize_tag(getattr(tu, "tag", "") or "") if tu else ""
        unit = tag if tag in loops else links.get(tag, "")
        z.air_loop = unit if unit in loops else ""
        if z.air_loop:
            loops[z.air_loop].zone_ids.append(zid)
        elif tag in boxes:
            unlinked.append(f"{zid} ({tag})")

    model.air_loops = [loops[t] for t in sorted(loops)]
    out = [f"air loops: {n}" for n in notes if not n.startswith("air loops: ")]
    out += [n for n in notes if n.startswith("air loops: ")]
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
    if unlinked:
        out.append(
            "air loops: HVAC zones whose box has no known air handler: " + ", ".join(unlinked)
        )
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
