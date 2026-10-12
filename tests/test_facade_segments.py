"""Tests for per-facade wall segments and facade defaults (#699)."""

import pytest

from link._elevation import facade_from_meta, south_wall_segments, wall_segments

W, D = 20.0, 12.0


def _bldg(rooms):
    return {"W_m": W, "D_m": D, "rooms": rooms}


ROOMS = [
    {"number": "101", "rect_m": [0.0, 0.0, 8.0, 6.0]},  # NW corner
    {"number": "102", "rect_m": [8.0, 0.0, 20.0, 6.0]},  # NE corner
    {"number": "103", "rect_m": [0.0, 6.0, 12.0, 12.0]},  # SW corner
    {"number": "104", "rect_m": [12.0, 6.0, 20.0, 12.0]},  # SE corner
]


@pytest.mark.parametrize(
    "name,ref,fixed,length,axis",
    [
        ("south", (0.0, D), D, W, "x"),
        ("north", (0.0, 0.0), 0.0, W, "x"),
        ("east", (W, 0.0), W, D, "y"),
        ("west", (0.0, 0.0), 0.0, D, "y"),
    ],
)
def test_facade_defaults(name, ref, fixed, length, axis):
    f = facade_from_meta({"facade": name}, D, W)
    assert (f.ref_corner_m, f.fixed_coord_m, f.length_m, f.axis) == (ref, fixed, length, axis)


def test_facade_meta_overrides_and_unknown():
    f = facade_from_meta(
        {"facade": "east", "facade_length_m": 9.0, "facade_ref_corner_m": [W, 1.0]}, D, W
    )
    assert f.length_m == 9.0 and f.ref_corner_m == (W, 1.0)
    with pytest.raises(ValueError):
        facade_from_meta({"facade": "up"}, D, W)


def test_south_segments_unchanged():
    segs = south_wall_segments(_bldg(ROOMS))
    assert [(s["id"], s["s0"], s["s1"]) for s in segs] == [
        ("seg-103", 0.0, 12.0),
        ("seg-104", 12.0, 20.0),
    ]


def test_north_east_west_segments():
    b = _bldg(ROOMS)

    def seg(n):
        f = facade_from_meta({"facade": n}, D, W)
        return [(s["room_number"], s["s0"], s["s1"]) for s in wall_segments(b, f)]

    assert seg("north") == [("101", 0.0, 8.0), ("102", 8.0, 20.0)]
    assert seg("east") == [("102", 0.0, 6.0), ("104", 6.0, 12.0)]
    assert seg("west") == [("101", 0.0, 6.0), ("103", 6.0, 12.0)]


def test_polygon_room_with_two_edges_on_facade():
    # U-shaped room on the south wall: a courtyard notch splits its south edge.
    u = [(0, 6), (12, 6), (12, 12), (8, 12), (8, 10), (4, 10), (4, 12), (0, 12)]
    b = _bldg([{"number": "201", "rect_m": [0.0, 6.0, 12.0, 12.0], "polygon_m": u}])
    segs = south_wall_segments(b)
    assert [(s["id"], s["s0"], s["s1"]) for s in segs] == [
        ("seg-201", 0.0, 4.0),
        ("seg-201-1", 8.0, 12.0),
    ]
