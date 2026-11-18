"""Publish-nothing export gate: geometric checks + staged publish (#1404).

The gate never changes geometry. It observes a *staged* export (written
beside its final path, never onto it) and refuses to publish anything that
does not pass:

  * the gbXML 8.01 XSD + semantic validation,
  * the IFC4 structural validation,
  * the geometric checks below.

Geometric checks (all landed in this spike):

  ``shell_closure``      every edge of a closed shell is shared by exactly
                          two faces (no open shell, no over-shared edge).
  ``shell_winding``      the winding sub-check: the two faces that share an
                          edge traverse it in opposite directions, so the
                          shell has one consistent orientation.
  ``shell_volume``       the signed volume of every closed, consistently
                          wound shell is positive (a shell wound inside out
                          has a negative volume).
  ``planarity``          every face is planar within the tolerance below.
  ``degenerate_faces``   no face has repeated vertices or a zero area.
  ``duplicate_surfaces`` no two envelope Surfaces carry the same geometry.

A shell the exporter cannot represent (its own notes say so) is reported as
``not_checked`` with the reason: never silently passed, never hard-failed.

Tolerances (the gate measures, it never repairs):

  planarity      1.0e-3 m   (exported coordinates carry 4 decimals)
  degenerate     1.0e-9 m2  (area) and fewer than 3 distinct vertices
  volume         1.0e-9 m3  (a shell must enclose a positive volume)
  edge matching  1.0e-6 m   (coordinate rounding for shared-edge matching)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from bem.bem_export import (
    _commit_staged,
    _discard_staged,
    _stage_gbxml,
    _stage_ifc4,
    _staged_path,
    validate_gbxml,
    validate_ifc4,
)
from bem.bem_roof import is_sloped, space_shell
from bem.export_manifest import (
    build_manifest,
    normalised_ifc_digest,
    sha256_bytes,
    sha256_file,
    write_manifest,
)
from safe_xml import safe_xml_parse

GBXML_NS = "http://www.gbxml.org/schema"

PLANARITY_TOL_M = 1.0e-3
DEGENERATE_AREA_M2 = 1.0e-9
VOLUME_EPS_M3 = 1.0e-9
EDGE_QUANT_M = 1.0e-6

CHECK_NAMES = (
    "shell_closure",
    "shell_winding",
    "shell_volume",
    "planarity",
    "degenerate_faces",
    "duplicate_surfaces",
)


@dataclass
class CheckResult:
    """One geometric check: a status, a measurement and a reason."""

    name: str
    status: str  # "pass" | "fail" | "not_checked"
    measurement: dict = field(default_factory=dict)
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True when the check did not find a defect.

        ``not_checked`` is not a pass: it is reported so a caller can see
        that a shell could not be judged at all.
        """
        return self.status in ("pass", "not_checked")

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "measurement": self.measurement,
            "detail": self.detail,
        }


@dataclass
class GateResult:
    """Outcome of one gated export."""

    ok: bool = False
    checks: list[CheckResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    gbxml_ok: bool = False
    gbxml_errors: list[str] = field(default_factory=list)
    ifc4_ok: bool = False
    ifc4_errors: list[str] = field(default_factory=list)
    published: dict[str, Path] = field(default_factory=dict)
    # Repairs applied by the repair loop (#1407; library default off, on
    # by default at the bem-export entry points): one dict per applied
    # repair (RepairRecord.as_dict()), empty when none ran.
    repairs: list = field(default_factory=list)

    @property
    def failed_checks(self) -> list[str]:
        return [c.name for c in self.checks if c.status == "fail"]


# ---- geometry helpers -----------------------------------------------------


def _round_point(p) -> tuple[float, float, float]:
    return (round(p[0], 6), round(p[1], 6), round(p[2], 6))


def _newell(points) -> tuple[float, float, float]:
    """Area-weighted normal of a polygon (Newell's method).

    The magnitude is twice the polygon area; the direction is the normal
    for a counter-clockwise loop.
    """
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx / 2.0, ny / 2.0, nz / 2.0)


def _face_area(points) -> float:
    n = _newell(points)
    return math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) / 2.0


def _planarity_deviation(points) -> float:
    """Largest distance from the face's best-fit plane (0 for a flat face)."""
    if len(points) < 3:
        return 0.0
    n = _newell(points)
    norm = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    if norm < 1e-12:
        return 0.0
    unit = (n[0] / norm, n[1] / norm, n[2] / norm)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    cz = sum(p[2] for p in points) / len(points)
    return max(
        abs((p[0] - cx) * unit[0] + (p[1] - cy) * unit[1] + (p[2] - cz) * unit[2]) for p in points
    )


def _signed_volume(loops) -> float:
    """Enclosed volume of a closed shell (divergence theorem)."""
    v = 0.0
    for lp in loops:
        a = lp[0]
        for i in range(1, len(lp) - 1):
            b = lp[i]
            c = lp[i + 1]
            v += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
    return v


# ---- gbXML extraction -----------------------------------------------------


@dataclass
class _Face:
    owner: str
    kind: str  # "shell" | "surface"
    points: list


@dataclass
class _Shell:
    owner: str
    loops: list


def _loop_points(ploop) -> list:
    pts = []
    for cp in ploop.findall(f"{{{GBXML_NS}}}CartesianPoint"):
        coords = [c.text for c in cp.findall(f"{{{GBXML_NS}}}Coordinate")]
        if len(coords) >= 3 and all(c is not None for c in coords[:3]):
            pts.append((float(coords[0]), float(coords[1]), float(coords[2])))
    return pts


def _surface_points(surface) -> list:
    """The face of a Surface: its PlanarGeometry loop when present."""
    planar = surface.find(f"{{{GBXML_NS}}}PlanarGeometry")
    if planar is not None:
        ploop = planar.find(f"{{{GBXML_NS}}}PolyLoop")
        if ploop is not None:
            return _loop_points(ploop)
    return []


def _extract(root) -> tuple[list, list, list]:
    """(shells, faces, surfaces) from a parsed gbXML document."""
    shells: list[_Shell] = []
    faces: list[_Face] = []
    surfaces: list[tuple[str, list]] = []
    for space in root.iter(f"{{{GBXML_NS}}}Space"):
        sid = space.get("id") or "space"
        for shell in space.iter(f"{{{GBXML_NS}}}ClosedShell"):
            loops = []
            for ploop in shell.findall(f"{{{GBXML_NS}}}PolyLoop"):
                pts = _loop_points(ploop)
                if pts:
                    loops.append(pts)
            shells.append(_Shell(sid, loops))
            for i, lp in enumerate(loops):
                faces.append(_Face(f"{sid} shell loop {i}", "shell", lp))
    for surface in root.iter(f"{{{GBXML_NS}}}Surface"):
        sid = surface.get("id") or "surface"
        pts = _surface_points(surface)
        surfaces.append((sid, pts))
        if pts:
            faces.append(_Face(f"surface {sid}", "surface", pts))
    return shells, faces, surfaces


# ---- the checks -----------------------------------------------------------


def _shell_edges(loops):
    """Directed edges of a shell (zero-length edges are skipped)."""
    edges = []
    for lp in loops:
        n = len(lp)
        for i in range(n):
            a = _round_point(lp[i])
            b = _round_point(lp[(i + 1) % n])
            if a == b:
                continue
            edges.append((a, b))
    return edges


def _check_shell_closure(shells, skipped) -> CheckResult:
    total_edges = 0
    bad: list[str] = []
    for shell in shells:
        if shell.owner in skipped:
            continue
        # every edge of a shell is shared by exactly two of its own faces
        per_shell: dict[tuple, int] = {}
        for a, b in _shell_edges(shell.loops):
            key = (a, b) if a <= b else (b, a)
            per_shell[key] = per_shell.get(key, 0) + 1
        for key, count in per_shell.items():
            total_edges += 1
            if count != 2:
                bad.append(
                    f"{shell.owner}: edge {key[0]}->{key[1]} is shared by "
                    f"{count} faces (expected 2)"
                )
    measurement = {
        "shells": len(shells),
        "shells_checked": len([s for s in shells if s.owner not in skipped]),
        "shells_not_checked": sorted(skipped),
        "edges": total_edges,
        "edges_not_shared_by_two_faces": len(bad),
    }
    if bad:
        return CheckResult("shell_closure", "fail", measurement, "; ".join(bad[:5]))
    return CheckResult("shell_closure", "pass", measurement)


def _check_shell_winding(shells, skipped) -> CheckResult:
    unpaired: list[str] = []
    directed = 0
    for shell in shells:
        if shell.owner in skipped:
            continue
        counts: dict[tuple, int] = {}
        for a, b in _shell_edges(shell.loops):
            counts[(a, b)] = counts.get((a, b), 0) + 1
        for (a, b), count in counts.items():
            directed += count
            opposite = counts.get((b, a), 0)
            if opposite != count:
                unpaired.append(
                    f"{shell.owner}: edge {a}->{b} is traversed {count}x, "
                    f"its opposite {opposite}x (faces must wind consistently)"
                )
    measurement = {
        "shells": len(shells),
        "shells_not_checked": sorted(skipped),
        "directed_edges": directed,
        "edges_without_an_opposite": len(unpaired),
    }
    if unpaired:
        return CheckResult("shell_winding", "fail", measurement, "; ".join(unpaired[:5]))
    return CheckResult("shell_winding", "pass", measurement)


def _check_shell_volume(shells, skipped) -> CheckResult:
    volumes = []
    skipped_volume = 0
    negative = []
    for shell in shells:
        if shell.owner in skipped:
            continue
        edges = _shell_edges(shell.loops)
        counts: dict[tuple, int] = {}
        for a, b in edges:
            counts[(a, b)] = counts.get((a, b), 0) + 1
        consistent = all(counts.get((b, a), 0) == c for (a, b), c in counts.items())
        if not consistent:
            # the sign of the volume is meaningless without a consistent
            # winding: the shell_winding check reports that defect
            skipped_volume += 1
            continue
        v = _signed_volume(shell.loops)
        volumes.append(v)
        if v <= VOLUME_EPS_M3:
            negative.append(f"{shell.owner}: signed volume {v:.6f} m3 (must be > 0)")
    measurement = {
        "shells": len(shells),
        "shells_checked": len(volumes),
        "shells_not_checked": sorted(skipped),
        "shells_without_a_measurable_volume": skipped_volume,
        "min_volume_m3": min(volumes) if volumes else 0.0,
        "total_volume_m3": sum(volumes),
        "tolerance_m3": VOLUME_EPS_M3,
    }
    if negative:
        return CheckResult("shell_volume", "fail", measurement, "; ".join(negative[:5]))
    return CheckResult("shell_volume", "pass", measurement)


def _check_planarity(faces) -> CheckResult:
    worst = 0.0
    worst_owner = ""
    checked = 0
    bad = []
    for face in faces:
        if len(face.points) < 3:
            continue
        checked += 1
        dev = _planarity_deviation(face.points)
        if dev > worst:
            worst = dev
            worst_owner = face.owner
        if dev > PLANARITY_TOL_M:
            bad.append(f"{face.owner}: deviation {dev:.6f} m (tolerance {PLANARITY_TOL_M} m)")
    measurement = {
        "faces": len(faces),
        "faces_checked": checked,
        "max_deviation_m": worst,
        "worst_face": worst_owner,
        "tolerance_m": PLANARITY_TOL_M,
    }
    if bad:
        return CheckResult("planarity", "fail", measurement, "; ".join(bad[:5]))
    return CheckResult("planarity", "pass", measurement)


def _check_degenerate_faces(faces) -> CheckResult:
    bad = []
    for face in faces:
        distinct = {_round_point(p) for p in face.points}
        if len(distinct) < 3:
            bad.append(
                f"{face.owner}: only {len(distinct)} distinct vertices (a face needs at least 3)"
            )
            continue
        area = _face_area(face.points)
        if area < DEGENERATE_AREA_M2:
            bad.append(f"{face.owner}: area {area:.12f} m2 (degenerate)")
    measurement = {
        "faces": len(faces),
        "degenerate_faces": len(bad),
        "tolerance_m2": DEGENERATE_AREA_M2,
    }
    if bad:
        return CheckResult("degenerate_faces", "fail", measurement, "; ".join(bad[:5]))
    return CheckResult("degenerate_faces", "pass", measurement)


def _check_duplicate_surfaces(surfaces) -> CheckResult:
    seen: dict[tuple, list[str]] = {}
    for sid, pts in surfaces:
        if len(pts) < 3:
            continue
        key = tuple(sorted(_round_point(p) for p in pts))
        seen.setdefault(key, []).append(sid)
    dups = {k: v for k, v in seen.items() if len(v) > 1}
    measurement = {
        "surfaces": len(surfaces),
        "duplicate_groups": len(dups),
        "duplicate_surface_ids": sorted(sid for v in dups.values() for sid in v),
    }
    if dups:
        detail = "; ".join(", ".join(v) for v in list(dups.values())[:5])
        return CheckResult("duplicate_surfaces", "fail", measurement, detail)
    return CheckResult("duplicate_surfaces", "pass", measurement)


def unrepresentable_shells(model) -> dict:
    """Shells the exporter cannot represent: ``{space id: reason}``.

    A sloped roof can leave part of a plan without any roof plane; the
    exporter then writes that space's shell open on purpose. Such a shell is
    reported as not-checked, never as a defect and never as a pass.
    """
    out: dict[str, str] = {}
    roofs = list(getattr(model, "roof_planes", None) or [])
    if not is_sloped(roofs):
        return out
    h = getattr(model, "wall_height_m", 0.0)
    for sp in getattr(model, "spaces", None) or []:
        try:
            _loops, notes = space_shell(sp.polygon_m, roofs, h)
        except Exception:  # pragma: no cover - defensive: never fail the gate
            continue
        reasons = [n for n in notes if "left open" in n or "no roof plane" in n]
        if reasons:
            out[sp.sid] = "; ".join(reasons)
    return out


def geometric_checks(gbxml_path: str | Path, model=None) -> list:
    """Run every geometric check on a staged gbXML file.

    Returns the list of :class:`CheckResult` (one per check).
    """
    tree = safe_xml_parse(gbxml_path)
    root = tree.getroot()
    shells, faces, surfaces = _extract(root)
    skipped = unrepresentable_shells(model) if model is not None else {}
    return [
        _check_shell_closure(shells, skipped),
        _check_shell_winding(shells, skipped),
        _check_shell_volume(shells, skipped),
        _check_planarity(faces),
        _check_degenerate_faces(faces),
        _check_duplicate_surfaces(surfaces),
    ]


# ---- the gated publish ----------------------------------------------------


def _model_source_inputs(model) -> dict:
    """The inputs the publish layer can see: the model itself.

    ``publish_exports`` does not receive the source drawing or the schedule
    CSV, so the manifest records the model it exported (its source geometry
    and its settings) as an inline, hashable input. The content is embedded
    rather than referenced by path, so a manifest stays verifiable wherever
    the export directory is moved to.
    """
    spaces = []
    for sp in getattr(model, "spaces", None) or []:
        poly = [
            [round(float(x), 6), round(float(y), 6)]
            for x, y in (getattr(sp, "polygon_m", None) or [])
        ]
        spaces.append({"sid": getattr(sp, "sid", ""), "polygon_m": poly})
    payload = {
        "building_name": getattr(model, "building_name", ""),
        "wall_height_m": round(float(getattr(model, "wall_height_m", 0.0)), 6),
        "area_delta_pct": round(float(getattr(model, "area_delta_pct", 0.0)), 6),
        "simplify_tolerance_pct": round(float(getattr(model, "simplify_tolerance", 0.0)), 6),
        "spaces": spaces,
        "openings": len(getattr(model, "openings", None) or []),
    }
    content = json.dumps(payload, sort_keys=True)
    return {
        "model_source": {
            "content": content,
            "sha256": sha256_bytes(content.encode("utf-8")),
            "bytes": len(content.encode("utf-8")),
        }
    }


def _manifest_settings(model, extra: dict | None = None) -> dict:
    """The settings the manifest records (from the model, plus extras)."""
    settings = {
        "building_name": getattr(model, "building_name", ""),
        "wall_height_m": float(getattr(model, "wall_height_m", 0.0)),
        "simplify_tolerance_pct": float(getattr(model, "simplify_tolerance", 0.0)),
        "area_delta_pct": float(getattr(model, "area_delta_pct", 0.0)),
    }
    settings.update(extra or {})
    return settings


def _manifest_outputs(gb_staged: Path, ifc_staged: Path, gb_final: Path, ifc_final: Path) -> dict:
    """The published files, hashed (gbXML by bytes, IFC4 semantically)."""
    return {
        "gbxml": {
            "path": str(gb_final.resolve()),
            "sha256": sha256_file(gb_staged),
            "bytes": gb_staged.stat().st_size,
            "digest_kind": "sha256-bytes",
        },
        "ifc4": {
            "path": str(ifc_final.resolve()),
            "normalised_sha256": normalised_ifc_digest(ifc_staged),
            "bytes": ifc_staged.stat().st_size,
            "digest_kind": "normalised-semantic",
        },
    }


def _manifest_validation(model, result: GateResult, extra: dict | None = None) -> dict:
    """What the validators and the geometric checks measured."""
    validation = {
        "gbxml": {"ok": bool(result.gbxml_ok), "errors": list(result.gbxml_errors)},
        "ifc4": {"ok": bool(result.ifc4_ok), "errors": list(result.ifc4_errors)},
        "geometric_checks": [c.as_dict() for c in result.checks],
        "area_delta_pct": float(getattr(model, "area_delta_pct", 0.0)),
        "area_delta_tolerance_pct": float(getattr(model, "simplify_tolerance", 0.0)),
    }
    validation.update(extra or {})
    return validation


def publish_exports(
    model,
    out_dir: str | Path,
    base: str,
    *,
    gbxml_name: str | None = None,
    ifc_name: str | None = None,
    manifest_inputs: dict | None = None,
    manifest_settings: dict | None = None,
    manifest_validation: dict | None = None,
    replay_command: str | None = None,
    repair_loop: bool | None = None,
) -> GateResult:
    """Export, validate, check and publish - or publish nothing.

    The gbXML and IFC4 files are written to staged files in ``out_dir``, the
    validators and the geometric checks run against them, and only a run
    that passes everything is published with an atomic replace. A failed run
    deletes the staged files and leaves the previous exports untouched.

    A successful publish also writes the checksummed manifest
    ``<base>.export.json`` beside the exports. The manifest is written to a
    staged file and committed together with the exports, so a published
    export always has its manifest and a refused export leaves neither.

    ``manifest_inputs`` / ``manifest_settings`` / ``manifest_validation``
    let a caller (e.g. the CLI flow) add the inputs it owns - the source
    drawing and the schedule CSV - to the manifest this function writes.

    ``repair_loop`` (#1407) enables the deterministic repair loop for
    this publish: ``None`` (the default) takes the model's own
    ``repair_loop`` attribute, which itself defaults to off. With the
    loop off this function behaves exactly as it did before #1407. With
    it on, the staged gbXML's geometric failures in the loop's classes
    (R1 winding/volume, R2 duplicate surfaces) are repaired on the
    *staged* file - revalidated staged-only, never by re-running the
    validators - and the records are written to the manifest. The full
    validation below then runs on the final staged files exactly as
    before: the gate remains the only publish path and the final
    arbiter, and a repaired file that fails any part of it is refused.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gb_final = out_dir / (gbxml_name or f"{base}.xml")
    ifc_final = out_dir / (ifc_name or f"{base}.ifc")
    result = GateResult()
    repairs_enabled = (
        bool(getattr(model, "repair_loop", False)) if repair_loop is None else bool(repair_loop)
    )
    r4_record_count = len(getattr(model, "repair_records", None) or [])

    staged: list[Path] = []
    try:
        gb_staged = _stage_gbxml(model, gb_final)
        staged.append(gb_staged)
        ifc_staged = _stage_ifc4(model, ifc_final)
        staged.append(ifc_staged)
    except Exception as exc:  # the export itself failed: publish nothing
        _discard_staged(staged)
        result.errors.append(f"export failed: {exc}")
        return result

    if repairs_enabled:
        # R4 repairs (opening placements) happen inside the writers
        # while staging; their records ride on the model.
        result.repairs.extend((getattr(model, "repair_records", None) or [])[r4_record_count:])
        try:
            staged_checks = geometric_checks(gb_staged, model=model)
            if any(c.status == "fail" for c in staged_checks):
                from bem.repair_loop import repair_staged_gbxml

                xml_records, _staged_checks = repair_staged_gbxml(
                    gb_staged, staged_checks, model=model
                )
                result.repairs.extend(r.as_dict() for r in xml_records)
        except Exception as exc:
            _discard_staged(staged)
            result.errors.append(f"repair loop failed: {exc}")
            return result

    try:
        result.gbxml_ok, result.gbxml_errors = validate_gbxml(gb_staged)
        result.ifc4_ok, result.ifc4_errors = validate_ifc4(ifc_staged)
    except Exception as exc:
        _discard_staged(staged)
        result.errors.append(f"validation failed: {exc}")
        return result

    try:
        result.checks = geometric_checks(gb_staged, model=model)
    except Exception as exc:
        _discard_staged(staged)
        result.errors.append(f"geometric checks failed to run: {exc}")
        return result

    if not result.gbxml_ok:
        result.errors.extend(f"gbXML: {e}" for e in result.gbxml_errors[:10])
    if not result.ifc4_ok:
        result.errors.extend(f"IFC4: {e}" for e in result.ifc4_errors[:10])
    for check in result.checks:
        if check.status == "fail":
            result.errors.append(f"geometric check {check.name}: {check.detail}")
        elif check.status == "not_checked":
            result.errors.append(f"geometric check {check.name}: not checked ({check.detail})")

    if result.errors:
        _discard_staged(staged)
        result.ok = False
        return result

    # The manifest is staged next to the exports and committed with them, so
    # a published export always has its manifest and a refused export leaves
    # the previous manifest untouched.
    manifest_final = out_dir / f"{base}.export.json"
    validation_extra = dict(manifest_validation or {})
    if repairs_enabled:
        # #1407: every applied repair is a manifest fact; a loop-enabled
        # run with no repairs records the empty list, not a missing field.
        validation_extra["repairs"] = list(result.repairs)
    try:
        manifest = build_manifest(
            sheet=base,
            settings=_manifest_settings(model, manifest_settings),
            inputs={**_model_source_inputs(model), **(manifest_inputs or {})},
            outputs=_manifest_outputs(gb_staged, ifc_staged, gb_final, ifc_final),
            validation=_manifest_validation(model, result, validation_extra or None),
            replay_command=replay_command or f"matchline bem-export --sheet-id {base}",
        )
        manifest_staged = _staged_path(manifest_final)
        write_manifest(manifest, manifest_staged)
        staged.append(manifest_staged)
    except Exception as exc:
        _discard_staged(staged)
        result.errors.append(f"manifest failed: {exc}")
        result.ok = False
        return result

    try:
        _commit_staged(
            [
                (gb_staged, gb_final),
                (ifc_staged, ifc_final),
                (manifest_staged, manifest_final),
            ]
        )
    except Exception as exc:
        _discard_staged(staged)
        result.errors.append(f"publish failed (rolled back): {exc}")
        result.ok = False
        return result
    result.ok = True
    result.published = {
        "gbxml": gb_final,
        "ifc4": ifc_final,
        "manifest": manifest_final,
    }
    return result
