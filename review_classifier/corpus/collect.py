"""Corpus collection utilities for real-drawing review item examples.

Loads, saves, and exports labeled examples to/from the corpus directory.
"""

from __future__ import annotations

import json
import pathlib

from review_classifier.data import Example

CORPUS_DIR = pathlib.Path(__file__).parent


def _corpus_path(task: str, corpus_dir: pathlib.Path | None = None) -> pathlib.Path:
    d = corpus_dir or CORPUS_DIR
    return d / f"{task}.jsonl"


def load_corpus(task: str, corpus_dir: pathlib.Path | None = None) -> list[Example]:
    """Load all examples for a task from the corpus JSONL file.

    Args:
        task: One of ``route_to_review``, ``schedule_match``, ``extraction_type``.
        corpus_dir: Optional override for the corpus directory.

    Returns:
        List of ``Example`` objects loaded from the corpus.
    """
    path = _corpus_path(task, corpus_dir)
    if not path.exists():
        return []
    examples: list[Example] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        examples.append(Example(**obj))
    return examples


def save_example(example: Example, corpus_dir: pathlib.Path | None = None) -> None:
    """Append a single Example to the appropriate corpus JSONL file.

    Args:
        example: The labeled example to save.
        corpus_dir: Optional override for the corpus directory.
    """
    dir_path = corpus_dir or CORPUS_DIR
    dir_path.mkdir(parents=True, exist_ok=True)
    path = _corpus_path(example.task, corpus_dir)
    with path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "task": example.task,
                    "text": example.text,
                    "numeric": example.numeric,
                    "label": example.label,
                }
            )
            + "\n"
        )


def export_from_model(
    model_json_path: str | pathlib.Path,
    output_dir: pathlib.Path | None = None,
) -> dict[str, int]:
    """Export confirmed/rejected review items from a BuildingModel JSON as corpus examples.

    For each item in the review queue with ``status in ('confirmed', 'rejected')``,
    this creates a labeled ``Example`` and appends it to the appropriate corpus
    JSONL file. This lets operators accumulate training data by simply using
    ``matchline review --confirm`` and ``--reject`` on real drawings.

    Args:
        model_json_path: Path to a BuildingModel JSON file.
        output_dir: Optional override for the output corpus directory.

    Returns:
        A dict mapping task names to the number of examples exported.
    """
    from building_model import BuildingModel

    model_path = pathlib.Path(model_json_path)
    model = BuildingModel.from_json(model_path.read_text())

    counts: dict[str, int] = {}
    for item in model.review_queue:
        if item.status not in ("confirmed", "rejected"):
            continue

        # Map ReviewItem.kind to classifier task
        if item.kind in ("window_room_link", "fixture_assignment"):
            task = "route_to_review"
        elif item.kind == "schedule_mismatch":
            task = "schedule_match"
        else:
            task = "route_to_review"  # default

        # label=True for confirmed, label=False for rejected
        label = item.status == "confirmed"

        text = f"[{item.kind}] {item.description} (conf={item.confidence:.2f})"
        numeric = {"det_conf": item.confidence}

        example = Example(task=task, text=text, numeric=numeric, label=label)
        save_example(example, corpus_dir=output_dir)
        counts[task] = counts.get(task, 0) + 1

    return counts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Corpus collection utilities")
    sub = parser.add_subparsers(dest="command")

    load_p = sub.add_parser("load", help="Load corpus for a task")
    load_p.add_argument("task", help="Task name")

    export_p = sub.add_parser("export", help="Export confirmed/rejected items from a model")
    export_p.add_argument("model_json", help="BuildingModel JSON file path")
    export_p.add_argument("--out", default=None, help="Output corpus directory")

    args = parser.parse_args()

    if args.command == "load":
        examples = load_corpus(args.task)
        print(f"Loaded {len(examples)} examples for {args.task}")
    elif args.command == "export":
        counts = export_from_model(
            args.model_json, output_dir=pathlib.Path(args.out) if args.out else None
        )
        for task, n in counts.items():
            print(f"Exported {n} examples for {task}")
    else:
        parser.print_help()
