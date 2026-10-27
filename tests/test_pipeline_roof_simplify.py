"""Pipeline wiring for roof simplification (#1275).

Stage 3 simplifies roof planes by default (Alex's 2026-10-25 decision,
superseding the decision doc's opt-in Option C); `--no-roof-simplify` /
`roof_simplify: false` opts out. Fixtures mirror tests/test_roof_simplify.py
(a hip roof whose dormer merges into the south face, 5 planes -> 4).
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

from building_model import Provenance
from geometry.roof_geometry import roof_plane
from run_pipeline import _roof_simplify_enabled, _stage_3_simplify
from tests.model_factory import make_clean_model

LAT = 40.0
PROV = Provenance(
    sheet_id="x.ifc", revision=1, method="ifc_import:tier0:roof_plane", confidence=0.9
)


def _on(tilt_deg, facing, z0, pts):
    t = math.tan(math.radians(tilt_deg))
    rise = {
        "S": lambda x, y: -y,
        "N": lambda x, y: y + 6,
        "E": lambda x, y: 10 - x,
        "W": lambda x, y: x,
    }[facing]
    return [(x, y, z0 + t * rise(x, y)) for x, y in pts]


def _rp(pid, verts, level="L1"):
    return roof_plane(pid, verts, level_id=level, host_global_id=pid, provenance=PROV)


def _hip(notch=False):
    s_pts = [(0, 0), (10, 0), (7, -3), (3, -3)]
    if notch:
        s_pts = [(0, 0), (4, 0), (4, -1), (6, -1), (6, 0), (10, 0), (7, -3), (3, -3)]
    return [
        _rp("S", _on(30, "S", 3, s_pts)),
        _rp("N", _on(30, "N", 3, [(10, -6), (0, -6), (3, -3), (7, -3)])),
        _rp("E", _on(30, "E", 3, [(10, 0), (10, -6), (7, -3)])),
        _rp("W", _on(30, "W", 3, [(0, -6), (0, 0), (3, -3)])),
    ]


def _dormer():
    z_top = 3 + math.tan(math.radians(30))
    t15 = math.tan(math.radians(15))
    return _rp("D", [(4, 0, z_top - t15), (6, 0, z_top - t15), (6, -1, z_top), (4, -1, z_top)])


def _roofed_model(with_latitude=True):
    model = make_clean_model()
    if with_latitude:
        model.site_latitude_deg = LAT
    model.roof_planes = _hip(notch=True) + [_dormer()]
    return model


def _stage3_payload(tmp_path):
    return json.loads((tmp_path / "stage_03_simplified.json").read_text())


def test_stage_3_simplifies_roof_by_default(tmp_path):
    model = _roofed_model()
    _stage_3_simplify(model, 0.02, 3.0, tmp_path)
    record = _stage3_payload(tmp_path)["roof_simplify"]
    assert record["applied"] is True
    assert record["n_planes_before"] == 5
    assert record["n_planes_after"] == 4
    # The model itself is simplified and the source roof is kept for the
    # roof_solar_aperture conservation check.
    assert len(model.roof_planes) == 4
    assert len(model.source_roof_planes) == 5


def test_stage_3_opt_out_leaves_roof_untouched(tmp_path):
    model = _roofed_model()
    before = [p.id for p in model.roof_planes]
    _stage_3_simplify(model, 0.02, 3.0, tmp_path, roof_simplify=False)
    record = _stage3_payload(tmp_path)["roof_simplify"]
    assert record["applied"] is False
    assert "disabled" in record["reason"]
    assert [p.id for p in model.roof_planes] == before
    assert not model.source_roof_planes


def test_stage_3_no_latitude_records_skip(tmp_path):
    model = _roofed_model(with_latitude=False)
    assert model.site_latitude_deg is None
    _stage_3_simplify(model, 0.02, 3.0, tmp_path)
    record = _stage3_payload(tmp_path)["roof_simplify"]
    assert record["applied"] is False
    assert "latitude" in record["reason"]
    assert len(model.roof_planes) == 5
    assert not model.source_roof_planes


def test_stage_3_no_roof_planes_records_skip(tmp_path):
    model = make_clean_model()
    model.site_latitude_deg = LAT
    _stage_3_simplify(model, 0.02, 3.0, tmp_path)
    record = _stage3_payload(tmp_path)["roof_simplify"]
    assert record["applied"] is False
    assert record["n_planes_before"] == 0


def test_roof_simplify_enabled_defaults_and_opt_outs():
    plain = SimpleNamespace(no_roof_simplify=False)
    flagged = SimpleNamespace(no_roof_simplify=True)
    assert _roof_simplify_enabled(plain, None) is True
    assert _roof_simplify_enabled(plain, {}) is True
    assert _roof_simplify_enabled(plain, {"roof_simplify": True}) is True
    assert _roof_simplify_enabled(plain, {"roof_simplify": False}) is False
    assert _roof_simplify_enabled(plain, {"roof_simplify": "false"}) is False
    assert _roof_simplify_enabled(flagged, None) is False
    assert _roof_simplify_enabled(flagged, {"roof_simplify": True}) is False
