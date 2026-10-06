"""Atria: one space open through several storeys (#640).

An atrium is a space whose stated height (``Space.height_m``, from the IFC
space body or ``Qto_SpaceBaseQuantities.Height``) reaches past the next
storey's elevation by more than ``ATRIUM_TOL_M``. It stays one space on its
base level, with its full height and volume; the levels it opens through get
no floor or ceiling inside its footprint, and the roof over it is its own.

Nothing is inferred from names or from a hole in a floor plate: only a stated
height opens a space through a storey. When a space on a level the atrium
would pass through sits over its footprint, the source contradicts itself:
the atrium is stopped below that level and the conflict goes to review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from building_model import Provenance

ATRIUM_TOL_M = 0.3  # a stated height must clear the next storey by this much
MIN_OVERLAP_M2 = 1e-6
REVIEW_CONF = 0.5


@dataclass
class AtriaResult:
    spans: Dict[str, List[str]] = field(default_factory=dict)  # sid -> levels opened through
    top_z: Dict[str, float] = field(default_factory=dict)  # sid -> height of its top
    findings: List[str] = field(default_factory=list)


def _prov(note: str) -> Provenance:
    return Provenance(
        sheet_id="atria",
        revision=0,
        method="atria:stated_height",
        confidence=REVIEW_CONF,
        note=note,
    )


def find_atria(model, flag: bool = True) -> AtriaResult:
    """Spaces open through the storeys above their own, from stated heights."""
    from interstory import _poly

    levels = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    res = AtriaResult()
    if len(levels) < 2:
        return res
    level_ids = [lv.id for lv in levels]
    rows: Dict[str, list] = {lv.id: [] for lv in levels}
    for sid in sorted(model.spaces):
        sp = model.spaces[sid]
        if sp.level_id in rows:
            g = _poly(sp)
            if g is not None:
                rows[sp.level_id].append((sid, g))
    for sid in sorted(model.spaces):
        sp = model.spaces[sid]
        h = getattr(sp, "height_m", None)
        if not h or sp.level_id not in rows:
            continue
        base = levels[level_ids.index(sp.level_id)]
        top = base.elevation_z_m + float(h)
        above = [lv for lv in levels if base.elevation_z_m < lv.elevation_z_m < top - ATRIUM_TOL_M]
        if not above:
            continue
        g = _poly(sp)
        if g is None:
            continue
        keep: List[str] = []
        end_z = top
        for lv in above:
            over = [o for o, og in rows[lv.id] if og.intersection(g).area > MIN_OVERLAP_M2]
            if over:
                text = (
                    f"{sid} is stated {float(h):.2f} m tall, through level {lv.id}, but "
                    f"{', '.join(over)} on {lv.id} sits over it; atrium stopped below {lv.id}"
                )
                res.findings.append(text)
                if flag:
                    model.flag_for_review("atrium", text, REVIEW_CONF, _prov(text))
                end_z = lv.elevation_z_m
                break
            keep.append(lv.id)
        if keep:
            res.spans[sid] = keep
            res.top_z[sid] = end_z
    return res
