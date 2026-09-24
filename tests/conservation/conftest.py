"""Fixtures for conservation law tests."""

import pytest

from tests.conftest import _linked
from validate import polygon_area_px2


@pytest.fixture(scope="module")
def bldg_3room_wall_area_mismatch():
    """bldg_3room with the L1-101 space.area_m2 set 15% above polygon area.

    This injects a defect that violates the _check_space_area_matches_polygon
    conservation law (space floor area must equal shoelace(polygon)).
    """
    bldg, model, report = _linked(101, False, "elev_grid")
    sp = model.spaces["L1-101"]
    true_area = polygon_area_px2(sp.polygon_m)
    sp.area_m2 = true_area * 1.15
    return bldg, model, report
