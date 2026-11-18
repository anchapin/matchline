"""Stage-6 export visibility (#1409).

Stage 6 (`run_pipeline._stage_6_bem_export`) used to write gbXML/IFC4
directly to the final paths, discard both validators' results, and never
run the geometric checks the gated takeoff path enforces. It now records
all of that in `stage_06_bem/export_report.json` — report-only: the
exports are written exactly as before and nothing is blocked.

Covered here:

  * a clean linked-model export reports every geometric check passing
    and both validators ok;
  * an export whose gbXML shell is open (one wall loop dropped, the
    seeded-defect pattern from test_export_gate) surfaces
    `shell_closure` in the report's failed_checks instead of passing
    silently — while the export file itself is still written.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import run_pipeline
from link import build_model
from synth.multidiscipline import generate_building

NS = "http://www.gbxml.org/schema"
ET.register_namespace("", NS)


def _linked_model(seed: int = 101):
    bldg = generate_building(seed)
    model, _report = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
    return model


def _run_stage_6(model, out_dir: Path):
    sres, wall_height = run_pipeline._stage_3_simplify(model, 0.02, None, out_dir)
    run_pipeline._stage_6_bem_export(model, sres, wall_height, 0.02, out_dir)
    report_path = out_dir / "stage_06_bem" / "export_report.json"
    assert report_path.exists()
    return json.loads(report_path.read_text())


def test_stage_6_clean_export_reports_all_checks_passing(tmp_path):
    report = _run_stage_6(_linked_model(), tmp_path)
    assert report["gbxml"]["ok"] is True
    assert report["ifc4"]["ok"] is True
    assert report["failed_checks"] == []
    assert {c["name"] for c in report["geometric_checks"]} == {
        "shell_closure",
        "shell_winding",
        "shell_volume",
        "planarity",
        "degenerate_faces",
        "duplicate_surfaces",
    }
    assert all(c["status"] == "pass" for c in report["geometric_checks"])


def test_stage_6_open_shell_is_surfaced_not_silent(tmp_path, monkeypatch):
    real_write = run_pipeline.write_gbxml

    def writer(model, path):
        real_write(model, path)
        tree = ET.parse(path)
        shell = tree.getroot().find(f".//{{{NS}}}ClosedShell")
        loops = shell.findall(f"{{{NS}}}PolyLoop")
        shell.remove(loops[2])  # drop one wall loop: the shell is open
        tree.write(path, encoding="utf-8", xml_declaration=True)

    monkeypatch.setattr(run_pipeline, "write_gbxml", writer)
    report = _run_stage_6(_linked_model(), tmp_path)
    # The defective export is still written (report-only, no gate) …
    assert list((tmp_path / "stage_06_bem").glob("*.xml"))
    # … but the failure is now on the record instead of passing silently.
    assert "shell_closure" in report["failed_checks"]
