"""Site-EUI band for the simulation smoke test from CBECS 2018 (#752).

Reads the 2018 CBECS public use microdata (EIA, U.S. government data; not
committed, downloaded and checked by sha256) and computes weighted (FINALWT)
percentiles of site EUI = MFBTU / SQFT for the filter stored with each
reference in ``sim/reference_eui.json``. ``--write`` stores the result there.

    python sim/cbecs_band.py --csv cbecs2018_final_public.csv [--write]

The public file is masked and its consumption perturbed for disclosure
avoidance (User's Guide to the 2018 CBECS Public Use Microdata File), so
these percentiles differ slightly from EIA's published tables.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / "reference_eui.json"
URL = "https://www.eia.gov/consumption/commercial/data/2018/xls/cbecs2018_final_public.csv"
SHA256 = "3e37c81aab2938e63fae47bec837270ba696699aceef904d1326dd25970e7247"
KBTU_FT2_TO_MJ_M2 = 11.356526  # 1.055056 MJ/kBtu / 0.09290304 m2/ft2
PCTS = (5, 10, 25, 50, 65, 70, 75, 90, 95)
MAPS_URL = "https://www.eia.gov/consumption/commercial/maps.php"
# EIA, "Definitions of CBECS climate zones, 2018 CBECS" (MAPS_URL): ASHRAE
# 169-2021 county climate zones combined by thermal climate zone. Codes are
# the microdata's PUBCLIM values (codebook; 7 = withheld).
PUBCLIM_ZONES = {
    1: ("Cold or very cold", ("6A", "6B", "7", "8")),
    2: ("Cool", ("5A", "5B", "5C")),
    3: ("Mixed mild", ("4A", "4B", "4C")),
    4: ("Warm", ("3A", "3B", "3C")),
    5: ("Hot or very hot", ("1A", "1B", "2A", "2B")),
}


def pubclim_for(climate_zone: str) -> int:
    """The 2018 CBECS climate group (PUBCLIM) an ASHRAE 169 zone belongs to."""
    z = climate_zone.strip().upper()
    for code, (_, zones) in PUBCLIM_ZONES.items():
        if z in zones or (z[:1] in ("7", "8") and z[:1] in zones):
            return code
    raise KeyError(f"no 2018 CBECS climate group for ASHRAE zone {climate_zone!r}")


def weighted_percentile(values, weights, pct: float) -> float:
    """Weighted percentile, each record at the midpoint of its weight (linear between)."""
    pairs = sorted(zip(values, weights))
    total = sum(w for _, w in pairs)
    cum, xs, cs = 0.0, [], []
    for v, w in pairs:
        cs.append((cum + 0.5 * w) / total)
        xs.append(v)
        cum += w
    q = pct / 100.0
    if q <= cs[0]:
        return xs[0]
    for i in range(1, len(cs)):
        if q <= cs[i]:
            t = (q - cs[i - 1]) / (cs[i] - cs[i - 1])
            return xs[i - 1] + t * (xs[i] - xs[i - 1])
    return xs[-1]


def rows(csv_path, flt: dict):
    """(site EUI MJ/m2, FINALWT) for records matching the filter with MFBTU and SQFT."""
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r["MFBTU"] or not r["SQFT"] or float(r["SQFT"]) <= 0:
                continue
            if any(int(float(r[k])) not in v for k, v in flt.items()):
                continue
            yield float(r["MFBTU"]) / float(r["SQFT"]) * KBTU_FT2_TO_MJ_M2, float(r["FINALWT"])


def band(csv_path, cb: dict) -> dict:
    flt = {k.upper(): v for k, v in cb["filter"].items()}
    data = list(rows(csv_path, flt))
    eui, wt = [d[0] for d in data], [d[1] for d in data]
    pct = {f"p{p}": round(weighted_percentile(eui, wt, p), 1) for p in PCTS}
    lo, hi = cb["band_percentiles"]
    return {
        "records": len(data),
        "buildings_represented": round(sum(wt)),
        "percentiles_mj_m2": pct,
        "band_mj_m2": [pct[f"p{lo}"], pct[f"p{hi}"]],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", required=True, help=f"2018 CBECS microdata ({URL})")
    ap.add_argument("--reference", default=str(REFERENCE))
    ap.add_argument("--write", action="store_true", help="store the bands in the reference file")
    a = ap.parse_args(argv)
    got = hashlib.sha256(Path(a.csv).read_bytes()).hexdigest()
    if got != SHA256:
        ap.error(f"{a.csv}: sha256 {got}, expected {SHA256} (December 2022 revision)")
    path = Path(a.reference)
    data = json.loads(path.read_text())
    for ref in data["references"]:
        cb = ref["cbecs"]
        cb.update(band(a.csv, cb))
        print(ref["building_type"], ref["climate_zone"], json.dumps(cb["percentiles_mj_m2"]))
    if a.write:
        path.write_text(json.dumps(data, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
