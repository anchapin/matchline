"""Space opening deduplication (CFG-01, cross-level per #664)."""

from __future__ import annotations

from building_model import BuildingModel, Provenance, SpaceOpening
from opening_identity import (
    CROSS_LEVEL_DEDUP_TOL_M,
    OPENING_DIM_TOL_M,
    group_key,
    level_table,
    opening_z,
    same_opening,
)

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # same-space double-link tolerance (validate/export.py)

__all__ = [
    "CROSS_LEVEL_DEDUP_TOL_M",
    "OPENING_DIM_TOL_M",
    "_dedupe_space_openings",
]


def _conf(op: SpaceOpening) -> float:
    return op.provenance.confidence if op.provenance else 0.0


def _merge(cluster: list, levels: dict) -> SpaceOpening:
    """Collapse one cluster of (level_id, opening) to its primary opening."""

    def extent(entry):
        lid, op = entry
        z = opening_z(op, levels.get(lid, (0, 0.0))[1])
        return (z[1] - z[0]) if z else 0.0

    primary_lid, primary = max(cluster, key=lambda e: (_conf(e[1]), extent(e)))
    records: list[Provenance] = []
    seen: set = set()
    for _, op in [(primary_lid, primary)] + sorted(
        (e for e in cluster if e[1] is not primary), key=lambda e: -_conf(e[1])
    ):
        for p in op.source_provenance or ([op.provenance] if op.provenance else []):
            key = (p.sheet_id, p.revision)
            if key not in seen:
                seen.add(key)
                records.append(p)
    primary.source_provenance = []
    overflow = []
    for p in records:
        try:
            primary.add_source_provenance(p)
        except ValueError:
            overflow.append(p.sheet_id)
    primary.needs_review = any(op.needs_review for _, op in cluster) or bool(overflow)

    # Cropped halves of a tall window: the kept record may not span the union.
    zs = [opening_z(op, levels.get(lid, (0, 0.0))[1]) for lid, op in cluster]
    zs = [z for z in zs if z]
    pz = opening_z(primary, levels.get(primary_lid, (0, 0.0))[1])
    if zs and pz:
        union = max(z[1] for z in zs) - min(z[0] for z in zs)
        if union - (pz[1] - pz[0]) > CROSS_LEVEL_DEDUP_TOL_M:
            primary.needs_review = True
    if primary.provenance is not None:
        sheets = primary.source_sheet_ids + overflow
        primary.history.append(
            Provenance(
                sheet_id=primary.provenance.sheet_id,
                revision=primary.provenance.revision,
                method="window_dedup",
                confidence=_conf(primary),
                note=(
                    f"merged {len(cluster)} sightings of facade='{primary.host_facade}' "
                    f"tag='{primary.tag}' from sheets {sheets}"
                    + (f"; {overflow} not kept (max 2 sources)" if overflow else "")
                ),
            )
        )
    return primary


def _dedupe_space_openings(model: BuildingModel) -> None:
    """Merge duplicate SpaceOpening sightings across spaces and levels (#404, #664).

    Openings are grouped by (facade, tag, category); inside a group two are the
    same physical opening per ``opening_identity.same_opening``: along-wall
    centres within 5 cm, matching width, same or adjacent level, and across
    levels a continuous vertical extent. Each merged opening keeps every source
    sheet in ``source_provenance`` (max 2) with the best-confidence sighting as
    primary. Openings with no along-wall position are never merged: without a
    position there is no evidence two same-tag sightings are one window.
    """
    if not model.spaces:
        return
    levels = level_table(model)
    groups: dict[tuple, list[tuple[str, SpaceOpening]]] = {}
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.category == "skylight":
                continue
            groups.setdefault(group_key(op), []).append((sp.level_id, op))

    drop: set[int] = set()
    for entries in groups.values():
        n = len(entries)
        if n < 2:
            continue
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                (li, oi), (lj, oj) = entries[i], entries[j]
                if same_opening(oi, li, oj, lj, levels):
                    parent[find(i)] = find(j)
        clusters: dict[int, list] = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(entries[i])
        for cluster in clusters.values():
            if len(cluster) < 2:
                continue
            kept = _merge(cluster, levels)
            drop.update(id(op) for _, op in cluster if op is not kept)

    if drop:
        for sp in model.spaces.values():
            sp.openings = [op for op in sp.openings if id(op) not in drop]
