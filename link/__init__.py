"""Link package: deterministic BEM input linking from architectural plans.

Public API:
    build_model(bldg, elevation_key, building_name) -> tuple[BuildingModel, LinkReport]
    LinkReport: dataclass holding the link summary

Internal modules:
    _dedupe: space opening deduplication
    _intervals: interval overlap helpers
    _report: LinkReport dataclass
    _spaces: space construction from room polygons
    _envelope: envelope wall and fenestration linking
    _lighting: artificial lighting gain linking
    _mech: mechanical system linking
    _elevation: elevation-based zone association
    _schedules: schedule extraction
    _api: main build_model orchestration
"""

from link._api import build_model
from link._report import LinkReport
from registration import match_interval_to_segments

__all__ = ["build_model", "LinkReport", "match_interval_to_segments"]
