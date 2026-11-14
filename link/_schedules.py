from __future__ import annotations

from dataclasses import fields

from datasets_adapter import ScheduleEntry, normalize_tag

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Schedule extraction."""

# Every field a ScheduleEntry can carry. A source row is handed each one of
# them it actually holds, so the #746 thermal columns (U, SHGC, VT and their
# confidence/note) survive the link instead of being dropped here. A row
# without them yields exactly the entry it yields today (the dataclass
# defaults: None / "").
_ENTRY_FIELDS = frozenset(f.name for f in fields(ScheduleEntry))

# The fields the two row kinds set themselves; they are never taken from the
# raw row, so a stray key in a source row cannot contradict them.
_WINDOW_FIXED = ("tag", "category", "width_m", "height_m")
_LIGHTING_FIXED = ("tag", "category", "width_m", "height_m", "watts", "description", "lamp_type")


def _row_fields(row: dict, fixed: tuple) -> dict:
    """The ScheduleEntry fields this row carries besides the fixed ones."""
    return {k: v for k, v in row.items() if k in _ENTRY_FIELDS and k not in fixed}


def _window_entry(row: dict) -> ScheduleEntry:
    """One window/door schedule row as a ScheduleEntry.

    Every field the row carries is kept, in particular the #746 thermal
    fields (``u_value_w_m2k``, ``shgc``, ``vt``, ``thermal_confidence``,
    ``thermal_note``); a row without them yields the entry it yields today.
    """
    return ScheduleEntry(
        tag=row["tag"],
        category=row["category"],
        width_m=row["width_m"],
        height_m=row["height_m"],
        **_row_fields(row, _WINDOW_FIXED),
    )


def _lighting_entry(row: dict) -> ScheduleEntry:
    """One lighting schedule row as a ScheduleEntry."""
    return ScheduleEntry(
        tag=row["tag"],
        category="lighting",
        width_m=None,
        height_m=None,
        watts=row["watts"],
        description=row.get("description", ""),
        lamp_type=row.get("lamp_type", ""),
        **_row_fields(row, _LIGHTING_FIXED),
    )


def _duplicate_note(key: str, prev: ScheduleEntry, cur: ScheduleEntry) -> str:
    """Trace of two rows that normalise to the same schedule tag.

    The convention is the one the takeoff rollup uses
    (``datasets_adapter.rollup_takeoff`` normalises the schedule keys with
    ``normalize_tag`` and lets the LAST row of a key win): the later row
    wins here too. The overwrite is not silent -- the surviving entry keeps a
    note naming both rows, so the collision is visible in the model.
    """
    msg = (
        f"duplicate schedule tag '{key}': rows '{prev.tag}' and '{cur.tag}' "
        "normalise to the same tag; the last row wins (as in the takeoff rollup)"
    )
    return "; ".join(p for p in (cur.note, msg) if p)


def _schedules(bldg) -> tuple:
    win_sched = {}
    for row in bldg["window_schedule"]:
        e = _window_entry(row)
        # Tags are compared after ``normalize_tag`` on both sides, exactly as
        # the takeoff rollup does: an OCR read of ``w-1`` or ``W 1`` joins the
        # schedule row ``W-1``.
        key = normalize_tag(e.tag)
        prev = win_sched.get(key)
        if prev is not None:
            e.note = _duplicate_note(key, prev, e)
        win_sched[key] = e
    light_sched = {}
    for row in bldg["lighting_schedule"]:
        # The lighting join (``link/_lighting.py``) looks its rows up by the
        # normalised tag, so the lighting schedule is keyed like the
        # window/door one: an OCR read of ``a-1`` or ``A 1`` joins the
        # schedule row ``A-1``. The entry keeps the tag as the row states it.
        e = _lighting_entry(row)
        key = normalize_tag(e.tag)
        prev = light_sched.get(key)
        if prev is not None:
            e.note = _duplicate_note(key, prev, e)
        light_sched[key] = e
    return win_sched, light_sched
