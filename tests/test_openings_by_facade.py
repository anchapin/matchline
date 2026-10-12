"""Wall openings stay on their own facade through BEM export and IFC round trip."""

import pytest

from bem_geometry import BEMOpeningUnit
from bem_helpers import _distribute_openings, _edge_facades, _wall_edges

RECT_NORTH_UP = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]  # CCW, y=north


def _u(tag, facade="", cat="window"):
    return BEMOpeningUnit(category=cat, tag=tag, width_m=1.0, height_m=1.0, host_facade=facade)


def test_edge_facades_y_north_ccw():
    assert _edge_facades(RECT_NORTH_UP, y_north=True) == ["south", "east", "north", "west"]


def test_edge_facades_winding_does_not_matter():
    cw = list(reversed(RECT_NORTH_UP))
    got = dict(zip(_wall_edges(cw), _edge_facades(cw, y_north=True)))
    want = dict(zip(_wall_edges(RECT_NORTH_UP), _edge_facades(RECT_NORTH_UP, y_north=True)))
    for (a, b), f in want.items():
        assert got[(b, a)] == f


def test_edge_facades_y_down_flips_north_south():
    assert _edge_facades(RECT_NORTH_UP, y_north=False) == ["north", "east", "south", "west"]


def test_openings_go_on_their_facade():
    edges = _wall_edges(RECT_NORTH_UP)
    fac = _edge_facades(RECT_NORTH_UP, y_north=True)
    units = [_u("A", "south"), _u("A", "south"), _u("B", "east")]
    a = _distribute_openings(units, edges, fac)
    assert [len(a[i]) for i in range(4)] == [2, 1, 0, 0]


def test_unknown_facade_falls_back_to_length_split():
    edges = _wall_edges(RECT_NORTH_UP)
    fac = _edge_facades(RECT_NORTH_UP, y_north=True)
    units = [_u(str(k)) for k in range(4)] + [_u("X", "roofline")]
    a = _distribute_openings(units, edges, fac)
    assert sum(len(v) for v in a.values()) == 5
    # same split as when no facades are known at all
    plain = _distribute_openings(units, edges)
    assert {i: len(v) for i, v in a.items()} == {i: len(v) for i, v in plain.items()}


def test_no_facades_is_old_behaviour():
    edges = _wall_edges(RECT_NORTH_UP)
    units = [_u("A", "south"), _u("A", "south")]
    a = _distribute_openings(units, edges)  # no edge facades given
    assert [len(a[i]) for i in range(4)] == [1, 0, 1, 0]


def test_facade_split_over_several_edges_of_that_facade():
    # L-shape (CCW, y=north): two separate south-facing edges, 10 m and 4 m
    ring = [(0, 0), (10, 0), (10, 4), (14, 4), (14, 10), (0, 10)]
    edges = _wall_edges(ring)
    fac = _edge_facades(ring, y_north=True)
    south = [i for i, f in enumerate(fac) if f == "south"]
    assert len(south) == 2
    a = _distribute_openings([_u(str(k), "south") for k in range(7)], edges, fac)
    assert sum(len(a[i]) for i in south) == 7
    assert sum(len(a[i]) for i in range(len(edges)) if i not in south) == 0


def test_ifc_roundtrip_keeps_windows_on_south(tmp_path):
    pytest.importorskip("ifcopenshell")
    from ifc_export import _export_ifc
    from ifc_import import import_ifc
    from tests.model_factory import make_clean_model

    m = make_clean_model()
    bm = import_ifc(_export_ifc(m, tmp_path / "f.ifc"))
    ops = [o for s in bm.spaces.values() for o in s.openings]
    assert len(ops) == 2
    assert {o.host_facade for o in ops} == {"south"}
    assert all(o in bm.spaces["L1-101"].openings for o in ops)
