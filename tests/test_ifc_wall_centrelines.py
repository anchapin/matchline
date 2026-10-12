"""IFC envelope segments move onto wall body centrelines (#579)."""

import math

import pytest

T = 0.2


def _with_walls(tmp_path, extra):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.spatial as sp
    import ifcopenshell.guid as guid

    from tests.test_ifc_closet_merge import _place
    from tests.test_ifc_import import _add_solid, _rect_profile, make_ifc_fixture

    path = tmp_path / "base.ifc"
    make_ifc_fixture(path)
    f = ifcopenshell.open(str(path))
    storey = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    walls = []
    for name, start, ref, length, oy in extra:
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        _add_solid(f, w, _rect_profile(f, length, T, ox=length / 2, oy=oy), 3.0, body)
        walls.append(w)
    sp.assign_container(f, products=walls, relating_structure=storey)
    out = tmp_path / "walls.ifc"
    f.write(str(out))
    return out, {w.Name: w.GlobalId for w in walls}


def _seg(model, gid):
    (e,) = [w for w in model.envelope if (w.provenance.note or "").startswith(f"GlobalId={gid} ")]
    return e


def test_face_axis_moves_to_body_centre(tmp_path):
    from ifc_import import import_ifc

    # axis on one face, body 0.2 m to the +y side (IFC): centre is 0.1 m over,
    # which is canonical y = -30.1
    path, gids = _with_walls(tmp_path, [("Face", (40, 30, 0), (1, 0, 0), 5.0, T / 2)])
    model = import_ifc(path)
    e = _seg(model, gids["Face"])
    assert e.from_m[1] == pytest.approx(-30.1, abs=1e-6)
    assert e.to_m[1] == pytest.approx(-30.1, abs=1e-6)
    assert math.dist(e.from_m, e.to_m) == pytest.approx(5.0, abs=1e-6)


def test_centred_axis_stays_put(tmp_path):
    from ifc_import import import_ifc

    path, gids = _with_walls(tmp_path, [("Centred", (40, 30, 0), (1, 0, 0), 5.0, 0.0)])
    model = import_ifc(path)
    e = _seg(model, gids["Centred"])
    assert e.from_m[1] == pytest.approx(-30.0, abs=1e-6)
    assert "wall centrelines" not in (e.provenance.note or "")


def test_shading_keeps_face_offset(tmp_path):
    """The exported plate sits on the face of its centred wall (#609, #611),
    half a wall off the centreline segment; that gap comes back as offset_m."""
    pytest.importorskip("ifcopenshell")
    from tests.test_ifc_shading_import import _roundtrip

    _, bm = _roundtrip(tmp_path)
    by_id = {s.id: s for s in bm.shading}
    assert by_id["SH-OVH-1"].offset_m == pytest.approx(T / 2, abs=1e-4)
    assert by_id["SH-OVH-1"].depth_m == pytest.approx(0.6, abs=1e-3)


def test_own_line_needs_placement_near_an_end():
    from types import SimpleNamespace

    from ifc_import import _on_own_line

    ew = SimpleNamespace(from_m=[0.1, -0.1], to_m=[9.9, -0.1])
    assert _on_own_line(ew, (0.0, 0.0), 0.3)  # face placement, centreline segment
    assert _on_own_line(ew, (10.0, 0.0), 0.3)  # either end
    assert not _on_own_line(ew, (5.0, 0.0), 0.3)  # mid-span: moved elsewhere
    assert not _on_own_line(ew, (0.0, 1.0), 0.3)  # off the line
