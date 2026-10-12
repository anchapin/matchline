"""The linked-model adapter writes gbXML/IFC north-up, not mirrored N-S.

Before this fix model_from_linked_model passed the canonical y-down ring
straight through, so a south wall came out with Azimuth 0 (north) and the
south windows were written onto north-facing surfaces.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from bem_export import write_gbxml
from geometry_simplify import footprint_from_regions
from run_pipeline import _north_up, model_from_linked_model
from tests.model_factory import make_clean_model

G = "{http://www.gbxml.org/schema}"


def _bem():
    m = make_clean_model()
    ring = footprint_from_regions([sp.polygon_m for sp in m.spaces.values()])
    return model_from_linked_model(
        model=m, simplified_ring=ring, wall_height_m=3.0, simplify_tolerance=0.0
    )


def _walls(tmp_path):
    p = tmp_path / "b.xml"
    write_gbxml(_bem(), p)
    out = []
    for su in ET.parse(p).getroot().iter(G + "Surface"):
        if su.get("surfaceType") != "ExteriorWall":
            continue
        az = float(su.find(f"{G}RectangularGeometry/{G}Azimuth").text)
        out.append((az, len(su.findall(G + "Opening"))))
    return out


def test_north_up_flips_y_only():
    assert _north_up([(1, 2), (3.5, -4)]) == [(1.0, -2.0), (3.5, 4.0)]


def test_ring_is_north_up_and_ccw():
    bem = _bem()
    ys = [p[1] for p in bem.ring_m]
    assert max(ys) == pytest.approx(0.0) and min(ys) == pytest.approx(-6.0)
    area2 = sum(
        a[0] * b[1] - b[0] * a[1] for a, b in zip(bem.ring_m, bem.ring_m[1:] + bem.ring_m[:1])
    )
    assert area2 > 0


def test_ring_facades_match_azimuths(tmp_path):
    bem = _bem()
    expect = {"north": 0.0, "east": 90.0, "south": 180.0, "west": 270.0}
    walls = _walls(tmp_path)
    assert [expect[f] for f in bem.ring_facades] == [az for az, _ in walls]


def test_south_windows_land_on_south_facing_walls(tmp_path):
    walls = _walls(tmp_path)
    with_openings = {az for az, n in walls if n}
    assert with_openings == {180.0}
    assert sum(n for _, n in walls) == 2


def test_space_polygons_share_the_ring_frame():
    bem = _bem()
    for sp in bem.spaces:
        assert all(-6.0 - 1e-9 <= p[1] <= 1e-9 for p in sp.polygon_m)
