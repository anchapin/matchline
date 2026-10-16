"""Takeoff diff between two drawing-set revisions (#993).

A revision changes what the set takes off: rooms appear, disappear, rename or
grow; windows and doors move with the walls; and the mechanical symbol
takeoff (legend rows mapped to detector classes) shifts with the equipment
plan.  ``diff_takeoffs`` compares two pipeline outputs and returns counts per
change class, one section per takeoff:

* ``rooms``: spaces matched by id (added / removed / renamed / area_changed /
  moved_level, with the changed ids listed).  An id that disappears while a
  similar space appears (same level, area, name tokens, and nearby centroid)
  is reported as ``reidentified`` with its old/new id pair instead of one
  added and one removed (#1046).
* ``windows`` and ``doors``: per-space counts of the space's openings by
  category, summed into added / removed with the count of spaces whose count
  changed.  Openings present in both revisions with identity evidence (a
  unique tag, or a >=50% bbox overlap on a sheet both revisions share) are
  reported as ``moved`` instead of one added and one removed (#1045).
* ``symbols``: legend rows mapped to HVAC detector classes (from the set
  reports, when given) summed per class into added / removed.

The report is a plain JSON document (schema ``matchline.takeoff_diff/1``);
nothing here edits either model.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

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


def _bbox_iou(a: List[float], b: List[float]) -> float:
    """Intersection-over-union of two [x0, y0, x1, y1] boxes."""
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _opening_inventory(spaces: Dict[str, dict], cat: str) -> List[dict]:
    """One entry per opening of ``cat``, with its cross-revision identity
    evidence: the tag and the per-sheet provenance bboxes (#1045)."""
    inv: List[dict] = []
    for sid, sp in spaces.items():
        for o in sp.get("openings") or []:
            o = o or {}
            if str(o.get("category") or "").lower() != cat:
                continue
            bboxes: List[Tuple[str, List[float]]] = []
            for p in [o.get("provenance")] + list(o.get("source_provenance") or []):
                if not isinstance(p, dict) or not p.get("sheet_id") or not p.get("bbox"):
                    continue
                try:
                    box = [float(x) for x in p["bbox"]]
                except (TypeError, ValueError):
                    continue
                if len(box) == 4:
                    bboxes.append((str(p["sheet_id"]), box))
            inv.append(
                {
                    "space": sid,
                    "tag": str(o.get("tag") or "").strip().upper(),
                    "bboxes": bboxes,
                }
            )
    return inv


def _moved_openings(old_inv: List[dict], new_inv: List[dict]) -> List[Tuple[int, int]]:
    """Pairs of openings present in both revisions, strongest evidence first
    (#1045): a tag that names exactly one opening per revision, then a bbox
    overlap (IoU >= 0.5) on a sheet both revisions share.  Openings without
    shared evidence never pair: an untagged opening with no comparable
    geometry stays added/removed rather than being guessed into a move."""
    pairs: List[Tuple[int, int]] = []
    used_old: set = set()
    used_new: set = set()
    old_by_tag: Dict[str, List[int]] = {}
    new_by_tag: Dict[str, List[int]] = {}
    for i, o in enumerate(old_inv):
        if o["tag"]:
            old_by_tag.setdefault(o["tag"], []).append(i)
    for j, o in enumerate(new_inv):
        if o["tag"]:
            new_by_tag.setdefault(o["tag"], []).append(j)
    for tag, oi in old_by_tag.items():
        nj = new_by_tag.get(tag)
        if nj and len(oi) == 1 and len(nj) == 1:
            pairs.append((oi[0], nj[0]))
            used_old.add(oi[0])
            used_new.add(nj[0])
    for i, o in enumerate(old_inv):
        if i in used_old or not o["bboxes"]:
            continue
        best, best_j = 0.0, None
        for j, n in enumerate(new_inv):
            if j in used_new or not n["bboxes"]:
                continue
            for s_old, b_old in o["bboxes"]:
                for s_new, b_new in n["bboxes"]:
                    if s_old != s_new:
                        continue
                    v = _bbox_iou(b_old, b_new)
                    if v > best:
                        best, best_j = v, j
        if best_j is not None and best >= 0.5:
            pairs.append((i, best_j))
            used_old.add(i)
            used_new.add(best_j)
    return pairs


def _name_tokens(name) -> set:
    return set(_norm_name(name).split())


def _polygon_centroid(points) -> Optional[List[float]]:
    """Shoelace centroid of [[x, y], ...]; None for degenerate polygons."""
    pts = []
    for p in points or []:
        try:
            pts.append((float(p[0]), float(p[1])))
        except (TypeError, ValueError, IndexError):
            continue
    if len(pts) < 3:
        return None
    a = cx = cy = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-12:
        return None
    return [cx / (3.0 * a), cy / (3.0 * a)]


def _space_fingerprint(sp: dict) -> dict:
    """Cross-revision identity evidence for one space (#1046)."""
    area = 0.0
    try:
        area = float(sp.get("area_m2") or 0.0)
    except (TypeError, ValueError):
        pass
    return {
        "name": _norm_name(sp.get("name")),
        "tokens": _name_tokens(sp.get("name")),
        "area": area,
        "level": str(sp.get("level_id") or ""),
        "centroid": _polygon_centroid(sp.get("polygon_m")),
    }


def _reidentify_spaces(
    old_sp: Dict[str, dict], new_sp: Dict[str, dict], removed: List[str], added: List[str]
) -> List[Tuple[str, str]]:
    """Pair spaces whose id changed between revisions (#1046).

    Two spaces pair only on same level and area within the same relative
    tolerance the per-id diff already uses, plus identity evidence: identical
    normalized name, or name tokens overlapping >= 50% with a centroid close
    enough to be the same room (distance <= max(1 m, 15% of sqrt(area)).
    Ambiguous names (the same normalized name on several removed or several
    added spaces at one level) never pair; greedy one-to-one otherwise."""
    if not removed or not added:
        return []
    old_fp = {sid: _space_fingerprint(old_sp[sid]) for sid in removed}
    new_fp = {sid: _space_fingerprint(new_sp[sid]) for sid in added}

    name_old: Dict[Tuple[str, str], List[str]] = {}
    name_new: Dict[Tuple[str, str], List[str]] = {}
    for sid, fp in old_fp.items():
        name_old.setdefault((fp["name"], fp["level"]), []).append(sid)
    for sid, fp in new_fp.items():
        name_new.setdefault((fp["name"], fp["level"]), []).append(sid)

    def _centroid_ok(fa: dict, fb: dict) -> bool:
        ca, cb = fa["centroid"], fb["centroid"]
        if ca is None or cb is None:
            return True  # no geometry on a side: name and area carry the pair
        scale = max(1.0, 0.15 * ((max(fa["area"], fb["area"], 0.0)) ** 0.5))
        return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5 <= scale

    def _area_ok(fa: dict, fb: dict) -> bool:
        bigger = max(abs(fa["area"]), abs(fb["area"]), 1e-9)
        return abs(fb["area"] - fa["area"]) / bigger <= _AREA_REL_EPS

    candidates: List[Tuple[float, str, str]] = []
    for oid, fa in old_fp.items():
        for nid, fb in new_fp.items():
            if fa["level"] != fb["level"] or not _area_ok(fa, fb):
                continue
            if fa["name"] and fa["name"] == fb["name"]:
                if (
                    len(name_old[(fa["name"], fa["level"])]) == 1
                    and len(name_new[(fb["name"], fb["level"])]) == 1
                ):
                    candidates.append((3.0, oid, nid))
                    continue
            inter = len(fa["tokens"] & fb["tokens"])
            union = len(fa["tokens"] | fb["tokens"])
            if union and inter / union >= 0.5 and fa["centroid"] and fb["centroid"]:
                if _centroid_ok(fa, fb):
                    candidates.append((2.0 + inter / union, oid, nid))

    pairs: List[Tuple[str, str]] = []
    used_old: set = set()
    used_new: set = set()
    for _, oid, nid in sorted(candidates, reverse=True):
        if oid in used_old or nid in used_new:
            continue
        pairs.append((oid, nid))
        used_old.add(oid)
        used_new.add(nid)
    return pairs


def review_items(diff: dict) -> List[dict]:
    """Review items from a takeoff-diff report (#1048).

    A revision that removes rooms or flips opening counts is exactly what a
    reviewer should look at first, so the diff becomes queue items in the
    same currency as the pipeline's own flags: removed rooms first (urgency
    3), then opening-count changes (urgency 2 when something was removed),
    then added rooms and symbol-count shifts.  Reidentified spaces (#1046)
    produce no item: the identity is resolved, not open.  Ids are stable, so
    re-running the same diff feeds no duplicates (see
    ``review_report.attach_takeoff_diff_items``).
    """
    rooms = diff.get("rooms") or {}
    out: List[dict] = []

    def item(rid, kind, target, description, confidence, urgency) -> dict:
        return {
            "id": rid,
            "kind": kind,
            "target": dict(target),
            "description": description,
            "confidence": confidence,
            "urgency": urgency,
            "provenance": {
                "sheet_id": "",
                "revision": 0,
                "method": "takeoff_diff",
                "confidence": confidence,
            },
        }

    for sid in rooms.get("removed_ids") or []:
        out.append(
            item(
                f"takeoff-diff-room-removed-{sid}",
                "revision_room_removed",
                {"kind": "space", "id": sid},
                f"room {sid} was removed in the newer revision",
                0.7,
                3,
            )
        )
    for cat, key in (("window", "windows"), ("door", "doors")):
        sec = diff.get(key) or {}
        if not (sec.get("added") or sec.get("removed")):
            continue
        parts = []
        if sec.get("added"):
            parts.append(f"{sec['added']} added")
        if sec.get("removed"):
            parts.append(f"{sec['removed']} removed")
        if sec.get("moved"):
            parts.append(f"{sec['moved']} moved")
        out.append(
            item(
                f"takeoff-diff-{key}",
                "revision_opening_count",
                {"kind": "takeoff", "id": key},
                f"{cat} counts changed: " + ", ".join(parts),
                0.7,
                2 if sec.get("removed") else 1,
            )
        )
    for sid in rooms.get("added_ids") or []:
        out.append(
            item(
                f"takeoff-diff-room-added-{sid}",
                "revision_room_added",
                {"kind": "space", "id": sid},
                f"room {sid} is new in the newer revision",
                0.7,
                1,
            )
        )
    sym = diff.get("symbols") or {}
    if sym.get("provided") and (sym.get("added") or sym.get("removed")):
        out.append(
            item(
                "takeoff-diff-symbols",
                "revision_symbol_count",
                {"kind": "takeoff", "id": "symbols"},
                (
                    "mechanical symbols changed: "
                    f"{sym.get('added', 0)} added, {sym.get('removed', 0)} removed"
                ),
                0.6,
                1,
            )
        )
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

    reidentified_pairs = _reidentify_spaces(old_sp, new_sp, removed, added)
    for oid, nid in reidentified_pairs:
        removed.remove(oid)
        added.remove(nid)
    reidentified = [{"old_id": oid, "new_id": nid} for oid, nid in reidentified_pairs]

    def opening_section(cat: str) -> dict:
        old_c: Dict[str, int] = {}
        new_c: Dict[str, int] = {}
        for sid, sp in old_sp.items():
            old_c[sid] = _opening_counts(sp).get(cat, 0)
        for sid, sp in new_sp.items():
            new_c[sid] = _opening_counts(sp).get(cat, 0)
        # Openings matched across revisions (#1045) are not churn: a window
        # that moved from space A to space B used to report one added and one
        # removed.  Matched pairs are taken out of the per-space deltas before
        # they are summed, so added/removed stay the genuine changes and the
        # relocation is its own class.  Without identity evidence the numbers
        # reduce exactly to the previous net counting.
        old_inv = _opening_inventory(old_sp, cat)
        new_inv = _opening_inventory(new_sp, cat)
        moved_in: Dict[str, int] = {}
        moved_out: Dict[str, int] = {}
        n_moved = 0
        for i, j in _moved_openings(old_inv, new_inv):
            s_old, s_new = old_inv[i]["space"], new_inv[j]["space"]
            if s_old == s_new:
                continue
            n_moved += 1
            moved_out[s_old] = moved_out.get(s_old, 0) + 1
            moved_in[s_new] = moved_in.get(s_new, 0) + 1
        n_added = n_removed = 0
        spaces_changed = 0
        for sid in set(old_c) | set(new_c):
            d = new_c.get(sid, 0) - old_c.get(sid, 0)
            genuine = d - moved_in.get(sid, 0) + moved_out.get(sid, 0)
            if d or moved_in.get(sid, 0) or moved_out.get(sid, 0):
                spaces_changed += 1
            n_added += max(genuine, 0)
            n_removed += max(-genuine, 0)
        return {
            "added": n_added,
            "removed": n_removed,
            "moved": n_moved,
            "spaces_changed": spaces_changed,
        }

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
            "reidentified": len(reidentified),
            "reidentified_pairs": reidentified,
            "added_ids": added,
            "removed_ids": removed,
            "renamed_ids": renamed,
            "area_changed_ids": area_changed,
        },
        "windows": windows,
        "doors": doors,
        "symbols": symbols,
    }
