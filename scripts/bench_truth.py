#!/usr/bin/env python3
"""Independent benchmark ground truth, read straight from the IFC (#1165).

Usage:
    python scripts/bench_truth.py --config scripts/benchmarks.json [--force]

For each benchmark building with an arch IFC on disk this writes
``truth.json`` next to the IFC (outside the repo, like the IFC itself). The
benchmark scores matchline's IFC path against that frozen file instead of
against a second ``import_ifc`` of the same file, which compared the importer
with itself (area error 0.0% by construction, wall length error hard-coded).

Truth is read with ifcopenshell only. This module must never import matchline's
IFC reader (``ifc.ifc_import``); ``tests/test_bench_truth.py`` pins that.

- rooms: every ``IfcSpace``, keyed by GlobalId. Area is
  ``Qto_SpaceBaseQuantities.NetFloorArea`` (falling back to ``GrossFloorArea``),
  else the downward-facing area of the space's tessellated body from
  ``ifcopenshell.geom``. ``area_source`` records which one was used.
- exterior wall length: walls with ``Pset_WallCommon.IsExternal`` True; length
  from ``Qto_WallBaseQuantities.Length``, else the 2D length of the wall's
  ``Axis`` representation, else the longest plan extent of the wall's
  tessellated body from ``ifcopenshell.geom`` (the same geometry policy the
  room areas use). A file with no wall flagged external has no exterior wall
  truth (``null`` plus a reason), never 0.
- windows / doors: raw ``by_type`` counts; window area is
  ``OverallWidth * OverallHeight`` where both are stated.

``truth.json`` carries the IFC's sha256. The benchmark refuses a truth file
whose sha256 no longer matches the IFC, so truth cannot drift silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

TRUTH_SCHEMA = "matchline.bench_truth/1"
TRUTH_FILENAME = "truth.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _psets(el) -> dict:
    import ifcopenshell.util.element as ue

    try:
        return ue.get_psets(el)
    except Exception:
        return {}


def _qto_value(psets: dict, qset: str, names: tuple[str, ...]) -> float | None:
    q = psets.get(qset) or {}
    for n in names:
        v = q.get(n)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _length_scale(f) -> float:
    import ifcopenshell.util.unit as uu

    try:
        return float(uu.calculate_unit_scale(f))
    except Exception:
        return 1.0


def _geom_floor_area(el) -> float | None:
    """Downward-facing area (m2) of the element's tessellated body, or None."""
    try:
        import ifcopenshell.geom
        import numpy as np

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, el)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
    except Exception:
        return None
    if not len(faces):
        return None
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.linalg.norm(cross, axis=1)
    keep = norm > 0
    if not keep.any():
        return None
    nz = cross[keep, 2] / norm[keep]
    down = nz < -0.99
    area = float(norm[keep][down].sum() / 2.0)
    return area if area > 0 else None


def _axis_length(el, scale: float) -> float | None:
    """2D length of the wall's Axis representation (model units * scale)."""
    rep = getattr(el, "Representation", None)
    for r in getattr(rep, "Representations", None) or []:
        if r.RepresentationIdentifier != "Axis":
            continue
        for item in r.Items or []:
            pts = None
            if item.is_a("IfcPolyline"):
                pts = [p.Coordinates for p in item.Points]
            elif item.is_a("IfcTrimmedCurve") and item.BasisCurve.is_a("IfcLine"):
                t1 = [t for t in item.Trim1 if t.is_a("IfcCartesianPoint")]
                t2 = [t for t in item.Trim2 if t.is_a("IfcCartesianPoint")]
                if t1 and t2:
                    pts = [t1[0].Coordinates, t2[0].Coordinates]
            elif item.is_a("IfcIndexedPolyCurve"):
                pts = list(item.Points.CoordList)
            if pts and len(pts) >= 2:
                length = sum(math.dist(tuple(p[:2]), tuple(q[:2])) for p, q in zip(pts, pts[1:]))
                return length * scale if length > 0 else None
    return None


def _geom_plan_length(el, scale: float) -> float | None:
    """Longest plan extent (m) of the element's tessellated body, or None.

    Fallback for walls with neither a Qto length nor an Axis: the body's
    vertices via ``ifcopenshell.geom`` (world coords), projected on the
    principal plan direction found by PCA. A straight wall's long axis is
    its length; a curved or L-shaped wall approximates it. Like
    ``_geom_floor_area``, the tessellation comes back in metres, so
    ``scale`` is accepted for symmetry but not applied.
    """
    try:
        import ifcopenshell.geom
        import numpy as np

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, el)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
    except Exception:
        return None
    xy = np.unique(np.round(verts[:, :2], 9), axis=0)
    if len(xy) < 2:
        return None
    centered = xy - xy.mean(axis=0)
    cov = centered.T @ centered / len(centered)
    evals, evecs = np.linalg.eigh(cov)
    best = 0.0
    for i in range(2):
        d = evecs[:, i]
        proj = centered @ d
        best = max(best, float(proj.max() - proj.min()))
    return best if best > 0 else None


def _is_external(psets: dict) -> bool | None:
    v = (psets.get("Pset_WallCommon") or {}).get("IsExternal")
    return v if isinstance(v, bool) else None


def build_truth(path: Path) -> dict:
    """Ground-truth quantities for one IFC, read with ifcopenshell only."""
    import ifcopenshell

    path = Path(path)
    f = ifcopenshell.open(str(path))
    scale = _length_scale(f)

    rooms = {}
    for sp in sorted(f.by_type("IfcSpace"), key=lambda e: e.GlobalId):
        ps = _psets(sp)
        area = _qto_value(ps, "Qto_SpaceBaseQuantities", ("NetFloorArea", "GrossFloorArea"))
        source = "qto" if area is not None else None
        if area is None:
            area = _geom_floor_area(sp)
            source = "geometry" if area is not None else None
        rooms[sp.GlobalId] = {
            "name": (sp.Name or ""),
            "long_name": (getattr(sp, "LongName", None) or ""),
            "area_m2": area,
            "area_source": source,
        }

    walls = f.by_type("IfcWall")  # includes IfcWallStandardCase subtypes
    flagged = [w for w in walls if _is_external(_psets(w)) is not None]
    ext_len = 0.0
    ext_n = 0
    ext_geom = 0
    ext_unmeasured = 0
    for w in sorted(walls, key=lambda e: e.GlobalId):
        ps = _psets(w)
        if _is_external(ps) is not True:
            continue
        length = _qto_value(ps, "Qto_WallBaseQuantities", ("Length",))
        if length is None:
            length = _axis_length(w, scale)
        source = "measured"
        if length is None:
            # body-geometry fallback, same policy as room areas: an
            # unmeasured wall must not shrink the total (#1205)
            length = _geom_plan_length(w, scale)
            source = "geometry"
        if length is None:
            ext_unmeasured += 1
            continue
        ext_len += length
        if source == "measured":
            ext_n += 1
        else:
            ext_geom += 1
    if ext_n or ext_geom:
        wall_len: float | None = ext_len
        wall_reason = ""
    elif not flagged:
        wall_len, wall_reason = None, "no wall states Pset_WallCommon.IsExternal"
    elif ext_unmeasured:
        wall_len, wall_reason = None, "exterior walls have no Qto length, Axis, or measurable body"
    else:
        wall_len, wall_reason = None, "no wall has Pset_WallCommon.IsExternal = True"

    windows = f.by_type("IfcWindow")
    win_area = 0.0
    win_sized = 0
    for w in windows:
        ww, hh = getattr(w, "OverallWidth", None), getattr(w, "OverallHeight", None)
        if ww and hh:
            win_area += float(ww) * float(hh) * scale * scale
            win_sized += 1

    return {
        "schema": TRUTH_SCHEMA,
        "ifc_sha256": sha256_file(path),
        "ifc_schema": f.schema,
        "ifcopenshell_version": getattr(ifcopenshell, "version", ""),
        "rooms": rooms,
        "exterior_wall_length_m": wall_len,
        "exterior_wall_reason": wall_reason,
        "exterior_walls_measured": ext_n,
        "exterior_walls_from_geometry": ext_geom,
        "exterior_walls_unmeasured": ext_unmeasured,
        "windows": len(windows),
        "windows_sized": win_sized,
        "window_area_m2": win_area if win_sized else None,
        "doors": len(f.by_type("IfcDoor")),
    }


def truth_path_for(arch_ifc: Path) -> Path:
    return Path(arch_ifc).parent / TRUTH_FILENAME


def write_truth(arch_ifc: Path, out: Path | None = None) -> Path:
    truth = build_truth(arch_ifc)
    out = out or truth_path_for(arch_ifc)
    out.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return out


class TruthMismatch(RuntimeError):
    """truth.json was frozen from a different IFC than the one on disk."""


def load_or_freeze_truth(arch_ifc: Path) -> tuple[dict, str]:
    """(truth, how) for ``arch_ifc``: ``how`` is ``frozen`` or ``built``.

    Reuses a frozen ``truth.json`` when its sha256 matches the IFC; writes one
    when none exists; raises ``TruthMismatch`` when the IFC changed under it.
    """
    p = truth_path_for(arch_ifc)
    if p.exists():
        truth = json.loads(p.read_text())
        if truth.get("schema") != TRUTH_SCHEMA:
            raise TruthMismatch(f"{p} has schema {truth.get('schema')!r}, want {TRUTH_SCHEMA}")
        if truth.get("ifc_sha256") != sha256_file(arch_ifc):
            raise TruthMismatch(
                f"{p} was frozen from a different IFC (sha256 mismatch);"
                " regenerate with scripts/bench_truth.py --force"
            )
        return truth, "frozen"
    write_truth(arch_ifc, p)
    return json.loads(p.read_text()), "built"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, help="benchmarks.json (see DATASETS.md)")
    ap.add_argument("--force", action="store_true", help="overwrite existing truth.json")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text())
    base = Path(args.config).resolve().parent
    n_written = 0
    for e in cfg.get("buildings", []):
        v = e.get("arch_ifc")
        if not v:
            continue
        ifc = Path(v).expanduser()
        ifc = ifc if ifc.is_absolute() else base / ifc
        if not ifc.exists():
            print(f"{e['name']}: missing ({ifc})")
            continue
        out = truth_path_for(ifc)
        if out.exists() and not args.force:
            print(f"{e['name']}: {out} exists (use --force to rebuild)")
            continue
        write_truth(ifc, out)
        n_written += 1
        print(f"{e['name']}: wrote {out}")
    print(f"{n_written} truth file(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
