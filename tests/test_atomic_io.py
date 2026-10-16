"""Atomic JSON writes (#1039): the destination holds old bytes or new bytes,
never a partial write, and a failed write leaves the original intact."""

from __future__ import annotations

import json
from pathlib import Path

import corrections
import legend_learning
import run_pipeline
from atomic_io import write_text_atomic


def test_replaces_content(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text('{"old": 1}')
    write_text_atomic(p, '{"new": 2}')
    assert p.read_text() == '{"new": 2}'


def test_failure_preserves_original_and_no_leftovers(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.json"
    p.write_text('{"old": 1}')

    import os as _os

    def broken_replace(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(_os, "replace", broken_replace)
    try:
        write_text_atomic(p, '{"new": 2}')
    except OSError:
        pass
    else:
        raise AssertionError("expected the simulated failure to propagate")
    assert p.read_text() == '{"old": 1}'
    assert sorted(x.name for x in tmp_path.iterdir()) == ["x.json"]


def test_write_json_failure_preserves_original(tmp_path: Path):
    p = tmp_path / "stage.json"
    p.write_text('{"old": 1}')
    bad: dict = {}
    bad["self"] = bad  # circular reference: json.dumps raises ValueError
    try:
        run_pipeline.write_json(p, bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
    assert p.read_text() == '{"old": 1}'


def test_corrections_save_roundtrip_and_failure(tmp_path: Path):
    model = tmp_path / "m.ifc"
    model.write_text("stub")
    doc = {"schema": corrections.SCHEMA, "corrections": [{"kind": "k", "id": "1"}]}
    corrections.save(model, doc)
    loaded = corrections.load(model)
    assert loaded["schema"] == corrections.SCHEMA
    assert loaded["corrections"][0]["id"] == "1"
    try:
        corrections.save(model, {"corrections": object()})
    except TypeError:
        pass
    else:
        raise AssertionError("expected TypeError")
    assert corrections.load(model)["corrections"][0]["id"] == "1"


def test_save_tags_roundtrip_and_failure(tmp_path: Path):
    p = tmp_path / "sub" / "legend_tags.json"
    legend_learning.save_tags(p, {"HT-1": "ahu"})
    assert json.loads(p.read_text())["tags"] == {"HT-1": "ahu"}
    try:
        legend_learning.save_tags(p, {"bad tag!": "x"})
    except legend_learning.LegendTagsError:
        pass
    else:
        raise AssertionError("expected LegendTagsError")
    assert json.loads(p.read_text())["tags"] == {"HT-1": "ahu"}
