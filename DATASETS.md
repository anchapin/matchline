# External Datasets

External datasets are **not committed** to the repository. All demos and commands that
require external data fail clearly when the data is absent.

Licenses: each dataset's license, and every artifact trained or derived from it, is
recorded in [`license_ledger.json`](assets/license_ledger.json) (see
[docs/license_ledger.md](docs/license_ledger.md)). Artifacts from non-commercial or
copyleft data are evaluation-only and never ship in a release.

Base path convention: all external datasets live under `~/workspace/datasets/`.

---

## AEC-Bench (AEC Geometric Bench)

**Local path:** `~/workspace/datasets/aec-geometric-bench/dataset`

**Environment variable:** None (path passed via `--aec-bench` CLI flag or `AEC_ROOT` in scripts)

**What it is:** Annotated architectural drawings (15-sheet release) with XML annotations
(`annotations_15.xml`) covering rooms, doors, windows, and wall boundaries.

**Used for in matchline:**

- `matchline run --aec-bench /path/to/aec-bench` — unified pipeline on real drawings
- `synth/gap_experiment.py` — gap analysis against synthetic vs. real building geometry
- `datasets_adapter.py` — validation of the synthetic data adapter against real annotations
- `detector/convert_aec.py` — conversion script producing `~/workspace/datasets/detector_yolo/aec/`

**Setup:**

```bash
# Clone or extract AEC-Bench into the expected location
mkdir -p ~/workspace/datasets/aec-geometric-bench/
# Place dataset contents so that annotations_15.xml is directly under:
# ~/workspace/datasets/aec-geometric-bench/dataset/annotations_15.xml
```

**Reference:** <http://aec-bench.github.io/>

---

## CMP Facade Database

**Local path:** `~/workspace/datasets/cmp-facade`

**Environment variable:** None (overridden via `--data-root` CLI flag or `DATA_ROOT` in scripts)

**What it is:** Annotated facade images with bounding-box XML annotations for windows,
doors, and facade elements. License: CC BY-SA (no version named). `configs/facade_priors.json`
is derived from it and stays CC BY-SA; see `NOTICE.md`.

**Used for in matchline:**

- `matchline facade-takeoff --n 5` — CMP Facade area takeoffs (wall/glazing/door fractions)
- `run_facade_takeoff.py` — generates `configs/facade_priors.json` for the BEM review queue
- `facade_takeoff.py` — core extraction logic for facade elements

**Setup:**

```bash
mkdir -p ~/workspace/datasets/cmp-facade
# Download from http://cmp.felk.cvut.cz/~tylecr1/facade/
# Extract so images and XML annotations are directly under ~/workspace/datasets/cmp-facade/
```

**Reference:** <http://cmp.felk.cvut.cz/~tylecr1/facade/> — Tylecek & Sara

---

## CubiCasa5K

**Local path:** `~/workspace/datasets/cubicasa5k/cubicasa5k`

**Environment variable:** None

**What it is:** 5,000 annotated floor plan images with room labels, door/window symbols,
and spatial hierarchy. Used widely in the building performance community.

**Used for in matchline:**

- `detector/convert_cubicasa.py --src ~/workspace/datasets/cubicasa5k/cubicasa5k --out ~/workspace/datasets/detector_yolo/cubicasa` — converts to YOLO format for detector fine-tuning

**Setup:**

```bash
mkdir -p ~/workspace/datasets/cubicasa5k/
# Download from official CubiCasa5K source and place contents under:
# ~/workspace/datasets/cubicasa5k/cubicasa5k/
```

**Output:** YOLO-formatted annotations are written to `~/workspace/datasets/detector_yolo/cubicasa/`

---

## FloorPlanCAD

**Local path:** `~/workspace/datasets/floorplancad/train-00000-of-00001.parquet`

**Environment variable:** None

**What it is:** CAD-grade floor plan dataset in Parquet format, covering architectural
symbols, walls, doors, windows, and room labels at high spatial precision.

**Used for in matchline:**

- `datasets_adapter.load_floorplancad(root)` — symbol crops + takeoff regions; schema in `docs/datasets_adapter.md`
- `detector/convert_floorplancad.py --src ~/workspace/datasets/floorplancad/train-00000-of-00001.parquet --out ~/workspace/datasets/detector_yolo/floorplancad` — converts to YOLO format for detector fine-tuning

**Setup:**

```bash
mkdir -p ~/workspace/datasets/floorplancad/
# Download data/train-00000-of-00001.parquet from
# https://huggingface.co/datasets/tilak1114/FloorPlanCAD and place it at:
# ~/workspace/datasets/floorplancad/train-00000-of-00001.parquet
```

**Output:** YOLO-formatted annotations are written to `~/workspace/datasets/detector_yolo/floorplancad/`

---

## Detector Runs (YOLO fine-tuning output)

**Local path:** `~/workspace/datasets/detector_runs/<name>/`

**Environment variable:** None

**What it is:** Output directory for YOLO fine-tuning runs started via `detector/train.py`.
Each run gets its own timestamped subdirectory.

**Used for in matchline:**

- `detector/train.py` — YOLO fine-tuning entry point; reads source datasets and writes run outputs here
- `detector/eval_zero_shot.py` — zero-shot evaluation reads `~/workspace/datasets/detector_yolo/aec/labels/eval`

**Setup:** Created automatically by `detector/train.py`. No manual setup required.

**Note:** These directories are never created inside the repo — they always live under
`~/workspace/datasets/detector_runs/`.

---

## Converted YOLO Datasets (intermediate artifacts)

**Local path:** `~/workspace/datasets/detector_yolo/{aec,cubicasa,floorplancad}/`

**Environment variable:** None

**What it is:** YOLO-format annotation directories produced by the conversion scripts
(`convert_aec.py`, `convert_cubicasa.py`, `convert_floorplancad.py`) from the raw
external datasets. Used as inputs to `detector/train.py`.

| Source dataset | Output subdirectory |
|---|---|
| AEC-Bench | `~/workspace/datasets/detector_yolo/aec/` |
| CubiCasa5K | `~/workspace/datasets/detector_yolo/cubicasa/` |
| FloorPlanCAD | `~/workspace/datasets/detector_yolo/floorplancad/` |

**Setup:** Produced automatically by running the respective `convert_*.py` script.

---

## Dataset Presence Check

When a dataset is required but absent, the CLI fails with a clear error rather than
producing incorrect results. For example:

```bash
# matchline run --aec-bench ~/workspace/datasets/aec-geometric-bench/dataset
# → fails clearly if annotations_15.xml is not found

# matchline facade-takeoff --data-root ~/workspace/datasets/cmp-facade
# → fails clearly if CMP Facade XML/image files are not found
```

See `docs/cli.md` for the `--data-root` and `--aec-bench` flags, and each dataset's
conversion script for the exact expected file layout.

---

## TUM GNI BIM Dataset

**Local path:** `~/workspace/datasets/matchline-bench/gni-<n>/arch.ifc` (8 candidates
extracted from the 2025 BIMfundamentals set; the full archives stay under
`~/workspace/datasets/gni/`)

**Environment variable:** None (paths passed via the benchmark `--config`)

**What it is:** 224 anonymized building models in a mix of IFC 2x3 and IFC 4 (Technical University of Munich,
GNI research group), including commercial multi-storey office and school buildings
with IfcSpaces, walls, windows and material layers.

**Used for in matchline:**

- `scripts/benchmark.py --config benchmarks.json` - real-building scoreboard (#748)
- `ifc_import` Tier 0 robustness work: these files are why the kernel-unsafe
  geometry fallbacks exist (IfcPolygonalFaceSet bodies, IfcIndexedPolyCurve
  profiles)

**Reference:** <https://zenodo.org/records/19722012> (CC BY 4.0)

**Benchmark adoption (#929 acceptance):** `gni-18` (`model_18.ifc`, IFC2X3, 1.7 MB,
from `2025_BIMfundamentals.zip`) is a benchmark row in `scripts/benchmarks.json`
and licensed in `license_ledger.json` as `gni_18`. Scored on the #748 harness:
15/15 rooms, 63/63 windows, 17/17 doors, 0.0% area error, via the in-memory
IFC2X3 upgrade. To stage it:

```bash
curl -L -o /tmp/gni.zip "https://zenodo.org/api/records/19722012/files/2025_BIMfundamentals.zip/content"
unzip -j /tmp/gni.zip "2025_BIMfundamentals/model_18.ifc" -d ~/workspace/datasets/matchline-bench/gni-18/
mv ~/workspace/datasets/matchline-bench/gni-18/model_18.ifc ~/workspace/datasets/matchline-bench/gni-18/arch.ifc
```

### TUM GNI 2026 BIM projects (GNI 2026_BIMprojects)

**Local path:** `~/workspace/datasets/matchline-bench/gni26-<building>/arch.ifc`
(one directory per building, e.g. `gni26-p1-arc/`, `gni26-p8-str/`; data never
committed)

**Environment variable:** None for the paths (the benchmark `--config` carries
them); the raised import limits below are read from the environment.

**What it is:** the 2026 GNI student-project set (the `GNI 2026_BIMprojects`
archive), 16 buildings in mixed IFC2X3 / IFC4, scored on the #748 harness. The
measured scoreboard is [docs/benchmark_gni26.md](docs/benchmark_gni26.md); the
per-building staging it used follows the one-directory-per-building convention
documented under the real-building benchmark section (never a shared
directory, #1259).

**Raised import limits for the large buildings (local runs only):** the
import caps are environment variables with safe defaults
(`ifc/ifc_import.py`, `limits.py`):

| variable | default | used for |
|---|---|---|
| `MATCHLINE_MAX_IFC_SIZE_MB` | 100 (MB) | `600` for `gni26-p1-arc` (178 MB) and `gni26-p2-arc` (101 MB) |
| `MATCHLINE_MAX_IFC_ELEMENTS` | 500000 | `1500000` for `gni26-p4-arc` (1.05 M) and `gni26-p5-arc` (1.49 M), `2200000` for `gni26-p2-arc` (1.84 M), `3200000` for `gni26-p1-arc` (3.06 M) |

The same two variables cover the older benchmark rows: molio needs
`MATCHLINE_MAX_IFC_ELEMENTS=1500000`. CI keeps the defaults; these overrides
are for local runs on machines with the memory for them.

**Two buildings publish no row** in
[docs/benchmark_gni26.md](docs/benchmark_gni26.md), for resource reasons, not
extractor failures: `gni26-p6-arc` (535 MB IFC) exceeds a 3 GiB memory budget
on import (killed at 3.2 GiB RSS), and `gni26-p8-arc` exceeded a ~870 s
scoring budget. The measured scoreboard therefore covers 13 of the 16 staged
buildings.

---

## Real-building benchmark (#748)

**Local path:** `~/workspace/datasets/matchline-bench/<building>/` - one
directory per building (see the staging convention below)

**Environment variable:** None for the paths (passed via `--config`); the
raised import limits for large buildings are read from the environment:
`MATCHLINE_MAX_IFC_SIZE_MB` (default 100 MB) and
`MATCHLINE_MAX_IFC_ELEMENTS` (default 500000)

**What it is:** A scoreboard over 17 licensed real-building models, each
with a ground-truth architectural IFC (IfcSpaces) and, where available, an
HVAC IFC and a drawing-set PDF. `scripts/benchmark.py` runs the IFC path, the
HVAC-terminal check and the cross-frontend comparison per building and writes
`scoreboard.md` plus raw JSON. Data is never committed; a building whose IFC
is not on disk shows as `missing`.

**Candidates (evaluation only unless noted):**

- buildingSMART sample projects, <https://github.com/buildingSMART/Sample-Test-Files>
  (CC BY-NC or project-specific; check each file's license before use)
- BSI ISO 16739 sample IFCs (the Clinic A and B architectural/HVAC files used
  by the Clinic gate, same terms)
- AEC-Bench 15-sheet release, <http://aec-bench.github.io/> (CC BY-NC,
  evaluation only; see the AEC-Bench section above)
- Esplanades architectural IFC, real multi-storey building (Estonian project),
  <https://github.com/buildingsmart-community/Community-Sample-Test-Files> (CC
  BY 4.0, repo-level license; no per-file README). Verified 2026-10-14: 12.1 MB
  IFC2X3, 7 storeys, 285 IfcSpaces, 846 walls, 211 doors, 297 windows. No HVAC
  IFC or drawing set; quantity sets sit on non-space elements.
- Duplex Apartment architectural IFC, same repo (CC BY 4.0). Verified
  2026-10-14: 2.4 MB IFC2X3, 4 storeys, 21 spaces each with area
  BaseQuantities, 57 walls, 14 doors, 24 windows; a separate rooms-and-spaces
  model (37 spaces) and MEP/plumbing/electrical files sit alongside. The MEP
  discipline file (`Duplex_MEP_20110907.ifc`, 17.9 MB, 105 IfcFlowTerminal of
  which 103 are space-bound) is wired as the duplex `hvac_ifc` since
  2026-10-15. The MEP file carries each IfcSpace twice under different
  GlobalIds with identical footprints (named e.g. `A101` and `A101-M`; 42
  spaces, 21 distinct footprints, terminals reference only the plain copies),
  which tied point-in-polygon assignment for every terminal. The HVAC loader
  now collapses identical footprints per storey and remaps ground-truth ids
  onto the kept copy (#1008, 2026-10-15): the scoreboard assigns 73/103, the
  remaining 30 are honestly ambiguous (on boundaries) and go to review. No
  drawing set.

- Molio building IFC, same repo (CC BY 4.0, repo-level license). Verified
  2026-10-15: 73.9 MB IFC2X3, 10 storeys, 117 IfcSpaces each with
  BaseQuantities, 189 walls, 119 windows, 119 doors, 969,835 entities. The
  entity count exceeds the default import cap, so benchmark runs need
  `MATCHLINE_MAX_IFC_ELEMENTS=1500000` (~156 s import, ~1.2 GB peak RSS).
- TUM GNI BIM dataset, <https://zenodo.org/records/19722012> (CC BY 4.0):
  eight real-building IFC4/IFC2X3 models from TUM Georg Nemetschek Institute
  BIM student projects. Seven more (gni-49/50/54/74/115/136/149) joined the
  scoreboard 2026-10-15 alongside gni-18.

  gni-49 window defect (documented 2026-10-15, #1009): the file's 71
  `IfcWindow` elements carry no usable location - every placement resolves to
  (0, 0, 0) - while the 61 wall voids are placed properly and no
  `IfcRelFillsElement` links exist, so there is nothing to adopt geometrically.
  The window rows are defective in the file itself: `OverallWidth` reads
  0.1795 against a name stating 1800/2100 (mm), so even the attributes are
  untrustworthy. Since the #1206 direct enumeration (2026-10-20) the model
  recovers 62 of the 71 window elements from the properly placed wall voids
  (56 via the tier-0 void path, 6 via direct fill) and leaves the remaining 9
  unattached rather than guessing positions; the defective `OverallWidth`
  values keep this building's paired window-area error an open defect
  (930.5%, the scoreboard's worst). Doors are genuinely absent (0 `IfcDoor`).

- TUM GNI BIM Dataset `2026_BIMprojects` (CC BY 4.0 for the IFC models), <https://github.com/ZijianWang-ZW/GNI-BIM-Dataset> (WS 2025/26 *BIM Project* master course): 16 IFC from 9 student teams, 7 with paired architectural + structural models of the same building. Used for the 2026-10-23 GNI 2026 scoreboard pass ([docs/benchmark_gni26.md](docs/benchmark_gni26.md), #748); staged under per-building directories, never committed.
- IFC-Bench projects, <https://huggingface.co/datasets/sylvainHellin/ifc-bench>
  (TUM, Hellin et al. 2026; each project ships its own `license.txt`). Five
  joined `scripts/benchmarks.json` on 2026-10-20; checked by reading each IFC
  with ifcopenshell:
  - `ac20`: KIT AC20 office (KIT IAI), CC BY 4.0. 10.9 MB IFC4, 5 storeys,
    82 IfcSpaces all with quantity sets, 121 walls, 206 windows, 77 doors. No
    wall carries `Pset_WallCommon.IsExternal = True`, so exterior wall-length
    truth is unavailable for it.
  - `digital-hub`: RWTH E3D DigitalHub office (<https://github.com/RWTH-E3D/DigitalHub>),
    MIT. 9.0 MB IFC4 architecture model, 3 storeys, 64 IfcSpaces with
    quantity sets, 178 walls (100 external), 47 windows, 64 doors. The
    ventilation IFC (12.7 MB, 296 terminals) is wired as `hvac_ifc`
    (stage `ventilation.ifc` as `digital-hub/hvac.ifc`).
  - `fzk-house`: KIT FZK-Haus, CC BY 4.0. 2.6 MB IFC4, 2 storeys, 7 IfcSpaces,
    13 walls (none flagged external), 11 windows, 5 doors. Small sanity row.
  - `smiley-west`: KIT Smiley West terraced houses, CC BY 4.0 (a corrected
    version is also on <https://zenodo.org/records/10680698>, CC BY 4.0).
    6.1 MB IFC4, 5 storeys, 140 IfcSpaces, 281 walls (152 external), 80
    windows, 170 doors.
  - `wbdg-office`: WBDG Common BIM Files two-storey office, architectural IFC.
    4.1 MB IFC2X3, 3 storeys, 99 IfcSpaces, 487 walls (18 external), 69
    windows, 102 doors. **License confirmed (2026-10-20):** the model is the
    2011-09-14 "Office" release of the bSa Common BIM Files, developed by
    ERDC-CERL (US Army Corps of Engineers, project chair Bill East) and first
    published by the buildingSMART alliance at NIBS. IFC-Bench's `license.txt`
    credited the Medical-Dental files by mistake and listed CC BY 4.0; neither
    is correct, and IFC-Bench corrected the file on 2026-10-20 (commit
    `0e22f2e`). The original project page carried a reuse clause: anyone may
    copy, distribute, display, use and make derivative works, with proper
    academic citation of the source whenever the models are presented, and no
    cost or use restriction in any for-profit or not-for-profit setting. The
    clause is preserved in the [bSa Common BIM Files Wayback snapshot of 10
    March 2013](https://web.archive.org/web/20130310082112/http://www.buildingsmartalliance.org/index.php/projects/commonbimfiles).
    NIBS was asked directly in parallel on 2026-10-20; if NIBS answers
    differently, its answer governs and this row is revisited. Publishing
    numbers derived from the model is allowed under the clause with the source
    cited: cite ERDC-CERL / buildingSMART alliance Common BIM Files, Office
    (2011), via IFC-Bench `projects/wbdg_office`.

  Not adopted from IFC-Bench: `sixty5` (architecture IFC is 342.7 MB, over the
  100 MB import cap), `west_riverside_hospital` (CC BY 3.0, 80.9 MB IFC4
  architecture model; not yet inspected, candidate for a later row), the four
  GPL-3 BIMserver models (`4351`, `hitos`, `ettenheim_gis`,
  `samuel_macalister_sample_house`), `schependomlaan` (already rejected: no
  IfcSpaces), and the `fantasy_*` TUM student models (MIT; overlap the GNI
  rows). Staging, for each adopted project (`<p>` is the IFC-Bench project
  name, `<b>` the benchmark row name):

  ```bash
  mkdir -p ~/workspace/datasets/matchline-bench/<b>
  curl -L -o ~/workspace/datasets/matchline-bench/<b>/arch.ifc \
    "https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/main/projects/<p>/arc.ifc"
  # digital-hub only:
  curl -L -o ~/workspace/datasets/matchline-bench/digital-hub/hvac.ifc \
    "https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/main/projects/digital_hub/ventilation.ifc"
  ```

**Rejected candidates (2026-10-15 licensed-data search):** Schependomlaan
as-planned IFCs (same community repo, CC BY 4.0) carry no IfcSpaces (no
ground truth); the Krijnen & Hesselink balcony models
(<https://zenodo.org/records/5047709>, CC BY 4.0) are walls-and-slabs only,
no IfcSpaces; RestageIFC Aachen (<https://zenodo.org/records/21470658>,
CC BY 4.0) is 189.8 MB, over the 100 MB import cap, and an assembly/schedule
model. NIST NZERTF drawings are public domain but residential and
drawings-only (no IFC ground truth for this harness).

**IFC2X3 datasets (#929):** Esplanades and Duplex import via the in-memory
IFC2X3 -> IFC4 schema upgrade (`model.source_schema` says "IFC2X3"; the
scoreboard's schema column shows it). On the scoreboard, windows and doors
truth counts come from the raw IFC entity table (`by_type`), not the imported
model, so a model count below truth means openings were folded during
wall/host processing. Esplanades' import is dominated by the upgrade (~450 s
for 189k entities).

**Setup:**

```bash
mkdir -p ~/workspace/datasets/matchline-bench/
# one directory per building, containing:
#   arch.ifc        ground-truth architectural IFC with IfcSpaces
#   hvac.ifc        optional HVAC IFC (terminals)
#   drawings.pdf    optional drawing set for the cross-frontend check
# Esplanades and the Duplex Apartment come from the buildingSMART community
# sample repo (git LFS): clone it with git lfs installed and copy the .ifc
# files, or fetch the blobs through the LFS batch API.
```

**Staging convention: one directory per building (#1259).** Every benchmark
building is staged in its **own** directory, never several buildings in one
shared directory:

```
<root>/<building>/arch.ifc     the building's ground-truth IFC
<root>/<building>/hvac.ifc     optional HVAC IFC
<root>/<building>/drawings.pdf optional drawing set
<root>/<building>/truth.json   frozen truth, written by scripts/bench_truth.py
```

This is not cosmetic. The frozen truth file has one fixed name (`truth.json`)
and is written **next to the IFC** (`scripts/bench_truth.py:truth_path_for`),
pinned to that IFC's sha256. Two buildings staged in one shared directory
therefore write the same file: the second building's `truth.json` replaces the
first's, and the harness reads whichever landed last - a truth pin that
crosses between buildings, scored against the wrong model. (The pin still
catches the swap as a sha256 mismatch, so the collision surfaces as a
`TruthMismatch` error row rather than a silently wrong score, but the run is
lost either way.)

The harness now enforces the convention instead of trusting it (#1259):
`scripts/benchmark.py` detects buildings that share a truth directory and
reports each of them as an `error` row naming the shared directory, and
`scripts/bench_truth.py` refuses to write truth for such a config (exit code
2) rather than overwriting one building's truth with another's. New dataset
additions must follow the per-building layout; the 2026-10-23 GNI 2026 staging
already did.

**Config format** (passed to `--config`; relative paths resolve against the
config file):

```json
{
  "buildings": [
    {
      "name": "example-clinic",
      "source_url": "https://...",
      "license": "CC BY-NC 4.0 (evaluation only)",
      "arch_ifc": "~/workspace/datasets/matchline-bench/example-clinic/arch.ifc",
      "hvac_ifc": "~/workspace/datasets/matchline-bench/example-clinic/hvac.ifc",
      "drawing_set": null
    }
  ]
}
```

Run: `python scripts/benchmark.py --config scripts/benchmarks.json --out scoreboard.md --json scoreboard.json [--openstudio]`. The checked-in config lists seventeen buildings (bsi-clinic, esplanades, duplex, molio, gni-18/49/50/54/74/115/136/149, and from IFC-Bench ac20, digital-hub, fzk-house, smiley-west, wbdg-office); rows report `missing` until each IFC is placed under `~/workspace/datasets/matchline-bench/<building>/` (one directory per building - the staging convention below), and molio additionally needs `MATCHLINE_MAX_IFC_ELEMENTS=1500000`. Large buildings may also need `MATCHLINE_MAX_IFC_SIZE_MB` raised; both variables are read from the environment, and the defaults (100 MB / 500000 entities) are unchanged in CI.

**Per-building staging (required).** Every benchmark building gets its own
directory under the bench root (`matchline-bench/<building>/…`) holding its
IFC and its `truth.json` together. Truth is pinned to the IFC's sha256, so two
buildings sharing one directory can serve the wrong truth file (a collision
reads whatever landed last); never stage two buildings side-by-side in one
flat directory. The GNI 2026 staging follows this convention.

**Large buildings (raised limits, local runs only).** CI defaults stay
untouched. Large GNI 2026 buildings need the import caps raised in local runs:
`MATCHLINE_MAX_IFC_SIZE_MB=600` (the limit reads file size as MiB: gni26-p1-arc is 178 MiB, gni26-p2-arc 101 MiB)
and `MATCHLINE_MAX_IFC_ELEMENTS=1500000` (gni26-p4-arc has 1.05M elements,
gni26-p5-arc 1.49M), `2200000` for gni26-p2-arc (1.84M) and `3200000` for
gni26-p1-arc (3.06M). Two buildings publish no rows in the 2026-10-23
scoreboard as environment caps, not extractor failures: `gni26-p6-arc` (535 MB)
exceeds a 3 GiB memory budget on import, and `gni26-p8-arc` did not finish
scoring inside a ~870 s budget across three attempts.

**2026-10-15 scoreboard (no `--openstudio`):** all twelve buildings import and
score; rooms, area, window and door counts match ground truth everywhere
except gni-49 windows (truth 71 / model 0, #1009). Two defects found by the
first run are fixed: gni-74's gbXML export no longer divides by zero on
sub-0.1 m walls (#1011), and the duplex MEP terminals score 73/103 after the
loader collapses the file's duplicated spaces (#1008).

**Schedule-truth benchmark (#1240).** The clinic's COBie workbook
(`clinic-design.xlsx`, the project's real schedule deliverable, sha256
`ceef8f581a5244fe4de66ab9f1c89e5706f6c57868eeccd4f942f92e429fe5f1`) joins
the bench as `bsi-clinic`'s `schedule_workbook`; truth is frozen next to it
by `scripts/schedule_truth.py` (openpyxl only, never imports the extractor;
schema `matchline.schedule_truth/1`; workbook sha256 pinned, and a drifted
truth file refuses the run until re-frozen). Run:
`python scripts/schedule_bench.py --config scripts/benchmarks.json
[--building bsi-clinic] [--export-validity]`; the measured run is recorded
in [docs/1.0_claims.md](docs/1.0_claims.md#mechanical-schedule-extraction-1240).

**Independent truth (#1165):** the 2026-10-15 area errors (0.0% everywhere)
compared `import_ifc` with itself, and wall length error was hard-coded to
0.0. The harness now scores against `truth.json`, read straight from each IFC
with ifcopenshell by `scripts/bench_truth.py` (room `NetFloorArea`, or body
geometry when the file has no quantities; exterior wall length from
`Pset_WallCommon.IsExternal` walls; window area from the window's tessellated body (two largest extents), falling back to stated `OverallWidth`/`OverallHeight` when the body is not tessellable, since gni-49's stated attributes are corrupt #1220; raw door counts) and pinned to the
IFC's sha256. Freeze it once per building with
`python scripts/bench_truth.py --config scripts/benchmarks.json` (the
benchmark also writes it on first run); a changed IFC under an old
`truth.json` is an `error` row until it is rebuilt with `--force`. A building
whose IFC flags no wall as external (gni-18) leaves the wall length column
blank rather than 0. LPD median error is still a self-consistency check.

**2026-10-20 scoreboard (independent truth, no `--openstudio`):** all seventeen
buildings import and score (molio with `MATCHLINE_MAX_IFC_ELEMENTS=1500000`).
Each building's run artifacts now include `<building>-convention.json` (volume
bias, non-room area, area budget, area definitions from
`review/convention_report.py`), so the convention report is attached to every
benchmark run (#1165). Measured per-slice accuracy against frozen IFC truth:

- Rooms: 1,323 truth spaces vs 1,304 model spaces (98.6% recovered). Paired
  room area error: median 0.0%, range 0.0%-109.7%. The duplex outlier (21/21
  rooms matched, areas ~110% high) is a real finding, not a scoring bug.
- Exterior wall length: median error 26.9%, range 3.5%-797.8% over the 14
  buildings whose IFC flags walls as external (ac20, fzk-house and gni-18 flag
  none, so their column is blank). gni-49's ~800% and esplanades'/molio's
  ~100% are open defects to file, not hidden by the median.
- Windows: 1,423 truth vs 1,343 model (94.4%). Window-area error: median
  0.19%, range 0.0%-100% (gni-49 recovers 0 windows, #1009; esplanades and
  molio area off by 100%; digital-hub 36.2%).
- Doors: 1,155 truth vs 1,143 model (99.0%).
- LPD: still no independent truth; the column is a self-consistency check and
  stayed blank this run. OpenStudio and cross-frontend columns were not
  exercised this run.

Error bounds are the min/median/max spread across buildings, as published;
[`docs/1.0_claims.md`](docs/1.0_claims.md) cites only these measured numbers.
