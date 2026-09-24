from __future__ import annotations

from building_model import (
    BuildingModel,
    Level,
)
from link._dedupe import _dedupe_space_openings
from link._elevation import _link_elevation
from link._envelope import _build_envelope
from link._lighting import _link_lighting
from link._mech import _link_mech
from link._report import LinkReport
from link._schedules import _schedules
from link._spaces import _build_spaces

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Main build_model orchestration."""


def build_model(
    bldg: dict,
    elevation_key: str = "elev_grid",
    building_name: str = "",
) -> tuple[BuildingModel, LinkReport]:
    """Build the canonical BuildingModel for one building.

    elevation_key: "elev_grid" or "elev_nogrid" -- the two elevations are
    linked in separate runs; cross-sheet window dedup is applied as a
    post-processing step (window tag + geometric proximity, see Issue #1
    in docs/design-docs/open-issues.md). Pass None to skip elevation
    linking entirely (used by elevation_windows.link_elevations, which
    does multi-elevation detect -> dedup -> attach itself).
    """
    from dataclasses import asdict

    model = BuildingModel(name=building_name or bldg["building_id"])
    level_id = bldg["level_id"]
    model.levels.append(Level(id=level_id, name="Level 1", wall_height_m=bldg["wall_height_m"]))
    report = LinkReport(
        building_id=bldg["building_id"],
        elevation_path=(
            "grid" if elevation_key == "elev_grid" else "geometric" if elevation_key else "none"
        ),
    )

    spaces = _build_spaces(bldg, model, level_id, bldg["wall_height_m"])
    report.n_spaces = len(spaces)
    _build_envelope(bldg, model, level_id, bldg["wall_height_m"])
    win_sched, light_sched = _schedules(bldg)
    for tag, e in {**win_sched, **light_sched}.items():
        model.schedules[tag] = asdict(e)

    _link_lighting(bldg, model, spaces, light_sched, report)
    _link_mech(bldg, model, spaces, report)
    if elevation_key is not None:
        _link_elevation(bldg, model, spaces, elevation_key, win_sched, report)
        from elevation_windows import compute_daylit_zones

        W = bldg["W_m"]
        D = bldg["D_m"]
        for sp in model.spaces.values():
            compute_daylit_zones(sp, W, D)

    # Issue #1: cross-sheet window deduplication — dedupe after all elevation
    # linking so that two runs of the same facade produce one SpaceOpening
    # per physical window.
    _dedupe_space_openings(model)

    report.review_items = len(model.review_queue)
    return model, report
