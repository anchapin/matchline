"""The publish-nothing export gate and its geometric checks (#1404).

Phase 1 of issue #1404: an export is staged, validated and geometrically
checked before it is published; a failing run publishes nothing.

Covered here:

  * the staged write (staged temp files in the target directory),
  * the six seeded defect classes (open shell, flipped normal/winding,
    non-planar quad, duplicate surface, degenerate face, negative volume),
  * a failed run leaves a pre-existing good export byte-identical,
  * the unmodified synth corpus exports clean through the gate.
"""

from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from bem import bem_export
from bem.export_gate import geometric_checks, publish_exports

NS = "http://www.gbxml.org/schema"
ET.register_namespace("", NS)

# The synth corpus the Phase 0 audit walked (53 sheets).
CORPUS_SHEETS = 53


# ---- fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    """The unmodified synth corpus: one gt.json per sheet."""
    from synth.sheets import generate_sheet

    out = tmp_path_factory.mktemp("corpus")
    for seed in range(CORPUS_SHEETS):
        _img, gt = generate_sheet(seed, px_per_m=50.0)
        (out / f"{gt['sheet_id']}.gt.json").write_text(json.dumps(gt))
    return out


@pytest.fixture(scope="module")
def sheet(corpus) -> Path:
    return corpus / "sheet_000.gt.json"


def _model(gt_path: Path):
    import run_bem_export

    gt = json.loads(Path(gt_path).read_text())
    model, _rep = run_bem_export._build_model(gt, gt["sheet_id"])
    return model


def _out_dir(tmp_path) -> Path:
    out = tmp_path / "bem_out"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---- defect injection -----------------------------------------------------


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


def _defect_open_shell(root):
    """Drop one wall loop: the shell is no longer closed."""
    shell = _first_shell(root)
    shell.remove(_loops(shell)[2])


def _defect_flipped_normal(root):
    """Reverse one face: its winding no longer matches the shell."""
    shell = _first_shell(root)
    _reverse_loop(_loops(shell)[2])


def _defect_non_planar_quad(root):
    """Push one corner of a wall quad out of its plane along the face normal.

    (Supervisor fix at collection: the original z-lift kept the corner inside
    the wall's vertical plane, so the face stayed planar and only shell
    closure/winding fired — the fixture did not seed the defect it named.)
    """
    import math

    shell = _first_shell(root)
    loop = _loops(shell)[2]
    pts = _points(loop)

    def _xyz(pt):
        return [float(c.text) for c in pt.findall(f"{{{NS}}}Coordinate")]

    a, b, c = _xyz(pts[0]), _xyz(pts[1]), _xyz(pts[2])
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = [
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ]
    norm = math.sqrt(sum(x * x for x in n))
    n = [x / norm for x in n]
    coords = pts[-1].findall(f"{{{NS}}}Coordinate")
    for i in range(3):
        coords[i].text = f"{float(coords[i].text) + 0.5 * n[i]:.4f}"


def _defect_duplicate_surface(root):
    """A second Surface with the same geometry (a different id)."""
    surface = root.find(f".//{{{NS}}}Surface")
    clone = copy.deepcopy(surface)
    # Uniquify every id in the clone subtree: the seeded defect is duplicate
    # GEOMETRY (the gate's job to catch), not duplicate XML ids, which the
    # XSD validator would reject first. (Supervisor fix at collection.)
    for el in clone.iter():
        if el.get("id"):
            el.set("id", f"{el.get('id')}-copy")
    parent = None
    for el in root.iter():
        if surface in list(el):
            parent = el
            break
    parent.append(clone)


def _defect_degenerate_face(root):
    """Collapse a face onto two vertices: zero area, repeated vertices."""
    shell = _first_shell(root)
    loop = _loops(shell)[2]
    pts = _points(loop)
    for target, source in ((pts[1], pts[0]), (pts[3], pts[2])):
        for coord, ref in zip(
            target.findall(f"{{{NS}}}Coordinate"), source.findall(f"{{{NS}}}Coordinate")
        ):
            coord.text = ref.text


def _defect_negative_volume(root):
    """Wind the whole shell inside out: the signed volume goes negative."""
    shell = _first_shell(root)
    for loop in _loops(shell):
        _reverse_loop(loop)


DEFECTS = {
    "open_shell": (_defect_open_shell, "shell_closure"),
    "flipped_normal": (_defect_flipped_normal, "shell_winding"),
    "non_planar_quad": (_defect_non_planar_quad, "planarity"),
    "duplicate_surface": (_defect_duplicate_surface, "duplicate_surfaces"),
    "degenerate_face": (_defect_degenerate_face, "degenerate_faces"),
    "negative_volume": (_defect_negative_volume, "shell_volume"),
}


_REAL_WRITE_GBXML = bem_export.write_gbxml


def _inject(monkeypatch, kind: str) -> None:
    """Make the gbXML writer emit a defective file.

    Always wraps the ORIGINAL writer: a test that loops over several defect
    kinds in one monkeypatch scope must seed exactly one defect per call,
    not accumulate the previous kinds' wrappers. (Supervisor fix at
    collection.)
    """
    real = _REAL_WRITE_GBXML
    defect = DEFECTS[kind][0]

    def writer(model, path):
        real(model, path)
        tree = ET.parse(path)
        defect(tree.getroot())
        tree.write(path, encoding="utf-8", xml_declaration=True)

    monkeypatch.setattr(bem_export, "write_gbxml", writer)


# ---- the staged transaction ----------------------------------------------


def test_export_is_staged_in_the_target_directory(sheet, tmp_path, monkeypatch):
    """The export is written to a staged file beside the final path."""
    seen: list[Path] = []
    real = bem_export.write_gbxml

    def writer(model, path):
        seen.append(Path(path))
        real(model, path)

    monkeypatch.setattr(bem_export, "write_gbxml", writer)
    out = _out_dir(tmp_path)
    result = publish_exports(_model(sheet), out, "sheet_000")
    assert result.ok, result.errors
    assert seen, "the gbXML writer was never called"
    staged = seen[0]
    assert staged.parent == out
    assert staged != out / "sheet_000.xml"
    assert staged.name != "sheet_000.xml"
    assert (out / "sheet_000.xml").exists()
    assert (out / "sheet_000.ifc").exists()
    # no staged file survives a successful publish
    assert not [p for p in out.iterdir() if ".staged-" in p.name]


def test_failed_export_creates_no_final_file(sheet, tmp_path, monkeypatch):
    """A refused export leaves no final file and no staged file behind."""
    _inject(monkeypatch, "open_shell")
    out = _out_dir(tmp_path)
    result = publish_exports(_model(sheet), out, "sheet_000")
    assert not result.ok
    assert result.errors
    assert not (out / "sheet_000.xml").exists()
    assert not (out / "sheet_000.ifc").exists()
    assert sorted(p.name for p in out.iterdir()) == []


def test_failed_export_leaves_previous_good_export_byte_identical(sheet, tmp_path, monkeypatch):
    """Gap A: a corrupted export cannot damage the good one already there."""
    out = _out_dir(tmp_path)
    first = publish_exports(_model(sheet), out, "sheet_000")
    assert first.ok, first.errors
    before = {p.name: p.read_bytes() for p in sorted(out.iterdir())}

    _inject(monkeypatch, "non_planar_quad")
    second = publish_exports(_model(sheet), out, "sheet_000")
    assert not second.ok

    after = {p.name: p.read_bytes() for p in sorted(out.iterdir())}
    assert after == before


def test_failed_export_does_not_touch_the_model(sheet, tmp_path, monkeypatch):
    """The gate observes; it never repairs geometry."""
    _inject(monkeypatch, "duplicate_surface")
    model = _model(sheet)
    before = [list(sp.polygon_m) for sp in model.spaces]
    out = _out_dir(tmp_path)
    result = publish_exports(model, out, "sheet_000")
    assert not result.ok
    assert [list(sp.polygon_m) for sp in model.spaces] == before


# ---- the six seeded defect classes ---------------------------------------


@pytest.mark.parametrize("kind", sorted(DEFECTS))
def test_seeded_defect_is_refused(sheet, tmp_path, monkeypatch, kind):
    """Every seeded defect class is caught: exit 1, nothing published."""
    _inject(monkeypatch, kind)
    out = _out_dir(tmp_path)
    result = publish_exports(_model(sheet), out, "sheet_000")
    assert not result.ok, f"{kind} was not caught"
    assert kind.replace("_", " ") or True
    failed = {c.name for c in result.checks if c.status == "fail"}
    assert DEFECTS[kind][1] in failed, f"{kind}: failed checks were {failed}"
    assert not (out / "sheet_000.xml").exists()
    assert not (out / "sheet_000.ifc").exists()
    assert sorted(p.name for p in out.iterdir()) == []


def test_seeded_defects_keep_the_validators_happy(sheet, tmp_path, monkeypatch):
    """The geometric gate (not the XSD) is what catches the seeded defects."""
    for kind, (_defect, check_name) in DEFECTS.items():
        _inject(monkeypatch, kind)
        out = _out_dir(tmp_path)
        result = publish_exports(_model(sheet), out, "sheet_000")
        assert result.gbxml_ok, f"{kind}: the gbXML validator rejected the file"
        assert result.ifc4_ok, f"{kind}: the IFC4 validator rejected the file"
        assert check_name in result.failed_checks


# ---- the unmodified corpus ------------------------------------------------


def test_synth_corpus_exports_clean(corpus, tmp_path):
    """Every corpus sheet exports clean and gets a manifest."""
    from bem.export_manifest import verify_manifest

    out = tmp_path / "bem_out"
    out.mkdir(parents=True)
    sheets = sorted(corpus.glob("*.gt.json"))
    assert len(sheets) == CORPUS_SHEETS
    for gt_path in sheets:
        sheet_id = gt_path.name[: -len(".gt.json")]
        result = publish_exports(_model(gt_path), out, sheet_id)
        assert result.ok, f"{sheet_id}: {result.errors}"
        assert all(c.status == "pass" for c in result.checks), (
            f"{sheet_id}: {[c.as_dict() for c in result.checks]}"
        )
        manifest = out / f"{sheet_id}.export.json"
        assert manifest.exists()
        report = verify_manifest(manifest)
        assert report.ok, f"{sheet_id}: {report.drift}"


def test_geometric_checks_do_not_modify_the_file(sheet, tmp_path):
    """The checks are read-only."""
    out = _out_dir(tmp_path)
    result = publish_exports(_model(sheet), out, "sheet_000")
    assert result.ok, result.errors
    gbxml = out / "sheet_000.xml"
    before = gbxml.read_bytes()
    checks = geometric_checks(gbxml)
    assert checks and all(c.status == "pass" for c in checks)
    assert gbxml.read_bytes() == before
