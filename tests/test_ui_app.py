"""Unit tests for the matchline-ui web app (#982).

The app imports gradio lazily, so these run in the plain test install: they
cover the parts that must match the CLI (command construction) and the
result plumbing (stage watching, counts, summary, output discovery).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ui.app import (
    DEFAULT_SIMPLIFY_TOL,
    build_command,
    find_outputs,
    load_stage_model,
    next_stage_lines,
    opening_counts,
    summarize,
)


def test_build_command_set(tmp_path):
    cmd = build_command(tmp_path / "set.pdf", None, 0.03, tmp_path / "out")
    assert cmd[1:3] == ["-m", "run_pipeline"]
    assert "--set" in cmd and str(tmp_path / "set.pdf") in cmd
    assert "--seed" not in cmd
    tol = cmd[cmd.index("--simplify-tol") + 1]
    assert tol == "0.03"
    assert cmd[cmd.index("--out-dir") + 1] == str(tmp_path / "out")


def test_build_command_seed(tmp_path):
    cmd = build_command(None, 7, DEFAULT_SIMPLIFY_TOL, tmp_path)
    assert "--seed" in cmd and "7" in cmd
    assert "--set" not in cmd
    assert cmd[cmd.index("--simplify-tol") + 1] == "0.02"


def test_next_stage_lines_in_order(tmp_path):
    out = tmp_path / "out"
    (out / "stage_01_building.json").parent.mkdir(parents=True)
    (out / "stage_01_building.json").write_text("{}")
    bem = out / "stage_06_bem"
    bem.mkdir()
    # an empty directory does not count; only once it holds something
    lines = next_stage_lines(out, set())
    assert [(name, line) for name, line in lines] == [("stage_01_building.json", lines[0][1])]
    (bem / "b.xml").write_text("<gbXML/>")
    lines2 = next_stage_lines(out, {name for name, _ in lines})
    assert [name for name, _ in lines2] == ["stage_06_bem"]
    assert next_stage_lines(out, {name for name, _ in lines} | {n for n, _ in lines2}) == []


def test_load_stage_model(tmp_path):
    out = tmp_path
    assert load_stage_model(out) is None
    (out / "stage_02_model.json").write_text("not json")
    assert load_stage_model(out) is None
    (out / "stage_02_model.json").write_text(json.dumps({"model": {"name": "X"}}))
    assert load_stage_model(out) == {"name": "X"}


def test_load_stage_model_unwraps_seed_wrapper(tmp_path):
    """The synthetic path stores to_json()'s wrapper; spaces sit one level deeper."""
    out = tmp_path
    payload = {"model": {"model_version": "1.0", "model": {"name": "X", "spaces": {}}}}
    (out / "stage_02_model.json").write_text(json.dumps(payload))
    model = load_stage_model(out)
    assert model is not None and model["name"] == "X"


def test_opening_counts():
    model = {
        "spaces": {
            "L1-1": {
                "openings": [
                    {"category": "window"},
                    {"category": "window"},
                    {"category": "door"},
                ]
            },
            "L1-2": {"openings": [{"category": "skylight"}, {"category": "door"}]},
            "L1-3": {"openings": []},
        }
    }
    assert opening_counts(model) == {"window": 2, "door": 2, "skylight": 1}


def test_summarize_counts():
    model = {
        "name": "Clinic",
        "levels": [{"id": "L1"}],
        "spaces": {
            "L1-1": {"openings": [{"category": "window"}]},
            "L1-2": {"openings": [{"category": "door"}]},
        },
        "review_queue": [{"id": "a"}, {"id": "b"}],
    }
    text = summarize(model)
    assert "Clinic" in text
    assert "Levels: 1" in text and "Rooms: 2" in text
    assert "Windows: 1" in text and "Doors: 1" in text
    assert "Items to review: 2" in text


def test_find_outputs(tmp_path):
    out = tmp_path
    assert find_outputs(out)["gbxml"] is None
    bem = out / "stage_06_bem"
    bem.mkdir()
    (bem / "clinic.xml").write_text("<gbXML/>")
    (bem / "clinic.ifc").write_text("ISO-10303-21;")
    review = out / "review"
    review.mkdir()
    (review / "review.html").write_text("<html/>")
    (out / "stage_02_model.json").write_text("{}")
    found = find_outputs(out)
    assert found["gbxml"] == bem / "clinic.xml"
    assert found["ifc"] == bem / "clinic.ifc"
    assert found["review_html"] == review / "review.html"
    assert found["model_json"] == out / "stage_02_model.json"


def test_run_flow_requires_input():
    """No PDF and no demo tick yields the instruction, not a pipeline call."""
    import ui.app as app

    result = next(app.run_flow(None, False, None, DEFAULT_SIMPLIFY_TOL))
    assert isinstance(result, tuple)
    assert "demo" in result[0] or "PDF" in result[0]


def test_gradio_import_is_lazy():
    """The module must import without gradio installed (core install)."""
    import ui.app as app

    assert callable(app.build_command)
    try:
        import gradio  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    if not installed:
        # build_demo is the only gradio entry point; without the extra it
        # must raise ImportError, not NameError.
        with pytest.raises(ImportError):
            app.build_demo()


def test_build_command_ifc(tmp_path):
    cmd = build_command(tmp_path / "model.ifc", None, 0.02, tmp_path / "out")
    assert "--ifc" in cmd and str(tmp_path / "model.ifc") in cmd
    assert "--set" not in cmd and "--seed" not in cmd


def test_build_command_zip_runs_the_set_path(tmp_path):
    cmd = build_command(tmp_path / "set.zip", None, 0.02, tmp_path / "out")
    assert "--set" in cmd and str(tmp_path / "set.zip") in cmd
    assert "--ifc" not in cmd


def test_build_command_image_runs_the_image_path(tmp_path):
    cmd = build_command(tmp_path / "sheet.png", None, 0.02, tmp_path / "out")
    assert "--image" in cmd and str(tmp_path / "sheet.png") in cmd


def test_input_kind_covers_the_three_inputs():
    from ui.app import input_kind

    assert input_kind(Path("a.pdf")) == "set"
    assert input_kind(Path("a.zip")) == "set"
    assert input_kind(Path("a.ifc")) == "ifc"
    assert input_kind(Path("a.ifcxml")) == "ifc"
    assert input_kind(Path("a.png")) == "image"
    assert input_kind(Path("a.JPG")) == "image"
