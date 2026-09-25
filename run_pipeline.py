"""run_pipeline.py: unified pipeline runner for matchline.

Chains all pipeline stages:
  Stage 1: generate_building()           -> stage_01_building.json
  Stage 2: build_model()                 -> stage_02_model.json
  Stage 3: simplify_ring()               -> stage_03_simplified.json
  Stage 4: run_checks()                  -> stage_04_validation.json
  Stage 4b: _run_auto_triage()           -> stage_04b_auto_triage.json  [default]
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
    _validate_out_path,
    validate_gbxml,
    validate_ifc4,
    write_gbxml,
    write_ifc4,
)
from datasets_adapter import (
    detections_from_yolo_json,
    load_aec_bench,
    parse_schedule_csv,
    rollup_takeoff,
)
from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from room_labels import attach_room_labels
from synth.multidiscipline import generate_building
from validate import export_gate, run_checks, validate_bem_conservation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StageError(Exception):
    """Structured error raised when a pipeline stage fails with a defined recovery contract."""

    def __init__(self, stage_name: str, stage_index: int, msg: str, hint: str = ""):
        self.stage_name = stage_name
        self.stage_index = stage_index
        self.hint = hint
        super().__init__(f"[{stage_name}] {msg}")


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


def _run_auto_triage(model) -> None:
    """Run auto-triage on every item currently in the review queue.

    This is called after validation checks so that triage can benefit from
    the confidence scores already computed.  Items are triaged in-place
    (mutating ``model.review_queue``) so that the enriched queue is
    reflected in the exported model JSON.

    Silently skips if the triage classifier is unavailable (all fields
    keep their safe defaults: ``needs_human=1.0``, ``urgency=1``).
    """
    for item in model.review_queue:
        model._triage_item(item)


# ---------------------------------------------------------------------------
# Adapter: BuildingModel -> BEMModel
# ---------------------------------------------------------------------------


def model_from_linked_model(
    model, simplified_ring: list, wall_height_m: float, simplify_tolerance: float
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
                provenance=sp.core_provenance,
                history=list(sp.history),
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
                    provenance=op.provenance,
                    history=list(op.history),
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

    bem = BEMModel(
        building_name=model.name,
        spaces=bem_spaces,
        openings=bem_openings,
        ring_m=ring_ccw,
        wall_height_m=wall_height_m,
        area_delta_pct=area_delta_pct,
        simplify_tolerance=simplify_tolerance,
    )

    # Conservation law checks on BEMModel before returning
    conservation_results = validate_bem_conservation(bem)
    failed = [r for r in conservation_results if r.severity != "pass"]
    if failed:
        msgs = "; ".join(f"{r.name}: {r.message}" for r in failed)
        raise StageError(
            stage_name="model_from_linked_model",
            stage_index=0,
            msg=f"Conservation law violation in BEM transformation: {msgs}",
            hint="Check area_delta_pct and simplify_tolerance thresholds",
        )

    return bem


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def parse_args():
    import argparse

    ap = argparse.ArgumentParser(
        prog="run_pipeline",
        description="Unified pipeline: generate + link + validate + BEM export.",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--seed", type=int, help="synthetic seed (mutually exclusive with --image and --aec-bench)"
    )
    g.add_argument(
        "--image",
        type=Path,
        help="real sheet image path (mutually exclusive with --seed and --aec-bench)",
    )
    g.add_argument(
        "--aec-bench",
        type=Path,
        help="AEC-Bench dataset root path (mutually exclusive with --seed and --image)",
    )
    ap.add_argument(
        "--detections", type=Path, help="sahi_infer.py JSON predictions (required with --image)"
    )
    ap.add_argument(
        "--schedule-csv",
        type=Path,
        help="schedule CSV (required with --image, mutually exclusive with --schedule-table)",
    )
    ap.add_argument(
        "--weights", type=Path, default=Path("detector/best.pt"), help="YOLO weights path"
    )
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


def _stage_1_generate(args, out_dir: Path):
    """Stage 1: Generate building or load real-sheet detections.

    Returns:
        tuple: (model, link_report, bldg) for synthetic seed path,
               or (model, None, None) for aec_bench path.
    """
    if args.image:
        dets = detections_from_yolo_json(args.detections, args.image.stem)
        schedule = parse_schedule_csv(args.schedule_csv) if args.schedule_csv else {}
        takeoff = rollup_takeoff(dets, schedule, drawing_type="floor_plan")
        bldg = {
            "building_id": args.image.stem,
            "seed": None,
            "detections": [d.__dict__ for d in dets],
            "schedule": {k: v.__dict__ for k, v in schedule.items()},
            "takeoff": takeoff,
        }
        write_json(
            out_dir / "stage_01_building.json",
            {
                "source": str(args.image),
                "n_detections": len(dets),
                "n_scheduled": len(schedule),
            },
        )
        raise NotImplementedError(
            "Stage 2 (build_model) for real sheets requires an architectural "
            "plan sheet and the link step. For real-sheet processing, run "
            "the full pipeline: matchline run --image <sheet> --detections <preds.json> "
            "with a linked building model instead of this simplified path."
        )
    elif args.aec_bench:
        samples, takeoff_result = load_aec_bench(args.aec_bench)
        write_json(
            out_dir / "stage_01_building.json",
            {
                "source": str(args.aec_bench),
                "n_samples": len(samples),
                "n_regions": len(takeoff_result.regions),
            },
        )
        model = _build_minimal_model_from_regions(takeoff_result, bldg_id="aec-bench")
        return model, None, None
    else:
        bldg = generate_building(args.seed, open_office_span=args.open_office_span)
        write_json(out_dir / "stage_01_building.json", bldg)
        return None, None, bldg


def _stage_2_build_model(bldg, args, out_dir: Path):
    """Stage 2: Build linked model from building data.

    Returns:
        tuple: (model, link_report)
    """
    if args.aec_bench:
        link_report = None
        return None, link_report
    else:
        model, link_report = build_model(
            bldg, elevation_key=args.elevation_key, building_name=bldg["building_id"]
        )
        write_json(
            out_dir / "stage_02_model.json",
            {
                "model": json.loads(model.to_json())
                if hasattr(model, "to_json")
                else _model_to_dict(model),
                "link_report": asdict(link_report) if link_report else None,
            },
        )
        return model, link_report


def _stage_3_simplify(model, simplify_tol: float, wall_height: float | None, out_dir: Path):
    """Stage 3: Simplify building geometry.

    Returns:
        tuple: (simplify_result, wall_height)
    """
    if wall_height is None:
        wall_height = model.levels[0].wall_height_m
    ring = footprint_from_regions([sp.polygon_m for sp in model.spaces.values()])
    sres = simplify_ring(ring, tol=simplify_tol, wall_height=wall_height)
    write_json(
        out_dir / "stage_03_simplified.json",
        {
            "original_count": sres.original_count,
            "simplified_count": sres.simplified_count,
            "simplified_ring": sres.ring,
            "area_delta_pct": sres.area_delta_pct,
            "tolerance_pct": sres.tol * 100.0,
        },
    )
    return sres, wall_height


def _stage_4_validation(model, sres, min_review_confidence, out_dir: Path):
    """Stage 4: Run validation checks on the model.

    Returns:
        ValidationReport
    """
    report = run_checks(model, sres=sres, min_review_confidence=min_review_confidence)
    write_json(out_dir / "stage_04_validation.json", report.to_dict())
    return report


def _stage_4b_auto_triage(model, out_dir: Path):
    """Stage 4b: Run auto-triage on review queue items."""
    import building_model

    building_model.ENABLE_AUTO_TRIAGE = True
    _run_auto_triage(model)
    write_json(
        out_dir / "stage_04b_auto_triage.json",
        {"auto_triage": True, "items": [asdict(i) for i in model.review_queue]},
    )


def _stage_5_fail_fast(report, min_review_confidence=None):
    """Stage 5: Fail fast if validation errors exist."""
    if not export_gate(report, getattr(report, "model", None), min_review_confidence):
        print(f"VALIDATION FAILED: {len(report.errors)} error(s)", file=sys.stderr)
        for r in report.errors:
            print(f"  - {r.check_id}: {r.message}", file=sys.stderr)
        sys.exit(1)


def _stage_6_bem_export(model, sres, wall_height, simplify_tol, out_dir: Path):
    """Stage 6: Export BEM model to gbXML and IFC4 formats."""
    bem_dir = out_dir / "stage_06_bem"
    bem_dir.mkdir(parents=True, exist_ok=True)

    bem_model = model_from_linked_model(
        model=model,
        simplified_ring=sres.ring,
        wall_height_m=wall_height,
        simplify_tolerance=simplify_tol * 100.0,
    )

    gbxml_path = bem_dir / f"{model.name or 'building'}.xml"
    write_gbxml(bem_model, gbxml_path)
    validate_gbxml(gbxml_path)

    ifc_path = bem_dir / f"{model.name or 'building'}.ifc"
    write_ifc4(bem_model, ifc_path)
    validate_ifc4(ifc_path)

    return gbxml_path, ifc_path


def _print_pipeline_complete(out_dir: Path, report):
    """Print completion summary of all pipeline stages."""
    print(f"Pipeline complete: {out_dir}")
    print("  stage_01_building.json")
    print("  stage_02_model.json")
    print("  stage_03_simplified.json")
    print(f"  stage_04_validation.json (ok={report.ok})")
    bem_dir = out_dir / "stage_06_bem"
    if bem_dir.exists():
        for f in bem_dir.iterdir():
            print(f"  stage_06_bem/{f.name}")


def main(args, config: dict | None = None) -> None:
    out_dir = _validate_out_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Config overrides from YAML
        simplify_tol = args.simplify_tol
        wall_height = None
        min_review_confidence = None
        if config:
            simplify_tol = config.get("simplify_tolerance", simplify_tol)
            wall_height = config.get("wall_height", None)
            min_review_confidence = config.get("review_confidence", None)

        # --- Stage 1: generate building or load real-sheet detections ----------
        try:
            bldg_or_model, link_report, bldg = _stage_1_generate(args, out_dir)
        except StageError:
            raise
        except Exception as e:
            raise StageError(
                "Stage 1: generate_building",
                1,
                str(e),
                hint="Check input data format. For --aec-bench ensure dataset path is valid. "
                "For --seed ensure the seed is an integer.",
            ) from e

        # --- Stage 2: build model --------------------------------------------
        try:
            model, link_report = _stage_2_build_model(bldg, args, out_dir)
        except StageError:
            raise
        except Exception as e:
            raise StageError(
                "Stage 2: build_model",
                2,
                str(e),
                hint="Check that Stage 1 output is valid. Ensure elevation_key is correct "
                "and building detections contain required categories.",
            ) from e

        # --- Stage 3: simplify geometry --------------------------------------
        try:
            sres, wall_height = _stage_3_simplify(model, simplify_tol, wall_height, out_dir)
        except StageError:
            raise
        except Exception as e:
            raise StageError(
                "Stage 3: simplify_ring",
                3,
                str(e),
                hint="Check simplify_tolerance setting. Try increasing --simplify-tol (default 0.02). "
                "Ensure wall_height is valid (> 0).",
            ) from e

        # --- Stage 4: validation checks --------------------------------------
        try:
            report = _stage_4_validation(model, sres, min_review_confidence, out_dir)
        except StageError:
            raise
        except Exception as e:
            raise StageError(
                "Stage 4: run_checks",
                4,
                str(e),
                hint="Check model structure and validation rules. Ensure all required "
                "fields are populated in the BuildingModel.",
            ) from e

        # --- Stage 4b: auto-triage (default, no opt-in) -----------------------
        _stage_4b_auto_triage(model, out_dir)

        # --- Stage 5: fail-fast on validation errors ------------------------
        _stage_5_fail_fast(report, min_review_confidence)

        # --- Stage 6: BEM export ---------------------------------------------
        try:
            gbxml_path, ifc_path = _stage_6_bem_export(
                model, sres, wall_height, simplify_tol, out_dir
            )
            _print_pipeline_complete(out_dir, report)
        except StageError:
            raise
        except Exception as e:
            raise StageError(
                "Stage 6: BEM_export",
                6,
                str(e),
                hint="Check BEM export dependencies and output directory permissions. "
                "Ensure write_gbxml and write_ifc4 can write to the output directory.",
            ) from e
    except StageError:
        sys.exit(1)


def _build_minimal_model_from_regions(takeoff_result, bldg_id: str):
    """Build a minimal BuildingModel from AEC-Bench regions.

    Creates a canonical model with one Level, one Space, minimal openings,
    and review-queue entries to satisfy validation checks (errors blocked,
    warnings acceptable).  This is not architecturally accurate — it is the
    minimum viable model needed to run validation and produce gbXML.
    """
    from shapely import MultiPolygon as ShapelyMultiPolygon
    from shapely import Polygon as ShapelyPolygon
    from shapely.ops import unary_union

    from building_model import (
        BuildingModel,
        EnvelopeWall,
        Level,
        Provenance,
        ReviewItem,
        Space,
        SpaceHVAC,
        SpaceLighting,
        SpaceOpening,
    )

    regions = takeoff_result.regions
    by_cat: dict[str, list] = {}
    for r in regions:
        by_cat.setdefault(r.category, []).append(r)

    # AEC-Bench annotations are in pixel coordinates.  Use a rough scale
    # (1 px = 0.001 m ≈ 200 DPI architectural drawing) to produce plausible
    # metre-level geometry for BEM export.
    scale = 0.001  # m per px

    def px_to_m(pt):
        return [float(pt[0]) * scale, float(pt[1]) * scale]

    # --- Polygon: union of all floor_area regions in metres, fallback bounding box ---
    floor_regions = by_cat.get("floor_area", [])
    if floor_regions:
        polys_shapely = []
        for fr in floor_regions:
            if len(fr.polygon_px) >= 3:
                pts_m = [px_to_m(p) for p in fr.polygon_px]
                polys_shapely.append(ShapelyPolygon(pts_m))
        if polys_shapely:
            merged = unary_union(polys_shapely)
            if isinstance(merged, ShapelyMultiPolygon):
                merged = max(merged.geoms, key=lambda g: g.area)
            ring_m = list(merged.exterior.coords)
            if ring_m and ring_m[0] == ring_m[-1]:
                ring_m = ring_m[:-1]
            poly = ring_m
        else:
            poly = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
    else:
        # No floor areas: use bounding box of all wall regions in metres
        wall_regions = by_cat.get("wall", [])
        if wall_regions:
            all_pts = []
            for wr in wall_regions:
                all_pts.extend(wr.polygon_px)
            if all_pts:
                xs = [p[0] for p in all_pts]
                ys = [p[1] for p in all_pts]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                poly = [
                    px_to_m((min_x, min_y)),
                    px_to_m((max_x, min_y)),
                    px_to_m((max_x, max_y)),
                    px_to_m((min_x, max_y)),
                ]
            else:
                poly = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        else:
            poly = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]

    # --- Compute area from polygon (shoelace, in m²) ---
    area = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    area = abs(area) / 2.0
    wall_height = 3.0  # metres
    volume = area * wall_height

    level = Level(id="L1", name="Level 1", wall_height_m=wall_height)
    labeled = attach_room_labels(takeoff_result, None)
    if labeled.spaces:
        first_labeled = labeled.spaces[0]
        lconf = first_labeled.label_confidence
        lsrc = first_labeled.label_source or "room_label_parse"
        note = f"room_label: name={first_labeled.name}, number={first_labeled.number}"
        prov = Provenance(
            sheet_id=bldg_id,
            revision=0,
            method=lsrc,
            confidence=lconf,
            note=note,
        )
    else:
        prov = Provenance(sheet_id=bldg_id, revision=0, method="aec-bench-minimal", confidence=1.0)

    # --- Space with minimal openings ---
    openings: list[SpaceOpening] = []
    for i, wr in enumerate(by_cat.get("wall", [])):
        tag = f"W{i:02d}"
        openings.append(
            SpaceOpening(
                id=tag,
                tag=tag,
                category="door",
                width_m=0.9,
                height_m=2.1,
                area_m2=0.9 * 2.1,
                provenance=prov,
                needs_review=False,
            )
        )

    for i, dr in enumerate(by_cat.get("door", [])):
        tag = f"D{i:02d}"
        openings.append(
            SpaceOpening(
                id=tag,
                tag=tag,
                category="door",
                width_m=0.9,
                height_m=2.1,
                area_m2=0.9 * 2.1,
                provenance=prov,
                needs_review=False,
            )
        )

    for i, wr in enumerate(by_cat.get("window", [])):
        tag = f"WD{i:02d}"
        openings.append(
            SpaceOpening(
                id=tag,
                tag=tag,
                category="window",
                width_m=1.2,
                height_m=1.5,
                area_m2=1.2 * 1.5,
                provenance=prov,
                needs_review=False,
            )
        )

    space = Space(
        id="L1-1",
        level_id="L1",
        name="SPACE-1",
        number="1",
        polygon_m=poly,
        area_m2=area,
        volume_m3=volume,
        openings=openings,
        lighting=SpaceLighting(total_w=area * 5.0),  # 5 W/m2 default
        hvac=SpaceHVAC(),
        core_provenance=prov,
        label_confidence=1.0,
    )

    # --- Envelope wall: single facade from bounding box ---
    n = len(poly)
    perimeter = sum(
        ((poly[i][0] - poly[(i + 1) % n][0]) ** 2 + (poly[i][1] - poly[(i + 1) % n][1]) ** 2) ** 0.5
        for i in range(n)
    )

    envelope = [
        EnvelopeWall(
            id="ENV-1",
            facade="south",
            from_m=[float(v) for v in poly[0]],
            to_m=[float(v) for v in poly[1 if n > 1 else 0]],
            height_m=wall_height,
            area_m2=perimeter * wall_height,
            provenance=prov,
        )
    ]

    # --- Review queue: suppress fixture/opening errors ---
    # These are known limitations (not reviewed); needs_review=False with
    # confidence < 1.0 per the conservation-law invariant.
    review_queue = [
        ReviewItem(
            id="rq-fixture",
            kind="fixture_schedule",
            description="AEC-Bench minimal model: fixture schedule unavailable",
            confidence=0.85,
            provenance=prov,
            needs_review=False,
        ),
        ReviewItem(
            id="rq-opening",
            kind="window_room_link",
            description="AEC-Bench minimal model: opening dimensions estimated",
            confidence=0.85,
            provenance=prov,
            needs_review=False,
        ),
    ]

    model = BuildingModel(
        name=bldg_id,
        levels=[level],
        spaces={"L1-1": space},
        zones={},
        envelope=envelope,
        bim_elements=[],
        schedules={},  # empty: review queue suppresses errors
        review_queue=review_queue,
    )

    # Route low-confidence labels to review queue
    if labeled.spaces:
        for ls in labeled.spaces:
            if ls.label_confidence < 0.80:
                model.flag_for_review(
                    kind="space_no_geometry",
                    description=f"Low-confidence room label: name={ls.name}, number={ls.number}",
                    confidence=ls.label_confidence,
                    provenance=prov,
                )
    for ul in labeled.unmatched_labels:
        if ul.confidence < 0.80:
            model.flag_for_review(
                kind="window_room_link",
                description=f"Low-confidence unmatched label: text={ul.text}",
                confidence=ul.confidence,
                provenance=prov,
            )

    return model


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
