from __future__ import annotations

import math

from building_model import (
    BuildingModel,
    EnvelopeWall,
    Provenance,
)

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Envelope wall and fenestration linking."""


def _build_envelope(bldg, model: BuildingModel, level_id: str, wall_height_m: float) -> None:
    W, D = bldg["W_m"], bldg["D_m"]
    meta = bldg["sheets"]["arch"]["meta"]
    runs = [
        ("south", [0.0, D], [W, D]),
        ("north", [W, 0.0], [0.0, 0.0]),
        ("east", [W, 0.0], [W, D]),
        ("west", [0.0, D], [0.0, 0.0]),
    ]
    for i, (facade, a, b) in enumerate(runs):
        length = math.dist(a, b)
        model.envelope.append(
            EnvelopeWall(
                id=f"{level_id}-EW{i + 1}",
                facade=facade,
                from_m=list(a),
                to_m=list(b),
                length_m=length,
                height_m=wall_height_m,
                area_m2=length * wall_height_m,
                provenance=Provenance(
                    sheet_id=meta["sheet_id"],
                    revision=meta["revision"],
                    method="arch_plan_parse",
                    confidence=1.0,
                    note=f"footprint {facade} wall run",
                ),
            )
        )


# ---------------------------------------------------------------------------
# 2. Lighting plan -> fixtures to spaces
# ---------------------------------------------------------------------------
