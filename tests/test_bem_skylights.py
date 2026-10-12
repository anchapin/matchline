"""Skylights on the gbXML roof (roadmap item 3, wave 2a).

Takeoff lines give a count and schedule dimensions, never a position, so the
exporter synthesises a layout on the flat roof. These tests pin what that
layout must guarantee: every skylight inside the roof with its setback, no two
overlapping, an even spread rather than a corner pile, area never shrunk to
fit, and a schema-valid gbXML with FixedSkylight openings on the roof surface.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml
from bem_helpers import (
    SKYLIGHT_CLEARANCE_M,
    SKYLIGHT_SETBACK_M,
    _opening_type,
    _place_skylights_on_roof,
    _roof_outline,
)

RING = [(0.0, 0.0), (20.0, 0.0), (20.0, 12.0), (0.0, 12.0)]
L_RING = [(0.0, 0.0), (20.0, 0.0), (20.0, 6.0), (8.0, 6.0), (8.0, 14.0), (0.0, 14.0)]


def _sky(n, w=1.2, h=1.2, tag="SK-1"):
    return [BEMOpeningUnit("skylight", tag, w, h) for _ in range(n)]


def _rects(placements):
    return [Polygon(p["rect"]) for p in placements]


def test_skylight_opening_type_is_fixed_skylight():
    assert _opening_type("skylight") == "FixedSkylight"
    assert _opening_type("window") == "FixedWindow"
    assert _opening_type("door") == "NonSlidingDoor"


@pytest.mark.parametrize("ring", [RING, L_RING], ids=["rect", "L-shape"])
def test_skylights_inside_roof_with_setback_and_clearance(ring):
    placed, notes = _place_skylights_on_roof(_sky(6), ring)
    assert len(placed) == 6 and not notes
    usable = Polygon(ring).buffer(-SKYLIGHT_SETBACK_M + 1e-9)
    rects = _rects(placed)
    for r in rects:
        assert usable.contains(r)
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            assert rects[i].distance(rects[j]) >= SKYLIGHT_CLEARANCE_M - 1e-9


def test_skylight_area_is_never_shrunk():
    placed, _ = _place_skylights_on_roof(_sky(4, 1.5, 0.9), RING)
    for r in _rects(placed):
        assert math.isclose(r.area, 1.5 * 0.9, rel_tol=1e-9)


def test_skylights_spread_instead_of_piling_in_a_corner():
    placed, _ = _place_skylights_on_roof(_sky(4), RING)
    cs = [r.centroid for r in _rects(placed)]
    nearest = min(a.distance(b) for i, a in enumerate(cs) for b in cs[i + 1 :])
    # Four 1.2 m skylights on a 20 x 12 m roof: a corner pile would sit
    # 1.5 m apart; a real spread is several metres.
    assert nearest > 5.0


def test_skylight_that_cannot_fit_is_skipped_with_a_note_not_scaled():
    placed, notes = _place_skylights_on_roof(_sky(1, 30.0, 2.0, tag="SK-HUGE"), RING)
    assert placed == []
    assert len(notes) == 1 and "SK-HUGE" in notes[0] and "skipped" in notes[0]


def test_overfull_roof_places_what_fits_and_names_the_rest():
    placed, notes = _place_skylights_on_roof(_sky(200, 2.0, 2.0), RING)
    assert 0 < len(placed) < 200
    assert len(notes) == 200 - len(placed)


def test_placement_is_deterministic():
    a, _ = _place_skylights_on_roof(_sky(5), L_RING)
    b, _ = _place_skylights_on_roof(_sky(5), L_RING)
    assert [p["rect"] for p in a] == [p["rect"] for p in b]


def _model(openings):
    sp = BEMSpace("sp-1", "OPEN OFFICE 101", "101", list(RING), 240.0, 720.0)
    return BEMModel(
        building_name="Sky Test",
        spaces=[sp],
        openings=openings,
        ring_m=list(RING),
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )


def test_gbxml_puts_skylights_on_roof_and_validates(tmp_path):
    import xml.etree.ElementTree as ET

    m = _model([BEMOpeningUnit("window", "A", 1.2, 1.5) for _ in range(4)] + _sky(3))
    path = write_gbxml(m, tmp_path / "sky.xml")
    ok, errors = validate_gbxml(path)
    assert ok, errors
    ns = {"g": "http://www.gbxml.org/schema"}
    root = ET.parse(path).getroot()
    roof = root.find(".//g:Surface[@id='roof-001']", ns)
    sky = roof.findall("g:Opening", ns)
    assert len(sky) == 3
    for op in sky:
        assert op.get("openingType") == "FixedSkylight"
        assert op.get("coordinatesAbsolute") == "true"
        pts = [
            [float(c.text) for c in cp]
            for cp in op.findall("g:PlanarGeometry/g:PolyLoop/g:CartesianPoint", ns)
        ]
        assert len(pts) == 4 and all(math.isclose(p[2], 3.0) for p in pts)
        poly = Polygon([(p[0], p[1]) for p in pts])
        assert poly.exterior.is_ccw  # outward normal +z, same as the roof
        assert Polygon(RING).contains(poly)
    walls = [
        op
        for su in root.findall(".//g:Surface[@surfaceType='ExteriorWall']", ns)
        for op in su.findall("g:Opening", ns)
    ]
    assert len(walls) == 4 and all(o.get("openingType") == "FixedWindow" for o in walls)


def test_gbxml_without_skylights_has_no_roof_openings(tmp_path):
    import xml.etree.ElementTree as ET

    m = _model([BEMOpeningUnit("window", "A", 1.2, 1.5)])
    path = write_gbxml(m, tmp_path / "plain.xml")
    ns = {"g": "http://www.gbxml.org/schema"}
    roof = ET.parse(path).getroot().find(".//g:Surface[@id='roof-001']", ns)
    assert roof.findall("g:Opening", ns) == []


def test_ifc4_notes_count_roof_skylights(tmp_path):
    pytest.importorskip("ifcopenshell")
    from bem_export import write_ifc4

    m = _model(_sky(2))
    write_ifc4(m, tmp_path / "sky.ifc")
    assert any("Roof slab with 2 of 2 skylight(s)" in n for n in m.notes)


def test_degenerate_space_footprint_falls_back_to_whole_roof():
    u = BEMOpeningUnit("skylight", "SK-1", 1.2, 1.2, space_sid="sp-x")
    placed, notes = _place_skylights_on_roof([u], RING, regions={"sp-x": [(0, 0), (1, 1)]})
    assert len(placed) == 1 and not notes


def test_roof_outline_falls_back_to_space_union_when_ring_is_degenerate():
    west = [(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)]
    east = [(10.0, 0.0), (20.0, 0.0), (20.0, 8.0), (10.0, 8.0)]
    out = _roof_outline([(0.0, 0.0), (20.0, 0.0)], {"w": west, "e": east})
    assert out and math.isclose(Polygon(out).area, 160.0) and Polygon(out).exterior.is_ccw
    # disjoint footprints give no single roof: no guessing a hull
    far = [(30.0, 0.0), (35.0, 0.0), (35.0, 5.0), (30.0, 5.0)]
    assert _roof_outline([], {"w": west, "f": far}) is None
