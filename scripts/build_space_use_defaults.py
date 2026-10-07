#!/usr/bin/env python3
"""Regenerate space_use_defaults_data.py from the DOE commercial prototypes.

Source: the DOE Commercial Prototype Building Models, ASHRAE 90.1-2019
edition, as encoded in NREL openstudio-standards (pinned tag below). The two
input files are the prototype space-type table and the prototype schedule
library:

  lib/openstudio-standards/standards/ashrae_90_1/ashrae_90_1_2019/data/ashrae_90_1_2019.spc_typ.json
  lib/openstudio-standards/standards/ashrae_90_1/data/ashrae_90_1.schedules.json

Usage (fetches both files at the pinned tag unless paths are given):

  python scripts/build_space_use_defaults.py [SPC_TYP_JSON SCHEDULES_JSON]

Every value written is copied from one source row; the row key
(building_type, space_type) is stored next to it. Edit SPACE_TYPE_ROWS to
change which prototype row a matchline space type uses, then re-run.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

TAG = "v0.8.6"
TEMPLATE = "90.1-2019"
BASE = f"https://raw.githubusercontent.com/NREL/openstudio-standards/{TAG}/lib/openstudio-standards/standards/ashrae_90_1"  # noqa: E501
SPC_URL = f"{BASE}/ashrae_90_1_2019/data/ashrae_90_1_2019.spc_typ.json"
SCH_URL = f"{BASE}/data/ashrae_90_1.schedules.json"

FT2_PER_M2 = 10.7639  # 1 m2 = 10.7639 ft2

# matchline space_type -> (prototype building_type, prototype space_type).
# Office rows come from the large-office prototype so one schedule family
# covers an office floor; the rest use the prototype that has the space.
SPACE_TYPE_ROWS = {
    "open_office": ("Office", "OpenOffice"),
    "closed_office": ("Office", "ClosedOffice"),
    "conference": ("Office", "Conference"),
    "break_room": ("Office", "BreakRoom"),
    "classroom": ("Office", "Classroom"),
    "dining": ("Office", "Dining"),
    "corridor": ("Office", "Corridor"),
    "lobby": ("Office", "Lobby"),
    "elevator_lobby": ("Office", "Elevator Lobby"),
    "restroom": ("Office", "Restroom"),
    "storage": ("Office", "Storage"),
    "stair": ("Office", "Stair"),
    "mechanical_electrical": ("Office", "Elec/MechRoom"),
    "it_room": ("Office", "IT_Room"),
    "print_room": ("Office", "PrintRoom"),
    "vending": ("Office", "Vending"),
    "data_center": ("Office", "OfficeLarge Data Center"),
    "retail": ("Retail", "Retail"),
    "kitchen": ("SecondarySchool", "Kitchen"),
    "gym": ("SecondarySchool", "Gym"),
    "auditorium": ("SecondarySchool", "Auditorium"),
    "library": ("SecondarySchool", "Library"),
    "warehouse": ("Warehouse", "Bulk"),
    # fallback when a room name matches nothing: the large-office
    # whole-building average
    "office_whole_building": ("Office", "WholeBuilding - Lg Office"),
}

# day types written per schedule: matchline key -> source day_types to try
DAY_TYPES = {
    "weekday": ("Wkdy", "Weekday", "WD", "Default"),
    "saturday": ("Sat", "Wknd", "Default"),
    "sunday_holiday": ("Sun|Hol", "Sun", "Hol", "Wknd", "Default"),
}


def _load(arg, url):
    if arg:
        return json.loads(Path(arg).read_text())
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 (pinned https URL)
        return json.loads(r.read())


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _schedule(rows, name):
    mine = [r for r in rows if r["name"] == name]
    if not mine:
        raise SystemExit(f"schedule {name!r} not found in {SCH_URL}")
    out = {}
    for key, wanted in DAY_TYPES.items():
        hit = None
        for dt in wanted:
            for r in mine:
                parts = {p.strip() for p in (r["day_types"] or "").split("|")}
                if dt == r["day_types"] or dt in parts:
                    hit = r
                    break
            if hit:
                break
        if hit is None:
            raise SystemExit(f"schedule {name!r}: no day type for {key}")
        vals = [float(v) for v in hit["values"]]
        if hit["type"] == "Constant":
            vals = vals[:1] * 24
        if len(vals) != 24:
            raise SystemExit(f"schedule {name!r} {key}: {len(vals)} values, need 24")
        out[key] = [round(v, 6) for v in vals]
    return out


def _activity(rows, name):
    """Constant W/person from a prototype activity schedule (None if unnamed)."""
    if not name:
        return None
    mine = [r for r in rows if r["name"] == name]
    if not mine:
        raise SystemExit(f"activity schedule {name!r} not found in {SCH_URL}")
    vals = {float(v) for r in mine for v in r["values"]}
    if len(vals) != 1:
        raise SystemExit(f"activity schedule {name!r} is not constant: {sorted(vals)}")
    return _r(vals.pop(), 3)


def build(spc, sch):
    rows = {
        (r["building_type"], r["space_type"]): r
        for r in spc["space_types"]
        if r["template"] == TEMPLATE
    }
    table, needed = {}, set()
    for key, src in SPACE_TYPE_ROWS.items():
        r = rows.get(src)
        if r is None:
            raise SystemExit(f"{key}: no {TEMPLATE} row for {src}")
        lpd = r["lighting_per_area"]
        occ = r["occupancy_per_area"]
        epd = r["electric_equipment_per_area"]
        scheds = {
            "lighting": r["lighting_schedule"],
            "occupancy": r["occupancy_schedule"],
            "equipment": r["electric_equipment_schedule"],
        }
        needed.update(s for s in scheds.values() if s)
        table[key] = {
            "source_building_type": src[0],
            "source_space_type": src[1],
            "lighting_w_ft2": _r(lpd),
            "occupancy_per_1000ft2": _r(occ),
            "equipment_w_ft2": _r(epd),
            "lpd_w_m2": _r(None if lpd is None else lpd * FT2_PER_M2, 3),
            "people_per_m2": _r(None if occ is None else occ / 1000.0 * FT2_PER_M2, 5),
            "equipment_w_m2": _r(None if epd is None else epd * FT2_PER_M2, 3),
            "lighting_schedule": scheds["lighting"],
            "occupancy_schedule": scheds["occupancy"],
            "equipment_schedule": scheds["equipment"],
            "activity_schedule": r.get("occupancy_activity_schedule"),
            "activity_w_per_person": _activity(
                sch["schedules"], r.get("occupancy_activity_schedule")
            ),
        }
    schedules = {n: _schedule(sch["schedules"], n) for n in sorted(needed)}
    return table, schedules


def render(table, schedules):
    lines = [
        '"""Space-use defaults: DOE Commercial Prototype Building Models.',
        "",
        "GENERATED by scripts/build_space_use_defaults.py -- do not edit by hand.",
        "",
        "Source:  U.S. DOE Commercial Prototype Building Models",
        f"Edition: ASHRAE Standard 90.1-2019 (template {TEMPLATE!r})",
        f"Encoded: NREL openstudio-standards {TAG}",
        f"  space types: {SPC_URL}",
        f"  schedules:   {SCH_URL}",
        "",
        "Each SPACE_USE_DEFAULTS row names its source row as",
        "(source_building_type, source_space_type). The IP values are copied",
        "verbatim; SI values are converted with 1 m2 = 10.7639 ft2. Schedules",
        "are hourly fractions (24 values, hour 0 = midnight to 1 am).",
        "activity_w_per_person is the constant value of the row's prototype",
        "occupancy activity schedule (total heat per person, W).",
        '"""',
        "",
        "# ruff: noqa: E501  (generated table rows)",
        "# fmt: off",
        f"SOURCE = {'DOE Commercial Prototype Building Models'!r}",
        f"EDITION = {'ASHRAE 90.1-2019'!r}",
        f"SOURCE_VERSION = {'openstudio-standards ' + TAG!r}",
        f"SOURCE_URL = {SPC_URL!r}",
        f"SCHEDULES_URL = {SCH_URL!r}",
        "",
        "SPACE_USE_DEFAULTS = {",
    ]
    for k, row in table.items():
        lines.append(f"    {k!r}: {{")
        for f, v in row.items():
            lines.append(f"        {f!r}: {v!r},")
        lines.append("    },")
    lines += ["}", "", "SCHEDULES = {"]
    for n, days in schedules.items():
        lines.append(f"    {n!r}: {{")
        for d, vals in days.items():
            lines.append(f"        {d!r}: {vals!r},")
        lines.append("    },")
    lines += ["}", "# fmt: on", ""]
    return "\n".join(lines)


def main(argv):
    spc = _load(argv[0] if len(argv) > 0 else None, SPC_URL)
    sch = _load(argv[1] if len(argv) > 1 else None, SCH_URL)
    table, schedules = build(spc, sch)
    out = Path(__file__).resolve().parent.parent / "space_use_defaults_data.py"
    out.write_text(render(table, schedules))
    print(f"wrote {out} ({len(table)} space types, {len(schedules)} schedules)")


if __name__ == "__main__":
    main(sys.argv[1:])
