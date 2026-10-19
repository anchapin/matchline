#!/usr/bin/env python3
"""Generate the pinned public API surface (gate #1159).

Walks the declared public modules and packages with ast (no imports), collects
top-level public functions and classes with their signatures, and writes
tests/api_surface.json (schema "matchline.api_surface/1"). The CI guard test
compares the live surface against that snapshot; on drift, regenerate this
file and add a deprecation notice per docs/api.md before changing the pins.

Also renders docs/api_reference.md, the human-readable page of every
public symbol with its signature, generated from the same walk; the CI
guard keeps that page in sync with the pins (gate #1167).

Run:  python3 scripts/generate_api_surface.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "api_surface.json"
DOC = ROOT / "docs" / "api_reference.md"
SCHEMA = "matchline.api_surface/1"

# Public surface: every module or package a 1.0 user drives through the
# `matchline` CLI or the docs guides. Rule (docs/api.md): a top-level module
# is public when it has a module-named guide under docs/, plus run_pipeline
# (documented at docs/pipeline.md). Packages are public when the README
# module table documents them. Everything else is internal.
PUBLIC_MODULES = [
    "bem_export",
    "building_model",
    "cli",
    "construction_library",
    "corrections",
    "datasets_adapter",
    "detection_provider",
    "drawing_scale",
    "elevation_windows",
    "export_package",
    "facade_takeoff",
    "geometry_simplify",
    "grid_detect",
    "hvac_trace",
    "ifc_export",
    "ifc_import",
    "jesse",
    "legend_learning",
    "lighting",
    "mech_schedule",
    "pdf_ingest",
    "pdf_schedules",
    "plan_walls",
    "polygon_classify",
    "real_set",
    "registration",
    "review_report",
    "review_viewer",
    "room_labels",
    "run_pipeline",
    "run_review",
    "safe_xml",
    "sheet_index",
    "sim_smoke",
    "space_merge",
    "space_use_defaults",
    "symbols",
]
PUBLIC_PACKAGES = ["link", "validate"]


def _sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return ast.unparse(node.args)


def _class_sig(node: ast.ClassDef) -> str:
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
            return ast.unparse(item.args)
    return ""


def _public_defs(tree: ast.Module) -> dict:
    out: dict = {"functions": {}, "classes": {}}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith(
            "_"
        ):
            out["functions"][node.name] = _sig(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out["classes"][node.name] = _class_sig(node)
    return out


def build() -> dict:
    surface: dict = {"schema": SCHEMA, "modules": {}, "packages": {}}
    for name in PUBLIC_MODULES:
        path = ROOT / f"{name}.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        surface["modules"][name] = _public_defs(tree)
    for name in PUBLIC_PACKAGES:
        path = ROOT / name / "__init__.py"
        tree = ast.parse(path.read_text(), filename=str(path))
        surface["packages"][name] = _public_defs(tree)
    return surface


def render_reference(surface: dict) -> str:
    """Render the public API reference page from a built surface dict.

    Same walk, same pins as the snapshot: functions with their signatures,
    classes with their ``__init__`` signatures (empty when none is
    defined). Sorted so the page is byte-stable across runs.
    """
    lines = [
        "# Public API reference (gate #1167)",
        "",
        "Every public symbol matchline ships, generated from the same surface",
        "walk that pins `tests/api_surface.json` (schema",
        "`matchline.api_surface/1`). Regenerate with",
        "`python3 scripts/generate_api_surface.py`; the CI guard keeps this",
        "page in sync with the pins. What counts as public, breaking, or",
        "deprecated is in [api.md](api.md); per-module usage is in each",
        "module's guide under `docs/`.",
        "",
    ]
    for name in sorted(surface["modules"]):
        sigs = surface["modules"][name]
        lines.append(f"## `{name}`")
        lines.append("")
        for fn in sorted(sigs["functions"]):
            lines.append(f"- `{fn}({sigs['functions'][fn]})`")
        for cls in sorted(sigs["classes"]):
            init = sigs["classes"][cls]
            lines.append(f"- class `{cls}({init})`" if init else f"- class `{cls}`")
        lines.append("")
    for name in sorted(surface["packages"]):
        sigs = surface["packages"][name]
        lines.append(f"## Package `{name}`")
        lines.append("")
        for fn in sorted(sigs["functions"]):
            lines.append(f"- `{fn}({sigs['functions'][fn]})`")
        for cls in sorted(sigs["classes"]):
            init = sigs["classes"][cls]
            lines.append(f"- class `{cls}({init})`" if init else f"- class `{cls}`")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    surface = build()
    OUT.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")
    DOC.write_text(render_reference(surface))
    n = sum(len(s["functions"]) + len(s["classes"]) for s in surface["modules"].values())
    n += sum(len(s["functions"]) + len(s["classes"]) for s in surface["packages"].values())
    print(f"{len(PUBLIC_MODULES)}+{len(PUBLIC_PACKAGES)} files, {n} public symbols -> {OUT}")


if __name__ == "__main__":
    main()
