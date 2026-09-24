---
phase: "04-review"
plan: "03"
type: "execute"
wave: 2
depends_on:
  - "04-review-01"
files_modified:
  - "review_classifier/train.py"
  - "review_classifier/data.py"
autonomous: true
requirements:
  - "RVIEW-04"
must_haves:
  truths:
    - "Real-drawing review items are collected into a machine-learning-ready corpus"
    - "Classifier can be retrained on combined synthetic + real corpus"
    - "RVIEW-04 acceptance criterion: ≥70%% human-decision accuracy measured over curated set"
  artifacts:
    - path: "review_classifier/train.py"
      provides: "Classifier training script with corpus loading and retraining"
      min_lines: 60
    - path: "review_classifier/corpus/"
      provides: "Curated real-drawing review item examples"
  key_links:
    - from: "review_classifier/train.py"
      to: "review_classifier.model"
      via: "TypedDecider.fit()"
      pattern: "TypedDecider"
    - from: "review_classifier/train.py"
      to: "review_classifier.data"
      via: "Example corpus format"
      pattern: "Example"
---

<objective>
Build a corpus collection pipeline for real-drawing review items and a training script that retrains the classifier on combined synthetic + real data. This closes the loop on RVIEW-04: the classifier improves over time as operators confirm/reject items.

Purpose: The prototype classifier was trained only on synthetic data. Real drawing runs will surface genuinely ambiguous cases. Collecting these as labeled examples and retraining periodically improves classifier accuracy toward the 70%% human-decision-match target.
Output: `review_classifier/train.py` + `review_classifier/corpus/` directory
</objective>

<context>
@.planning/phases/04-review/04-review-01-PLAN.md

From review_classifier/model.py (lines 44-52):
```python
class TypedDecider:
    def fit(self, examples: list[Example]) -> "TypedDecider":
        X = self.featurizer.fit(examples).transform(examples)
        y = np.array([e.label for e in examples])
        self._calibrated = CalibratedClassifierCV(
            estimator=self._base(), method="sigmoid", cv=self.cv
        )
        self._calibrated.fit(X, y)
        self._classes_ = list(self._calibrated.classes_)
        return self
```

From review_classifier/data.py (lines 24-29):
```python
@dataclass
class Example:
    task: str  # "route_to_review" | "schedule_match" | "extraction_type"
    text: str  # free-text rendering of the observation
    numeric: dict = field(default_factory=dict)
    label: object = None  # bool for binary tasks, str for extraction_type
```

Key insight: The classifier operates on `Example` objects with `task`, `text`, `numeric`, and `label`. Real-drawing items need to be converted to this format. The `corpus/` directory stores curated examples as JSON lines (`.jsonl`) files, one per task.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create review_classifier corpus format and collection script</name>
  <files>review_classifier/corpus/README.md, review_classifier/corpus/route_to_review.jsonl, review_classifier/corpus/schedule_match.jsonl, review_classifier/corpus/extraction_type.jsonl</files>
  <action>
Create the corpus infrastructure for collecting real-drawing labeled examples:

1. **Create `review_classifier/corpus/README.md`** documenting the corpus format:
   - One `.jsonl` file per classifier task
   - Each line: JSON-encoded `Example` (task, text, numeric, label)
   - Adding new examples: append to the appropriate `.jsonl` file

2. **Create `review_classifier/corpus/route_to_review.jsonl`** with seed synthetic examples (copy 20-30 examples from `data.generate('route_to_review', n=1500, seed=...)` as a starting seed corpus — these are labeled so the file is NOT empty)

3. **Create `review_classifier/corpus/schedule_match.jsonl`** and **`extraction_type.jsonl`** with seed synthetic examples for each task (20-30 each)

4. **Create `review_classifier/corpus/collect.py`**:
   - `load_corpus(task: str) -> list[Example]`: reads the `.jsonl` file for the task and returns a list of `Example` objects
   - `save_example(task: str, example: Example)`: appends the example to the appropriate `.jsonl`
   - `export_from_model(model_json_path: str, output_dir: str)`: 
     - Loads a BuildingModel JSON file
     - For each item in `model.review_queue` where `status in ('confirmed', 'rejected')`:
       - Maps `item.kind` → task
       - Creates an `Example` with `text=f"[{item.kind}] {item.description}", numeric={"conf": item.confidence}`, `label=(item.status == 'confirmed')`
       - Saves to corpus
     - This lets operators export their confirm/reject decisions as training examples

5. **Corpus quality rules**:
   - Each confirmed example label = `True` (human said "route to review = yes")
   - Each rejected example label = `False` (human said "route to review = no" — item was not actually ambiguous)
   - For `schedule_match`: confirmed = `True` (match is correct), rejected = `False`
</action>
  <verify>
    <automated>python -c "
from review_classifier.corpus.collect import load_corpus, save_example, export_from_model
from review_classifier.data import Example
import pathlib, tempfile, json

# Test load_corpus
examples = load_corpus('route_to_review')
print(f'Loaded {len(examples)} route_to_review examples from corpus')
assert len(examples) >= 20, f'Expected >= 20, got {len(examples)}'

# Test save_example
with tempfile.TemporaryDirectory() as tmpdir:
    test_file = pathlib.Path(tmpdir) / 'test.jsonl'
    ex = Example(task='route_to_review', text='test item', numeric={'conf': 0.5}, label=True)
    save_example(ex, test_file)
    loaded = [json.loads(l) for l in open(test_file)]
    assert len(loaded) == 1
    assert loaded[0]['label'] == True

print('PASS')
"</automated>
  </verify>
  <done>Corpus directory with seed synthetic examples exists; load_corpus and save_example functions work; export_from_model exports confirmed/rejected items from a BuildingModel JSON</done>
</task>

<task type="auto">
  <name>Task 2: Create review_classifier/train.py retraining script</name>
  <files>review_classifier/train.py</files>
  <action>
Create `review_classifier/train.py` that retrains the classifier on combined synthetic + corpus data and saves the trained model:

1. **CLI interface** via `argparse`:
   - `--task` (required): which task to train (`route_to_review`, `schedule_match`, or `extraction_type`)
   - `--corpus-dir` (default: `review_classifier/corpus/`): where corpus `.jsonl` files live
   - `--synthetic-n` (default: 1500): how many synthetic examples to generate
   - `--out` (default: `review_classifier/trained_model.pkl`): where to save the trained model
   - `--seed` (default: 20260919): random seed for reproducibility

2. **Training pipeline**:
   a. Generate `n` synthetic examples: `data.generate(task, n, seed=seed)`
   b. Load corpus examples: `collect.load_corpus(task)` (returns list of `Example`)
   c. Combine: synthetic + corpus (corpus examples are pre-labeled by human operators)
   d. Train `TypedDecider(cv=5).fit(combined_examples)`
   e. Save the fitted `TypedDecider` to `--out` using `pickle`

3. **Accuracy reporting**:
   - Use 5-fold cross-validation on the combined dataset
   - Print accuracy for each fold and the mean
   - If corpus has ≥20 examples, compute accuracy on just the corpus subset (this is the RVIEW-04 metric)

4. **Serialization**:
   - Use `pickle.dumps(decider)` → `pathlib.Path(out).write_bytes()`
   - The `run_review.py` `format_review_list()` function loads this to get classifier predictions

5. **Train all tasks script**:
   - If `--task all`: train all three tasks in sequence
   - After each task, print the corpus accuracy
</action>
  <verify>
    <automated>python -c "
import pathlib, tempfile
from review_classifier.train import train_task
from review_classifier.data import generate
from review_classifier.model import TypedDecider
import pickle

with tempfile.TemporaryDirectory() as tmpdir:
    out_path = str(pathlib.Path(tmpdir) / 'model.pkl')
    
    # Generate small synthetic set + no corpus
    decider = train_task('route_to_review', synthetic_n=100, corpus_examples=[], 
                         out_path=out_path, seed=42, verbose=False)
    
    # Verify it saved and loads
    loaded_bytes = pathlib.Path(out_path).read_bytes()
    loaded = pickle.loads(loaded_bytes)
    print(f'Trained and loaded model: {type(loaded).__name__}')
    assert hasattr(loaded, 'decide')
    print('PASS')
"</automated>
  </verify>
  <done>`review_classifier/train.py --task route_to_review` trains the classifier and saves `trained_model.pkl`; the saved model loads correctly and can be used for predictions</done>
</task>

</tasks>

<verification>
- `python -m review_classifier.train --task route_to_review` produces `review_classifier/trained_model.pkl`
- The saved model loads correctly and `decide()` works on new examples
- Corpus accuracy ≥70%% on confirmed/rejected items is printed after training
</verification>

<success_criteria>
The classifier is retrainable on combined synthetic + real-drawing corpus. After retraining on a corpus of ≥20 real confirmed/rejected items, the classifier achieves ≥70%% human-decision-match accuracy on that corpus. `review_classifier/train.py --task all` retrains all three task classifiers and saves them.
</success_criteria>

<output>
After completion, create `.planning/phases/04-review/04-review-03-SUMMARY.md`
</output>
