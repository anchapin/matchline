"""The unified equipment model (#1237).

Each piece of equipment on the mechanical schedules becomes one
``Equipment`` object carrying its tag, type, capacities and airflow as the
schedule states them, the central unit that feeds it (``served_by``, from
the box-to-unit schedule links of #747), and the HVAC zones it serves, each
relationship naming its source. The sources, in the order they are trusted:

* ``plan_terminal_tag``: the zone's terminal unit on the plan carries the
  schedule tag (a duct-traced terminal, or the plan placement of a terminal
  tag, which is how the real-set zones are born).
* ``plan_tag_placement``: the schedule tag is placed on the plan inside a
  space the zone serves, so the zone is fed through that room.
* ``schedule_box_link``: the zone's terminal box names this central unit in
  a schedule column (#747); the plan ties the box to the zone, the schedule
  ties the box to the unit.

A zone served by no known air handler, or by more than one, keeps no
reference and goes to review (the #746 rule; a box-airflow sum above the
supply fan airflow stays normal diversity and is never a review item). A
self-serving terminal (a fan coil) resolves the zone to itself: the serving
relationship is real, but no central air handler is named, so the zone's
``air_loop`` stays empty exactly as the gbXML writer expects.

The model is export-agnostic: ``air_loops.build_air_loops`` derives the
gbXML air loops from it, and the BuildingSync writer (#1238) maps the
objects directly.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from building_model import Equipment, Provenance, ServingRef


def _norm(tag) -> str:
    from datasets_adapter import normalize_tag

    return normalize_tag(tag)


def build_equipment(model, equipment: List[dict]) -> List[str]:
    """Fill ``model.equipment`` from the schedule rows and resolve serving.

    Returns unprefixed report notes (the caller adds its own prefix). Zones
    with no known air handler, or served by more than one, get no reference
    and a review item (kind ``air_loop``, target kind ``hvac_zone``), never
    a guess. Safe to call again: a zone already flagged for the same reason
    is not flagged twice.
    """
    from zone_capacity import BOXES, CENTRAL, _airflow_m3s, _cap, served_by_from_schedules

    rows = [e for e in (equipment or []) if e.get("tag")]
    if not rows:
        return []
    boxes = {e["tag"]: e for e in rows if e.get("kind") in BOXES}
    terminals = {e["tag"]: e for e in rows if e.get("kind") in BOXES or e.get("kind") == "fcu"}
    links, link_notes = served_by_from_schedules(rows)

    items: Dict[str, Equipment] = {}
    for e in rows:
        tag = _norm(e["tag"])
        eid = f"eq-{tag}"
        n = 2
        while eid in items:
            eid = f"eq-{tag}-{n}"
            n += 1
        eq = Equipment(
            id=eid,
            tag=tag,
            kind=e.get("kind") or "unknown",
            sheet=e.get("sheet") or "",
            airflow_m3s=_airflow_m3s(e),
            plan_locs=[x for x in (e.get("located") or []) if isinstance(x, dict)],
            provenance=Provenance(e.get("sheet") or "", 0, "pdf_schedule_equipment", 0.8),
        )
        v = e.get("cfm_max") if e.get("cfm_max") is not None else e.get("cfm")
        if eq.airflow_m3s is not None:
            eq.sources.append(f"{tag} supply airflow {v:g} {e.get('airflow_unit')}")
        for duty in ("cooling_total", "cooling_sensible", "heating"):
            btuh, src = _cap(e, duty)
            if btuh is not None:
                setattr(eq, f"{duty}_btuh", round(btuh, 1))
                eq.sources.append(src)
            elif src:
                link_notes.append(src)
        eq.served_by = links.get(tag, "")
        items[eid] = eq
    by_tag: Dict[str, Equipment] = {}
    for eq in items.values():
        by_tag.setdefault(eq.tag, eq)
    model.equipment = list(items.values())
    central_tags = {eq.tag: eq for eq in items.values() if eq.kind in CENTRAL}

    notes: List[str] = list(link_notes)
    flagged = {
        (i.kind, (i.target or {}).get("kind"), (i.target or {}).get("id"))
        for i in getattr(model, "review_queue", [])
    }

    def _flag(zid: str, text: str) -> None:
        key = ("air_loop", "hvac_zone", zid)
        if key in flagged:
            return
        model.flag_for_review(
            "air_loop",
            text,
            0.5,
            Provenance("model", 0, "equipment_model", 0.5, note=text),
            target={"kind": "hvac_zone", "id": zid},
        )
        flagged.add(key)

    # zone claims: HVAC zone -> [(serving tag, source, confidence, note)]
    claims: Dict[str, List[Tuple[str, str, float, str]]] = {}
    for zid in sorted(model.zones):
        z = model.zones[zid]
        t = _norm(getattr(getattr(z, "terminal_unit", None), "tag", "") or "")
        got: List[Tuple[str, str, float, str]] = []
        if t:
            if t in central_tags:
                got.append((t, "plan_terminal_tag", 0.9, f"terminal unit {t}"))
            elif t in by_tag:
                # a scheduled terminal (box or fan coil) serves its own zone
                eq = by_tag[t]
                eq.serves.append(ServingRef(zid, "plan_terminal_tag", 0.9, f"terminal unit {t}"))
                if t in links:
                    got.append((links[t], "schedule_box_link", 0.8, f"via box {t}"))
        # plan placements of terminal tags inside the zone's rooms
        spaces = set(z.space_ids)
        for tag, row in terminals.items():
            for loc in row.get("located") or []:
                sid = loc.get("space_id") if isinstance(loc, dict) else None
                if not sid or sid not in spaces:
                    continue
                eq = by_tag.get(tag)
                if eq is None:
                    continue
                conf = float(loc.get("confidence") or 0.5)
                eq.serves.append(
                    ServingRef(zid, "plan_tag_placement", conf, f"{tag} tagged in {sid}")
                )
                if tag in links:
                    got.append((links[tag], "plan_tag_placement", conf, f"{tag} tagged in {sid}"))
        if got:
            claims[zid] = got

    multi: List[str] = []
    resolved: Dict[str, List[Tuple[str, str, float, str]]] = {}
    for zid, got in claims.items():
        units = sorted({u for u, *_ in got})
        if len(units) > 1:
            multi.append(f"{zid}: {', '.join(units)}")
            _flag(
                zid,
                f"HVAC zone {zid}: served by more than one air handler: " + ", ".join(units),
            )
            continue
        (u,) = units
        resolved[zid] = got
        if u in central_tags:
            model.zones[zid].air_loop = u
        seen = set()
        for _, src, conf, note in got:
            key = (u, src, note)
            if key in seen:
                continue
            seen.add(key)
            by_tag[u].serves.append(ServingRef(zid, src, conf, note))

    unresolved: List[str] = []
    unknown: List[str] = []
    no_terminal: List[str] = []
    for zid in sorted(model.zones):
        z = model.zones[zid]
        t = _norm(getattr(getattr(z, "terminal_unit", None), "tag", "") or "")
        if zid in resolved:
            continue
        z.air_loop = ""
        if not t:
            no_terminal.append(zid)
            _flag(zid, f"HVAC zone {zid}: no terminal unit traced; no known air handler")
        elif t in boxes and t not in links:
            unresolved.append(f"{zid} ({t})")
            _flag(zid, f"HVAC zone {zid}: terminal box {t} has no known air handler")
        elif t not in by_tag:
            unknown.append(f"{zid} ({t})")
            _flag(
                zid,
                f"HVAC zone {zid}: terminal unit {t} is not on the schedules; no known air handler",
            )

    if unresolved:
        notes.append("HVAC zones whose box has no known air handler: " + ", ".join(unresolved))
    if unknown:
        notes.append(
            "HVAC zones whose terminal unit is not on the schedules: " + ", ".join(unknown)
        )
    if no_terminal:
        notes.append("HVAC zones with no terminal unit on the plan: " + ", ".join(no_terminal))
    if multi:
        notes.append("HVAC zones served by more than one air handler: " + "; ".join(multi))
    return notes
