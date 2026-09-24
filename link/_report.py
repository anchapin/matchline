from __future__ import annotations

from dataclasses import dataclass, field

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""LinkReport dataclass."""


@dataclass
class LinkReport:
    building_id: str
    elevation_path: str  # "grid" | "geometric"
    n_spaces: int = 0
    n_zones: int = 0
    fixtures_assigned: int = 0
    fixtures_unassigned: int = 0
    sensors_assigned: int = 0
    diffusers_assigned: int = 0
    windows_linked: int = 0
    windows_unlinked: int = 0
    review_items: int = 0
    mean_confidence_by_method: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 1. Arch plan -> spaces + envelope
# ---------------------------------------------------------------------------
