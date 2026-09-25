# External Datasets

Real-drawing work uses public datasets that live **outside the repo** under `~/workspace/datasets/`.

## Never commit datasets or credentials

Datasets, credentials, and machine paths **must never** be committed to the repo. See `CONTRIBUTING.md` → Never List.

## Dataset Summary

| Dataset | License | Location |
|---------|---------|----------|
| AEC Geometric Bench | CC BY-SA | `~/workspace/datasets/aec_geometric_bench/` |
| CMP Facade | CC BY-SA | `~/workspace/datasets/cmp_facade/` |
| CubiCasa5K | CC BY-NC-SA 4.0 | `~/workspace/datasets/cubicasa5k/` |
| FloorPlanCAD | Per dataset terms | `~/workspace/datasets/floorplancad/` |

## Setup

```bash
mkdir -p ~/workspace/datasets
# Download each dataset per its license terms
# Scripts that need them will fail clearly with a helpful message if absent
```

## Use in Code

Datasets are accessed via `cli.py --data-root` flags or environment variables. All dataset access is behind lazy imports — code that doesn't use a dataset does not pay import costs.

Demos that need datasets fail with clear messages if the data is absent.

## Disallowed Paths

The following paths are **never** used in code or committed to the repo:

- `~/workspace/.venv-det` — YOLO detector venv
- Absolute paths beyond `~/workspace/datasets/`
