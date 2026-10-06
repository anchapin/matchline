"""Export -> import -> export of matchline's own IFC keeps the footprint (#609).

Exported walls are centred on the ring edge, so the centreline import reads
back from the wall body is the ring that was exported. Before, the whole body
sat on one side and the re-imported ring shrank by half a wall thickness,
failing BEM volume conservation on the second export.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

from ifc_export import _bem_from_model, _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402


def _area(ring):
    n = len(ring)
    return (
        abs(
            sum(
                ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1]
                for i in range(n)
            )
        )
        / 2
    )


def _chain(tmp_path, rounds=2):
    b = IfcBuilder()
    box_plan(b)
    models = [import_ifc(b.write(tmp_path / "src.ifc"))]
    for k in range(rounds):
        out = tmp_path / f"r{k}.ifc"
        _export_ifc(models[-1], out)  # raises when validation fails
        models.append(import_ifc(out))
    return models


def test_second_export_passes_validation(tmp_path):
    _chain(tmp_path, rounds=2)


def test_reimported_ring_matches_exported_ring(tmp_path):
    m1, m2, m3 = _chain(tmp_path, rounds=2)
    rings = [_bem_from_model(m).ring_m for m in (m1, m2, m3)]
    assert _area(rings[1]) == pytest.approx(_area(rings[0]), rel=1e-6)
    assert _area(rings[2]) == pytest.approx(_area(rings[0]), rel=1e-6)
    assert sorted(map(tuple, rings[1])) == pytest.approx(sorted(map(tuple, rings[0])), abs=1e-6)


def test_round_trip_keeps_space_area_and_volume(tmp_path):
    m1, m2, m3 = _chain(tmp_path, rounds=2)
    for m in (m2, m3):
        for sid, sp in m1.spaces.items():
            assert m.spaces[sid].area_m2 == pytest.approx(sp.area_m2, rel=1e-6)
            assert m.spaces[sid].volume_m3 == pytest.approx(sp.volume_m3, rel=1e-6)


def test_round_trip_keeps_wall_thickness_and_facades(tmp_path):
    _, m2, m3 = _chain(tmp_path, rounds=2)
    for m in (m2, m3):
        assert sorted(w.facade for w in m.envelope) == ["east", "north", "south", "west"]
        thick = [e.thickness_m for e in m.bim_elements if getattr(e, "thickness_m", None)]
        assert thick and all(t == pytest.approx(0.2, abs=1e-6) for t in thick)


def test_round_trip_keeps_openings_on_their_facade(tmp_path):
    m1, m2, m3 = _chain(tmp_path, rounds=2)

    def ops(m):
        return sorted(
            (o.id, o.host_facade, round(o.width_m, 6), round(o.height_m, 6))
            for s in m.spaces.values()
            for o in s.openings
        )

    assert ops(m2) == ops(m1)
    assert ops(m3) == ops(m1)
