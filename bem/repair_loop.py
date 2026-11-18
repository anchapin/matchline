"""Deterministic repair loop for staged BEM exports (#1407).

Phase 1 of the Solvaix-mined spike (#1406): the publish-nothing export
gate (``bem/export_gate.py``, #1404) *measures* geometric defects and
refuses to publish — it never repairs. This module is the separate
step that sits between the staged export and that gate (off by
default at the library level; on by default at the ``bem-export``
entry points since Alex's 2026-11-17 enablement decision) and
repairs the small set of defect classes whose fix is unique,
deterministic and conservation-preserving (Phase 0 of #1406 classified
every candidate class by hand; the report is
``matchline-validate-repair-loop-phase0-report.md`` in the spike record):

  ``R1``  a shell wound inside out (``shell_volume`` fails) or one face
          flipped (``shell_winding`` fails): reverse the inside-out
          shell, or the unique single-loop reversal that restores
          winding consistency. If the single-loop fix is not unique,
          the defect is not repairable here.
  ``R2``  duplicate envelope surfaces (``duplicate_surfaces`` fails):
          drop the later duplicates, keep the first in document order.
  ``R4``  opening placements on one wall that overlap: shift only the
          openings whose position was *not* stated in the source
          (``s_center_m``) into the wall's free intervals, preserving
          widths and order. Stated placements are source facts and
          never move; a stated-vs-stated overlap is not repairable.

Everything else — degenerate faces (R3, excluded permanently: the
original geometry is unrecoverable), planarity, shell-closure gaps,
conservation and ASHRAE failures (R5) — is detector-only. No repair in
this module touches the model, the IFC4 export, or a source-stated fact.

The loop's inner revalidation is staged-only by design (Alex's
re-scope, #1406 Phase 0b): after a repair, only the geometric checks
(R1/R2) or only the placement-overlap check (R4) re-run. The full
publish gate still runs on the final files before anything publishes;
a repair never publishes anything by itself. A repair that does not
make its check pass — or that makes a different check fail — is
reverted, and the original failure proceeds exactly as it does without
the loop.

Every applied repair is recorded as a provenance fact (see
:class:`RepairRecord`); the gated publish appends the records to the
export manifest. Nothing here is silent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from bem.export_gate import _round_point, _signed_volume, geometric_checks
from safe_xml import safe_xml_parse

GBXML_NS = "http://www.gbxml.org/schema"

#: Geometric checks whose failure the staged-gbXML loop may repair,
#: mapped to the repair class that owns them.
CHECK_TO_CLASS = {
    "shell_volume": "R1",
    "shell_winding": "R1",
    "duplicate_surfaces": "R2",
}

#: The classes this module repairs, in the order they are attempted.
REPAIR_CLASSES = ("R1", "R2")

MAX_ITERATIONS = 2

# Placement geometry, mirroring bem_helpers._place_openings_on_wall:
# openings keep a 0.05 m reveal at each wall end and a 0.1 m gap.
_REVEAL_M = 0.05
_GAP_M = 0.1


@dataclass
class RepairRecord:
    """One applied repair: a provenance fact for the export manifest."""

    repair_class: str  # "R1" | "R2" | "R4"
    check: str  # the check that failed and was repaired
    iteration: int
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    affected: dict = field(default_factory=dict)

    @property
    def method(self) -> str:
        return f"repair:{self.repair_class}"

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "repair_class": self.repair_class,
            "check": self.check,
            "iteration": self.iteration,
            "before": self.before,
            "after": self.after,
            "affected": self.affected,
        }


# ---- gbXML tree helpers ---------------------------------------------------


def _loops(shell) -> list:
    return shell.findall(f"{{{GBXML_NS}}}PolyLoop")


def _points(loop) -> list:
    return loop.findall(f"{{{GBXML_NS}}}CartesianPoint")


def _loop_points(loop) -> list:
    pts = []
    for cp in _points(loop):
        coords = [c.text for c in cp.findall(f"{{{GBXML_NS}}}Coordinate")]
        if len(coords) >= 3 and all(c is not None for c in coords[:3]):
            pts.append((float(coords[0]), float(coords[1]), float(coords[2])))
    return pts


def _reverse_loop(loop) -> None:
    pts = _points(loop)
    for pt in pts:
        loop.remove(pt)
    for pt in reversed(pts):
        loop.append(pt)


def _shell_loops_points(shell) -> list:
    return [_loop_points(lp) for lp in _loops(shell)]


def _winding_consistent(shell) -> bool:
    """True when every directed shell edge is traversed both ways equally."""
    counts: dict[tuple, int] = {}
    for pts in _shell_loops_points(shell):
        n = len(pts)
        for i in range(n):
            a = _round_point(pts[i])
            b = _round_point(pts[(i + 1) % n])
            if a == b:
                continue
            counts[(a, b)] = counts.get((a, b), 0) + 1
    return all(counts.get((b, a), 0) == c for (a, b), c in counts.items())


# ---- R1: winding / volume ---------------------------------------------------


def repair_winding(root) -> dict | None:
    """Repair inside-out shells and uniquely-flipped faces in a gbXML tree.

    Returns a description of what changed, or ``None`` when no shell is
    repairable (no negative-volume shell and no shell with a unique
    single-loop winding fix).
    """
    reversed_shells: list[str] = []
    flipped_loops: list[dict] = []
    for space in root.iter(f"{{{GBXML_NS}}}Space"):
        sid = space.get("id") or "space"
        for shell in space.iter(f"{{{GBXML_NS}}}ClosedShell"):
            if _winding_consistent(shell):
                # A consistently wound shell with a negative volume is
                # wound inside out: reverse every loop. (Consistency is
                # judged first — flipping one large face also makes the
                # signed volume negative, and reversing the whole shell
                # would leave that face flipped.)
                loops_pts = _shell_loops_points(shell)
                if loops_pts and _signed_volume(loops_pts) < 0:
                    for lp in _loops(shell):
                        _reverse_loop(lp)
                    reversed_shells.append(sid)
                continue
            # One flipped face: search the single-loop reversals. The fix
            # is applied only when exactly one loop restores consistency.
            winners = []
            for i in range(len(_loops(shell))):
                trial = copy.deepcopy(shell)
                _reverse_loop(_loops(trial)[i])
                if _winding_consistent(trial):
                    winners.append(i)
            if len(winners) == 1:
                _reverse_loop(_loops(shell)[winners[0]])
                flipped_loops.append({"space": sid, "loop": winners[0]})
    if not reversed_shells and not flipped_loops:
        return None
    return {
        "shells_reversed": reversed_shells,
        "loops_reversed": flipped_loops,
    }


# ---- R2: duplicate surfaces -------------------------------------------------


def _surface_key(surface) -> tuple | None:
    planar = surface.find(f"{{{GBXML_NS}}}PlanarGeometry")
    if planar is None:
        return None
    ploop = planar.find(f"{{{GBXML_NS}}}PolyLoop")
    if ploop is None:
        return None
    pts = _loop_points(ploop)
    if len(pts) < 3:
        return None
    return tuple(sorted(_round_point(p) for p in pts))


def repair_duplicate_surfaces(root) -> dict | None:
    """Drop later duplicate Surfaces (same geometry), keep the first.

    Returns the dropped surface ids, or ``None`` when there are none.
    """
    seen: dict[tuple, str] = {}
    removed: list = []
    # Snapshot first: removing elements while iterating the tree would
    # skip surfaces (the Phase 0 prototype's first R2 bug).
    for surface in list(root.iter(f"{{{GBXML_NS}}}Surface")):
        key = _surface_key(surface)
        if key is None:
            continue
        sid = surface.get("id") or "surface"
        if key in seen:
            removed.append((surface, sid))
        else:
            seen[key] = sid
    if not removed:
        return None
    for surface, _sid in removed:
        parent = surface.getparent()
        if parent is not None:
            parent.remove(surface)
    return {"removed_surface_ids": [sid for _s, sid in removed]}


_REPAIR_FUNCS = {
    "R1": repair_winding,
    "R2": repair_duplicate_surfaces,
}

_CLASS_CHECKS = {
    "R1": ("shell_winding", "shell_volume"),
    "R2": ("duplicate_surfaces",),
}


# ---- the staged-gbXML loop ----------------------------------------------------


def _failed(checks) -> dict:
    return {c.name: c for c in checks if c.status == "fail"}


def repair_staged_gbxml(
    staged_path: str | Path,
    checks: list,
    *,
    model=None,
    max_iterations: int = MAX_ITERATIONS,
) -> tuple[list[RepairRecord], list]:
    """Run the repair loop on a staged gbXML file.

    ``checks`` are the :class:`~bem.export_gate.CheckResult` from
    ``geometric_checks`` on the staged file. At most one class is
    repaired per iteration and at most ``max_iterations`` iterations
    run; after each applied repair only the geometric checks re-run
    (staged-only inner revalidation). A repair whose target check does
    not pass afterwards, or that makes a previously passing check fail,
    is reverted byte-for-byte and the loop stops.

    Returns ``(records, final_checks)``: the applied repairs and the
    geometric checks of the file as left on disk.
    """
    staged_path = Path(staged_path)
    records: list[RepairRecord] = []
    current = list(checks)
    for iteration in range(1, max_iterations + 1):
        failed = _failed(current)
        target_class = next(
            (cls for cls in REPAIR_CLASSES if any(ch in failed for ch in _CLASS_CHECKS[cls])),
            None,
        )
        if target_class is None:
            break
        target_checks = [ch for ch in _CLASS_CHECKS[target_class] if ch in failed]
        snapshot = staged_path.read_bytes()
        tree = safe_xml_parse(staged_path)
        info = _REPAIR_FUNCS[target_class](tree.getroot())
        if info is None:
            break  # the defect is present but has no unique repair
        tree.write(staged_path, encoding="utf-8", xml_declaration=True)
        after_checks = geometric_checks(staged_path, model=model)
        after_failed = _failed(after_checks)
        new_failures = set(after_failed) - set(failed)
        still_failing = [ch for ch in target_checks if ch in after_failed]
        if still_failing or new_failures:
            staged_path.write_bytes(snapshot)
            break
        before = {ch: failed[ch].measurement for ch in target_checks}
        after = {
            ch: next(c.measurement for c in after_checks if c.name == ch) for ch in target_checks
        }
        records.append(
            RepairRecord(
                repair_class=target_class,
                check=target_checks[0],
                iteration=iteration,
                before=before,
                after=after,
                affected=info,
            )
        )
        current = after_checks
    return records, current


# ---- R4: opening placements ---------------------------------------------------


def _placement_tag(placement) -> str:
    tag = placement.get("tag")
    if tag is None:
        tag = getattr(placement.get("unit"), "tag", "?")
    return str(tag)


def _is_stated(placement) -> bool:
    unit = placement.get("unit")
    return unit is not None and getattr(unit, "s_center_m", None) is not None


def placement_overlaps(placements) -> list[dict]:
    """Overlapping placement pairs on one wall (the R4 detector).

    ``placements`` are the dicts ``_place_openings_on_wall`` returns.
    Returns one dict per overlapping pair, with the two openings' tags
    and the overlap length in metres.
    """
    out: list[dict] = []
    ps = sorted(placements, key=lambda p: (p["s0"], p["s1"]))
    for i, a in enumerate(ps):
        for b in ps[i + 1 :]:
            if b["s0"] >= a["s1"]:
                break
            overlap = min(a["s1"], b["s1"]) - max(a["s0"], b["s0"])
            if overlap > 1e-9:
                out.append(
                    {
                        "tag_a": _placement_tag(a),
                        "tag_b": _placement_tag(b),
                        "overlap_m": round(overlap, 6),
                    }
                )
    return out


def repair_placement_overlaps(placements, wall_length_m: float):
    """Shift unstated openings into the wall's free intervals (R4 repair).

    Stated placements (``s_center_m`` on the unit) are source facts and
    are returned unchanged — the same dict objects. Unstated openings
    keep their width and their original order and are re-placed, in
    order, into the free intervals of ``[_REVEAL_M, L - _REVEAL_M]``
    left by the stated placements (each stated interval is expanded by
    the layout gap the placement function itself uses).

    Returns ``(repaired, moves)`` where ``moves`` describes each shifted
    opening, or ``(None, [])`` when the unstated openings do not fit —
    including every stated-vs-stated overlap, where nothing may move.
    """
    if not placement_overlaps(placements):
        return list(placements), []
    stated = [p for p in placements if _is_stated(p)]
    moving = [p for p in placements if not _is_stated(p)]
    if not moving:
        return None, []
    lo, hi = _REVEAL_M, wall_length_m - _REVEAL_M
    blocked = sorted((p["s0"] - _GAP_M, p["s1"] + _GAP_M) for p in stated)
    free: list[list[float]] = []
    cur = lo
    for b0, b1 in blocked:
        if b0 > cur:
            free.append([cur, min(b0, hi)])
        cur = max(cur, b1)
    if cur < hi:
        free.append([cur, hi])
    placed: list = list(stated)
    moves: list[dict] = []
    fi = 0
    cursor = free[0][0] if free else None
    for p in moving:
        width = p["s1"] - p["s0"]
        ok = False
        while fi < len(free):
            start = max(cursor, free[fi][0])
            if start + width <= free[fi][1] + 1e-9:
                new = dict(p)
                new["s0"] = round(start, 6)
                new["s1"] = round(start + width, 6)
                placed.append(new)
                if abs(new["s0"] - p["s0"]) > 1e-9 or abs(new["s1"] - p["s1"]) > 1e-9:
                    moves.append(
                        {
                            "tag": _placement_tag(p),
                            "old": [p["s0"], p["s1"]],
                            "new": [new["s0"], new["s1"]],
                        }
                    )
                cursor = start + width + _GAP_M
                ok = True
                break
            fi += 1
            cursor = free[fi][0] if fi < len(free) else None
        if not ok:
            return None, []
    repaired = sorted(placed, key=lambda p: p["s0"])
    if placement_overlaps(repaired):
        return None, []
    return repaired, moves
