#!/usr/bin/env python3
"""Generate the pinned public API surface (gate #1159).

Walks the declared public modules and packages with ast (no imports), collects
top-level public functions and classes with their signatures, and writes
tests/api_surface.json (schema "matchline.api_surface/1"). The CI guard test
compares the live surface against that snapshot; on drift, regenerate this
file and add a deprecation notice per docs/api.md before changing the pins.

Run:  python3 scripts/generate_api_surface.py
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "api_surface.json"
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


def main() -> None:
    surface = build()
    OUT.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n")
    n = sum(len(s["functions"]) + len(s["classes"]) for s in surface["modules"].values())
    n += sum(len(s["functions"]) + len(s["classes"]) for s in surface["packages"].values())
    print(f"{len(PUBLIC_MODULES)}+{len(PUBLIC_PACKAGES)} files, {n} public symbols -> {OUT}")


if __name__ == "__main__":
    main()
