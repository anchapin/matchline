"""Heating and cooling capacity per terminal zone (#747, slice 3).

Alex's rules (#747 issuecomment-6092815093):

* A central unit's coil (an AHU or RTU feeding VAV boxes) is split across the
  boxes it serves by each box's design (max) supply airflow divided by the
  unit's supply fan total design airflow. When the unit's schedule row gives
  no airflow, the split uses the sum of its boxes' max airflows instead, and
  the result is marked derived.
* A box's own reheat capacity is added to its share of the central heating.
* A self-serving unit (a fan coil, or an AHU/RTU with no boxes assigned to it)
  uses its own capacity.
* Every capacity carries its source and units. Anything unsettled (which unit
  a box hangs off, a capacity or airflow without units) goes to review and is
  never defaulted.

Nothing here reads drawings: the box-to-unit link comes from the caller
(schedule columns, ``served_by_from_schedules``). ``classify_spaces`` turns
zone capacities into a Section 3.2 category per space (slice 4b);
``real_set`` writes it to ``Space.conditioning``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from space_conditioning import to_btuh

AIRFLOW_M3S = {"cfm": 0.3048**3 / 60.0, "l/s": 0.001}
DUTIES = ("cooling_sensible", "cooling_total", "heating")
CENTRAL = ("ahu",)
BOXES = ("vav",)
SELF = ("fcu",)


def _airflow_m3s(e: dict) -> Optional[float]:
    """Design (max) supply airflow on a schedule row in m3/s, or None when the
    row has none, it isn't positive, or its unit is unstated."""
    v = e.get("cfm_max") if e.get("cfm_max") is not None else e.get("cfm")
    k = AIRFLOW_M3S.get(e.get("airflow_unit") or "")
    if v is None or k is None or v <= 0:
        return None
    return v * k


def _cap(e: dict, key: str):
    """(Btu/h, source) for one capacity on a row, (None, None) when the row has
    none, or (None, reason) when it can't be converted."""
    c = (e.get("capacities") or {}).get(key)
    if not c:
        return None, None
    try:
        btuh = to_btuh(c["value"], c["unit"])
    except ValueError:
        return None, f"{e['tag']} {c['column']} {c['value']:g} states no unit, not used"
    unit = c["unit"] or "?"
    return btuh, f"{e['tag']} {c['column']} {c['value']:g} {unit}"


def zone_capacities(
    equipment: List[dict], served_by: Optional[Dict[str, str]] = None
) -> Dict[str, dict]:
    """Capacity per zone-serving unit, keyed by its tag.

    ``equipment`` is mechanical schedule rows (``pdf_schedules``), each with
    ``tag``, ``kind`` and optionally ``capacities``, ``cfm_max``/``cfm`` and
    ``airflow_unit``. ``served_by`` maps a VAV box tag to the tag of the
    central unit feeding it.

    Each entry: ``served_by`` (central tag or ""), ``share`` (the box's
    fraction of the central unit, or None), ``derived`` (True when the split
    used the boxes' summed airflow), and per duty either
    ``{"btuh", "sources"}`` or None, plus ``review`` reasons. A duty is None
    when nothing on the schedules gives it; a reason in ``review`` says why
    when it was there but couldn't be used.
    """
    served_by = {k.upper(): v.upper() for k, v in (served_by or {}).items()}
    rows = {e["tag"].upper(): e for e in equipment if e.get("tag")}
    central = {t: e for t, e in rows.items() if e.get("kind") in CENTRAL}
    boxes = {t: e for t, e in rows.items() if e.get("kind") in BOXES}
    on_unit: Dict[str, List[str]] = {}
    for b in boxes:
        u = served_by.get(b)
        if u in central:
            on_unit.setdefault(u, []).append(b)

    out: Dict[str, dict] = {}

    def entry(served="", share=None, derived=False):
        r = {"served_by": served, "share": share, "derived": derived, "review": []}
        r.update({d: None for d in DUTIES})
        return r

    unlinked = sorted(b for b in boxes if not served_by.get(b))
    # self-serving units: fan coils, and central units with no boxes on them.
    # While some box's central unit is unknown, an AHU with no boxes assigned
    # may be feeding it, so it isn't called single-zone.
    for t, e in rows.items():
        if t in central and t not in on_unit and unlinked:
            r = entry()
            r["review"].append(
                f"{t}: no boxes assigned, but {', '.join(unlinked)} have no known "
                "central unit, so it may not be single-zone"
            )
            out[t] = r
            continue
        if e.get("kind") in SELF or (t in central and t not in on_unit):
            r = entry()
            for d in DUTIES:
                v, src = _cap(e, d)
                r[d] = {"btuh": round(v, 1), "sources": [src]} if v is not None else None
                if v is None and src:
                    r["review"].append(src)
            out[t] = r

    for b, e in boxes.items():
        u = served_by.get(b)
        if not u:
            r = entry()
            r["review"].append(f"{b}: which central unit feeds it isn't known")
        elif u not in central:
            r = entry(u)
            r["review"].append(f"{b}: its central unit {u} isn't on the mechanical schedules")
        else:
            r = _box_share(b, e, u, central[u], [boxes[x] for x in on_unit[u]])
        out[b] = r
    return out


def _box_share(b: str, box: dict, u: str, unit: dict, siblings: List[dict]) -> dict:
    r = {"served_by": u, "share": None, "derived": False, "review": []}
    q_box = _airflow_m3s(box)
    q_fan = _airflow_m3s(unit)
    if q_fan is None:
        flows = [_airflow_m3s(s) for s in siblings]
        if any(f is None for f in flows):
            q_fan = None
        else:
            q_fan = sum(flows)
            r["derived"] = True
    if q_box is None:
        r["review"].append(f"{b}: no design airflow with units on its schedule row")
    elif not q_fan:
        r["review"].append(
            f"{b}: {u} has no supply airflow and not every box on it has one, so it can't be split"
        )
    elif q_box > q_fan * 1.0001:
        r["review"].append(f"{b}: its airflow exceeds {u}'s supply airflow")
    else:
        r["share"] = round(q_box / q_fan, 6)
    how = "sum of its boxes' max airflow" if r["derived"] else "its supply fan design airflow"
    for d in DUTIES:
        val, srcs = 0.0, []
        have = False
        if r["share"] is not None:
            v, src = _cap(unit, d)
            if v is not None:
                val += v * r["share"]
                srcs.append(f"{src} x {r['share']:.4f} ({b} airflow / {u} {how})")
                have = True
            elif src:
                r["review"].append(src)
        if d == "heating":
            v, src = _cap(box, "reheat")
            if v is not None:
                val += v
                srcs.append(src + " (reheat)")
                have = True
            elif src:
                r["review"].append(src)
        r[d] = {"btuh": round(val, 1), "sources": srcs} if have else None
    return r


# what a central unit's tag looks like, so a SYSTEM column saying "VAV" or
# "CHW" isn't read as naming one
_CENTRAL_TAG = re.compile(r"^(AHU|RTU|AH|DOAS|MAU|ACU)[-\s]?\d")

# a VAV schedule column naming the central unit a box hangs off ("AHU",
# "SERVED BY", "FED FROM", "SYSTEM"); "SERVES" names rooms, so it's not one
_UNIT_COL = re.compile(r"\bAHU\b|\bRTU\b|AIR HANDL|SERVED\s*BY|FED\s*FROM|\bSYSTEM\b")


def served_by_from_schedules(equipment: List[dict]) -> Tuple[Dict[str, str], List[str]]:
    """Box tag -> central unit tag, read from a column on each VAV row (#747).

    A box links only when a column whose header names its central unit (AHU,
    RTU, AIR HANDLER, SERVED BY, FED FROM, SYSTEM) holds a tag that matches
    an AHU/RTU row on the schedules. A box whose column
    names an unscheduled unit, or whose columns name two different units, is
    left unlinked with a reason, so ``zone_capacities`` sends it to review.
    Boxes with no such column are simply not linked here (HVAC tracing may
    link them).
    """
    from datasets_adapter import normalize_tag

    central = {
        normalize_tag(e["tag"]) for e in equipment if e.get("tag") and e.get("kind") in CENTRAL
    }
    links: Dict[str, str] = {}
    notes: List[str] = []
    for e in equipment:
        if e.get("kind") not in BOXES or not e.get("tag"):
            continue
        box = normalize_tag(e["tag"])
        named = set()
        for h, v in (e.get("values") or {}).items():
            hu = str(h).upper()
            if not _UNIT_COL.search(hu) or re.match(r"^(TAG|MARK)\b", hu):
                continue
            t = normalize_tag(str(v or ""))
            if t and t != box and (t in central or _CENTRAL_TAG.match(t)):
                named.add(t)
        if not named:
            continue
        if len(named) > 1:
            notes.append(f"{box}: its schedule row names {', '.join(sorted(named))}, not linked")
            continue
        (u,) = named
        if u in central:
            links[box] = u
        else:
            notes.append(f"{box}: its schedule row names {u}, which isn't on the schedules")
    return links, notes


SQFT_PER_M2 = 1.0 / 0.3048**2


def space_duties(
    zones: Dict[str, object], spaces: Dict[str, object], caps: Dict[str, dict]
) -> Dict[str, dict]:
    """Heating and cooling output serving each space, in Btu/h (#747 slice 4b).

    A zone's capacity (``zone_capacities``, keyed by its terminal unit's tag)
    is spread over the floor area it serves, so each space gets its area share;
    a space in several zones adds them up. A duty is None for a space when any
    zone serving it doesn't know that duty, so a missing piece is never read
    as zero. Spaces with no zone are left out: no terminal unit found is not
    evidence of no HVAC.

    Each entry: ``area_ft2``, one Btu/h value or None per duty, ``zones``,
    ``sources``, ``derived`` (any share came from summed box airflow) and
    ``review`` reasons carried from the zones.
    """
    out: Dict[str, dict] = {}
    caps = {k.upper(): v for k, v in (caps or {}).items()}
    # one scheduled unit placed as the terminal of several zones (a tag reused
    # on two floors, say) can't hand its whole capacity to each of them
    zones_of: Dict[str, List[str]] = {}
    for zid, z in zones.items():
        t = (getattr(getattr(z, "terminal_unit", None), "tag", "") or "").upper()
        if t:
            zones_of.setdefault(t, []).append(zid)
    for zid in sorted(zones):
        z = zones[zid]
        tu = getattr(z, "terminal_unit", None)
        tag = (getattr(tu, "tag", "") or "").upper()
        sids = [s for s in getattr(z, "space_ids", []) or [] if s in spaces]
        if not sids:
            continue
        c = caps.get(tag)
        areas = {s: getattr(spaces[s], "area_m2", None) for s in sids}
        missing = sorted(s for s, a in areas.items() if not a or a <= 0)
        zone_area = sum(a for a in areas.values() if a and a > 0)
        for s in sids:
            a = areas[s]
            r = out.setdefault(
                s,
                {
                    "area_ft2": round(a * SQFT_PER_M2, 1) if a and a > 0 else None,
                    **{d: 0.0 for d in DUTIES},
                    "zones": [],
                    "sources": [],
                    "derived": False,
                    "review": [],
                },
            )
            r["zones"].append(zid)
            if c is None:
                r["review"].append(
                    f"{zid}: its unit {tag or '(untagged)'} has no capacity on the schedules"
                )
                for d in DUTIES:
                    r[d] = None
                continue
            if len(zones_of.get(tag, [])) > 1:
                r["review"].append(
                    f"{zid}: {tag} is the unit of {len(zones_of[tag])} zones "
                    f"({', '.join(sorted(zones_of[tag]))}), so its capacity can't go to one of them"
                )
                for d in DUTIES:
                    r[d] = None
                continue
            r["review"] += [x for x in c.get("review", []) if x not in r["review"]]
            r["derived"] = r["derived"] or bool(c.get("derived"))
            if missing:
                r["review"].append(
                    f"{zid}: no floor area for {', '.join(missing)}, so its capacity can't be split"
                )
                for d in DUTIES:
                    r[d] = None
                continue
            frac = a / zone_area
            for d in DUTIES:
                v = c.get(d)
                if v is None or r[d] is None:
                    r[d] = None
                    continue
                r[d] += v["btuh"] * frac
                part = f"{zid} {d.replace('_', ' ')} {v['btuh']:g} Btu/h"
                if len(sids) > 1:
                    part += f" x {frac:.3f} of its {zone_area * SQFT_PER_M2:.0f} ft2"
                r["sources"].append(part + " (" + "; ".join(v["sources"]) + ")")
    for r in out.values():
        for d in DUTIES:
            if r[d] is not None:
                r[d] = round(r[d], 1)
    return out


def classify_spaces(
    zones: Dict[str, object],
    spaces: Dict[str, object],
    caps: Dict[str, dict],
    climate_zone: str = "",
) -> Dict[str, dict]:
    """Section 3.2 category per served space (``space_conditioning``).

    Sensible cooling settles "cooled"; total cooling alone never does. The
    indirectly-conditioned test is not run here, so a space that is neither
    cooled nor heated stays in review rather than being called unconditioned.
    A space whose zone carried a review reason can still be conditioned (the
    output it was given already clears the bar), but is otherwise review.
    """
    from space_conditioning import classify_space

    out: Dict[str, dict] = {}
    for sid, r in space_duties(zones, spaces, caps).items():
        sens = r["cooling_sensible"]
        tot = r["cooling_total"] if sens is None else None
        cat = classify_space(r["area_ft2"], sens, r["heating"], climate_zone, tot, None)
        reasons = list(cat.reasons)
        category = cat.category
        if r["review"] and category != "conditioned":
            category = "review"
            reasons += r["review"]
        out[sid] = {
            "category": category,
            "reasons": reasons,
            "derived": r["derived"],
            "area_ft2": r["area_ft2"],
            "cooling_sensible_btuh": sens,
            "cooling_total_btuh": r["cooling_total"],
            "heating_btuh": r["heating"],
            "cooling_btuh_ft2": None
            if cat.cooling_btuh_ft2 is None
            else round(cat.cooling_btuh_ft2, 2),
            "heating_btuh_ft2": None
            if cat.heating_btuh_ft2 is None
            else round(cat.heating_btuh_ft2, 2),
            "zones": r["zones"],
            "sources": r["sources"],
            "review": r["review"],
        }
    return out
