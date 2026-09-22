# CLI reference: `matchline`

Entry point: `cli.py:main`. Unified command-line interface for all
demo/evaluation scripts in this repo.

```bash
matchline <command> [options]
```

Run `matchline --help` for the full list of commands.

---

## Global flags

`--help, -h`
: Show help for the selected command.

---

## `matchline validate`

Validation battery demo using two synthetic buildings (`bldg_3room`, `bldg_open_office`).

**Demo only.** Produces a validation report to stdout.

---

## `matchline run`

Unified pipeline: generate + link + validate + BEM export.

```bash
matchline run --seed 42
matchline run --aec-bench /path/to/aec-bench
matchline run --seed 42 --config pipeline.yaml
```

`--seed N`
: Integer seed for the synthetic building generator. Mutually exclusive with `--aec-bench`.

`--aec-bench PATH`
: Path to AEC-Bench dataset root (contains `annotations_15.xml`). Mutually exclusive with `--seed`.

`--out-dir DIR` (default: `bem_out`)
: Output directory for all intermediate JSON and BEM files.

`--open-office-span`
: Use the open-office span instead of the default `bldg_3room` synthetic building.

`--elevation-key KEY` (default: `elev_grid`)
: Which elevation key to use for the elevation-linking step.

`--simplify-tol FLOAT` (default: `0.02`)
: Simplification tolerance as a fraction (0.02 = 2%).

`--config FILE`
: YAML config file. Keys:

  - `simplifyTolerance` (float): overrides `--simplify-tol`
  - `wallHeight` (float, metres): overrides level default
  - `reviewConfidence` (float): minimum confidence for review queue items

  Example:

  ```yaml
  reviewConfidence: 0.85
  simplifyTolerance: 0.01
  wallHeight: 3.5
  ```

**Stages written to `--out-dir`:**

| Stage | File | Description |
|---|---|---|
| 1 | `stage_01_building.json` | Generated building / AEC-Bench source |
| 2 | `stage_02_model.json` | Linked BuildingModel |
| 3 | `stage_03_simplified.json` | Simplified geometry ring |
| 4 | `stage_04_validation.json` | Validation report |
| 4b | `stage_04b_auto_triage.json` | Auto-triage results *(opt-in)* |
| 6 | `stage_06_bem/` | gbXML + IFC4 export |

Stage 4b is written only when `ENABLE_AUTO_TRIAGE=1` is set in the environment.
In that case, every `ReviewItem` in the model's review queue is triaged
in-place using the `review_classifier` models before the BEM export stage.

**Exit codes:**

- `0` — pipeline completed, export gate passed
- `1` — validation errors blocked export

---

## `matchline bem-export`

gbXML + IFC4 export demo using `sheet_007` (synthetic).

```bash
matchline bem-export --sheet-id sheet_007
```

`--sheet-id ID` (default: `sheet_007`)
: Sheet identifier used in the synthetic model.

---

## `matchline elevation-windows`

Exact window placement and daylight analysis demo using synthetic buildings.

```bash
matchline elevation-windows
```

See [`elevation_windows.md`](elevation_windows.md) for details on the method.

---

## `matchline facade-takeoff`

Facade area takeoffs on the CMP Facade dataset.

```bash
matchline facade-takeoff --n 5
matchline facade-takeoff --full --data-root ~/workspace/datasets/cmp-facade
```

`--n N` (default: `5`)
: Number of facades for the detailed demo.

`--data-root PATH`
: Override the default CMP Facade dataset root (`~/workspace/datasets/cmp-facade`).

`--full`
: Run the full 606-facade sweep and write results to `facade_priors.json`.

`--out-json PATH`
: Override the output path for the full sweep results.

**Requires:** CMP Facade dataset at `~/workspace/datasets/cmp-facade` (or `--data-root`).

---

## `matchline room-labels`

OCR room labeling demo using synthetic buildings.

```bash
matchline room-labels
```

---

## `matchline multidiscipline`

Cross-discipline linking demo using three synthetic buildings (`bldg_3room`, `bldg_open_office`, `bldg_8room`).

```bash
matchline multidiscipline
```

Demonstrates multi-building model linking and cross-building consistency checks.

---

## `matchline mnist`

WiSARD MNIST evaluation (research, not production symbol spotting).

```bash
matchline mnist --data-dir data --out-path mnist_results.json
```

`--data-dir PATH` (default: `data`)
: Directory containing `mnist_X.npy` and `mnist_y.npy`.

`--out-path PATH` (default: `mnist_results.json`)
: Output JSON path.

**Requires:** `data/mnist_X.npy` and `data/mnist_y.npy`.

---

## `matchline symbols`

Symbol evaluation and GD&T invariant check.

```bash
matchline symbols --out-path symbols_results.json
```

`--out-path PATH` (default: `symbols_results.json`)
: Output JSON path.

---

## `matchline ifc-import`

Import an IFC file into the canonical `BuildingModel`.

```bash
matchline ifc-import building.ifc --out model.json
```

`path`
: Input `.ifc` file.

`--out PATH`
: Write canonical model JSON here. Defaults to `<stem>_model.json`.

**Tier 0 import.** Only geometry and basic entity mapping; no space boundaries yet.
See [`ifc_import.md`](ifc_import.md) for details on the import method and limitations.

---

## `matchline ifc-export`

Export a `BuildingModel` JSON file to IFC4.

```bash
matchline ifc-export model.json out.ifc
```

`model`
: Path to a `BuildingModel` JSON file.

`out`
: Output IFC4 file path.

---

## `matchline review`

Review queue interactive tool: list open `ReviewItem`s, confirm or reject triage decisions.

```bash
matchline review model.json
matchline review model.json --show-all
matchline review model.json --confirm RVW-001
matchline review model.json --reject RVW-002 --enable-auto-triage
```

`model`
: Path to a `BuildingModel` JSON file.

`--show-all`
: Also show confirmed and rejected items (default: open items only).

`--confirm ID`
: Confirm a review item. Marks it confirmed, then re-runs validation.

`--reject ID`
: Reject a review item. Marks it rejected, then re-runs validation.

`--enable-auto-triage`
: Enable the auto-triage classifier when loading the model.
  The `ENABLE_AUTO_TRIAGE=1` environment variable is also respected.

See [`building_model.md`](building_model.md) for the `ReviewItem` data model.

---

## Environment variables

`ENABLE_AUTO_TRIAGE=1`
: Enables the auto-triage classifier globally (in `matchline review` and in
  `matchline run` Stage 4b). When set, every `ReviewItem` added to the
  review queue is scored by the `review_classifier` triage models, populating
  `needs_human` (P-needs-human, 0–1) and `urgency` (0–3) fields.
