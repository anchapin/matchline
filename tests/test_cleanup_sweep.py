"""Tests for the weekly cleanup-sweep growth check."""

from __future__ import annotations

from scripts.cleanup_sweep import count_paths, evaluate


def _baseline(files: list[str]) -> dict:
    file_count, dirs = count_paths(files)
    return {
        "files": file_count,
        "dirs": len(dirs),
        "file_names": sorted(files),
        "dir_names": sorted(dirs),
    }


def test_growth_within_threshold_passes():
    baseline = _baseline(["a.py", "pkg/b.py"])
    result = evaluate(baseline, ["a.py", "pkg/b.py", "pkg/c.py"])
    assert result.ok
    assert result.new_files == 1 and result.new_dirs == 0


def test_file_growth_over_threshold_fails():
    baseline = _baseline(["a.py"])
    paths = ["a.py"] + [f"g{i}.py" for i in range(11)]
    result = evaluate(baseline, paths, max_new_files=10)
    assert not result.ok
    assert result.new_files == 11


def test_dir_growth_over_threshold_fails():
    baseline = _baseline(["a.py"])
    paths = ["a.py"] + [f"d{i}/x.py" for i in range(4)]
    result = evaluate(baseline, paths, max_new_dirs=3)
    assert not result.ok
    assert result.new_dirs == 4


def test_rename_keeps_count_flat_but_lists_new_names():
    baseline = _baseline(["old.py"])
    result = evaluate(baseline, ["new.py"])
    assert result.ok
    assert result.new_file_names == ["new.py"]
    assert result.removed_file_names == ["old.py"]


def test_count_paths_excludes_root_and_dedups_dirs():
    files, dirs = count_paths(["a.py", "pkg/b.py", "pkg/c.py", "pkg/sub/d.py"])
    assert files == 4
    assert dirs == {"pkg", "pkg/sub"}
