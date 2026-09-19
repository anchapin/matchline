"""Demo: build 2 synthetic buildings, link them, run the validation battery.

Usage:  python3 run_validation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from synth.multidiscipline import generate_building  # noqa: E402
from link import build_model  # noqa: E402
from geometry_simplify import (  # noqa: E402
    footprint_from_regions, simplify_ring)
from validate import run_checks, export_gate  # noqa: E402


def main():
    configs = [(301, False, "elev_grid"), (302, True, "elev_nogrid")]
    for seed, span, ekey in configs:
        bldg = generate_building(seed, open_office_span=span)
        model, link_report = build_model(
            bldg, elevation_key=ekey, building_name=bldg["building_id"])
        h = model.levels[0].wall_height_m
        ring = footprint_from_regions(
            [sp.polygon_m for sp in model.spaces.values()])
        sres = simplify_ring(ring, tol=0.02, wall_height=h)
        report = run_checks(model, sres=sres)
        print(f"=== {bldg['building_id']} "
              f"(span={span}, elev={ekey}) ===")
        print(f"  link: {link_report.n_spaces} spaces, "
              f"{link_report.n_zones} zones, "
              f"{link_report.fixtures_assigned} fixtures, "
              f"{link_report.windows_linked} windows, "
              f"{link_report.review_items} review items")
        print(f"  simplify: {sres.original_count} -> "
              f"{sres.simplified_count} surfaces, "
              f"area delta {sres.area_delta_pct:+.3f}%")
        print(report.compact())
        print(f"  export gate: "
              f"{'OPEN' if export_gate(report) else 'CLOSED'}")
        print()


if __name__ == "__main__":
    main()
