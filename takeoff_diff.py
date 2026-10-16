"""Takeoff diff between two drawing-set revisions (#993).

A revision changes what the set takes off: rooms appear, disappear, rename or
grow; windows and doors move with the walls; and the mechanical symbol
takeoff (legend rows mapped to detector classes) shifts with the equipment
plan.  ``diff_takeoffs`` compares two pipeline outputs and returns counts per
change class, one section per takeoff:

* ``rooms``: spaces matched by id (added / removed / renamed / area_changed /
  moved_level, with the changed ids listed).
* ``windows`` and ``doors``: per-space counts of the space's openings by
  category, summed into added / removed with the count of spaces whose count
  changed.
* ``symbols``: legend rows mapped to HVAC detector classes (from the set
  reports, when given) summed per class into added / removed.

The report is a plain JSON document (schema ``matchline.takeoff_diff/1``);
nothing here edits either model.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

SCHEMA = "matchline.takeoff_diff/1"

_AREA_REL_EPS = 0.01  # 1% area growth counts as changed, rounding noise does not


def _model(payload: dict) -> dict:
    """Accept either a stage_02 wrapper ({"model": ...}) or a raw model."""
    if isinstance(payload, dict) and isinstance(payload.get("model"), dict):
        return payload["model"]
    return payload or {}


def _norm_name(name) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().upper())


def _opening_counts(space: dict) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for o in space.get("openings") or []:
        cat = str((o or {}).get("category") or "unknown").lower()
        out[cat] = out.get(cat, 0) + 1
    return out


def diff_takeoffs(
    old: dict,
    new: dict,
    old_report: Optional[dict] = None,
    new_report: Optional[dict] = None,
) -> dict:
    """Compare two revisions' takeoffs; returns the change-class counts."""
    old_m, new_m = _model(old), _model(new)
    old_sp: Dict[str, dict] = dict(old_m.get("spaces") or {})
    new_sp: Dict[str, dict] = dict(new_m.get("spaces") or {})

    added: List[str] = []
    removed: List[str] = []
    renamed: List[str] = []
    area_changed: List[str] = []
    moved_level: List[str] = []
    unchanged = 0

    for sid in sorted(set(old_sp) | set(new_sp)):
        a, b = old_sp.get(sid), new_sp.get(sid)
        if a is None:
            added.append(sid)
            continue
        if b is None:
            removed.append(sid)
            continue
        changed = False
        if _norm_name(a.get("name")) != _norm_name(b.get("name")):
            renamed.append(sid)
            changed = True
        try:
            aa, ba = float(a.get("area_m2") or 0.0), float(b.get("area_m2") or 0.0)
        except (TypeError, ValueError):
            aa = ba = 0.0
        bigger = max(abs(aa), abs(ba), 1e-9)
        if abs(ba - aa) / bigger > _AREA_REL_EPS:
            area_changed.append(sid)
            changed = True
        if (a.get("level_id") or "") != (b.get("level_id") or ""):
            moved_level.append(sid)
            changed = True
        # was: `sid not in renamed + area_changed + moved_level`, three list
        # concatenations per space (#1043); the flag is the O(1) equivalent,
        # and a union set built before the loop would go stale as the lists grow
        if not changed:
            unchanged += 1

    def opening_section(cat: str) -> dict:
        old_c: Dict[str, int] = {}
        new_c: Dict[str, int] = {}
        for sid, sp in old_sp.items():
            old_c[sid] = _opening_counts(sp).get(cat, 0)
        for sid, sp in new_sp.items():
            new_c[sid] = _opening_counts(sp).get(cat, 0)
        n_added = n_removed = 0
        spaces_changed = 0
        for sid in set(old_c) | set(new_c):
            d = new_c.get(sid, 0) - old_c.get(sid, 0)
            if d:
                spaces_changed += 1
                n_added += max(d, 0)
                n_removed += max(-d, 0)
        return {"added": n_added, "removed": n_removed, "spaces_changed": spaces_changed}

    windows = opening_section("window")
    doors = opening_section("door")

    symbols: dict = {"provided": False, "added": 0, "removed": 0, "by_class": {}}
    if old_report is not None or new_report is not None:

        def class_counts(report) -> Dict[str, int]:
            out: Dict[str, int] = {}
            for leg in (report or {}).get("hvac_legends") or []:
                for row in leg.get("rows") or []:
                    # real_set.py writes legend rows with the key "class" (#1034);
                    # "cls" stays as the legacy hand-written-fixture spelling
                    cls = row.get("class") if "class" in row else row.get("cls")
                    if cls:
                        out[cls] = out.get(cls, 0) + 1
            return out

        oc, nc = class_counts(old_report), class_counts(new_report)
        symbols["provided"] = True
        for cls in sorted(set(oc) | set(nc)):
            d = nc.get(cls, 0) - oc.get(cls, 0)
            if d:
                symbols["by_class"][cls] = {"added": max(d, 0), "removed": max(-d, 0)}
            symbols["added"] += max(d, 0)
            symbols["removed"] += max(-d, 0)

    return {
        "schema": SCHEMA,
        "rooms": {
            "added": len(added),
            "removed": len(removed),
            "renamed": len(renamed),
            "area_changed": len(area_changed),
            "moved_level": len(moved_level),
            "unchanged": unchanged,
            "added_ids": added,
            "removed_ids": removed,
            "renamed_ids": renamed,
            "area_changed_ids": area_changed,
        },
        "windows": windows,
        "doors": doors,
        "symbols": symbols,
    }
