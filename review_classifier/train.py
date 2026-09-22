"""Retrain the review classifier on synthetic + corpus data.

Usage:
    python -m review_classifier.train --task route_to_review
    python -m review_classifier.train --task all
    python -m review_classifier.train --task schedule_match --synthetic-n 500 --out /tmp/model.pkl
"""

from __future__ import annotations

import argparse
import json
import pathlib
import pickle

from sklearn.model_selection import cross_val_score

from review_classifier.data import Example, generate
from review_classifier.model import TypedDecider

DEFAULT_CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"
DEFAULT_OUT_DIR = pathlib.Path(__file__).parent
VALID_TASKS = ["route_to_review", "schedule_match", "extraction_type"]


def model_path_for_task(task: str, out_dir: pathlib.Path | None = None) -> pathlib.Path:
    """Path for a per-task model file."""
    d = out_dir or DEFAULT_OUT_DIR
    return d / f"trained_model_{task}.pkl"


def load_corpus_for_task(task: str, corpus_dir: pathlib.Path) -> list[Example]:
    """Load corpus examples for a given task from a specific corpus directory."""
    path = corpus_dir / f"{task}.jsonl"
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


def train_task(
    task: str,
    synthetic_n: int = 1500,
    corpus_examples: list[Example] | None = None,
    out_path: pathlib.Path | None = None,
    seed: int = 20260919,
    verbose: bool = True,
) -> TypedDecider:
    """Train a TypedDecider for one task on synthetic + corpus examples.

    Args:
        task: The task to train (``route_to_review``, ``schedule_match``, ``extraction_type``).
        synthetic_n: Number of synthetic examples to generate.
        corpus_examples: Real-drawing examples (optional). If None, loaded from ``DEFAULT_CORPUS_DIR``.
        out_path: Path to save the trained model (optional).
        seed: Random seed for synthetic data generation.
        verbose: If True, print training info and accuracy metrics.

    Returns:
        The fitted ``TypedDecider``.
    """
    if task not in VALID_TASKS:
        raise ValueError(f"unknown task {task!r}; expected one of {VALID_TASKS}")

    corpus = corpus_examples if corpus_examples is not None else load_corpus_for_task(task, DEFAULT_CORPUS_DIR)
    synthetic = generate(task, n=synthetic_n, seed=seed)
    combined = synthetic + corpus

    if verbose:
        print(f"Training {task}")
        print(f"  synthetic examples : {len(synthetic)}")
        print(f"  corpus examples    : {len(corpus)}")
        print(f"  combined examples : {len(combined)}")

    decider = TypedDecider(cv=5, seed=seed)
    decider.fit(combined)

    # 5-fold CV on combined dataset
    X = decider.featurizer.fit(combined).transform(combined)
    y = [e.label for e in combined]
    cv_scores = cross_val_score(decider._base(), X, y, cv=5)
    cv_mean = cv_scores.mean()
    cv_std = cv_scores.std()

    if verbose:
        print(f"  5-fold CV accuracy : {cv_mean:.3f} ± {cv_std:.3f}")

    # Corpus-only accuracy (the RVIEW-04 metric)
    if len(corpus) >= 5:
        X_corpus = decider.featurizer.transform(corpus)
        y_corpus = [e.label for e in corpus]
        corpus_cv = min(5, len(corpus))
        corpus_scores = cross_val_score(decider._base(), X_corpus, y_corpus, cv=corpus_cv)
        corpus_mean = corpus_scores.mean()
        if verbose:
            target = "≥0.70" if corpus_mean >= 0.70 else "<0.70 (need more corpus)"
            print(f"  corpus-only accuracy: {corpus_mean:.3f}  (RVIEW-04 target: {target})")
    else:
        if verbose:
            print(f"  corpus-only accuracy: N/A (only {len(corpus)} corpus examples; need ≥5)")

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(pickle.dumps(decider))
        if verbose:
            print(f"  saved model -> {out_path}")

    return decider


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m review_classifier.train",
        description="Retrain the review classifier on synthetic + corpus data.",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=VALID_TASKS + ["all"],
        help="Task to train (or 'all' for all three tasks)",
    )
    parser.add_argument(
        "--corpus-dir",
        type=pathlib.Path,
        default=DEFAULT_CORPUS_DIR,
        help=f"Corpus directory (default: {DEFAULT_CORPUS_DIR})",
    )
    parser.add_argument(
        "--synthetic-n",
        type=int,
        default=1500,
        help="Number of synthetic examples per task (default: 1500)",
    )
    parser.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260919,
        help="Random seed for reproducibility (default: 20260919)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Train but don't save the model",
    )

    args = parser.parse_args()

    if args.task == "all":
        tasks = VALID_TASKS
    else:
        tasks = [args.task]

    for task in tasks:
        corpus_examples = load_corpus_for_task(task, args.corpus_dir)
        out_path = model_path_for_task(task, args.out_dir) if not args.no_save else None
        _decider = train_task(
            task,
            synthetic_n=args.synthetic_n,
            corpus_examples=corpus_examples,
            out_path=out_path,
            seed=args.seed,
            verbose=True,
        )
        print()


if __name__ == "__main__":
    main()
