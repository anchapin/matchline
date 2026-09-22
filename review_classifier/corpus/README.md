# Review Classifier Corpus

Collected real-drawing labeled examples for retraining the `TypedDecider` classifier.

## Format

One `.jsonl` file per classifier task. Each line is a JSON-encoded `Example`:

```
{"task": "route_to_review", "text": "[window_room_link] Window W1 may belong to Room 101 (conf=0.62)", "numeric": {"det_conf": 0.62}, "label": true}
{"task": "route_to_review", "text": "[fixture_assignment] Fixture F1 assigned with low confidence (conf=0.55)", "numeric": {"det_conf": 0.55}, "label": false}
```

## Labeling convention

| Task | `label: true` | `label: false` |
|------|---------------|----------------|
| `route_to_review` | Confirmed by human — route to review queue | Rejected — no review needed |
| `schedule_match` | Confirmed — schedule match is correct | Rejected — schedule match is wrong |
| `extraction_type` | One of `{door, window, room_label, fixture}` | — |

## Adding examples

1. **Manual**: append JSON lines to the appropriate `.jsonl` file
2. **From confirmed/rejected items**: use `collect.export_from_model(model_json_path, output_dir)`
3. **Programmatic**: use `collect.save_example(example, corpus_dir=...)`

## Retraining

```bash
python -m review_classifier.train --task route_to_review
python -m review_classifier.train --task all   # train all 3 tasks
```
