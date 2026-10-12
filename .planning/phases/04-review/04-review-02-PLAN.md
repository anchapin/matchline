---
phase: "04-review"
plan: "02"
type: "execute"
wave: 2
depends_on:
  - "04-review-01"
files_modified:
  - "run_review.py"
autonomous: true
requirements:
  - "RVIEW-02"
must_haves:
  truths:
    - "`matchline review --model model.json --confirm <id>` updates item status to 'confirmed' and re-runs validation"
    - "`matchline review --model model.json --reject <id>` updates item status to 'rejected' and re-runs validation"
    - "Confirmed/rejected item no longer appears in open list"
    - "Model JSON is saved back to disk after mutation"
  artifacts:
    - path: "run_review.py"
      provides: "Confirm/reject mutations with model save and revalidation"
      min_lines: 50
  key_links:
    - from: "run_review.py"
      to: "building_model"
      via: "ReviewItem.status = 'confirmed'/'rejected'"
      pattern: "item.status"
    - from: "run_review.py"
      to: "validate"
      via: "run_checks(model) after mutation"
      pattern: "run_checks"
---

<objective>
Add `--confirm <id>` and `--reject <id>` mutations to `run_review.py` that update the `ReviewItem.status`, save the modified model back to disk, and re-run validation to confirm the model is still valid after the mutation.

Purpose: Closes the feedback loop — human decisions update the model and immediately re-validate that the model still passes all checks.
Output: `run_review.py` updated with confirm/reject + save + revalidation
</objective>

<context>
@.planning/phases/04-review/04-review-01-PLAN.md

From building_model.py (line 330):
```python
status: str = "open"  # "open" | "confirmed" | "rejected"
```

From building_model.py (lines 402-421):
```python
def to_json(self) -> str:
    return json.dumps(self.to_dict(), indent=1)

@classmethod
def from_json(cls, s: str) -> "BuildingModel":
    return cls.from_dict(json.loads(s))
```

From validate.py (line ~_check_review_queue_sound):
```python
def _check_review_queue_sound(ctx) -> CheckResult:
    # validates review queue items are well-formed
```

Key insight: `validate.run_checks(model)` takes a `BuildingModel` and returns a `CheckContext`/`CheckResult`. It does NOT write to disk — that's the caller's responsibility.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add confirm/reject mutations to run_review.py</name>
  <files>run_review.py</files>
  <action>
Extend `run_review.py` with confirm/reject functionality in `main()`:

1. **Handle `--confirm <id>`**:
   - Load model from JSON (`BuildingModel.from_json(pathlib.Path(args.model).read_text())`)
   - Find the `ReviewItem` with `item.id == args.confirm`
   - If not found: print `f"Error: item {args.confirm} not found"` and `sys.exit(1)`
   - If already confirmed/rejected: print `f"Item {args.confirm} already {item.status}"` and `sys.exit(1)`
   - Set `item.status = "confirmed"`
   - Save model back: `pathlib.Path(args.model).write_text(model.to_json())`
   - Re-run validation: `ctx = run_checks(model)`, print validation summary
   - Print `f"Confirmed {args.confirm}. Validation: {ctx.n_errors} errors, {ctx.n_warnings} warnings"`

2. **Handle `--reject <id>`** (same pattern):
   - Set `item.status = "rejected"`
   - Save + re-run validation
   - Print `f"Rejected {args.confirm}. Validation: {ctx.n_errors} errors, {ctx.n_warnings} warnings"`

3. **Both mutations** must:
   - Update the model JSON in-place (overwrite the input file)
   - Re-run `run_checks(model)` and print the summary
   - If validation fails (non-zero errors), still save but print a warning

4. **Conflict handling**: If both `--confirm` and `--reject` are provided, print "Error: specify only one of --confirm or --reject" and `sys.exit(1)`.
</action>
  <verify>
    <automated>python -c "
import json, pathlib, tempfile, sys
from building_model import BuildingModel, Provenance, Space, Level
import run_review

# Build a model with 3 review items
m = BuildingModel(name='test')
m.levels.append(Level(id='L1', name='Level 1'))
m.flag_for_review('window_room_link', 'Window W1 may belong to Room 101', 0.62, Provenance(sheet_id='elev_A201', revision=1, method='geometric_fallback', confidence=0.62))
m.flag_for_review('fixture_assignment', 'Fixture F1 assigned to Space L1-101', 0.55, Provenance(sheet_id='arch_A101', revision=2, method='point_in_polygon', confidence=0.55))

with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as f:
    json.dump(json.loads(m.to_json()), f)
    path = f.name

# Test confirm mutation
class Args:
    model = path
    confirm = 'RVW-0001'
    reject = None
    show_all = False

import io, contextlib
captured = io.StringIO()
try:
    with contextlib.redirect_stdout(captured):
        run_review.main(Args())
except SystemExit:
    pass
output = captured.getvalue()
print(f'confirm output: {output[:200]}')
assert 'Confirmed' in output or 'RVW-0001' in output

# Reload model and check status
m2 = BuildingModel.from_json(pathlib.Path(path).read_text())
assert m2.review_queue[0].status == 'confirmed', f'Expected confirmed, got {m2.review_queue[0].status}'

pathlib.Path(path).unlink()
print('PASS')
"</automated>
  </verify>
  <done>`--confirm <id>` sets item.status='confirmed', saves model, re-runs validation; `--reject <id>` sets item.status='rejected', saves model, re-runs validation</done>
</task>

</tasks>

<verification>
- `matchline review --model model.json --confirm RVW-0001` confirms item and prints validation summary
- `matchline review --model model.json --reject RVW-0002` rejects item and prints validation summary
- After confirm, the item disappears from the open list (status is now 'confirmed')
- Model JSON on disk reflects the new status
</verification>

<success_criteria>
`matchline review --model model.json --confirm <id>` and `--reject <id>` update the item status, save the model to disk, and re-run validation. Confirmed/rejected items no longer appear in the default (open-only) list.
</success_criteria>

<output>
After completion, create `.planning/phases/04-review/04-review-02-SUMMARY.md`
</output>
