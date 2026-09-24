---
phase: "04-review"
plan: "01"
type: "execute"
wave: 1
depends_on: []
files_modified:
  - "run_review.py"
  - "cli.py"
autonomous: true
requirements:
  - "RVIEW-01"
  - "RVIEW-03"
must_haves:
  truths:
    - "`matchline review --model model.json` prints each open ReviewItem with kind, description, confidence"
    - "Classifier suggestion shown inline for each review item (TypedDecider.decide)"
    - "Review items formatted as a numbered list with clear status indicators"
  artifacts:
    - path: "run_review.py"
      provides: "Review queue CLI with list/confirm/reject subcommands"
      min_lines: 80
    - path: "cli.py"
      provides: "matchline review CLI subcommand"
      exports:
        - "cmd_review"
  key_links:
    - from: "cli.py"
      to: "run_review.py"
      via: "cmd_review() calls run_review.main()"
      pattern: "run_review.main"
    - from: "run_review.py"
      to: "building_model"
      via: "BuildingModel.from_json(), ReviewItem"
      pattern: "BuildingModel.from_json"
    - from: "run_review.py"
      to: "review_classifier.model"
      via: "TypedDecider.decide() for suggestion"
      pattern: "TypedDecider"
    - from: "run_review.py"
      to: "validate"
      via: "run_checks() for re-validation"
      pattern: "run_checks"
---

<objective>
Create `run_review.py` and wire `matchline review` into `cli.py` so operators can list open review items with classifier suggestions inline. The list command shows every open `ReviewItem` with its kind, description, provenance, confidence, and a classifier prediction.

Purpose: Makes the review queue actionable and transparent — operators see not just what needs review, but what the classifier thinks should happen.
Output: `run_review.py` + `cli.py` updated
</objective>

<context>
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/REQUIREMENTS.md

From building_model.py (lines 324-330):
```python
@dataclass
class ReviewItem:
    id: str
    kind: str  # e.g. "window_room_link", "fixture_assignment"
    description: str
    confidence: float
    provenance: Provenance = None
    status: str = "open"  # "open" | "confirmed" | "rejected"
```

From building_model.py (lines 391-399):
```python
def flag_for_review(self, kind: str, description: str, confidence: float, provenance: Provenance) -> ReviewItem:
    rid = f"RVW-{len(self.review_queue) + 1:03d}"
    item = ReviewItem(id=rid, kind=kind, description=description, confidence=confidence, provenance=provenance)
    self.review_queue.append(item)
    return item
```

From review_classifier/model.py (lines 66-73):
```python
@dataclass
class Decision:
    label: object
    confidence: float
    probabilities: dict

class TypedDecider:
    def decide(self, example: Example) -> Decision:
        # Returns argmax label + confidence
```

From review_classifier/data.py (lines 24-29):
```python
@dataclass
class Example:
    task: str  # "route_to_review" | "schedule_match" | "extraction_type"
    text: str  # free-text rendering of the observation
    numeric: dict = field(default_factory=dict)
    label: object = None
```
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create run_review.py with list command</name>
  <files>run_review.py</files>
  <action>
Create `run_review.py` with a `main()` function and CLI argument parsing using `argparse`:

1. **Imports**: `BuildingModel` from `building_model`, `TypedDecider` from `review_classifier.model`, `Example` from `review_classifier.data`, `run_checks` from `validate`, `json`, `pathlib.Path`, `argparse`, `dataclasses.asdict`.

2. **Kind-to-task mapping**: Map `ReviewItem.kind` to the classifier task string:
   - `"window_room_link"` → `"route_to_review"`
   - `"fixture_assignment"` → `"route_to_review"`
   - `"schedule_mismatch"` → `"schedule_match"`
   - Any other kind → default to `"route_to_review"`

3. **Example construction**: For each open `ReviewItem`, construct a `review_classifier.data.Example`:
   - `task`: from kind-to-task mapping above
   - `text`: formatted string like `f"[{item.kind}] {item.description} (conf={item.confidence:.2f})"`
   - `numeric`: `{"det_conf": item.confidence}` as a simple proxy

4. **Classifier invocation**:
   - Load the pre-trained classifier from `review_classifier/model.py` using a DEFAULT MODEL path `review_classifier/trained_model.pkl` if it exists; if not, skip classification and print "(no model — train with review_classifier/train.py)".
   - Call `decider.decide(example)` for each item.
   - For `route_to_review`: label=True means "route to human review" (always True here, so label is always the class to confirm/reject).
   - Display the prediction label and confidence alongside the item.

5. **List output format** (one line per item):
   ```
   [{id}] {kind}  conf={confidence:.2f}  → {classifier_label} ({classifier_conf:.2f})
     {description}
     provenance: {prov.sheet_id} r{prov.revision} via {prov.method}
   ```

6. **CLI arguments**:
   - `model` (positional): path to `BuildingModel.json`
   - `--show-all` (flag): also show confirmed/rejected items (default: open only)
</action>
  <verify>
    <automated>python -c "
import json, pathlib, tempfile
from building_model import BuildingModel, Provenance, Space, Level
from run_review import format_review_list

# Build a model with 3 review items
m = BuildingModel(name='test')
m.levels.append(Level(id='L1', name='Level 1'))
m.flag_for_review('window_room_link', 'Window W1 may belong to Room 101 or 102', 0.62, Provenance(sheet_id='elev_A201', revision=1, method='geometric_fallback', confidence=0.62))
m.flag_for_review('fixture_assignment', 'Fixture F1 assigned to Space L1-101 with low confidence', 0.55, Provenance(sheet_id='arch_A101', revision=2, method='point_in_polygon', confidence=0.55))
m.flag_for_review('schedule_mismatch', 'Tag A appears in schedule but not on sheet', 0.71, Provenance(sheet_id='arch_A101', revision=2, method='schedule_join', confidence=0.71))

with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
    f.write(m.to_json())
    path = f.name

items, classifier_available = format_review_list(path)
print(f'Listed {len(items)} items, classifier_available={classifier_available}')
assert len(items) == 3, f'Expected 3, got {len(items)}'
pathlib.Path(path).unlink()
print('PASS')
"</automated>
  </verify>
  <done>Running `matchline review --model model.json` on a model with review items produces a numbered list of items with kind, description, confidence, and (if model available) classifier suggestion</done>
</task>

<task type="auto">
  <name>Task 2: Wire matchline review into cli.py</name>
  <files>cli.py</files>
  <action>
Add `cmd_review()` and the `review` subcommand to `cli.py`:

1. **Add `cmd_review` function**:
   ```python
   def cmd_review(args: argparse.Namespace) -> None:
       import run_review
       run_review.main(args)
   ```

2. **Add `review` subparser** to `build_parser()`:
   ```python
   p = sub.add_parser("review", help="Review queue: list, confirm, or reject low-confidence items")
   p.add_argument("model", help="BuildingModel JSON file path")
   p.add_argument("--confirm", metavar="ID", help="Confirm a review item (marks confirmed, re-runs validation)")
   p.add_argument("--reject", metavar="ID", help="Reject a review item (marks rejected, re-runs validation)")
   p.add_argument("--show-all", action="store_true", help="Also show confirmed/rejected items")
   p.set_defaults(func=cmd_review)
   ```
   Note: `--confirm` and `--reject` are processed in `run_review.main()`.
</action>
  <verify>
    <automated>python -c "
import subprocess
result = subprocess.run(['python', '-m', 'matchline', 'review', '--help'], capture_output=True, text=True)
assert result.returncode == 0
assert 'Review queue' in result.stdout or 'review' in result.stdout.lower()
print('PASS')
"</automated>
  </verify>
  <done>`matchline review --help` works and shows the review subcommand with model, confirm, reject, show-all options</done>
</task>

</tasks>

<verification>
- `python -m matchline review --help` shows the review subcommand
- `python -m matchline review tests/fixtures/bldg_3room_model.json` (or any model with review items) lists them with kind, description, confidence
- If `trained_model.pkl` exists, classifier suggestions appear inline
</verification>

<success_criteria>
`matchline review --model model.json` prints a numbered list of open review items showing: id, kind, description, confidence, provenance, and (if classifier model available) classifier suggestion with confidence.
</success_criteria>

<output>
After completion, create `.planning/phases/04-review/04-review-01-SUMMARY.md`
</output>
