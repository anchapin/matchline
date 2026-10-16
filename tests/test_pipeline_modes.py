"""Progressive takeoff mode vs full-BEM mode (#998).

Progressive mode stops after the takeoff (stage 1: symbols, areas, counts):
no BEM construction, no review-queue gate, no export. Full mode is the
unchanged default.
"""

import argparse
import json
from pathlib import Path

import pytest

import run_pipeline
from cli import build_parser


def _args(out, mode=None, **kw):
    a = dict(
        seed=101,
        aec_bench=None,
        image=None,
        set_pdf=None,
        ifc_path=None,
        detections=None,
        schedule_csv=None,
        weights=Path("detector/best.pt"),
        out_dir=Path(out),
        open_office_span=False,
        elevation_key="elev_grid",
        detector_config=None,
        storey_height=None,
        climate_zone=None,
        building_category="Nonresidential",
        simplify_tol=0.02,
        mode=mode or "full",
    )
    a.update(kw)
    return argparse.Namespace(**a)


def test_progressive_mode_stops_after_takeoff(tmp_path, capsys):
    run_pipeline.main(_args(tmp_path / "prog", mode="progressive"))
    out = tmp_path / "prog"
    assert (out / "stage_01_building.json").exists()
    # no BEM construction, no review-queue gate, no export
    assert not (out / "stage_02_model.json").exists()
    assert not (out / "stage_04_validation.json").exists()
    assert not (out / "stage_06_bem").exists()
    assert not (out / "review").exists()
    assert "progressive mode" in capsys.readouterr().out


def test_progressive_image_writes_takeoff_without_model(tmp_path):
    # A single sheet image gives symbols and a takeoff but no walls or rooms:
    # in progressive mode that is the deliverable, not an error.
    from PIL import Image

    img = tmp_path / "sheet.png"
    Image.new("L", (64, 64), 255).save(img)
    dets = tmp_path / "preds.json"
    dets.write_text(json.dumps({"image": str(img), "width": 64, "height": 64, "preds": []}))
    run_pipeline.main(_args(tmp_path / "img_out", mode="progressive", image=img, detections=dets))
    out = tmp_path / "img_out"
    assert (out / "stage_01_takeoff.json").exists()
    assert not (out / "stage_02_model.json").exists()
    assert not (out / "stage_06_bem").exists()


def test_full_mode_is_the_default_and_unchanged(tmp_path):
    run_pipeline.main(_args(tmp_path / "full"))  # --mode omitted -> full
    out = tmp_path / "full"
    assert (out / "stage_06_bem").exists()
    assert (out / "review" / "review.html").exists()


def test_cli_run_exposes_mode(tmp_path):
    args = build_parser().parse_args(
        ["run", "--seed", "101", "--out-dir", str(tmp_path), "--mode", "progressive"]
    )
    assert args.mode == "progressive"


def test_cli_run_rejects_unknown_mode():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--seed", "1", "--mode", "turbo"])
