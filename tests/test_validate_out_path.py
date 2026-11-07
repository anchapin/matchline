"""Tests for the single shared ``_validate_out_path`` helper (#1337).

``_validate_out_path`` used to exist twice: a weak copy in ``bem/bem_export.py``
(string ``startswith`` prefix check, no parent-dir creation) and the strong copy
in ``bem/bem_helpers.py`` (``relative_to`` check + ``parent.mkdir``).  These
tests pin the strong semantics for every exporter that uses the helper:

  * relative paths escaping the working directory are rejected (traversal),
  * the "cwd-prefix sibling" trick (a sibling directory whose name starts with
    the cwd name) is rejected too — the weak prefix check let it through,
  * a path whose parent directory does not exist yet is accepted and the
    parent is created, through both the gbXML and the IFC write paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bem.bem_export import BEMModel, BEMSpace, write_gbxml
from bem.bem_helpers import _validate_out_path


def _simple_bem_model() -> BEMModel:
    space = BEMSpace(
        sid="L1-101",
        name="Office 101",
        number="101",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        area_m2=100.0,
        volume_m3=300.0,
        lighting_w=0.0,
    )
    return BEMModel(
        building_name="Test Building",
        spaces=[space],
        openings=[],
        ring_m=[(0, 0), (20, 0), (20, 20), (0, 20)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.1,
        skipped_openings=[],
        notes=[],
        zones=[],
    )


class TestSingleDefinition:
    def test_bem_export_reexports_the_helpers_version(self):
        """``bem.bem_export`` must expose the *same* function object."""
        from bem import bem_export
        from bem import bem_helpers

        assert bem_export._validate_out_path is bem_helpers._validate_out_path

    def test_ifc_export_uses_the_shared_version(self):
        from bem import bem_helpers
        from ifc import ifc_export

        assert ifc_export._validate_out_path is bem_helpers._validate_out_path

    def test_run_pipeline_uses_the_shared_version(self):
        from bem import bem_helpers
        import run_pipeline

        assert run_pipeline._validate_out_path is bem_helpers._validate_out_path


class TestTraversalRejected:
    def test_relative_path_escaping_cwd_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="escap"):
            _validate_out_path("../outside.txt")

    def test_deep_relative_path_escaping_cwd_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="escap"):
            _validate_out_path("a/b/../../../../outside.txt")

    def test_cwd_prefix_sibling_is_rejected(self, tmp_path, monkeypatch):
        """A sibling dir named ``<cwd-name>-evil`` must not pass the check.

        The old weak implementation compared ``str(resolved).startswith(cwd)``,
        so ``../proj-evil/x`` (where cwd is ``.../proj``) was accepted.
        """
        cwd = tmp_path / "proj"
        cwd.mkdir()
        sibling = tmp_path / "proj-evil"
        sibling.mkdir()
        monkeypatch.chdir(cwd)
        with pytest.raises(ValueError, match="escap"):
            _validate_out_path("../proj-evil/evil.txt")

    def test_gbxml_write_path_rejects_traversal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="escap"):
            write_gbxml(_simple_bem_model(), "../evil.gbxml")

    def test_ifc_write_path_rejects_traversal(self, tmp_path, monkeypatch):
        from ifc.ifc_export import _export_ifc
        from tests.model_factory import make_clean_model

        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="escap"):
            _export_ifc(make_clean_model(), "../evil.ifc")


class TestParentDirectoryCreated:
    def test_helper_creates_missing_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = Path("nested/dir/out.gbxml")
        assert not target.parent.exists()

        returned = _validate_out_path(target)

        assert returned == target
        assert target.parent.is_dir()

    def test_gbxml_write_path_creates_missing_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = Path("gbxml_out/nested/model.gbxml")
        assert not target.parent.exists()

        written = write_gbxml(_simple_bem_model(), target)

        assert written == target
        assert target.is_file()
        assert target.parent.is_dir()

    def test_ifc_write_path_creates_missing_parent_directory(self, tmp_path, monkeypatch):
        from ifc.ifc_export import _export_ifc
        from tests.model_factory import make_clean_model

        monkeypatch.chdir(tmp_path)
        target = Path("ifc_out/nested/model.ifc")
        assert not target.parent.exists()

        written = _export_ifc(make_clean_model(), target)

        assert Path(written) == target
        assert target.is_file()
        assert target.parent.is_dir()


class TestAbsolutePathsAllowed:
    def test_absolute_path_outside_cwd_is_allowed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "abs" / "out.gbxml"

        returned = _validate_out_path(target)

        assert returned == target
        assert target.parent.is_dir()
