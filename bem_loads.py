"""Space loads and schedules for the gbXML/IFC writers (#691).

The canonical model carries per-room occupant density, plug-load density,
lighting power and three schedule names on ``Space.use`` / ``Space.lighting``
(drawing values, else DOE prototype defaults from #685). The ifc_export
adapter copies them onto ``BEMSpace``; this module writes them:

gbXML, per Space: ``Description`` (where the loads came from),
``PeopleNumber`` (NumberOfPeople = density x area), ``LightPowerPerArea`` and
``EquipPowerPerArea`` (W/m2), and ``lightScheduleIdRef`` /
``peopleScheduleIdRef`` / ``equipmentScheduleIdRef`` pointing at
``Schedule`` -> ``YearSchedule`` -> ``WeekSchedule`` -> ``DaySchedule``
elements (24 hourly fractions; weekday, Saturday, Sunday and holiday).

``PeopleHeatGain`` (WattPerPerson, heatGainType Total) is written when the
space has an activity level: the DOE prototype row's constant occupancy
activity schedule (#693). IFC carries it as ``Pset_SpaceThermalLoad.People``
(W = people x W/person). Rows without one get no heat gain; none is invented.
"""

from __future__ import annotations

import re

from bem_helpers import _el, _fmt

# BEM day key -> gbXML dayType values it is written under
DAY_TYPES = (
    ("weekday", ("Weekday",)),
    ("saturday", ("Sat",)),
    ("sunday_holiday", ("Sun", "Holiday")),
)
YEAR_BEGIN = "2014-01-01"  # the prototype schedules' own date range
YEAR_END = "2014-12-31"
SCHEDULE_REF_ATTRS = (
    ("lighting_schedule", "lightScheduleIdRef"),
    ("occupancy_schedule", "peopleScheduleIdRef"),
    ("equipment_schedule", "equipmentScheduleIdRef"),
)


def schedule_id(name: str) -> str:
    """Schedule name -> an xsd:ID-safe gbXML id."""
    return "sch-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_")


def _profile(name: str):
    from space_use_defaults import SCHEDULES

    return SCHEDULES.get(name)


def write_schedules(root, spaces) -> dict:
    """Write every schedule the spaces reference; return {name: schedule id}.

    Names with no profile in the defaults table (a drawing-supplied schedule
    name, say) are skipped, and those spaces get no schedule reference.
    """
    names = sorted({getattr(sp, a, "") for sp in spaces for a, _ in SCHEDULE_REF_ATTRS} - {""})
    written = {}
    for name in names:
        prof = _profile(name)
        if prof is None:
            continue
        sid = schedule_id(name)
        week = _el(root, "WeekSchedule", id=f"{sid}-week", scheduleType="Fraction")
        _el(week, "Name", f"{name} week")
        for key, day_types in DAY_TYPES:
            day_id = f"{sid}-{key}"
            ds = _el(root, "DaySchedule", id=day_id, scheduleType="Fraction")
            _el(ds, "Name", f"{name} {key}")
            for v in prof[key]:
                _el(ds, "ScheduleValue", _fmt(v))
            for dt in day_types:
                _el(week, "Day", dayScheduleIdRef=day_id, dayType=dt)
        sch = _el(root, "Schedule", id=sid, type="Fraction")
        _el(sch, "Name", name)
        ys = _el(sch, "YearSchedule", id=f"{sid}-year")
        _el(ys, "BeginDate", YEAR_BEGIN)
        _el(ys, "EndDate", YEAR_END)
        _el(ys, "WeekScheduleId", weekScheduleIdRef=f"{sid}-week")
        written[name] = sid
    return written


def lpd_w_m2(sp):
    """Lighting power per area from total watts (None when unknown)."""
    if (sp.lighting_w or 0.0) > 0 and (sp.area_m2 or 0.0) > 0:
        return sp.lighting_w / sp.area_m2
    return None


def write_space_loads(se, sp, schedule_ids: dict) -> None:
    """Loads and schedule refs on one gbXML Space element."""
    if getattr(sp, "loads_source", ""):
        _el(se, "Description", f"Space-use loads: {sp.loads_source}")
    area = sp.area_m2 or 0.0
    if sp.people_per_m2 is not None and area > 0:
        _el(se, "PeopleNumber", _fmt(sp.people_per_m2 * area), unit="NumberOfPeople")
        act = getattr(sp, "activity_w_per_person", None)
        if act is not None:
            _el(se, "PeopleHeatGain", _fmt(act), unit="WattPerPerson", heatGainType="Total")
    lpd = lpd_w_m2(sp)
    if lpd is not None:
        _el(se, "LightPowerPerArea", _fmt(lpd), unit="WattPerSquareMeter")
    if sp.equipment_w_m2 is not None:
        _el(se, "EquipPowerPerArea", _fmt(sp.equipment_w_m2), unit="WattPerSquareMeter")
    for attr, ref in SCHEDULE_REF_ATTRS:
        name = getattr(sp, attr, "")
        if name and name in schedule_ids:
            se.set(ref, schedule_ids[name])


def ifc_load_psets(sp) -> dict:
    """IFC4 pset name -> properties for one space (empty when nothing known)."""
    out = {}
    area = sp.area_m2 or 0.0
    if sp.people_per_m2 and area > 0:
        out["Pset_SpaceOccupancyRequirements"] = {
            "OccupancyNumber": round(sp.people_per_m2 * area, 3),
            "AreaPerOccupant": round(1.0 / sp.people_per_m2, 3),
        }
    thermal = {}
    if (sp.lighting_w or 0.0) > 0:
        thermal["Lighting"] = round(sp.lighting_w, 3)
    act = getattr(sp, "activity_w_per_person", None)
    if sp.people_per_m2 and act is not None and area > 0:
        thermal["People"] = round(sp.people_per_m2 * area * act, 3)
    if sp.equipment_w_m2 is not None and area > 0:
        thermal["EquipmentSensible"] = round(sp.equipment_w_m2 * area, 3)
    if thermal:
        out["Pset_SpaceThermalLoad"] = thermal
    return out
