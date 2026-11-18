"""The deterministic repair loop (#1407): R1/R2 staged-gbXML repairs and
the R4 opening-placement detector + repair.

Phase 1 of the Solvaix-mined spike (#1406). The loop is off by default
at the library level (``publish_exports`` / ``BEMModel``); the
publish-nothing gate (#1404) is unchanged and remains the final
arbiter. Since Alex's enablement decision (2026-11-17) it is ON by
default at the user-facing entry points (``matchline bem-export`` and
``run_bem_export.main``), with ``--no-repair-loop`` /
``repair_loop=False`` as the opt-out — covered in the entry-layer
section at the end of this file. Covered here:

  * R1: an inside-out shell and a uniquely flipped face are repaired;
    a shell with no unique single-loop fix is left byte-identical.
  * R2: later duplicate surfaces are dropped, the first is kept, the
    dropped ids are recorded — including the natural ``seed312-svc``
    duplicate case from the Phase 0 census (its pre-existing R5
    shell-closure failure is neither fixed nor worsened).
  * R4: the placement-overlap detector, and repairs that shift only
    unstated openings (stated placements byte-identical, widths kept);
    stated-vs-stated and no-fit overlaps are not repaired.
  * Publish integration: with the loop on, a seeded R1/R2 export
    publishes *through the full gate* with the repairs in the manifest;
    a clean export is byte-identical to the loop-off export; a defect
    outside the loop's classes still refuses the export.
  * No collateral damage: no geometric check that passed before a
    repair fails after it.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from bem import bem_export
from bem.export_gate import geometric_checks, publish_exports
from bem.repair_loop import (
    placement_overlaps,
    repair_placement_overlaps,
    repair_staged_gbxml,
)

NS = "http://www.gbxml.org/schema"
ET.register_namespace("", NS)


# ---- fixtures -------------------------------------------------------------


def _model_for_seed(seed: int):
    import run_bem_export
    from synth.sheets import generate_sheet

    _img, gt = generate_sheet(seed, px_per_m=50.0)
    model, _rep = run_bem_export._build_model(gt, gt["sheet_id"])
    return model


@pytest.fixture(scope="module")
def sheet0_model():
    return _model_for_seed(0)


def _statuses(checks) -> dict:
    return {c.name: c.status for c in checks}


def _no_collateral(before: dict, after: dict) -> None:
    """Bar (d): no check that passed before a repair fails after it."""
    for name, status in before.items():
        if status == "pass":
            assert after[name] == "pass", f"{name} passed before, is {after[name]} after"


# ---- defect injection (same pattern as tests/test_export_gate.py) ---------


def _first_shell(root):
    return root.find(f".//{{{NS}}}ClosedShell")


def _loops(shell):
    return shell.findall(f"{{{NS}}}PolyLoop")


def _points(loop):
    return loop.findall(f"{{{NS}}}CartesianPoint")


def _reverse_loop(loop):
    pts = _points(loop)
    for pt in pts:
        loop.remove(pt)
    for pt in reversed(pts):
        loop.append(pt)


def _defect_negative_volume(root):
    shell = _first_shell(root)
    for loop in _loops(shell):
        _reverse_loop(loop)


def _defect_flipped_normal(root):
    shell = _first_shell(root)
    _reverse_loop(_loops(shell)[2])


def _defect_two_flipped_faces(root):
    shell = _first_shell(root)
    _reverse_loop(_loops(shell)[2])
    _reverse_loop(_loops(shell)[3])


def _defect_duplicate_surface(root):
    surface = root.find(f".//{{{NS}}}Surface")
    clone = copy.deepcopy(surface)
    for el in clone.iter():
        if el.get("id"):
            el.set("id", f"{el.get('id')}-copy")
    for el in root.iter():
        if surface in list(el):
            el.append(clone)
            return


def _defect_non_planar_quad(root):
    import math

    shell = _first_shell(root)
    loop = _loops(shell)[2]
    pts = _points(loop)

    def _xyz(pt):
        return [float(c.text) for c in pt.findall(f"{{{NS}}}Coordinate")]

    a, b, c = _xyz(pts[0]), _xyz(pts[1]), _xyz(pts[2])
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
    norm = math.sqrt(sum(x * x for x in n))
    n = [x / norm for x in n]
    coords = pts[-1].findall(f"{{{NS}}}Coordinate")
    for i in range(3):
        coords[i].text = f"{float(coords[i].text) + 0.5 * n[i]:.4f}"


def _staged_defect(tmp_path, model, defect) -> Path:
    """Write the model's gbXML, seed one defect, return the staged path."""
    staged = tmp_path / "staged.xml"
    bem_export.write_gbxml(model, staged)
    tree = ET.parse(staged)
    defect(tree.getroot())
    tree.write(staged, encoding="utf-8", xml_declaration=True)
    return staged


# ---- R1 -------------------------------------------------------------------


def test_r1_inside_out_shell_is_repaired(sheet0_model, tmp_path):
    staged = _staged_defect(tmp_path, sheet0_model, _defect_negative_volume)
    before = _statuses(geometric_checks(staged, model=sheet0_model))
    assert before["shell_volume"] == "fail"

    records, final = repair_staged_gbxml(
        staged, geometric_checks(staged, model=sheet0_model), model=sheet0_model
    )
    after = _statuses(final)
    assert [r.repair_class for r in records] == ["R1"]
    assert records[0].method == "repair:R1"
    assert records[0].affected["shells_reversed"]
    assert all(s == "pass" for s in after.values())
    _no_collateral(before, after)
    ok, errors = bem_export.validate_gbxml(staged)
    assert ok, errors


def test_r1_uniquely_flipped_face_is_repaired(sheet0_model, tmp_path):
    staged = _staged_defect(tmp_path, sheet0_model, _defect_flipped_normal)
    before = _statuses(geometric_checks(staged, model=sheet0_model))
    assert before["shell_winding"] == "fail"

    records, final = repair_staged_gbxml(
        staged, geometric_checks(staged, model=sheet0_model), model=sheet0_model
    )
    after = _statuses(final)
    assert [r.repair_class for r in records] == ["R1"]
    assert records[0].affected["loops_reversed"]
    assert all(s == "pass" for s in after.values())
    _no_collateral(before, after)


def test_r1_no_unique_fix_leaves_the_file_byte_identical(sheet0_model, tmp_path):
    """Two flipped faces: no single-loop reversal restores consistency,
    so the loop must not guess — the staged file is left untouched."""
    staged = _staged_defect(tmp_path, sheet0_model, _defect_two_flipped_faces)
    before_bytes = staged.read_bytes()
    before = _statuses(geometric_checks(staged, model=sheet0_model))
    assert before["shell_winding"] == "fail"

    records, final = repair_staged_gbxml(
        staged, geometric_checks(staged, model=sheet0_model), model=sheet0_model
    )
    assert records == []
    assert _statuses(final)["shell_winding"] == "fail"
    assert staged.read_bytes() == before_bytes


# ---- R2 -------------------------------------------------------------------


def test_r2_duplicate_surface_is_dropped_and_logged(sheet0_model, tmp_path):
    staged = _staged_defect(tmp_path, sheet0_model, _defect_duplicate_surface)
    before = _statuses(geometric_checks(staged, model=sheet0_model))
    assert before["duplicate_surfaces"] == "fail"

    records, final = repair_staged_gbxml(
        staged, geometric_checks(staged, model=sheet0_model), model=sheet0_model
    )
    after = _statuses(final)
    assert [r.repair_class for r in records] == ["R2"]
    removed = records[0].affected["removed_surface_ids"]
    assert len(removed) == 1 and removed[0].endswith("-copy")
    assert all(s == "pass" for s in after.values())
    _no_collateral(before, after)


def test_r1_and_r2_repair_in_two_iterations(sheet0_model, tmp_path):
    """One class per iteration, max two: a file with both defects is
    repaired by an R1 pass and then an R2 pass."""

    def both(root):
        _defect_negative_volume(root)
        _defect_duplicate_surface(root)

    staged = _staged_defect(tmp_path, sheet0_model, both)
    records, final = repair_staged_gbxml(
        staged, geometric_checks(staged, model=sheet0_model), model=sheet0_model
    )
    assert [r.repair_class for r in records] == ["R1", "R2"]
    assert [r.iteration for r in records] == [1, 2]
    assert all(c.status == "pass" for c in final)


def test_natural_seed312_duplicate_surfaces(sheet0_model, tmp_path):
    """Bar (b), natural case: the Phase 0 census found duplicate surfaces
    in seed312-svc's linked-model export (plus a pre-existing R5
    shell-closure failure the loop must leave exactly as it is)."""
    from geometry.geometry_simplify import footprint_from_regions, simplify_ring
    from link import build_model
    from run_pipeline import model_from_linked_model
    from synth.multidiscipline import generate_building

    bldg = generate_building(312, service_rooms=True)
    linked, _rep = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
    h = linked.levels[0].wall_height_m
    ring = footprint_from_regions([sp.polygon_m for sp in linked.spaces.values()])
    sres = simplify_ring(ring, tol=0.02, wall_height=h)
    model = model_from_linked_model(
        model=linked, simplified_ring=sres, wall_height_m=h, simplify_tolerance=2.0
    )
    staged = tmp_path / "seed312.xml"
    bem_export.write_gbxml(model, staged)
    checks = geometric_checks(staged, model=model)
    before = _statuses(checks)
    assert before["duplicate_surfaces"] == "fail"
    assert before["shell_closure"] == "fail"  # the pre-existing R5 defect
    volume_before = next(
        c.measurement["total_volume_m3"] for c in checks if c.name == "shell_volume"
    )

    records, final = repair_staged_gbxml(staged, checks, model=model)
    after = _statuses(final)
    assert [r.repair_class for r in records] == ["R2"]
    assert len(records[0].affected["removed_surface_ids"]) == 4
    assert after["duplicate_surfaces"] == "pass"
    # the R5 failure is untouched: neither fixed nor worsened
    assert after["shell_closure"] == "fail"
    volume_after = next(c.measurement["total_volume_m3"] for c in final if c.name == "shell_volume")
    assert volume_after == pytest.approx(volume_before)
    _no_collateral(before, after)


# ---- publish integration ----------------------------------------------------


def _inject_writer(monkeypatch, defect) -> None:
    real = bem_export.write_gbxml

    def writer(model, path):
        real(model, path)
        tree = ET.parse(path)
        defect(tree.getroot())
        tree.write(path, encoding="utf-8", xml_declaration=True)

    monkeypatch.setattr(bem_export, "write_gbxml", writer)


def test_publish_with_loop_off_still_refuses_r2(sheet0_model, tmp_path, monkeypatch):
    _inject_writer(monkeypatch, _defect_duplicate_surface)
    out = tmp_path / "out"
    result = publish_exports(sheet0_model, out, "sheet_000")
    assert not result.ok
    assert result.repairs == []
    assert not (out / "sheet_000.xml").exists()


@pytest.mark.parametrize(
    "defect,check",
    [
        (_defect_negative_volume, "shell_volume"),
        (_defect_flipped_normal, "shell_winding"),
        (_defect_duplicate_surface, "duplicate_surfaces"),
    ],
)
def test_publish_with_loop_on_repairs_and_publishes_through_full_gate(
    sheet0_model, tmp_path, monkeypatch, defect, check
):
    """Bar (b): the seeded R1/R2 defects publish after repair — and the
    published file passed the FULL gate (XSD + IFC4 + geometric), not
    merely the loop's inner checks."""
    _inject_writer(monkeypatch, defect)
    out = tmp_path / "out"
    result = publish_exports(sheet0_model, out, "sheet_000", repair_loop=True)
    assert result.ok, result.errors
    assert result.gbxml_ok and result.ifc4_ok
    assert all(c.status == "pass" for c in result.checks)
    assert len(result.repairs) == 1
    assert result.repairs[0]["check"] == check

    manifest = json.loads((out / "sheet_000.export.json").read_text())
    repairs = manifest["validation"]["repairs"]
    assert len(repairs) == 1 and repairs[0]["method"].startswith("repair:")
    from bem.export_manifest import verify_manifest

    assert verify_manifest(out / "sheet_000.export.json").ok


def test_publish_with_loop_on_still_refuses_an_r5_defect(sheet0_model, tmp_path, monkeypatch):
    """The loop does not widen the gate: a non-planar quad (R5) with the
    loop on is refused exactly as with it off."""
    _inject_writer(monkeypatch, _defect_non_planar_quad)
    out = tmp_path / "out"
    result = publish_exports(sheet0_model, out, "sheet_000", repair_loop=True)
    assert not result.ok
    assert "planarity" in result.failed_checks
    assert not (out / "sheet_000.xml").exists()


def _ifc_semantic_digest(path) -> str:
    """Order-insensitive semantic digest of an IFC4 file.

    The manifest's normalised digest (#1404) still varies between two
    publishes of the same model — a pre-existing writer property,
    verified loop-off vs loop-off: IfcOpenShell emits one entity's
    reference list in set order, which also renumbers the instances.
    Sorting the normalised lines and canonicalising instance numbers
    makes the comparison about content, not emission order.
    """
    import hashlib
    import re

    from bem.export_manifest import normalise_ifc

    text = normalise_ifc(Path(path).read_text(encoding="utf-8"))
    lines = sorted(re.sub(r"#\d+", "#", text).splitlines())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def test_clean_export_is_byte_identical_with_the_loop_on(tmp_path):
    """Bar (a): no drift on the clean set — gbXML bytes identical, IFC4
    semantically identical, and the manifest records the empty repair
    list the brief requires for a loop-enabled run."""
    model_off = _model_for_seed(0)
    model_on = _model_for_seed(0)
    out_off, out_on = tmp_path / "off", tmp_path / "on"
    res_off = publish_exports(model_off, out_off, "sheet_000")
    res_on = publish_exports(model_on, out_on, "sheet_000", repair_loop=True)
    assert res_off.ok and res_on.ok
    assert (out_on / "sheet_000.xml").read_bytes() == (out_off / "sheet_000.xml").read_bytes()
    assert _ifc_semantic_digest(out_on / "sheet_000.ifc") == _ifc_semantic_digest(
        out_off / "sheet_000.ifc"
    )
    manifest_on = json.loads((out_on / "sheet_000.export.json").read_text())
    assert manifest_on["validation"]["repairs"] == []
    manifest_off = json.loads((out_off / "sheet_000.export.json").read_text())
    assert "repairs" not in manifest_off["validation"]


# ---- R4 -------------------------------------------------------------------


def _unit(tag, width, stated=None, category="window"):
    return SimpleNamespace(
        tag=tag, category=category, width_m=width, height_m=1.2, sill_m=None, s_center_m=stated
    )


def _placements(units, L=10.0, h=3.0, **kw):
    from bem.bem_helpers import _place_openings_on_wall

    return _place_openings_on_wall(units, L, h, **kw)


def test_r4_detector_finds_stated_vs_unstated_overlap():
    units = [_unit("W-stated", 1.2, stated=2.0), _unit("W-seq1", 1.5), _unit("W-seq2", 1.0)]
    placements, _notes = _placements(units)
    overlaps = placement_overlaps(placements)
    assert overlaps, "the seeded stated-centre layout should overlap"
    assert {o["tag_a"] for o in overlaps} | {o["tag_b"] for o in overlaps} >= {"W-stated"}


def test_r4_detector_is_quiet_on_a_clean_layout():
    units = [_unit("W-a", 1.5), _unit("W-b", 1.0)]
    placements, _notes = _placements(units)
    assert placement_overlaps(placements) == []


@pytest.mark.parametrize("stated_first", [True, False])
def test_r4_repair_shifts_only_the_unstated_openings(stated_first):
    """Phase 0 scenarios S1/S2: the overlap resolves, the stated
    placement is the same dict object (never moved), widths unchanged."""
    stated = _unit("W-stated", 1.2, stated=2.0)
    seq = [_unit("W-seq1", 1.5), _unit("W-seq2", 1.0)]
    units = [stated, *seq] if stated_first else [*seq, stated]
    placements, _notes = _placements(units)
    assert placement_overlaps(placements)

    repaired, moves = repair_placement_overlaps(placements, 10.0)
    assert repaired is not None
    assert placement_overlaps(repaired) == []
    stated_after = next(p for p in repaired if p["unit"] is stated)
    stated_before = next(p for p in placements if p["unit"] is stated)
    assert stated_after is stated_before
    widths = {p["unit"].tag: p["s1"] - p["s0"] for p in repaired}
    assert widths == {
        "W-stated": pytest.approx(1.2),
        "W-seq1": pytest.approx(1.5),
        "W-seq2": pytest.approx(1.0),
    }
    assert moves and all(m["tag"] != "W-stated" for m in moves)


def test_r4_stated_vs_stated_overlap_is_not_repaired():
    """Phase 0 scenario S3: nothing may move, so there is no repair."""
    units = [_unit("W-a", 1.2, stated=4.0), _unit("W-b", 1.2, stated=4.6)]
    placements, _notes = _placements(units)
    assert placement_overlaps(placements)
    repaired, moves = repair_placement_overlaps(placements, 10.0)
    assert repaired is None and moves == []


def test_r4_crowded_wall_no_fit_is_not_repaired():
    """Phase 0 scenario S4: the unstated openings fit in no free interval."""
    units = [_unit("W-seq1", 2.0), _unit("W-stated", 2.0, stated=3.0), _unit("W-seq2", 1.8)]
    placements, _notes = _placements(units, L=6.0)
    assert placement_overlaps(placements)
    repaired, _moves = repair_placement_overlaps(placements, 6.0)
    assert repaired is None


def test_r4_placement_function_repairs_when_the_loop_is_on():
    units = [_unit("W-stated", 1.2, stated=2.0), _unit("W-seq1", 1.5), _unit("W-seq2", 1.0)]
    off, _ = _placements(units)
    stated_off = next(p for p in off if p["unit"].tag == "W-stated")

    log: list = []
    on, notes = _placements(units, repair_loop=True, repair_log=log)
    assert placement_overlaps(on) == []
    stated_on = next(p for p in on if p["unit"].tag == "W-stated")
    assert (stated_on["s0"], stated_on["s1"]) == (stated_off["s0"], stated_off["s1"])
    assert any("repair:R4" in n for n in notes)
    assert len(log) == 1 and log[0]["method"] == "repair:R4"


def test_r4_placement_function_is_unchanged_when_the_loop_is_off():
    units = [_unit("W-stated", 1.2, stated=2.0), _unit("W-seq1", 1.5), _unit("W-seq2", 1.0)]
    placements, notes = _placements(units)
    assert placement_overlaps(placements)  # today's behaviour: overlap, unchecked
    assert not any("repair:R4" in n for n in notes)


# ---- entry-layer enablement (Alex's decision, 2026-11-17) ------------------
#
# The loop is ON by default at the user-facing entry points only:
# ``matchline bem-export`` and ``run_bem_export.main`` (the simplification
# precedent — on by default, explicit opt-out). The library defaults
# above (``publish_exports``, ``BEMModel.repair_loop``) stay off.


def _entry_export(workdir: Path, defect=None, monkeypatch=None, **main_kw):
    """One export through ``run_bem_export.main`` in ``workdir``."""
    import run_bem_export
    from synth.sheets import generate_sheet

    if defect is not None:
        _inject_writer(monkeypatch, defect)
    workdir.mkdir(parents=True, exist_ok=True)
    _img, gt = generate_sheet(0, px_per_m=50.0)
    gt_path = workdir / f"{gt['sheet_id']}.gt.json"
    gt_path.write_text(json.dumps(gt))
    out = workdir / "out"
    ok = run_bem_export.main(gt["sheet_id"], gt_path=gt_path, out_dir=out, **main_kw)
    return ok, out


def test_entry_default_repairs_seeded_r2_and_records_it_in_the_manifest(
    tmp_path, monkeypatch, capsys
):
    """Enablement (a): no flag, no kwarg — the seeded duplicate-surface
    export publishes through the entry layer, repairs in its manifest."""
    ok, out = _entry_export(tmp_path, defect=_defect_duplicate_surface, monkeypatch=monkeypatch)
    capsys.readouterr()
    assert ok
    assert (out / "sheet_000.xml").exists()
    manifest = json.loads((out / "sheet_000.export.json").read_text())
    repairs = manifest["validation"]["repairs"]
    assert len(repairs) == 1
    assert repairs[0]["method"] == "repair:R2"
    assert repairs[0]["check"] == "duplicate_surfaces"


def test_entry_opt_out_restores_the_publish_failure(tmp_path, monkeypatch, capsys):
    """Enablement (b): ``repair_loop=False`` at the entry layer is the
    old behaviour — the seeded defect refuses the export."""
    ok, out = _entry_export(
        tmp_path,
        defect=_defect_duplicate_surface,
        monkeypatch=monkeypatch,
        repair_loop=False,
    )
    capsys.readouterr()
    assert not ok
    assert not (out / "sheet_000.xml").exists()


def test_cli_bem_export_repair_loop_defaults_on_with_opt_out():
    """The CLI mirrors the entry default: on, ``--no-repair-loop`` off,
    and the old ``--repair-loop`` spelling still parses (as on)."""
    from cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["bem-export"]).repair_loop is True
    assert parser.parse_args(["bem-export", "--no-repair-loop"]).repair_loop is False
    assert parser.parse_args(["bem-export", "--repair-loop"]).repair_loop is True


def test_entry_clean_export_with_defaults_matches_the_opt_out(tmp_path, capsys):
    """Enablement (c): a clean export through the entry layer with the
    new default is unchanged from the opt-out export — gbXML
    byte-identical, IFC4 semantically identical, and neither manifest
    carries a repairs key when nothing was repaired."""
    ok_on, out_on = _entry_export(tmp_path / "default")
    ok_off, out_off = _entry_export(tmp_path / "optout", repair_loop=False)
    capsys.readouterr()
    assert ok_on and ok_off
    assert (out_on / "sheet_000.xml").read_bytes() == (out_off / "sheet_000.xml").read_bytes()
    assert _ifc_semantic_digest(out_on / "sheet_000.ifc") == _ifc_semantic_digest(
        out_off / "sheet_000.ifc"
    )
    for out in (out_on, out_off):
        manifest = json.loads((out / "sheet_000.export.json").read_text())
        assert "repairs" not in manifest["validation"]
