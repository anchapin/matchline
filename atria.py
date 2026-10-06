"""Atria: one space open through several storeys (#640).

An atrium is a space whose stated height (``Space.height_m``, from the IFC
space body or ``Qto_SpaceBaseQuantities.Height``) reaches past the next
storey's elevation by more than ``ATRIUM_TOL_M``. It stays one space on its
base level, with its full height and volume; the levels it opens through get
no floor or ceiling inside its footprint, and the roof over it is its own.

Nothing is inferred from names. Two things open a space through a storey:

* a stated height past the next storey (``find_atria``);
* a stack of rooms with the same footprint on consecutive levels whose
  shared floor is an unfilled slab opening (``merge_atrium_stacks``, run on
  IFC import): the rooms above fold into the bottom one, which then carries
  the stack's full height.

When a space on a level the atrium would pass through sits over its
footprint, or a slab opening only partly lines up with the rooms around it,
the source contradicts itself or leaves it open: nothing is merged or opened
and the case goes to review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from shapely.geometry import Polygon
from shapely.ops import unary_union

from building_model import Provenance

ATRIUM_TOL_M = 0.3  # a stated height must clear the next storey by this much
STACK_IOU = 0.8  # rooms above and below must share this much of their footprints
VOID_COVER = 0.8  # the slab opening must cover this much of their overlap
VOID_Z_TOL_M = 0.5  # an opening belongs to the floor of the level nearest its mid-height
STACKABLE = ("room", "unassigned")  # shafts, cores and closets stack on their own rules
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


def _void_floors(levels, voids):
    """{level id: [opening polygons]} for the floor at the bottom of each level."""
    out: Dict[str, list] = {}
    for rect, z0, z1 in voids:
        mid = (float(z0) + float(z1)) / 2
        lv = min(levels[1:], key=lambda lv: (abs(lv.elevation_z_m - mid), lv.id), default=None)
        if lv is None or abs(lv.elevation_z_m - mid) > VOID_Z_TOL_M:
            continue
        g = Polygon(rect).buffer(0)
        if g.area > MIN_OVERLAP_M2:
            out.setdefault(lv.id, []).append(g)
    return out


def _fold(model, base, src, note: str) -> None:
    """Fold ``src`` (a room on an upper level) into the atrium ``base``."""
    base.openings.extend(src.openings)
    L, sl = base.lighting, src.lighting
    L.fixtures.extend(sl.fixtures)
    L.total_w = (L.total_w or 0.0) + (sl.total_w or 0.0)
    L.unmatched_tags.extend(t for t in sl.unmatched_tags if t not in L.unmatched_tags)
    if L.total_w and base.area_m2:
        L.lpd_w_m2 = L.total_w / base.area_m2
        L.lpd_w_ft2 = L.lpd_w_m2 / 10.7639
    H, sh = base.hvac, src.hvac
    H.zone_ids.extend(z for z in sh.zone_ids if z not in H.zone_ids)
    H.diffusers.extend(sh.diffusers)
    H.sensors.extend(sh.sensors)
    H.terminal_units.extend(sh.terminal_units)
    for z in model.zones.values():
        if src.id in z.space_ids:
            z.space_ids = list(dict.fromkeys(base.id if s == src.id else s for s in z.space_ids))
    for seg in model.envelope:
        if getattr(seg, "space_id", "") == src.id:
            seg.space_id = base.id
    base.merged_from.append(src.id)
    base.merged_from.extend(src.merged_from)
    prov = src.core_provenance
    base.history.append(
        Provenance(
            sheet_id=prov.sheet_id if prov else "",
            revision=prov.revision if prov else 0,
            method="atria:slab_opening_stack",
            confidence=0.9,
            note=note,
        )
    )
    del model.spaces[src.id]


def merge_atrium_stacks(model, voids, sheet: str = "", revision: int = 0) -> int:
    """Fold rooms stacked over unfilled slab openings into one atrium.

    ``voids`` is ``[(plan rectangle, z min, z max)]`` of unfilled openings in
    slabs, canonical frame, metres. A room on one level and a room on the
    next one up are one atrium when their footprints overlap by at least
    ``STACK_IOU`` (intersection over union) and openings in the floor between
    them cover at least ``VOID_COVER`` of that overlap. Chains fold into the
    bottom room, which keeps its id, name and footprint and gets the stack's
    full height; volume is the members' sum when all are known. An opening
    that touches a room pair without meeting both tests, or a room that would
    pair with more than one room, is left alone and sent to review.
    Returns the number of rooms folded.
    """
    levels = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    if len(levels) < 2 or not voids:
        return 0
    floors = _void_floors(levels, voids)
    if not floors:
        return 0

    def rows(lv):
        out = []
        for sid in sorted(model.spaces):
            sp = model.spaces[sid]
            if sp.level_id == lv.id and sp.poly_type in STACKABLE and len(sp.polygon_m) >= 3:
                g = Polygon([(float(p[0]), float(p[1])) for p in sp.polygon_m]).buffer(0)
                if g.area > MIN_OVERLAP_M2:
                    out.append((sid, g))
        return out

    def review(text, sid):
        prov = model.spaces[sid].core_provenance
        model.flag_for_review(
            "atrium",
            text,
            REVIEW_CONF,
            Provenance(
                sheet_id=prov.sheet_id if prov else sheet,
                revision=prov.revision if prov else revision,
                method="atria:slab_opening_stack",
                confidence=REVIEW_CONF,
                note=text,
            ),
        )

    root: Dict[str, str] = {}  # upper room -> bottom room of its stack
    pairs = []
    for lo, up in zip(levels, levels[1:]):
        holes = floors.get(up.id)
        if not holes:
            continue
        hole = unary_union(holes)
        cand: Dict[str, list] = {}
        for usid, ug in rows(up):
            for lsid, lg in rows(lo):
                ov = lg.intersection(ug)
                if ov.area <= MIN_OVERLAP_M2:
                    continue
                cut = hole.intersection(ov).area
                if cut <= MIN_OVERLAP_M2:
                    continue
                iou = ov.area / lg.union(ug).area
                cover = cut / ov.area
                if iou >= STACK_IOU and cover >= VOID_COVER:
                    cand.setdefault(usid, []).append(lsid)
                else:
                    review(
                        f"slab opening between {lsid} and {usid}: footprints match "
                        f"{iou:.0%}, opening covers {cover:.0%} of their overlap; "
                        f"not merged into an atrium",
                        usid,
                    )
        lowers = [l for ls in cand.values() for l in ls]
        for usid in sorted(cand):
            ls = cand[usid]
            if len(ls) > 1 or lowers.count(ls[0]) > 1:
                review(
                    f"{usid} lines up over a slab opening with more than one room "
                    f"({', '.join(sorted(set(ls + [u for u, v in cand.items() if ls[0] in v])))}); "
                    f"not merged into an atrium",
                    usid,
                )
                continue
            pairs.append((ls[0], usid, up))

    for lsid, usid, up in pairs:
        if lsid not in model.spaces or usid not in model.spaces:
            continue
        root[usid] = root.get(lsid, lsid)
    if not root:
        return 0

    elev = {lv.id: lv for lv in levels}
    stacks: Dict[str, list] = {}
    for usid in root:
        stacks.setdefault(root[usid], []).append(usid)
    folded = 0
    for bsid in sorted(stacks):
        base = model.spaces[bsid]
        members = sorted(stacks[bsid], key=lambda s: elev[model.spaces[s].level_id].elevation_z_m)
        top = model.spaces[members[-1]]
        top_lv = elev[top.level_id]
        top_h = top.height_m or top_lv.wall_height_m
        base_lv = elev[base.level_id]
        vols = [model.spaces[s].volume_m3 for s in [bsid] + members]
        height = round(top_lv.elevation_z_m + float(top_h) - base_lv.elevation_z_m, 4)
        for usid in members:
            _fold(
                model,
                base,
                model.spaces[usid],
                f"absorbed {usid} on {model.spaces[usid].level_id}: same footprint over "
                f"an unfilled slab opening (atrium)",
            )
            folded += 1
        base.height_m = height
        if all(v is not None for v in vols):
            base.volume_m3 = round(sum(vols), 4)
        elif base.area_m2:
            base.volume_m3 = round(base.area_m2 * height, 4)
    return folded
