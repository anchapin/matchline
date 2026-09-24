from __future__ import annotations

from building_model import (
    BuildingModel,
    Provenance,
    SpaceOpening,
)
from link._intervals import _intervals_overlap

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Space opening deduplication utilities."""


def _dedupe_space_openings(model: BuildingModel) -> None:
    """Merge duplicate SpaceOpening entries in each space (Issue #1).

    When two elevation runs of the same facade are linked separately, the
    same physical window produces two SpaceOpening entries (one per run).
    Deduplication is by tag (primary) + geometric proximity (fallback for
    untagged or conflicting entries): entries whose host_interval_m
    overlaps by >= OPENING_DEDUP_TOL_M are merged to one.

    Merged entries carry a compound sheet_id (sheet1+sheet2) and the
    higher of the two confidences.
    """
    for sp in model.spaces.values():
        if not sp.openings:
            continue
        # Group by tag (non-empty tags: primary dedup key)
        by_tag: dict[str, list[tuple[int, SpaceOpening]]] = {}
        untagged: list[tuple[int, SpaceOpening]] = []
        for idx, op in enumerate(sp.openings):
            if op.tag:
                by_tag.setdefault(op.tag, []).append((idx, op))
            else:
                untagged.append((idx, op))

        kept = []  # indices to KEEP
        removed = set()  # indices to DROP

        # Tag-based merge: for each tag with 2+ entries, keep one
        for tag, entries in by_tag.items():
            if len(entries) < 2:
                kept.append(entries[0][0])
                continue
            # Find the entry with highest confidence; use its provenance
            best_idx, best_op = max(
                entries, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
            )
            sheets = sorted(
                {
                    o.provenance.sheet_id
                    for _, o in entries
                    if o.provenance and o.provenance.sheet_id
                }
            )
            best_op = SpaceOpening(
                id=best_op.id,
                tag=tag,
                category=best_op.category,
                width_m=best_op.width_m,
                height_m=best_op.height_m,
                sill_m=best_op.sill_m,
                head_m=best_op.head_m,
                host_facade=best_op.host_facade,
                host_interval_m=best_op.host_interval_m,
                s_center_m=best_op.s_center_m,
                area_m2=best_op.area_m2,
                provenance=best_op.provenance,
                needs_review=any(o.needs_review for _, o in entries),
            )
            best_op.provenance = Provenance(
                sheet_id="+".join(sheets),
                revision=best_op.provenance.revision if best_op.provenance else 1,
                method="window_dedup",
                confidence=max(
                    (o.provenance.confidence for _, o in entries if o.provenance), default=0.9
                ),
                note=f"deduplicated {len(entries)} entries for tag '{tag}'; sheets: {sheets}",
            )
            kept.append(best_idx)
            sp.openings[best_idx] = best_op
            removed.update(idx for idx, _ in entries if idx != best_idx)

        # Geometric dedup for untagged entries: interval overlap merge
        # Process in along-wall order (by s_center_m)
        untagged_sorted = sorted(untagged, key=lambda x: x[1].s_center_m or 0.0)
        used = set()
        for i, (idx_i, op_i) in enumerate(untagged_sorted):
            if idx_i in removed or i in used:
                continue
            group = [(idx_i, op_i)]
            for j, (idx_j, op_j) in enumerate(untagged_sorted[i + 1 :], i + 1):
                if idx_j in removed or j in used:
                    continue
                if not _intervals_overlap(
                    op_i.host_interval_m, op_j.host_interval_m, OPENING_DEDUP_TOL_M
                ):
                    break
                group.append((idx_j, op_j))
                used.add(j)
            # Keep best by confidence
            best_idx2, best_op2 = max(
                group, key=lambda x: x[1].provenance.confidence if x[1].provenance else 0
            )
            sheets2 = sorted(
                {o.provenance.sheet_id for _, o in group if o.provenance and o.provenance.sheet_id}
            )
            best_op2 = SpaceOpening(
                id=best_op2.id,
                tag="",
                category=best_op2.category,
                width_m=best_op2.width_m,
                height_m=best_op2.height_m,
                sill_m=best_op2.sill_m,
                head_m=best_op2.head_m,
                host_facade=best_op2.host_facade,
                host_interval_m=best_op2.host_interval_m,
                s_center_m=best_op2.s_center_m,
                area_m2=best_op2.area_m2,
                provenance=Provenance(
                    sheet_id="+".join(sheets2),
                    revision=best_op2.provenance.revision if best_op2.provenance else 1,
                    method="window_dedup",
                    confidence=max(
                        (o.provenance.confidence for _, o in group if o.provenance), default=0.9
                    ),
                    note=f"geometric dedup {len(group)} untagged entries",
                ),
                needs_review=any(o.needs_review for _, o in group),
            )
            kept.append(best_idx2)
            sp.openings[best_idx2] = best_op2
            removed.update(idx for idx, _ in group if idx != best_idx2)

        # Rebuild openings list preserving order
        sp.openings = [op for k, op in enumerate(sp.openings) if k not in removed]
