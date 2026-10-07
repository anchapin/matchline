"""Smoke-test ``load_archcad`` against the real ArchCAD download.

ArchCAD (https://huggingface.co/datasets/jackluoluo/ArchCAD) is gated and
licensed CC BY-NC 4.0, so it is never committed. The ``archcad-smoke``
workflow downloads ``data/json.zip`` with the repo's ``HF_TOKEN`` secret and
runs this script, which reports only counts and schema facts (key names,
primitive types, class ids), never drawing content.

    python scripts/archcad_smoke.py --root /path/with/json.zip --max-samples 500
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import sys
import traceback
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datasets_adapter import ARCHCAD_TO_TAKEOFF, ArchCADFormatError, load_archcad  # noqa: E402


def _ellipse_endpoint_fit(p: dict) -> str:
    """Check the loader's ELLIPSE convention against the file's own endpoints."""
    from datasets_adapter import _archcad_ellipse

    tag = "ccw" if p.get("is_ccw") is not False else "cw"
    try:
        pts = _archcad_ellipse(p, "probe")
        tol = 0.01 * math.hypot(*p["major_axis"][:2])
        ok = all(
            math.dist(pts[i], p[k][:2]) <= tol
            for i, k in ((0, "start_point"), (-1, "end_point"))
            if k in p
        )
    except Exception as e:  # noqa: BLE001 - report, never fail the probe
        return f"{tag}:error:{type(e).__name__}"
    return f"{tag}:{'match' if ok else 'mismatch'}"


def probe_schema(zip_path: Path, n_files: int) -> list[str]:
    """Describe the raw JSON layout so drift from the dataset card is visible."""
    out = []
    zf = zipfile.ZipFile(zip_path)
    names = sorted(n for n in zf.namelist() if n.endswith(".json"))
    out.append(f"- json.zip: {zip_path.stat().st_size / 1e6:.1f} MB, {len(names)} JSON files")
    out.append(f"- first names: {', '.join(names[:3])}")
    top, pkeys, ptypes, sems, insts = (collections.Counter() for _ in range(5))
    loose, other_keys = collections.Counter(), collections.Counter()
    ell_sem, ell_fit = collections.Counter(), collections.Counter()
    for n in names[:n_files]:
        data = json.loads(zf.read(n))
        if isinstance(data, dict):
            top["dict:" + ",".join(sorted(data.keys()))[:120]] += 1
            data = data.get("entities", data.get("primitives", []))
        else:
            top[type(data).__name__] += 1
        if not isinstance(data, list):
            continue
        for p in data:
            if not isinstance(p, dict):
                pkeys["<non-dict " + type(p).__name__ + ">"] += 1
                continue
            pkeys[",".join(sorted(p.keys()))] += 1
            ptypes[str(p.get("type"))] += 1
            sems[repr(p.get("semantic"))] += 1
            insts[type(p.get("instance")).__name__] += 1
            if p.get("instance") is None:
                loose[repr(p.get("semantic"))] += 1
            if p.get("type") == "ELLIPSE":
                ell_sem[repr(p.get("semantic"))] += 1
                ell_fit[_ellipse_endpoint_fit(p)] += 1
            if p.get("type") not in ("LINE", "ARC", "CIRCLE"):
                other_keys[f"{p.get('type')}: " + ",".join(sorted(p.keys()))] += 1
    out.append(f"- top-level shapes (first {n_files} files): {dict(top.most_common(5))}")
    out.append(f"- primitive key sets: {dict(pkeys.most_common(6))}")
    out.append(f"- primitive types: {dict(ptypes.most_common(12))}")
    out.append(f"- semantic values: {dict(sems.most_common(40))}")
    out.append(f"- instance value types: {dict(insts)}")
    out.append(f"- semantic ids with no instance: {dict(loose.most_common(40))}")
    out.append(f"- other primitive key sets: {dict(other_keys.most_common(5))}")
    out.append(f"- ELLIPSE by semantic id: {dict(ell_sem.most_common(20))}")
    out.append(
        "- ELLIPSE endpoints vs start_point/end_point (loader polyline, "
        f"within 1% of the major radius): {dict(ell_fit)}"
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--max-samples", type=int, default=500)
    ap.add_argument("--probe-files", type=int, default=200)
    args = ap.parse_args()
    root = Path(args.root)
    lines = ["## ArchCAD loader smoke test", ""]
    ok = True
    zips = sorted(root.rglob("json.zip"))
    if zips:
        lines += ["### Raw schema", *probe_schema(zips[0], args.probe_files), ""]
    lines.append(f"### load_archcad(max_samples={args.max_samples})")
    try:
        samples, takeoff = load_archcad(root, max_samples=args.max_samples)
        labels = collections.Counter(s.label for s in samples)
        slices = len({s.source for s in samples})
        lines.append(f"- {len(samples)} symbols from {slices} slices")
        lines.append(f"- symbols by class: {dict(labels.most_common())}")
        cats = collections.Counter()
        for lab, n in labels.items():
            if lab in ARCHCAD_TO_TAKEOFF:
                cats[ARCHCAD_TO_TAKEOFF[lab]] += n
        lines.append(f"- takeoff regions by category: {dict(cats)}")
        lines.append(f"- takeoff scale: {getattr(takeoff, 'scale', None)}")
        if not samples:
            ok = False
            lines.append("- FAIL: loaded zero symbols")
    except ArchCADFormatError as exc:
        ok = False
        lines.append(f"- FAIL (schema drift): {exc}")
    except Exception:  # noqa: BLE001 - report anything else verbatim
        ok = False
        lines.append("- FAIL:\n```\n" + traceback.format_exc()[-3000:] + "\n```")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
