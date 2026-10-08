"""Annual simulation smoke test for the gbXML export (#752).

CI proves the export imports into OpenStudio (#628); this proves it simulates
and gives a plausible answer. It translates the gbXML to an OSM, adds the DOE
prototype space types, loads and inferred HVAC template with
openstudio-standards (``sim/typical.rb``), runs a full year of EnergyPlus on a
TMY weather file, then reads:

* whether EnergyPlus completed, and its severe/fatal error count
* site EUI (MJ/m2) against the matching DOE prototype's EUI in
  ``sim/reference_eui.json``, within the band stated there
* occupied unmet heating and cooling hours (reported against the 300 h
  Appendix G limit, not enforced)

A model that does not complete, has severe errors or has no conditioned area
fails (exit 1). An EUI outside the band is reported, not auto-fixed (exit 0,
``status: eui_out_of_band``) unless ``--strict``.

Needs the OpenStudio CLI (3.11.0 bundles openstudio-standards 0.8.5 and
EnergyPlus 25.2.0); too slow and too large for the per-PR suite, so it runs
nightly (.github/workflows/sim-smoke.yml).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "sim" / "reference_eui.json"
TYPICAL_RB = ROOT / "sim" / "typical.rb"
PROTOTYPE_RB = ROOT / "sim" / "prototype.rb"

_DONE = re.compile(r"EnergyPlus Completed Successfully-- (\d+) Warning; (\d+) Severe Errors")
_ABUPS = "AnnualBuildingUtilityPerformanceSummary"


def read_err(path: str | Path) -> dict:
    """Completion and error counts from an ``eplusout.err``."""
    text = Path(path).read_text(errors="replace") if Path(path).exists() else ""
    m = _DONE.search(text)
    return {
        "completed": bool(m),
        "warnings": int(m.group(1)) if m else None,
        "severe": int(m.group(2)) if m else text.count("** Severe  **"),
        "fatal": "** Fatal  **" in text or "Terminated--Fatal Error" in text,
    }


def _num(v) -> float | None:
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def read_sql(path: str | Path) -> dict:
    """Site EUI, areas, unmet hours and end uses from an ``eplusout.sql``."""
    con = sqlite3.connect(str(path))
    try:

        def cell(table, row, col):
            r = con.execute(
                "select Value from TabularDataWithStrings where ReportName=? "
                "and TableName=? and RowName=? and ColumnName=?",
                (_ABUPS, table, row, col),
            ).fetchone()
            return _num(r[0]) if r else None

        end_uses = {}
        for row, col, val, units in con.execute(
            "select RowName, ColumnName, Value, Units from TabularDataWithStrings "
            "where ReportName=? and TableName='End Uses'",
            (_ABUPS,),
        ):
            v = _num(val)
            if units == "GJ" and v and row != "Total End Uses":
                end_uses[f"{row} / {col}"] = v
        total_gj = cell("Site and Source Energy", "Total Site Energy", "Total Energy")
        area = cell("Building Area", "Total Building Area", "Area")
        cond = cell("Building Area", "Net Conditioned Building Area", "Area")
        unmet_h = cell(
            "Comfort and Setpoint Not Met Summary",
            "Time Setpoint Not Met During Occupied Heating",
            "Facility",
        )
        unmet_c = cell(
            "Comfort and Setpoint Not Met Summary",
            "Time Setpoint Not Met During Occupied Cooling",
            "Facility",
        )
    finally:
        con.close()
    return {
        "site_energy_gj": total_gj,
        "floor_area_m2": area,
        "conditioned_area_m2": cond,
        "site_eui_mj_m2": (total_gj * 1000.0 / area) if total_gj and area else None,
        "unmet_heating_h": unmet_h,
        "unmet_cooling_h": unmet_c,
        "end_uses_mj_m2": {
            k: round(v * 1000.0 / area, 1) for k, v in sorted(end_uses.items()) if area
        },
    }


def load_reference(path, building_type, template, climate_zone) -> tuple[dict, dict]:
    """(the matching reference row, the whole reference file)."""
    data = json.loads(Path(path).read_text())
    for ref in data["references"]:
        if (ref["building_type"], ref["template"], ref["climate_zone"]) == (
            building_type,
            template,
            climate_zone,
        ):
            return ref, data
    raise KeyError(f"no reference EUI for {building_type} / {template} / {climate_zone} in {path}")


def judge(err: dict, res: dict, ref: dict, data: dict) -> dict:
    """The verdict: ``fail``, ``eui_out_of_band`` or ``ok``, with its reasons."""
    reasons, notes = [], []
    if not err["completed"] or err["fatal"]:
        reasons.append("EnergyPlus did not complete")
    if err["severe"]:
        reasons.append(f"{err['severe']} severe error(s)")
    if not res.get("conditioned_area_m2"):
        reasons.append("no conditioned floor area")
    band = data["band"]
    lo = ref["site_eui_mj_m2"] * band["low_factor"]
    hi = ref["site_eui_mj_m2"] * band["high_factor"]
    eui = res.get("site_eui_mj_m2")
    in_band = eui is not None and lo <= eui <= hi
    limit = data["unmet_hours_limit"]["hours"]
    for key in ("unmet_heating_h", "unmet_cooling_h"):
        v = res.get(key)
        if v is not None and v > limit:
            notes.append(f"{key} {v:.0f} h is over the {limit} h Appendix G limit")
    status = "fail" if reasons else ("ok" if in_band else "eui_out_of_band")
    if status == "eui_out_of_band":
        notes.append(
            f"site EUI {eui:.1f} MJ/m2 is outside {lo:.1f}-{hi:.1f} "
            f"({band['low_factor']}x-{band['high_factor']}x the {ref['building_type']} "
            f"{ref['template']} {ref['climate_zone']} prototype's {ref['site_eui_mj_m2']})"
            if eui is not None
            else "no site EUI"
        )
    return {
        "status": status,
        "reasons": reasons,
        "notes": notes,
        "band_mj_m2": [round(lo, 1), round(hi, 1)],
        "eui_ratio": round(eui / ref["site_eui_mj_m2"], 3) if eui else None,
    }


def _run(cmd, log: Path, timeout: int) -> int:
    with open(log, "w") as f:
        return subprocess.run(
            cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, check=False
        ).returncode


def annual(openstudio: str, osm: Path, epw: Path, out: Path, timeout: int) -> Path:
    """Run a full year on ``osm``; returns the EnergyPlus run directory."""
    osw = out / "annual.osw"
    osw.write_text(json.dumps({"seed_file": str(osm), "weather_file": str(epw), "steps": []}))
    _run([openstudio, "run", "-w", str(osw)], out / "annual.log", timeout)
    return out / "run"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gbxml", help="exported gbXML to simulate")
    ap.add_argument("--epw", required=True, help="TMY weather (.ddy and .stat beside it)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--openstudio", default=shutil.which("openstudio") or "openstudio")
    ap.add_argument("--building-type", default="SmallOffice")
    ap.add_argument("--template", default="90.1-2019")
    ap.add_argument("--climate-zone", default="5A")
    ap.add_argument("--reference", default=str(REFERENCE))
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per OpenStudio step")
    ap.add_argument("--strict", action="store_true", help="exit 1 on an EUI miss too")
    ap.add_argument(
        "--reference-only",
        action="store_true",
        help="simulate the DOE prototype instead and print its EUI (refreshes the reference)",
    )
    a = ap.parse_args(argv)
    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    epw = Path(a.epw).resolve()

    if a.reference_only:
        _run(
            [a.openstudio, str(PROTOTYPE_RB), str(out), a.building_type, a.template,
             a.climate_zone, epw.name],
            out / "prototype.log",
            a.timeout,
        )  # fmt: skip
        osm = out / "prototype.osm"
    else:
        if not a.gbxml:
            ap.error("--gbxml is required unless --reference-only")
        _run(
            [a.openstudio, str(TYPICAL_RB), str(Path(a.gbxml).resolve()), str(epw), str(out),
             a.building_type, a.template, a.climate_zone],
            out / "typical.log",
            a.timeout,
        )  # fmt: skip
        osm = out / "typical.osm"
    report = {"model": a.gbxml, "weather": epw.name, "osm_built": osm.exists()}
    if osm.exists():
        run = annual(a.openstudio, osm, epw, out, a.timeout)
        err = read_err(run / "eplusout.err")
        res = read_sql(run / "eplusout.sql") if (run / "eplusout.sql").exists() else {}
    else:
        err = {"completed": False, "warnings": None, "severe": 0, "fatal": True}
        res = {}
    report.update(err=err, result=res)
    if a.reference_only:
        report["status"] = "reference"
    else:
        ref, data = load_reference(a.reference, a.building_type, a.template, a.climate_zone)
        report["reference"] = ref
        report.update(judge(err, res, ref, data))
        if not osm.exists():
            report["reasons"].insert(0, "OpenStudio could not build the model (see typical.log)")
    (out / "sim_smoke_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report.get(k) for k in ("status", "reasons", "notes", "eui_ratio")}))
    if report["status"] == "fail" or (a.strict and report["status"] == "eui_out_of_band"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
