"""Shared fixtures: build linked BuildingModels from synthetic buildings.

Uses synth.multidiscipline.generate_building + link.build_model -- the same
builder API the pipeline uses. Seeds are FIXED here; the property-style
invariant test varies them.

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
    """3-room building, grid elevation path."""
    return _linked(101, open_office_span=False, elevation_key="elev_grid")


@pytest.fixture(scope="module")
def bldg_3room_geometric():
    """3-room building, geometric-fallback elevation path."""
    return _linked(101, open_office_span=False, elevation_key="elev_nogrid")


@pytest.fixture(scope="module")
def bldg_open_office():
    """Open office spanning two zones (many-to-many zone<->space)."""
    return _linked(102, open_office_span=True, elevation_key="elev_grid")


@pytest.fixture(scope="module")
def bldg_8room():
    return _linked(103, open_office_span=False, elevation_key="elev_grid")
