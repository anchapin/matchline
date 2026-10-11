"""Attach an IFC opening to a space by review decision (#799).

ifc_import leaves openings that sit over no space (a skylight over a void,
a wall opening in no room on either side) unattached and flags them for
review (kind ``opening_attachment``, targets ``bim_opening``). This module
lets a reviewer's edit attach such an opening to the room they name, built
the same way the automatic Tier 1 paths build it, and undo it on revert.
The wall items whose along-wall direction could not be determined (targets
``bim_element``) carry several openings and no single field: record-only.
"""

from __future__ import annotations

from building_model import BuildingModel, SpaceOpening


def find_bim_opening(model: BuildingModel, opening_id: str):
    """(host element, BimOpening) for ``opening_id``, or (None, None)."""
    for el in model.bim_elements:
        for bo in el.openings:
            if bo.id == opening_id:
                return el, bo
    return None, None


def host_space(model: BuildingModel, opening_id: str):
    """The space currently holding the opening, or None when unattached."""
    for sp in model.spaces.values():
        if any(o.id == opening_id for o in sp.openings):
            return sp
    return None


def check_opening_attachment(model: BuildingModel, opening_id: str, space_id: str) -> None:
    """Raise ValueError when the opening cannot attach to room ``space_id``.

    Used to validate a review edit before anything changes (#799). An
    opening already attached to another room may still be edited (it moves);
    only a missing opening or room, or a different level, refuses.
    """
    el, _ = find_bim_opening(model, opening_id)
    if el is None:
        raise ValueError(f"opening {opening_id!r} is not in the model")
    sp = model.spaces.get(space_id)
    if sp is None:
        raise ValueError(f"room {space_id!r} is not a space id in the model")
    if el.level_id and sp.level_id and sp.level_id != el.level_id:
        raise ValueError(
            f"room {space_id!r} is on level {sp.level_id!r}; {el.global_id} is on {el.level_id!r}"
        )


def attach_opening(model: BuildingModel, opening_id: str, space_id: str) -> None:
    """Attach the opening to room ``space_id`` by review decision (#799).

    Mirrors the automatic Tier 1 paths in ifc_import: a skylight keeps its
    plan centre and roof plane geometry; a wall opening keeps its sill and
    along-wall interval. The other side of a wall opening stays unknown
    (adjacent_space_id None) and needs_review is cleared: a reviewer
    resolved the attachment. A skylight re-runs the space's daylight area.
    """
    from ifc_import import _opening_construction_id

    el, bo = find_bim_opening(model, opening_id)
    sp = model.spaces.get(space_id)
    if el is None or bo is None or sp is None:
        return
    un_attach(model, opening_id)
    if bo.category == "skylight":
        w, h = bo.width_m or 0.0, bo.height_m or 0.0
        sp.openings.append(
            SpaceOpening(
                id=bo.id,
                tag=bo.tag,
                category="skylight",
                width_m=w,
                height_m=h,
                host_facade="roof",
                area_m2=round(w * h, 4) if w and h else None,
                provenance=bo.provenance,
                plan_center_m=list(bo.plan_center_m or []),
                tilt_deg=bo.tilt_deg,
                azimuth_deg=bo.azimuth_deg,
                construction_id=_opening_construction_id(model, bo),
                needs_review=False,
            )
        )
        _skylight_daylight(sp, model)
        return
    width = float(bo.width_m or 0.0)
    interval = None
    if width > 0 and bo.s_center_m is not None:
        length = float(el.length_m or 0.0)
        s1 = min(length, bo.s_center_m + width / 2.0) if length else bo.s_center_m + width / 2.0
        interval = [round(max(0.0, bo.s_center_m - width / 2.0), 3), round(s1, 3)]
    sp.openings.append(
        SpaceOpening(
            id=bo.id,
            tag=bo.tag,
            category=bo.category,
            width_m=width,
            height_m=bo.height_m or 0.0,
            sill_m=bo.sill_m,
            s_center_m=bo.s_center_m,
            host_interval_m=interval,
            provenance=bo.provenance,
            needs_review=False,
            construction_id=_opening_construction_id(model, bo),
        )
    )


def un_attach(model: BuildingModel, opening_id: str) -> None:
    """Remove the opening from whichever space holds it (revert, #799)."""
    sp = host_space(model, opening_id)
    if sp is None:
        return
    sp.openings = [o for o in sp.openings if o.id != opening_id]


def _skylight_daylight(sp, model: BuildingModel) -> None:
    from daylight_skylights import compute_skylight_daylight

    heights = {lv.id: lv.wall_height_m for lv in model.levels}
    compute_skylight_daylight(sp, heights.get(sp.level_id))
