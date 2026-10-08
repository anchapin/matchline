"""Clinic walls harness (#740): plan cut of a mesh and the sheet round trip.

The Clinic IFC itself is held out; these use small meshes and shapely shapes.
"""

import numpy as np
from shapely.geometry import Polygon, box

from plan_walls import compare_rooms, extract_walls
from scripts.validate_clinic_walls import M_PER_PT, cut_polygons, section, sheet_for


def _box_mesh(x0, y0, x1, y1, z0, z1):
    v = np.array(
        [[x, y, z] for z in (z0, z1) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))],
        dtype=float,
    )
    q = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 3, 2, 1), (4, 5, 6, 7)]
    f = np.array([t for a, b, c, d in q for t in ((a, b, c), (a, c, d))], dtype=int)
    return v, f


def test_plan_cut_of_a_wall_solid_is_its_footprint():
    v, f = _box_mesh(0.0, 0.0, 4.0, 0.2, 0.0, 3.0)
    polys = cut_polygons(section(v, f, 1.2))
    assert len(polys) == 1
    assert abs(polys[0].area - 0.8) < 1e-6
    assert section(v, f, 3.5) == []  # above the wall: nothing cut


def test_one_room_round_trips_through_the_sheet_in_ifc_coordinates():
    # a 5 x 4 m room (inside faces) bounded by 0.2 m walls, far from the origin
    ox, oy = 1000.0, 2000.0
    outer = box(ox - 0.2, oy - 0.2, ox + 5.2, oy + 4.2)
    inner = box(ox, oy, ox + 5.0, oy + 4.0)
    walls = [outer.difference(inner)]
    ring = list(inner.exterior.coords)[:-1]
    sheet, (dx, dy) = sheet_for(walls, [("101", "OFFICE", ring)])
    res = extract_walls(sheet, M_PER_PT)
    assert len(res.rooms) == 1
    assert res.rooms[0].label["number"] == "101"
    pred = [[(x + dx, y + dy) for x, y in res.rooms[0].polygon_m]]
    cmp = compare_rooms(pred, [ring])
    assert cmp["matched"] == 1
    # the room face is drawn on wall centrelines, so it is a little larger than the net room
    assert Polygon(pred[0]).area >= Polygon(ring).area
