"""run_pipeline.py: unified pipeline runner for matchline.

Chains all pipeline stages:
  Stage 1: generate_building()           -> stage_01_building.json
  Stage 2: build_model()                 -> stage_02_model.json
  Stage 3: simplify_ring()               -> stage_03_simplified.json
  Stage 4: run_checks()                  -> stage_04_validation.json
  Stage 5: (fail-fast on validation)
  Stage 6: BEM export (gbXML + IFC4)     -> stage_06_bem/

Each stage writes intermediate JSON so operators can inspect any step.
Validation errors block export (exit code 1, not silent).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bem_export import (
    BEMModel,
    BEMOpeningUnit,
    BEMSpace,
    write_gbxml,
    write_ifc4,
)
from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from synth.multidiscipline import generate_building
from validate import export_gate, run_checks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_json(p: Path, data: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, default=str))


def _ensure_ccw(ring: list) -> list:
    """Ensure ring is counter-clockwise (for shoelace area > 0)."""
    s = 0.0
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return list(reversed(ring)) if s < 0 else list(ring)


# ---------------------------------------------------------------------------
# Adapter: BuildingModel -> BEMModel
# ---------------------------------------------------------------------------


def model_from_linked_model(
    model, simplified_ring: list, wall_height_m: float, simplify_tol_pct: float
) -> BEMModel:
    """Build a BEMModel from the canonical BuildingModel.

    This adapter extracts the data needed for BEM export from the
    linked BuildingModel, converting to the BEMModel contract expected
    by write_gbxml and write_ifc4.
    """
    # --- spaces -----------------------------------------------------------
    bem_spaces = []
    for i, sp in enumerate(model.spaces.values()):
        poly = _ensure_ccw(sp.polygon_m)
        # Compute area from polygon
        area = 0.0
        n = len(poly)
        for j in range(n):
            x0, y0 = poly[j]
            x1, y1 = poly[(j + 1) % n]
            area += x0 * y1 - x1 * y0
        area = abs(area) / 2.0
        name = f"{sp.name} {sp.number}".strip() or f"SPACE-{i + 1:02d}"
        bem_spaces.append(
            BEMSpace(
                sid=sp.id,
                name=name,
                number=sp.number,
                polygon_m=poly,
                area_m2=area,
                volume_m3=area * wall_height_m,
            )
        )

    if not bem_spaces:
        raise ValueError("no spaces to export")

    # --- openings --------------------------------------------------------
    bem_openings = []
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.width_m is None or op.height_m is None:
                continue
            bem_openings.append(
                BEMOpeningUnit(
                    category=op.category,
                    tag=op.tag or "",
                    width_m=op.width_m,
                    height_m=op.height_m,
                )
            )

    # --- envelope ring ---------------------------------------------------
    ring_ccw = _ensure_ccw(simplified_ring)

    # --- area delta from simplifier (approximate from ring change) ----------
    # The simplifier reports relative area change; we pass it through
    # Original ring area vs simplified ring area
    orig_ring = footprint_from_regions([sp.polygon_m for sp in model.spaces.values()])
    orig_area = 0.0
    n = len(orig_ring)
    for i in range(n):
        x0, y0 = orig_ring[i]
        x1, y1 = orig_ring[(i + 1) % n]
        orig_area += x0 * y1 - x1 * y0
    orig_area = abs(orig_area) / 2.0

    simp_area = 0.0
    n = len(ring_ccw)
    for i in range(n):
        x0, y0 = ring_ccw[i]
        x1, y1 = ring_ccw[(i + 1) % n]
        simp_area += x0 * y1 - x1 * y0
    simp_area = abs(simp_area) / 2.0

    area_delta_pct = ((simp_area - orig_area) / orig_area * 100.0) if orig_area > 0 else 0.0

    return BEMModel(
        building_name=model.name,
        spaces=bem_spaces,
        openings=bem_openings,
        ring_m=ring_ccw,
        wall_height_m=wall_height_m,
        area_delta_pct=area_delta_pct,
        simplify_tol_pct=simplify_tol_pct,
    )


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def parse_args():
    import argparse

    ap = argparse.ArgumentParser(
        prog="run_pipeline",
        description="Unified pipeline: generate + link + validate + BEM export.",
    )
    ap.add_argument("--seed", type=int, required=True, help="random seed for synthetic building")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("bem_out"),
        help="output directory for stage JSONs (default: bem_out/)",
    )
    ap.add_argument(
        "--open-office-span",
        action="store_true",
        help="use open-office span layout instead of separate offices",
    )
    ap.add_argument(
        "--elevation-key",
        default="elev_grid",
        choices=["elev_grid", "elev_nogrid"],
        help="which elevation variant to link (default: elev_grid)",
    )
    ap.add_argument(
        "--simplify-tol",
        type=float,
        default=0.02,
        help="tolerance for geometry simplification (default: 0.02 = 2%%)",
    )
    return ap.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(args) -> None:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Stage 1: generate building ----------------------------------------
    bldg = generate_building(args.seed, open_office_span=args.open_office_span)
    write_json(out_dir / "stage_01_building.json", bldg)

    # --- Stage 2: build model --------------------------------------------
    model, link_report = build_model(
        bldg, elevation_key=args.elevation_key, building_name=bldg["building_id"]
    )
    write_json(out_dir / "stage_02_model.json", {
        "model": json.loads(model.to_json()) if hasattr(model, "to_json") else _model_to_dict(model),
        "link_report": asdict(link_report),
    })

    # --- Stage 3: simplify geometry --------------------------------------
    wall_height = model.levels[0].wall_height_m
    ring = footprint_from_regions([sp.polygon_m for sp in model.spaces.values()])
    sres = simplify_ring(ring, tol=args.simplify_tol, wall_height=wall_height)
    write_json(out_dir / "stage_03_simplified.json", {
        "original_count": sres.original_count,
        "simplified_count": sres.simplified_count,
        "simplified_ring": sres.ring,
        "area_delta_pct": sres.area_delta_pct,
        "tolerance_pct": sres.tol * 100.0,
    })

    # --- Stage 4: validation checks --------------------------------------
    report = run_checks(model, sres=sres)
    write_json(out_dir / "stage_04_validation.json", report.to_dict())

    # --- Stage 5: fail-fast on validation errors ------------------------
    if not export_gate(report):
        print(f"VALIDATION FAILED: {len(report.errors)} error(s)", file=sys.stderr)
        for r in report.errors:
            print(f"  - {r.check_id}: {r.message}", file=sys.stderr)
        sys.exit(1)

    # --- Stage 6: BEM export ---------------------------------------------
    bem_dir = out_dir / "stage_06_bem"
    bem_dir.mkdir(parents=True, exist_ok=True)

    bem_model = model_from_linked_model(
        model=model,
        simplified_ring=sres.ring,
        wall_height_m=wall_height,
        simplify_tol_pct=args.simplify_tol * 100.0,
    )

    gbxml_path = bem_dir / f"{model.name or 'building'}.xml"
    write_gbxml(bem_model, gbxml_path)

    ifc_path = bem_dir / f"{model.name or 'building'}.ifc"
    write_ifc4(bem_model, ifc_path)

    print(f"Pipeline complete: {out_dir}")
    print("  stage_01_building.json")
    print("  stage_02_model.json")
    print("  stage_03_simplified.json")
    print(f"  stage_04_validation.json (ok={report.ok})")
    print(f"  stage_06_bem/{gbxml_path.name}")
    print(f"  stage_06_bem/{ifc_path.name}")


def _model_to_dict(model) -> dict:
    """Convert BuildingModel to dict for JSON serialization."""
    return {
        "name": model.name,
        "model_version": model.model_version,
        "levels": [asdict(l) for l in model.levels],
        "spaces": {k: asdict(v) for k, v in model.spaces.items()},
        "zones": {k: asdict(v) for k, v in model.zones.items()},
        "envelope": [asdict(e) for e in model.envelope],
        "schedules": model.schedules,
        "review_queue": [asdict(r) for r in model.review_queue],
    }


if __name__ == "__main__":
    main(parse_args())
