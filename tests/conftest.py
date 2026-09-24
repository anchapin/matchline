"""Shared fixtures: build linked BuildingModels from synthetic buildings.

Uses synth.multidiscipline.generate_building + link.build_model -- the same
builder API the pipeline uses. Seeds are FIXED here; the property-style
invariant test varies them.

Only bldg_3room is actively used (by test_units.py); the others were
unused and removed — see issue #73.
"""

import pytest

from link import build_model
from synth.multidiscipline import generate_building


def _linked(seed: int, open_office_span: bool = False, elevation_key: str = "elev_grid"):
    bldg = generate_building(seed, open_office_span=open_office_span)
    model, report = build_model(
        bldg, elevation_key=elevation_key, building_name=bldg["building_id"]
    )
    return bldg, model, report


@pytest.fixture(scope="module")
def bldg_3room():
    """3-room building, grid elevation path. Used by test_units.py."""
    return _linked(101, open_office_span=False, elevation_key="elev_grid")
