from __future__ import annotations

from typing import TYPE_CHECKING

from building_model import (
    BuildingModel,
    ComponentRef,
    Provenance,
    Zone,
)
from registration import (
    Affine2D,
    PlanRegistration,
    assign_points_to_spaces,
)

if TYPE_CHECKING:
    from link._report import LinkReport

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Mechanical system linking."""


def _link_mech(bldg, model: BuildingModel, spaces: list, report: LinkReport) -> None:
    sh = bldg["sheets"]["mech"]
    meta = sh["meta"]
    reg = PlanRegistration(
        sheet_id=meta["sheet_id"],
        discipline="mech_plan",
        method="title_block",
        confidence=0.95,
        affine=Affine2D.from_scale_translate(meta["px_per_m"], *meta["origin_px"]),
    )
    space_of = {s.id: s for s in spaces}
    level_id = bldg["level_id"]

    # components -> canonical ComponentRefs
    comp_refs = {}
    for c in bldg["components"]:
        x_m, y_m = reg.to_meters(c["x_px"], c["y_px"])
        comp_refs[c["id"]] = ComponentRef(
            id=c["id"],
            type=c["type"],
            x_m=x_m,
            y_m=y_m,
            tag=c["tag"],
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="symbol_detection",
                confidence=0.9,
                bbox=[c["x_px"], c["y_px"], c["x_px"], c["y_px"]],
            ),
        )

    for z in bldg["zones"]:
        zid = f"{level_id}-{z['zone_id']}"
        # diffusers/sensors -> spaces by position (no id matching)
        dpts = []
        for d_ in z["diffusers"]:
            x_m, y_m = reg.to_meters(d_["x_px"], d_["y_px"])
            dpts.append({"id": d_["id"], "x_m": x_m, "y_m": y_m})
        spts = []
        for s_ in z["sensors"]:
            x_m, y_m = reg.to_meters(s_["x_px"], s_["y_px"])
            spts.append({"id": s_["id"], "x_m": x_m, "y_m": y_m})
        d_space = assign_points_to_spaces(dpts, spaces)
        s_space = assign_points_to_spaces(spts, spaces)

        zone = Zone(
            id=zid,
            level_id=level_id,
            duct_length_m=z.get("duct_length_m"),
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="duct_tracing",
                confidence=0.9,
                note="; ".join(z.get("audit", [])),
            ),
        )
        v = z["vav"]
        vx_m, vy_m = reg.to_meters(v["x_px"], v["y_px"])
        zone.terminal_unit = ComponentRef(
            id=v["id"],
            type="vav",
            x_m=vx_m,
            y_m=vy_m,
            tag=v["id"],
            provenance=Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="symbol_detection",
                confidence=0.9,
            ),
        )

        for d_ in z["diffusers"]:
            ref = comp_refs[d_["id"]]
            zone.diffusers.append(ref)
            sid = d_space[d_["id"]]
            if sid is None:
                model.flag_for_review(
                    "diffuser_assignment",
                    f"diffuser {d_['id']} falls in no space",
                    0.4,
                    ref.provenance,
                )
                continue
            report.diffusers_assigned += 1
            sp = space_of[sid]
            sp.hvac.diffusers.append(ref)
            if sid not in zone.space_ids:
                zone.space_ids.append(sid)
            if zid not in sp.hvac.zone_ids:
                sp.hvac.zone_ids.append(zid)
        for s_ in z["sensors"]:
            ref = comp_refs[s_["id"]]
            zone.sensors.append(ref)
            sid = s_space[s_["id"]]
            if sid is None:
                model.flag_for_review(
                    "sensor_assignment", f"sensor {s_['id']} falls in no space", 0.4, ref.provenance
                )
                continue
            report.sensors_assigned += 1
            space_of[sid].hvac.sensors.append(ref)
            # sensor -> zone by room co-location: the sensor's space may
            # sit in several zones; attach to the zone whose duct run
            # serves it (the zone that listed this sensor).
            # (space.hvac.zone_ids already has zid via diffusers)
        # terminal unit -> its space
        vpts = assign_points_to_spaces([{"id": v["id"], "x_m": vx_m, "y_m": vy_m}], spaces)
        vsid = vpts[v["id"]]
        if vsid is not None:
            space_of[vsid].hvac.terminal_units.append(zone.terminal_unit)
        for sid in zone.space_ids:
            space_of[sid].hvac.provenance = Provenance(
                sheet_id=meta["sheet_id"],
                revision=meta["revision"],
                method="duct_tracing",
                confidence=0.9,
                note=f"zone {zid} via diffuser positions",
            )
        model.zones[zid] = zone
    report.n_zones = len(model.zones)
    model.log_revision(
        meta["sheet_id"],
        meta["revision"],
        "ingest",
        f"{len(model.zones)} zones attached by diffuser positions",
    )


# ---------------------------------------------------------------------------
# 4. Elevation -> windows to rooms (grid path or geometric fallback)
# ---------------------------------------------------------------------------
