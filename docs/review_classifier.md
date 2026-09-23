# Review-Queue Decision Classifier — `review_classifier/`

Local ML triage layer that routes extraction items to human review or auto-resolves them. Scikit-learn TF-IDF + logistic regression with sigmoid calibration; fully offline, no data egress.

## Pipeline position

```
run_pipeline  →  flag_for_review  →  ReviewTriage  →  review_queue (human) / auto_resolved
```

`ReviewTriage` is invoked from `building_model.py::flag_for_review()` after extraction completes. It inspects each flagged item and returns a `TriageDecision` indicating whether human review is needed.

## Package structure

| File | Role |
|---|---|
| `model.py` | `TypedDecider` — TF-IDF + calibrated logistic regression, Jev-style API |
| `triage.py` | `ReviewTriage` — loads one `TypedDecider` per task, exposes `triage()` |
| `data.py` | Synthetic labeled dataset generator (prototype only) |
| `features.py` | TF-IDF vectorizer (1–2 grams) + numeric feature standardizer |
| `train.py` | Trains and serializes `TypedDecider` instances to `.pkl` via `joblib` |
| `evaluate.py` | Accuracy, F1, ECE, reliability diagrams |
| `run_demo.py` | CLI demo: train all tasks, print metrics, write `reliability.png` |

## Core API

### `TypedDecider` (`model.py`)

Logistic regression wrapped in `CalibratedClassifierCV` (Platt/sigmoid scaling).

```python
class TypedDecider:
    def noul(self, example: Example) -> float: ...
    # Returns P(yes) for yes/no decisions.

    def choice(self, example: Example, options: list[str]) -> dict[str, float]:
    # Returns {option: probability} for multi-class decisions.

    def decide(self, example: Example) -> tuple[str, float]:
    # Returns (label, confidence). confidence ∈ [0, 1].
```

### `ReviewTriage` (`triage.py`)

```python
class ReviewTriage:
    def triage(self, example: Example) -> TriageDecision: ...

class TriageDecision(NamedTuple):
    needs_human: bool
    urgency: int  # 0=low, 1=medium, 2=high
    auto_resolved: bool
    reason: str

def get_triage() -> ReviewTriage: ...
def reset_triage() -> None: ...
```

## Decision tasks

Three prototype tasks trained on synthetic data:

| Task | Type | Description |
|---|---|---|
| `route_to_review` | binary | Should this extraction item go to human review? |
| `schedule_match` | binary | Does this schedule row match this door/window tag? |
| `extraction_type` | 4-way | door / window / room_label / fixture |

Models are loaded lazily from `review_classifier/trained_model_<task>.pkl` on first call to `get_triage()`.

## Features

- **TF-IDF** (1–2 character n-grams) over the item's text rendering
- **Numeric features**: detector confidence, OCR edit distance, schedule-match score, candidate count, area delta
- All features standardized before stacking into the sparse input matrix

## Prototype results (synthetic data)

| Task | Accuracy | F1 | ECE (calibrated) |
|---|---|---|---|
| `route_to_review` | 0.9933 | 0.9923 | 0.0201 |
| `schedule_match` | 0.9467 | 0.9635 | 0.0291 |
| `extraction_type` | 1.0000 | 1.0000 | 0.0438 |

Results on synthetic data only. Real calibration **must be re-measured on actual drawings** before thresholds are trusted.

## Limitations

1. **Synthetic labels only.** The training labels are generated from invented rules; real OCR errors, detector failures, and schedule noise will differ. Re-calibrate on real data before production use.
2. **No pipeline integration yet.** The typed API is shaped for `flag_for_review()` wiring but that integration is still outstanding.
3. **TF-IDF text features.** Character n-grams are robust to OCR noise but miss semantic context; richer embeddings could improve `schedule_match`.
4. **Distribution shift unstudied.** New drawing styles or symbol sets will degrade accuracy and calibration.
5. **Pickle serialization.** Model files (`.pkl`) carry execution risk — see issue #72.
