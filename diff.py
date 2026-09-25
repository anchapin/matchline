"""Revision diffing — compare two model revisions at zone/space level.

Compares zones/spaces between an old (baseline) and new revision using
geometry + schedule hash fingerprints. Returns changed/unchanged identifiers
and a first-class diff summary for reviewer consumption.

Pipeline integration
-------------------
On re-run with a new revision, call :func:`diff_models` before extraction.
Use the returned ``changed_zone_ids`` to invalidate and re-extract those zones.
Use ``unchanged_zone_ids`` to carry forward existing facts (with original
provenance).  Anything the differ cannot classify goes to the review queue.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from building_model import BuildingModel, Space, SpaceHVAC, SpaceLighting

# ---------------------------------------------------------------------------
# Hashing primitives
# ---------------------------------------------------------------------------


def _str_fingerprint(value: Any) -> str:
    """Canonical JSON fingerprint for hashing; NaN → 0, None → null."""
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()[:16]


def compute_geometry_hash(space: Space) -> str:
    """Fingerprint of the space's physical geometry (polygon + area + volume)."""
    poly = space.polygon_m
    if poly is None:
        coords_out: list[tuple[float, float]] = []
    elif isinstance(poly, list):
        coords_out = [(round(x, 4), round(y, 4)) for x, y in poly]
    else:
        coords_out = [(round(x, 4), round(y, 4)) for x, y in poly.exterior.coords]

    key = {
        "type": "geometry",
        "area_m2": round(space.area_m2, 4) if space.area_m2 else 0.0,
        "volume_m3": round(space.volume_m3, 4) if space.volume_m3 else 0.0,
        "n_points": len(coords_out),
        "coords": coords_out,
    }
    return _sha1(_str_fingerprint(key))


def compute_schedule_hash(space: Space) -> str:
    """Fingerprint of the space's schedule-related properties.

    Covers lighting, HVAC, and opening inventory — everything that drives
    energy use but is independent of geometry.
    """
    lighting: SpaceLighting = space.lighting or SpaceLighting()
    hvac: SpaceHVAC = space.hvac or SpaceHVAC()

    openings_key: list[dict] = []
    if space.openings:
        for op in space.openings:
            openings_key.append(
                {
                    "cat": op.category,
                    "w": round(op.width_m, 4) if op.width_m else None,
                    "h": round(op.height_m, 4) if op.height_m else None,
                    "sill": round(op.sill_m, 4) if op.sill_m else None,
                }
            )

    key = {
        "type": "schedule",
        "lighting_total_w": round(lighting.total_w, 4) if lighting.total_w else 0.0,
        "lighting_lpd": round(lighting.lpd_w_m2, 4) if lighting.lpd_w_m2 else None,
        "lighting_n_fixtures": len(lighting.fixtures) if lighting.fixtures else 0,
        "hvac_zone_ids": sorted(hvac.zone_ids) if hvac.zone_ids else [],
        "hvac_n_diffusers": len(hvac.diffusers) if hvac.diffusers else 0,
        "hvac_n_sensors": len(hvac.sensors) if hvac.sensors else 0,
        "hvac_n_terminals": len(hvac.terminal_units) if hvac.terminal_units else 0,
        "n_openings": len(openings_key),
        "openings": openings_key,
    }
    return _sha1(_str_fingerprint(key))


def compute_space_fingerprint(space: Space) -> str:
    """Combined geometry + schedule fingerprint for a space."""
    geo = compute_geometry_hash(space)
    sched = compute_schedule_hash(space)
    return _sha1(f"geo={geo}/sched={sched}")


# ---------------------------------------------------------------------------
# Diff result types
# ---------------------------------------------------------------------------


@dataclass
class SpaceDiff:
    space_id: str
    space_name: str | None
    level_id: str | None
    geo_changed: bool
    sched_changed: bool
    reason: str


@dataclass
class DiffSummary:
    old_revision: str
    new_revision: str
    total_spaces: int = 0
    changed_space_ids: list[str] = field(default_factory=list)
    unchanged_space_ids: list[str] = field(default_factory=list)
    unclassified_space_ids: list[str] = field(default_factory=list)
    space_diffs: list[SpaceDiff] = field(default_factory=list)
    unclassified_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "old_revision": self.old_revision,
            "new_revision": self.new_revision,
            "total_spaces": self.total_spaces,
            "changed_space_ids": self.changed_space_ids,
            "unchanged_space_ids": self.unchanged_space_ids,
            "unclassified_space_ids": self.unclassified_space_ids,
            "space_diffs": [
                {
                    "space_id": d.space_id,
                    "space_name": d.space_name,
                    "level_id": d.level_id,
                    "geo_changed": d.geo_changed,
                    "sched_changed": d.sched_changed,
                    "reason": d.reason,
                }
                for d in self.space_diffs
            ],
            "unclassified_reason": self.unclassified_reason,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def diff_models(old: BuildingModel, new: BuildingModel) -> DiffSummary:
    """Compare two model revisions at zone/space level.

    Parameters
    ----------
    old :
        The baseline (previous) revision.
    new :
        The new revision being evaluated.

    Returns
    -------
    DiffSummary
        Contains changed_zone_ids (str), unchanged_zone_ids (str), and
        a first-class diff_summary with per-space change details.

    Notes
    -----
    A space is classified **unchanged** only when both its geometry hash
    and schedule hash match the old revision.  A change in either triggers
    re-extraction.  Spaces present only in the new revision are treated
    as changed (new).  Spaces present only in the old revision are added
    to ``unclassified_space_ids`` (sent to review queue).

    Provenance rule: carried-forward spaces keep their original extraction
    provenance; changed spaces receive a fresh provenance stamped with
    ``new_revision``.
    """
    old_rev = _last_revision(old)
    new_rev = _last_revision(new)

    old_spaces: dict[str, Space] = {s.id: s for s in old.spaces.values()}
    new_spaces: dict[str, Space] = {s.id: s for s in new.spaces.values()}

    all_ids = set(old_spaces.keys()) | set(new_spaces.keys())

    changed_ids: list[str] = []
    unchanged_ids: list[str] = []
    unclassified_ids: list[str] = []
    space_diffs: list[SpaceDiff] = []

    for sid in sorted(all_ids):
        old_sp = old_spaces.get(sid)
        new_sp = new_spaces.get(sid)

        if old_sp is None and new_sp is not None:
            # Brand-new space
            changed_ids.append(sid)
            space_diffs.append(
                SpaceDiff(
                    space_id=sid,
                    space_name=new_sp.name,
                    level_id=new_sp.level_id,
                    geo_changed=True,
                    sched_changed=True,
                    reason="new_space",
                )
            )
        elif new_sp is None and old_sp is not None:
            # Removed space — cannot classify, send to review
            unclassified_ids.append(sid)
            space_diffs.append(
                SpaceDiff(
                    space_id=sid,
                    space_name=old_sp.name,
                    level_id=old_sp.level_id,
                    geo_changed=True,
                    sched_changed=True,
                    reason="removed_in_new_revision",
                )
            )
        else:
            geo_h = compute_geometry_hash(new_sp)
            sched_h = compute_schedule_hash(new_sp)
            old_geo_h = compute_geometry_hash(old_sp)
            old_sched_h = compute_schedule_hash(old_sp)

            geo_changed = geo_h != old_geo_h
            sched_changed = sched_h != old_sched_h

            if geo_changed or sched_changed:
                changed_ids.append(sid)
                reasons: list[str] = []
                if geo_changed:
                    reasons.append("geometry")
                if sched_changed:
                    reasons.append("schedule")
                space_diffs.append(
                    SpaceDiff(
                        space_id=sid,
                        space_name=new_sp.name,
                        level_id=new_sp.level_id,
                        geo_changed=geo_changed,
                        sched_changed=sched_changed,
                        reason="+".join(reasons),
                    )
                )
            else:
                unchanged_ids.append(sid)
                space_diffs.append(
                    SpaceDiff(
                        space_id=sid,
                        space_name=new_sp.name,
                        level_id=new_sp.level_id,
                        geo_changed=False,
                        sched_changed=False,
                        reason="identical",
                    )
                )

    summary = DiffSummary(
        old_revision=old_rev or "unknown",
        new_revision=new_rev or "unknown",
        total_spaces=len(all_ids),
        changed_space_ids=changed_ids,
        unchanged_space_ids=unchanged_ids,
        unclassified_space_ids=unclassified_ids,
        space_diffs=space_diffs,
        unclassified_reason=("removed_spaces_review_required" if unclassified_ids else ""),
    )
    return summary


def _last_revision(model: BuildingModel) -> str:
    if not model.revision_log:
        return "r0"
    entry = model.revision_log[-1]
    return f"rev{entry.revision}"


def build_change_map(diff: DiffSummary) -> dict[str, str]:
    """Return a mapping of space_id → change_class for all spaces in *diff*.

    change_class values
    -------------------
    - ``unchanged`` : geometry + schedule identical to baseline → carry forward
    - ``changed``   : geometry or schedule changed → re-extract
    - ``new``       : appears only in the new revision → re-extract
    - ``removed``   : present in old but not new → review queue
    - ``unknown``   : space not present in either model → review queue

    Provenance rule
    ---------------
    ``unchanged`` spaces keep their original extraction provenance (recorded in
    ``core_provenance`` / ``history``).  ``changed`` / ``new`` spaces receive a
    fresh provenance stamp with the new revision number.
    """
    result: dict[str, str] = {}
    for d in diff.space_diffs:
        if d.reason == "identical":
            result[d.space_id] = "unchanged"
        elif d.reason == "new_space":
            result[d.space_id] = "new"
        elif d.reason in ("geometry", "schedule", "geometry+schedule"):
            result[d.space_id] = "changed"
        elif d.reason in ("removed_in_new_revision",):
            result[d.space_id] = "removed"
        else:
            result[d.space_id] = "unknown"
    for sid in diff.unclassified_space_ids:
        result[sid] = "removed"
    return result
