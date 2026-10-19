"""run_pipeline --ifc (#988): the IFC input path runs the full stage chain.

The validation gate is input-agnostic: unacknowledged review items block
export exactly as they do for a drawing set, so a first pass stops at the
gate with the review page written; after the human acknowledges the items
(the ``matchline review --confirm`` path) the same chain exports.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ifc.ifc_import import _ensure_ifc

_ensure_ifc()

import run_pipeline  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402


def _args(ifc_path, out_dir):
    return SimpleNamespace(
        out_dir=out_dir,
        simplify_tol=0.02,
        seed=None,
        image=None,
        aec_bench=None,
        set_pdf=None,
        ifc_path=ifc_path,
        detections=None,
        schedule_csv=None,
        elevation_key="elev_grid",
        open_office_span=False,
        detector_config=None,
        storey_height=None,
        climate_zone=None,
        building_category="Nonresidential",
        weights=None,
    )


def _box_ifc(tmp_path):
    b = IfcBuilder()
    box_plan(b)
    path = tmp_path / "box.ifc"
    b.write(path)
    return path


def test_ifc_runs_to_the_gate_then_stops_with_the_review_page(tmp_path):
    ifc_path = _box_ifc(tmp_path)
    out_dir = tmp_path / "out"
    with pytest.raises(SystemExit) as excinfo:
        run_pipeline.main(_args(ifc_path, out_dir))
    assert excinfo.value.code == 1
    stage01 = json.loads((out_dir / "stage_01_building.json").read_text())
    assert stage01["source"] == str(ifc_path)
    assert stage01["schema"] == "IFC4"
    assert (out_dir / "stage_02_model.json").exists()
    assert (out_dir / "stage_03_simplified.json").exists()
    assert (out_dir / "review" / "review.html").exists()
    assert not (out_dir / "stage_06_bem").exists()


def test_ifc_exports_once_review_items_are_acknowledged(tmp_path):
    ifc_path = _box_ifc(tmp_path)
    out_dir = tmp_path / "out"
    args = _args(ifc_path, out_dir)
    model, _, _ = run_pipeline._stage_1_generate(args, out_dir)
    model, _ = run_pipeline._stage_2_build_model(None, args, out_dir, model)
    sres, wall_height = run_pipeline._stage_3_simplify(model, 0.02, None, out_dir)
    from construction_library import apply_construction_library

    apply_construction_library(model, "", "Nonresidential")
    run_pipeline._stage_4_validation(model, sres, None, out_dir)
    run_pipeline._stage_4b_auto_triage(model, out_dir)
    # the human's confirm/reject path (matchline review --confirm): every
    # needs_review item ends up acknowledged, and the confirm step re-runs
    # validation on the acknowledged model (docs/cli.md)
    for item in model.review_queue:
        item.acknowledged = True
    report = run_pipeline._stage_4_validation(model, sres, None, out_dir)
    run_pipeline._stage_5_fail_fast(report, None)
    gbxml, ifc4 = run_pipeline._stage_6_bem_export(model, sres, wall_height, 0.02, out_dir)
    assert gbxml.exists() and ifc4.exists()
