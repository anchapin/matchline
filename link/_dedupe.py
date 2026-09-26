from __future__ import annotations

from building_model import (
    BuildingModel,
    Provenance,
    SpaceOpening,
)
from link._intervals import _intervals_overlap

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup
_UNTAGGED = "@untagged"

"""Space opening deduplication utilities."""


def _untagged_dedupe(entries: list[tuple[str, SpaceOpening]], kept_ids: set[str]) -> None:
    """Geometric dedup for untagged entries: interval overlap merge.

    Process in along-wall order (by s_center_m); entries whose host_interval_m
    overlaps by >= OPENING_DEDUP_TOL_M are merged to one.
    """
    if not entries:
        return
    # Sort by s_center_m
    sorted_entries = sorted(entries, key=lambda x: x[1].s_center_m or 0.0)
    used = set()
    for i, (sp_id_i, op_i) in enumerate(sorted_entries):
        if i in used:
            continue
        group = [(sp_id_i, op_i)]
        group_indices = {i}
        for j, (sp_id_j, op_j) in enumerate(sorted_entries[i + 1 :], i + 1):
            if j in used:
                continue
            if not _intervals_overlap(
                op_i.host_interval_m, op_j.host_interval_m, OPENING_DEDUP_TOL_M
            ):
                break
            group.append((sp_id_j, op_j))
            group_indices.add(j)
        best_sp_id, best_op = max(
            group, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
        )
        sheets = sorted(
            {o.provenance.sheet_id for _, o in group if o.provenance and o.provenance.sheet_id}
        )
        best_op.provenance = Provenance(
            sheet_id="+".join(sheets),
            revision=best_op.provenance.revision if best_op.provenance else 1,
            method="window_dedup",
            confidence=max(
                (o.provenance.confidence for _, o in group if o.provenance), default=0.9
            ),
            note=f"geometric dedup {len(group)} untagged entries for facade='{op_i.host_facade}'",
        )
        best_op.needs_review = any(o.needs_review for _, o in group)
        kept_ids.add(best_op.id)
        used.update(group_indices)


def _dedupe_space_openings(model: BuildingModel) -> None:
    """Merge duplicate SpaceOpening entries across ALL spaces (Issue #404).

    When two elevation runs of the same facade are linked separately (even
    across different building levels), the same physical window produces two
    SpaceOpening entries (one per run). Deduplication is by (facade, tag)
    across all spaces (not just per-space) + geometric proximity (fallback
    for untagged or conflicting entries): entries whose host_interval_m
    overlaps by >= OPENING_DEDUP_TOL_M are merged to one.

    Merged entries carry a compound sheet_id (sheet1+sheet2) and the
    higher of the two confidences.
    """
    if not model.spaces:
        return

    # Collect all openings grouped by (host_facade, tag) across ALL spaces
    facade_tag_groups: dict[tuple[str, str], list[tuple[str, SpaceOpening]]] = {}
    for sp in model.spaces.values():
        for op in sp.openings:
            key = (op.host_facade, op.tag or _UNTAGGED)
            facade_tag_groups.setdefault(key, []).append((sp.id, op))

    # Determine which opening IDs to keep per facade+tag group
    kept_op_ids: set[str] = set()
    for (facade, tag), entries in facade_tag_groups.items():
        if tag == _UNTAGGED:
            _untagged_dedupe(entries, kept_op_ids)
        else:
            best_sp_id, best_op = max(
                entries, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
            )
            best_op.provenance = Provenance(
                sheet_id="+".join(
                    sorted(
                        {
                            o.provenance.sheet_id
                            for _, o in entries
                            if o.provenance and o.provenance.sheet_id
                        }
                    )
                ),
                revision=getattr(best_op.provenance, "revision", 1),
                method="window_dedup",
                confidence=max(
                    (o.provenance.confidence for _, o in entries if o.provenance), default=0.9
                ),
                note=f"deduplicated {len(entries)} entries for facade='{facade}' tag='{tag}'; sheets: {sorted({o.provenance.sheet_id for _, o in entries if o.provenance and o.provenance.sheet_id})}",
            )
            best_op.needs_review = any(o.needs_review for _, o in entries)
            kept_op_ids.add(best_op.id)

    # Update each space's openings to only kept entries
    for sp in model.spaces.values():
        sp.openings = [op for op in sp.openings if op.id in kept_op_ids]
