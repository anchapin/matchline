from __future__ import annotations

from datasets_adapter import ScheduleEntry

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Schedule extraction."""


def _schedules(bldg) -> tuple:
    win_sched = {}
    for row in bldg["window_schedule"]:
        e = ScheduleEntry(
            tag=row["tag"],
            category=row["category"],
            width_m=row["width_m"],
            height_m=row["height_m"],
        )
        win_sched[e.tag] = e
    light_sched = {}
    for row in bldg["lighting_schedule"]:
        e = ScheduleEntry(
            tag=row["tag"],
            category="lighting",
            width_m=None,
            height_m=None,
            watts=row["watts"],
            description=row.get("description", ""),
            lamp_type=row.get("lamp_type", ""),
        )
        light_sched[e.tag] = e
    return win_sched, light_sched
