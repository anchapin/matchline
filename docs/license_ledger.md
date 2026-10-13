# License ledger (#751)

`license_ledger.json` ties every dataset matchline touches to its license, and
every trained or derived artifact to the datasets that fed it.
`scripts/check_release_licenses.py` enforces it in CI and before a release.

## Classes

| Class | Meaning | Datasets |
|---|---|---|
| permissive | may ship | matchline synthetic data, review corpus, BSI Clinic (CC BY 4.0, held out anyway) |
| share_alike | may ship only with its notice, under the source's license | CMP Facade (CC BY-SA) |
| noncommercial | evaluation only, never ships | CubiCasa5K, AEC Geometric Bench, ArchCAD, FloorPlanCAD (all CC BY-NC 4.0) |
| copyleft | evaluation only in a BSD-3-Clause release | Ultralytics YOLO11n pretrained weights (AGPL-3.0) |

An artifact takes the most restrictive class among its datasets.

## Artifacts

| Artifact | Trained on | Class | Committed |
|---|---|---|---|
| `facade_priors.json` | CMP Facade | share_alike | yes, with `NOTICE.md` |
| `review_classifier/trained_model_*.npz` (3) | synthetic + review corpus | permissive | yes |
| YOLO11n door/window weights (`~/workspace/datasets/detector_runs/yolo11n_{baseline,eca}_s*/`) | CubiCasa5K + YOLO11n COCO weights | copyleft (and noncommercial) | no |
| from-scratch detector checkpoints (`~/workspace/datasets/detector_runs/scratch-synth-*/ckpt.pt`) | matchline synthetic generators | permissive | no (produced by the `detector-train.yml` workflow) |

The YOLO11n detector weights are evaluation-only twice over: CubiCasa5K is
non-commercial, and the YOLO11n starting weights are AGPL-3.0. The from-scratch
synthetic checkpoints are the first detector weights that may ship: permissive
training data, no pretrained starting weights (they train from random
initialization on synthetic sheets only). `detection_provider.py` reads this ledger to mark a
detector's output evaluation only (see `docs/detection_provider.md`).

## The quarantine rule

Artifacts whose class is `noncommercial` or `copyleft` are for evaluation only.
They never go in a wheel, sdist or release asset. A share-alike artifact may ship
only when the build carries `NOTICE.md`.

## The check

```
python scripts/check_release_licenses.py                 # ledger + committed files
pip wheel --no-deps -w dist .
python scripts/check_release_licenses.py dist/*.whl      # ... and the build
```

It fails when:

- the ledger is malformed (unknown class, an artifact naming an unknown dataset,
  a share-alike artifact without a notice)
- a committed file that looks like a model or derived data (`*.npz`, `*.pt`,
  `*.pth`, `*.onnx`, `*.pkl`, `*.joblib`, `*.safetensors`, `*.h5`, `*.ckpt`,
  `*.tflite`, `*priors*.json`) is not in the ledger
- a build contains an evaluation-only artifact, a share-alike artifact without
  `NOTICE.md`, or a model-like file the ledger doesn't list

CI builds the wheel on every pull request and runs the check on it.

## Adding a dataset or an artifact

Add the dataset (id, name, license, class, source) before training on it, and
the artifact (path, kind, datasets, committed, produced_by) in the same commit
that adds or regenerates it. Paths outside the repo may use `*` wildcards.
