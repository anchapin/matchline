"""Openings on a facade split into one segment per space attach on IFC import.

Two collinear segments meeting at a wall's origin both touch it and can
share a length, so position alone ties. The wall's own Tier 0 envelope
segment carries its GlobalId and settles it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("ifcopenshell")

from building_model import EnvelopeWall, Provenance  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import (  # noqa: E402
    _own_envelope_edge,
    _wall_direction_from_envelope,
    import_ifc,
)
from tests.model_factory import add_wall_constructions, make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _split_roundtrip(tmp_path):
    m = make_clean_model()
    add_wall_constructions(m)
    p = tmp_path / "split.ifc"
    _export_ifc(m, p)
    return m, import_ifc(p)


def _seg(sid, a, b, gid):
    return EnvelopeWall(
        id=sid,
        facade="",
        from_m=list(a),
        to_m=list(b),
        provenance=Provenance(
            sheet_id="t.ifc",
            revision=1,
            method="ifc_import:tier0:envelope",
            confidence=0.95,
            note=f"GlobalId={gid} wall centerline segment",
        ),
    )


def _tie_model():
    # west run ends at (5, 0); east run starts there; both 5 m long
    return SimpleNamespace(
        envelope=[
            _seg("EW1", (0.0, 0.0), (5.0, 0.0), "WEST"),
            _seg("EW2", (5.0, 0.0), (10.0, 0.0), "EAST"),
        ]
    )


def _wall(gid, origin, length=5.0):
    return SimpleNamespace(global_id=gid, placement_m=[*origin, 0.0], length_m=length)


def test_split_facade_no_ambiguous_tie(tmp_path):
    _, back = _split_roundtrip(tmp_path)
    assert back.opening_attachment_summary.ambiguous_tie == 0
    assert not [r for r in back.review_queue if r.kind == "opening_attachment"]


def test_split_facade_attaches_every_opening(tmp_path):
    m, back = _split_roundtrip(tmp_path)
    n_src = sum(len(sp.openings) for sp in m.spaces.values())
    n_back = sum(len(sp.openings) for sp in back.spaces.values())
    assert n_back == n_src


def test_split_facade_roundtrip_has_no_validation_errors(tmp_path):
    _, back = _split_roundtrip(tmp_path)
    assert [c.check_id for c in run_checks(back).errors] == []


def test_identity_resolves_the_position_tie():
    model = _tie_model()
    # the east wall starts where the west run ends: position alone ties
    assert _wall_direction_from_envelope(_wall("EAST", (5.0, 0.0)), model) == ((1.0, 0.0), None)
    assert _wall_direction_from_envelope(_wall("WEST", (0.0, 0.0)), model) == ((1.0, 0.0), None)


def test_unknown_gid_still_refuses_to_guess():
    d, reason = _wall_direction_from_envelope(_wall("OTHER", (5.0, 0.0)), _tie_model())
    assert d is None and reason is not None


def test_own_edge_must_start_at_wall_origin():
    # a segment that moved away from the wall's placement is not trusted
    assert _own_envelope_edge(_wall("EAST", (7.0, 0.0)), _tie_model()) is None


def test_duplicate_gid_claims_are_not_trusted():
    model = _tie_model()
    model.envelope.append(_seg("EW3", (5.0, 0.0), (5.0, 5.0), "EAST"))
    assert _own_envelope_edge(_wall("EAST", (5.0, 0.0)), model) is None


def test_clean_model_attachment_unchanged(tmp_path):
    m = make_clean_model()
    p = tmp_path / "clean.ifc"
    _export_ifc(m, p)
    back = import_ifc(p)
    want = {sid: sorted(o.tag for o in sp.openings) for sid, sp in m.spaces.items()}
    got = {sid: sorted(o.tag for o in sp.openings) for sid, sp in back.spaces.items()}
    assert got == want
