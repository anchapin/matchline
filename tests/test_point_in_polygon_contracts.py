"""Contract pins for every containment implementation (#1129).

The codebase deliberately keeps several point-in-polygon / containment
implementations, each with its own boundary semantics. The design decision
stands (they are distinct contracts and must NOT be unified); this file pins
each one separately over a shared fixture family so none can drift silently.
docs/point-in-polygon-contracts.md carries the inventory and the semantics
table these tests enforce.

Fixture family used throughout:
- SQUARE: simple convex square,
- LSHAPE: simple non-convex L,
- BOWTIE: self-intersecting outline,
- degenerate rings (collinear triple, two vertices, empty),
- a square-with-hole (only where the contract supports holes),
- points on edges, on vertices, far outside.
"""

import random
from types import SimpleNamespace as NS

from shapely.geometry import Point, Polygon

import ifc.ifc_import as ifc_import
import room_labels
import space_merge
from bem.bem_helpers import _place_skylights_on_roof
from grade import Terrain
from mech_tags import room_of
from registration import assign_points_to_spaces
from synth.sheets import _point_in_rooms

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
LSHAPE = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]
BOWTIE = [(0, 0), (10, 10), (10, 0), (0, 10)]


def _far(rng):
    return (1000.0 + rng.random(), 1000.0 + rng.random())


def _interior_and_exterior_points(poly, n=200, seed=11, eps=1e-6):
    """Seeded points strictly inside ``poly`` and strictly outside it.

    Strictly means shapely .contains / no intersection with the boundary
    ring, so no contract's boundary rule is exercised here: this family is
    the shared in/out agreement check every implementation must pass.
    """
    rng = random.Random(seed)
    p = Polygon(poly)
    minx, miny, maxx, maxy = p.bounds
    # widen the sampling box so strictly-outside points are plentiful: a
    # polygon that fills its own bounding box (SQUARE) otherwise starves
    # the outside list
    pad = 0.5 * max(maxx - minx, maxy - miny)
    lo_x, hi_x = minx - pad, maxx + pad
    lo_y, hi_y = miny - pad, maxy + pad
    inside, outside = [], []
    while len(inside) < n or len(outside) < n:
        pt = (rng.uniform(lo_x, hi_x), rng.uniform(lo_y, hi_y))
        q = Point(pt)
        if p.boundary.distance(q) <= eps:
            continue
        if p.contains(q):
            if len(inside) < n:
                inside.append(pt)
        elif len(outside) < n:
            outside.append(pt)
    return inside, outside


class TestRoomLabelsRayCast:
    """room_labels.point_in_polygon: hand-rolled even-odd ray cast.

    Documented contract: polygon_px ring, fewer than 3 vertices is not a
    polygon (False), a single ring only (no hole support). Boundary points
    have NO documented rule; the observed behavior is orientation-dependent
    (a classic ray-cast artifact), so those anchors are regression pins, not
    a spec. Self-intersections resolve by parity (even-odd).
    """

    def test_degenerate_rings_are_not_polygons(self):
        assert not room_labels.point_in_polygon((5, 5), [])
        assert not room_labels.point_in_polygon((5, 5), [(0, 0)])
        assert not room_labels.point_in_polygon((5, 5), [(0, 0), (10, 10)])
        assert not room_labels.point_in_polygon((5, 5), [(0, 0), (5, 5), (10, 10)])

    def test_in_out_agreement_on_simple_rings(self):
        for poly in (SQUARE, LSHAPE):
            inside, outside = _interior_and_exterior_points(poly)
            for pt in inside:
                assert room_labels.point_in_polygon(pt, poly), pt
            for pt in outside:
                assert not room_labels.point_in_polygon(pt, poly), pt
            assert not room_labels.point_in_polygon(_far(random.Random(3)), poly)

    def test_boundary_is_orientation_dependent(self):
        # observed even-odd ray-cast behavior; pinned as regression anchors
        assert room_labels.point_in_polygon((5, 0), SQUARE)  # bottom edge
        assert room_labels.point_in_polygon((0, 5), SQUARE)  # left edge
        assert not room_labels.point_in_polygon((10, 5), SQUARE)  # right edge
        assert room_labels.point_in_polygon((0, 0), SQUARE)  # lower-left vertex
        assert not room_labels.point_in_polygon((10, 10), SQUARE)  # upper-right

    def test_self_intersecting_resolves_by_parity(self):
        # within the bowtie's bounding box every interior point sits in
        # exactly one lobe, so parity only diverges on the edges themselves:
        # (3, 5) is a lobe interior, (5, 5) is the crossing vertex and
        # (6, 4) sits on the anti-diagonal edge, and the raw ray cast calls
        # both of those out
        assert room_labels.point_in_polygon((3, 5), BOWTIE)
        assert not room_labels.point_in_polygon((5, 5), BOWTIE)
        assert not room_labels.point_in_polygon((6, 4), BOWTIE)

    def test_single_ring_no_hole_support(self):
        # a caller passing only the outer ring of a donut counts the hole's
        # interior as inside: holes are not part of this contract
        assert room_labels.point_in_polygon((5, 5), SQUARE)


class TestIfcImportRayCast:
    """ifc_import._point_in_polygon: ray cast with an explicit boundary rule.

    Documented contract: points on the boundary are treated as inside
    (True), n == 0 is not a polygon (False), single ring (no holes).
    """

    def test_boundary_is_inside(self):
        for pt in ((5, 0), (0, 5), (10, 5), (0, 0), (10, 10)):
            assert ifc_import._point_in_polygon(pt, SQUARE), pt

    def test_empty_ring(self):
        assert not ifc_import._point_in_polygon((5, 5), [])

    def test_collinear_ring_follows_the_boundary_rule(self):
        # every point of a collinear triple IS on its boundary ring
        ring = [(0, 0), (5, 5), (10, 10)]
        assert ifc_import._point_in_polygon((5, 5), ring)
        assert ifc_import._point_in_polygon((6, 6), ring)
        assert not ifc_import._point_in_polygon((3, 6), ring)

    def test_in_out_agreement_matches_the_room_labels_cast(self):
        for poly in (SQUARE, LSHAPE):
            inside, outside = _interior_and_exterior_points(poly, seed=17)
            for pt in inside:
                assert ifc_import._point_in_polygon(pt, poly), pt
            for pt in outside:
                assert not ifc_import._point_in_polygon(pt, poly), pt

    def test_boundary_rule_dominates_on_self_intersecting_outlines(self):
        # on the bowtie's own edges the boundary rule fires even where the
        # raw parity would call the point out: (6, 4) is on the
        # anti-diagonal edge and (5, 5) is the crossing vertex
        assert ifc_import._point_in_polygon((6, 4), BOWTIE)
        assert ifc_import._point_in_polygon((5, 5), BOWTIE)
        # a lobe interior point is simply inside; a far point is not
        assert ifc_import._point_in_polygon((3, 5), BOWTIE)
        assert not ifc_import._point_in_polygon((1000, 1000), BOWTIE)


class TestRegistrationDispatch:
    """registration.assign_points_to_spaces: dispatch, not geometry.

    Documented contract: canonical meters, the FIRST enclosing space in
    list order wins, a miss returns None (reported, never dropped).
    """

    def test_first_enclosing_space_wins(self):
        a = NS(id="A", polygon_m=SQUARE)
        b = NS(id="B", polygon_m=SQUARE)
        out = assign_points_to_spaces([{"id": "p1", "x_m": 5.0, "y_m": 5.0}], [a, b])
        assert out == {"p1": "A"}

    def test_miss_returns_none(self):
        a = NS(id="A", polygon_m=SQUARE)
        out = assign_points_to_spaces([{"id": "p1", "x_m": 1000.0, "y_m": 1000.0}], [a])
        assert out == {"p1": None}

    def test_degenerate_space_never_captures(self):
        # inherits the ray cast's <3-vertex rule
        a = NS(id="A", polygon_m=[(0, 0), (10, 10)])
        out = assign_points_to_spaces([{"id": "p1", "x_m": 5.0, "y_m": 5.0}], [a])
        assert out == {"p1": None}


class TestMechTagsRoomOf:
    """mech_tags.room_of: shapely covers per space, unique-cover only.

    Documented contract: the id of the ONE space whose polygon covers the
    point, else None; covers includes the boundary. Overlapping spaces
    resolve to None (ambiguity is reported, not guessed). Spaces with fewer
    than 3 vertices are skipped.
    """

    def test_interior_and_boundary_are_covered(self):
        sp = NS(id="A", polygon_m=SQUARE)
        assert room_of((5, 5), [sp]) == "A"
        assert room_of((5, 0), [sp]) == "A"

    def test_overlapping_spaces_resolve_to_none(self):
        a = NS(id="A", polygon_m=SQUARE)
        b = NS(id="B", polygon_m=SQUARE)
        assert room_of((5, 5), [a, b]) is None

    def test_degenerate_polygon_is_skipped(self):
        assert room_of((5, 5), [NS(id="A", polygon_m=[(0, 0), (5, 5)])]) is None

    def test_far_point(self):
        assert room_of((1000, 1000), [NS(id="A", polygon_m=SQUARE)]) is None


class TestSpaceMergeContains:
    """space_merge._contains: shapely covers on the merged polygon.

    Documented contract: covers, so boundary points are contained; holes
    ARE supported (shapely polygons with interior rings).
    """

    def test_interior_boundary_far(self):
        poly = Polygon(SQUARE)
        assert space_merge._contains(poly, (5, 5))
        assert space_merge._contains(poly, (5, 0))
        assert not space_merge._contains(poly, (1000, 1000))

    def test_hole_is_excluded(self):
        donut = Polygon(SQUARE, holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
        assert not space_merge._contains(donut, (5, 5))
        assert space_merge._contains(donut, (1, 1))


class TestGradeTerrainAt:
    """grade.Terrain.at: buffer(1e-9).contains over triangle footprints.

    Documented contract: z interpolated over the containing triangle, max
    over overlapping facets, None when nothing contains. Zero-area facets
    are dropped at construction. The 1e-9 buffer is the whole boundary
    tolerance: on-edge points are in, a point 1e-7 outside is out.
    """

    def _terrain(self):
        return Terrain(
            [
                [(0, 0, 0), (10, 0, 1), (10, 10, 2)],
                [(0, 0, 0), (10, 10, 2), (0, 10, 3)],
            ]
        )

    def test_degenerate_facet_is_dropped(self):
        t = Terrain([[(0, 0, 0), (5, 5, 1), (10, 10, 2)]])
        assert t.at(1, 1) is None
        assert not t

    def test_interior_and_edge_are_in(self):
        t = self._terrain()
        assert t.at(5, 5) == 1.0
        assert t.at(0, 5) == 1.5  # on the x=0 edge: buffer admits it

    def test_near_outside_beyond_the_buffer_is_out(self):
        t = self._terrain()
        assert t.at(-1e-7, 5) is None
        assert t.at(1000, 1000) is None


class TestSynthRectRooms:
    """synth.sheets._point_in_rooms: axis-aligned rect list, eps 1e-6.

    Documented contract: inclusive rect bounds widened by eps on every
    side; degenerate rects behave as zero-width bands within eps. No
    polygon machinery: rooms are rect tuples.
    """

    def test_inside_and_edges(self):
        rooms = [(0, 0, 10, 10)]
        assert _point_in_rooms(5, 5, rooms)
        assert _point_in_rooms(0, 5, rooms)
        assert _point_in_rooms(10, 5, rooms)

    def test_eps_widening(self):
        rooms = [(0, 0, 10, 10)]
        assert _point_in_rooms(10 + 1e-7, 5, rooms)  # within eps
        assert not _point_in_rooms(10 + 2e-6, 5, rooms)  # beyond eps

    def test_empty_and_disjoint(self):
        assert not _point_in_rooms(5, 5, [])
        assert not _point_in_rooms(0, 0, [(5, 5, 6, 6)])

    def test_degenerate_rect_is_a_band_within_eps(self):
        assert _point_in_rooms(5, 5, [(5, 0, 5, 10)])


class TestBemHelpersSkylightPlacement:
    """bem_helpers._place_skylights_on_roof: strict vectorized contains.

    Documented contract: a skylight box is placed only where
    ``shapely.contains(usable, box)`` holds, so a box touching the
    setback boundary is NOT contained and is skipped with a note; a
    degenerate roof outline skips every unit.
    """

    RING = [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_box_touching_the_setback_boundary_is_skipped(self):
        # usable is the roof inset 0.30 m: 9.4 x 9.4. A 9.4 m wide box can
        # only touch the usable edge, and strict contains refuses it
        placements, notes = _place_skylights_on_roof(
            [NS(tag="S1", width_m=9.4, height_m=2.0, space_sid="")], self.RING
        )
        assert placements == []
        assert len(notes) == 1 and "no room" in notes[0]

    def test_slightly_narrower_box_places(self):
        placements, notes = _place_skylights_on_roof(
            [NS(tag="S1", width_m=9.3, height_m=2.0, space_sid="")], self.RING
        )
        assert notes == []
        (x0, y0), _, (x1, _), _ = placements[0]["rect"]
        assert x0 >= 0.3 and x1 <= 9.7  # strictly inside the setback

    def test_degenerate_roof_skips_every_unit(self):
        placements, notes = _place_skylights_on_roof(
            [NS(tag="S1", width_m=2.0, height_m=2.0, space_sid="")], [(0, 0), (10, 10)]
        )
        assert placements == []
        assert "degenerate" in notes[0]
