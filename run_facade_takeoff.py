"""End-to-end demo: facade area takeoffs on the CMP Facade dataset.

Part 1: detailed takeoff + XML cross-check on a handful of facades.
Part 2: full 606-facade sweep -> priors table + agreement stats,
        saved to facade_priors.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from facade_takeoff import (
    CLASS_NAMES,
    dataset_priors,
    facade_takeoff,
    iter_facades,
    load_mask,
    parse_xml_boxes,
    sweep,
    xml_agreement,
)

DATA_ROOT = Path.home() / "workspace" / "datasets" / "cmp-facade"
OUT_JSON = Path(__file__).resolve().parent / "facade_priors.json"


def demo_facades(n: int = 5, data_root: "str | Path | None" = None) -> None:
    print("=" * 70)
    print("PART 1: per-facade takeoffs + XML cross-check")
    print("=" * 70)
    root = Path(data_root) if data_root else DATA_ROOT
    if not root.exists():
        raise SystemExit(
            f"CMP Facade dataset not found at {root}; it lives outside the repo "
            "(see CONTRIBUTING.md for setup). Pass --data-root to override."
        )
    facs = list(iter_facades(root))[:n]
    for i, (fid, png, xml) in enumerate(facs):
        mask = load_mask(png)
        # give the first facade a known width to exercise absolute areas
        t = facade_takeoff(mask, fid, width_m=24.0 if i == 0 else None, sheet_id=f"cmp_{fid}")
        print(
            f"\n[{fid}] {t.image_size_px[0]}x{t.image_size_px[1]} px  "
            f"wall_plane={t.wall_plane_px / 1000:.0f}k px"
        )
        print(
            f"  WWR={t.wwr:.3f}  door={t.frac_door:.3f}  "
            f"opaque={t.frac_opaque:.3f}  "
            f"(closure={t.frac_opaque + t.frac_glazing + t.frac_door:.6f})"
        )
        print(
            f"  glazing split: blind={t.frac_blind_of_glazing:.2f}  "
            f"shop={t.frac_shop_of_glazing:.2f}"
        )
        if t.px_per_m:
            print(
                f"  scaled: {t.px_per_m:.1f} px/m -> "
                f"wall={t.area_wall_plane_m2:.1f} m2  "
                f"glazing={t.area_glazing_m2:.1f} m2  "
                f"door={t.area_door_m2:.1f} m2"
            )
        ag = xml_agreement(mask, parse_xml_boxes(xml))
        g = ag["glazing_combined"]
        w = ag["per_class"][3]
        print(
            f"  XML vs mask glazing: area_ratio={g['ratio_xml_over_mask']:.2f} "
            f"iou={g['iou']:.2f} "
            f"recall_slop={g['coverage_mask_in_xml']:.2f} "
            f"prec_slop={g['coverage_xml_in_mask']:.2f}"
        )
        print(
            f"  windows: {w['n_xml_boxes']} XML boxes vs {w['n_mask_components']} mask components"
        )
        env = t.to_envelope_dict()
        print(f"  envelope handoff: wwr={env['wwr']:.3f} scaled={env['scale_supplied']}")


def full_sweep(data_root: "str | Path | None" = None, out_json: "str | Path | None" = None) -> dict:
    print("\n" + "=" * 70)
    print("PART 2: full dataset sweep (606 facades)")
    print("=" * 70)
    t0 = time.time()
    root = Path(data_root) if data_root else DATA_ROOT
    out_path = Path(out_json) if out_json else OUT_JSON
    if not root.exists():
        raise SystemExit(f"CMP Facade dataset not found at {root}; pass --data-root.")
    takeoffs, agreements, skipped = sweep(root, with_xml=True)
    dt = time.time() - t0
    print(
        f"sweep: {len(takeoffs)} ok, {len(skipped)} skipped, "
        f"{dt:.1f}s ({dt / max(len(takeoffs), 1):.2f}s/facade)"
    )
    for s in skipped[:10]:
        print("  SKIP:", s)

    priors = dataset_priors(takeoffs)

    def row(name, st):
        return (
            f"  {name:16s} n={st['n']:3d}  mean={st['mean']:.3f}  "
            f"median={st['median']:.3f}  p10={st['p10']:.3f}  "
            f"p90={st['p90']:.3f}  range=[{st['min']:.3f},{st['max']:.3f}]"
        )

    print("\nPriors (fractions over wall plane):")
    print(row("WWR", priors["wwr"]))
    print(row("door_fraction", priors["door_fraction"]))
    print(row("opaque_fraction", priors["opaque_fraction"]))
    print(row("blind_of_glazing", priors["blind_of_glazing"]))
    print(row("shop_of_glazing", priors["shop_of_glazing"]))

    # XML agreement aggregates (glazing combined)
    import numpy as np

    def agg(key):
        vs = [
            a["glazing_combined"][key] for a in agreements if a["glazing_combined"][key] is not None
        ]
        vs = np.array(vs)
        return (
            f"mean={vs.mean():.3f} median={np.median(vs):.3f} "
            f"p10={np.percentile(vs, 10):.3f} p90={np.percentile(vs, 90):.3f}"
        )

    print("\nXML-vs-mask agreement (glazing, all facades):")
    print("  area_ratio_xml/mask :", agg("ratio_xml_over_mask"))
    print("  iou (strict)        :", agg("iou"))
    print("  recall (slop-tol)   :", agg("coverage_mask_in_xml"))
    print("  precision (slop-tol):", agg("coverage_xml_in_mask"))

    # per-class area ratios
    print("\nPer-class XML/mask area ratios:")
    for lab in (3, 4, 8, 12):
        vs = np.array(
            [
                a["per_class"][lab]["ratio_xml_over_mask"]
                for a in agreements
                if a["per_class"][lab]["ratio_xml_over_mask"] is not None
            ]
        )
        print(
            f"  {CLASS_NAMES[lab]:8s} n={len(vs):3d} "
            f"mean={vs.mean():.2f} median={np.median(vs):.2f}"
        )

    # box count vs component count (windows)
    bw = np.array([a["per_class"][3]["n_xml_boxes"] for a in agreements])
    cw = np.array([a["per_class"][3]["n_mask_components"] for a in agreements])
    print(
        f"\nWindow instances: {bw.sum()} XML boxes vs "
        f"{cw.sum()} mask components "
        f"(ratio {bw.sum() / max(cw.sum(), 1):.2f})"
    )

    out = {
        "dataset": "CMP Facade (CC BY-SA)",
        "n_facades": len(takeoffs),
        "n_skipped": len(skipped),
        "priors": priors,
        "notes": (
            "Fractions are over the wall-plane pixel set "
            "{facade,window,door,blind,shop}. Glazing = window+blind+shop."
        ),
    }
    out_path.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {out_path}")
    return out


if __name__ == "__main__":
    demo_facades()
    full_sweep()
    print("\nALL PASS")
