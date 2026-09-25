"""Review queue CLI: list, confirm, or reject open review items.

Overview
--------
The review queue holds extraction decisions that fall below the confidence
threshold or that the pipeline could not auto-resolve. A review item
captures the provenance (sheet, revision, method), the extraction kind
(window_room_link, fixture_assignment, schedule_mismatch, extraction_type),
the raw confidence, and any triage metadata (urgency, auto-resolution).

Items pending review block BEM export via ``validate.py`` until they are
confirmed or rejected.

Usage:
    matchline review <model.json>                        # list open items
    matchline review <model.json> --show-all             # list all items
    matchline review <model.json> --confirm <id>         # confirm item
    matchline review <model.json> --reject <id>          # reject item

Triage flow
-----------
Each open item is evaluated by a task-specific TypedDecider loaded from
``.npz`` (never from ``.pkl``). The classifier returns a typed_decision
(``CONFIRM`` or ``REJECT``) and a confidence score.

Auto-triage resolves items automatically when:

* ``model.auto_triage`` is True (set via ``--auto-triage`` flag), and
* the item's ``needs_human`` value is less than 1.0, and
* the classifier confidence meets or exceeds the confidence threshold
  (default 0.75; also controllable via ``--confidence``).

Auto-resolved items are marked ``status = "confirmed"`` or
``status = "rejected"`` and carry ``auto_resolved = True`` with
``resolution`` set to ``"CONFIRM"`` or ``"REJECT"``. Items that cannot be
auto-resolved are left ``open`` for human review.

Urgency
~~~~~~~
Items with ``urgency > 0`` are highlighted as higher-priority. When
listing items, the sort key is ``(-urgency, auto_resolved)`` so that
high-urgency items needing human attention appear first.

Security model
--------------
Pickle deserialization is a supply-chain attack vector: a malicious
``.pkl`` file can execute arbitrary code on load. For this reason,
``run_review.py`` ships with ``_check_no_pkl_in_review_classifier()``,
a startup guard that walks the ``review_classifier/`` directory and
raises ``SecurityError`` if any ``.pkl`` files are present. Only
``.npz`` (safe numpy archive) format is permitted for model files.
``TypedDecider`` models are loaded via ``TypedDecider.from_npz()`` only.

Exit codes
----------
:0: Success (list printed, item confirmed/rejected, or validation passed)
:1: Error (file not found, validation failure, classifier error, or
   SecurityError from a blocked pickle file)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import warnings
from typing import TextIO

from building_model import BuildingModel, ReviewItem
from review_classifier.data import Example
from review_classifier.model import TypedDecider
from validate import run_checks

# Per-task model file names (relative to review_classifier/)
_TASK_MODEL_NAMES: dict[str, str] = {
    "route_to_review": "trained_model_route_to_review.pkl",
    "schedule_match": "trained_model_schedule_match.pkl",
    "extraction_type": "trained_model_extraction_type.pkl",
}

# Task name for each ReviewItem.kind
_KIND_TO_TASK: dict[str, str] = {
    "window_room_link": "route_to_review",
    "fixture_assignment": "route_to_review",
    "schedule_mismatch": "schedule_match",
    "extraction_type": "extraction_type",
    "lighting_extraction": "route_to_review",
    "hvac_extraction": "route_to_review",
    "room_label_extraction": "route_to_review",
    "window_extraction": "route_to_review",
    "elevation_extraction": "route_to_review",
    "facade_takeoff": "route_to_review",
}


def _kind_to_task(kind: str) -> str:
    return _KIND_TO_TASK.get(kind, "route_to_review")


def _item_to_example(item: ReviewItem) -> Example:
    """Convert a ReviewItem to a classifier Example."""
    task = _kind_to_task(item.kind)
    text = f"[{item.kind}] {item.description} (conf={item.confidence:.2f})"
    numeric = {"det_conf": item.confidence}
    return Example(task=task, text=text, numeric=numeric)


def _check_no_pkl_in_review_classifier() -> None:
    """Security check: reject any .pkl files in the review_classifier directory.

    Pickle files can execute arbitrary code on deserialization, making them a
    supply-chain attack vector. This check prevents loading any .pkl files from
    the review_classifier directory.
    """
    review_classifier_dir = pathlib.Path(__file__).parent / "review_classifier"
    pkl_files = list(review_classifier_dir.glob("*.pkl"))
    if pkl_files:
        pkl_names = ", ".join(f'"{p.name}"' for p in pkl_files)
        raise SecurityError(
            f"Pickle files are blocked in review_classifier directory: {pkl_names}. "
            "Pickle deserialization can execute arbitrary code and is a "
            "supply-chain attack vector. Use .npz format instead."
        )


class SecurityError(Exception):
    """Raised when a security check fails in the review pipeline."""

    pass


def _load_classifier_for_task(task: str) -> TypedDecider | None:
    """Load a pre-trained TypedDecider for a specific task, or None if not found.

    Loads from ``.npz`` (safe numpy archive). Pickle loading was removed —
    use ``TypedDecider.from_npz()`` only.
    """
    _check_no_pkl_in_review_classifier()
    model_file = _TASK_MODEL_NAMES.get(task)
    if model_file is None:
        return None
    base_path = pathlib.Path(__file__).parent / "review_classifier" / model_file
    npz_path = base_path.with_suffix(".npz")
    if npz_path.exists():
        try:
            return TypedDecider.from_npz(npz_path)
        except Exception:
            return None
    return None


def _urgency_sort_key(item: ReviewItem) -> tuple[int, bool]:
    """Sort key: urgency descending, then auto_resolved False first (human needs first)."""
    return (-item.urgency, item.auto_resolved)


def _format_item(item: ReviewItem, out: TextIO = sys.stdout) -> None:
    """Print a single ReviewItem to stdout with triage metadata."""
    prov = item.provenance
    prov_str = f"{prov.sheet_id} r{prov.revision} via {prov.method}" if prov else "no provenance"

    # Triage indicators
    triage_parts = []
    if item.urgency > 0:
        triage_parts.append(f"urgency={item.urgency}")
    if item.auto_resolved:
        triage_parts.append(f"AUTO-{item.resolution}")
    triage_str = f"  [{', '.join(triage_parts)}]" if triage_parts else ""

    print(f"  [{item.id}] {item.kind}  conf={item.confidence:.2f}{triage_str}", file=out)
    print(f"    → {item.description}", file=out)
    print(f"    provenance: {prov_str}", file=out)
    if item.needs_human < 1.0:
        print(f"    needs_human: {item.needs_human:.2f}", file=out)


def list_review_items(
    model_path: str | pathlib.Path,
    show_all: bool = False,
) -> tuple[list[ReviewItem], bool]:
    """Load the model, classify open items, and print the review queue.

    Items are ordered by urgency (highest first), then auto-resolved items
    are shown before human-needed items at each urgency level.

    Args:
        model_path: path to BuildingModel JSON file
        show_all: if True, also show confirmed/rejected items

    Returns:
        (items, classifier_available) — items is the list of printed items
    """
    model_path = pathlib.Path(model_path)
    raw = model_path.read_text()
    model = BuildingModel.from_json(raw)

    deciders: dict[str, TypedDecider] = {}
    classifier_available = False

    for task in set(_kind_to_task(item.kind) for item in model.review_queue):
        decider = _load_classifier_for_task(task)
        if decider is not None:
            deciders[task] = decider
            classifier_available = True

    if not classifier_available:
        print(
            "NOTE: no trained model found for any task "
            "(expected review_classifier/trained_model_{task}.pkl). "
            "Classifier suggestions will be omitted. "
            "Run: python -m review_classifier.train --task all",
            file=sys.stderr,
        )

    items = [item for item in model.review_queue if show_all or item.status == "open"]

    if not items:
        print("No review items in queue." if show_all else "No open review items.")
        return items, classifier_available

    # Sort by urgency (highest first), auto-resolved last within urgency band
    items.sort(key=_urgency_sort_key)

    n_auto = sum(1 for i in items if i.auto_resolved)
    print(
        f"Review queue — {len(items)} item(s) shown "
        f"({n_auto} auto-resolved) "
        f"(of {len(model.review_queue)} total)"
    )
    print()

    for item in items:
        classifier_label = None
        classifier_conf: float | None = None
        task = _kind_to_task(item.kind)
        decider = deciders.get(task)
        if decider is not None:
            try:
                example = _item_to_example(item)
                decision = decider.decide(example)
                classifier_label = decision.label
                classifier_conf = decision.confidence
            except Exception:
                classifier_label = "(classifier error)"
                classifier_conf = None

        status_icon = {"open": "○", "confirmed": "●", "rejected": "✗"}[item.status]

        triage_parts = []
        if item.urgency > 0:
            triage_parts.append(f"urgency={item.urgency}")
        if item.auto_resolved:
            triage_parts.append(f"AUTO-{item.resolution}")
        triage_str = f"  [{', '.join(triage_parts)}]" if triage_parts else ""

        print(f"{status_icon} [{item.id}] {item.kind}  conf={item.confidence:.2f}{triage_str}")
        if classifier_label is not None and classifier_conf is not None:
            print(f"   → classifier: {classifier_label} ({classifier_conf:.2f})")
        elif classifier_label is not None:
            print(f"   → classifier: {classifier_label}")

        prov = item.provenance
        prov_str = (
            f"{prov.sheet_id} r{prov.revision} via {prov.method}" if prov else "no provenance"
        )
        print(f"   {item.description}")
        print(f"   provenance: {prov_str}")
        if item.needs_human < 1.0:
            print(f"   needs_human: {item.needs_human:.2f}")
        print()

    return items, classifier_available


def format_review_list(
    model_path: str | pathlib.Path,
    show_all: bool = False,
) -> tuple[list[ReviewItem], bool]:
    """Deprecated alias for list_review_items.

    .. deprecated::
        Use :func:`list_review_items` instead.
    """
    warnings.warn(
        "format_review_list is deprecated, use list_review_items instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return list_review_items(model_path, show_all)


def _confirm_item(model: BuildingModel, item_id: str) -> tuple[BuildingModel, str]:
    """Mark a review item as confirmed. Returns (updated_model, message)."""
    item = next((i for i in model.review_queue if i.id == item_id), None)
    if item is None:
        raise ValueError(f"Item {item_id} not found in review queue.")
    if item.status != "open":
        raise ValueError(f"Item {item_id} is already {item.status}.")
    item.status = "confirmed"
    item.needs_review = False
    item.acknowledged = True
    return model, f"Confirmed {item_id}."


def _reject_item(model: BuildingModel, item_id: str) -> tuple[BuildingModel, str]:
    """Mark a review item as rejected. Returns (updated_model, message)."""
    item = next((i for i in model.review_queue if i.id == item_id), None)
    if item is None:
        raise ValueError(f"Item {item_id} not found in review queue.")
    if item.status != "open":
        raise ValueError(f"Item {item_id} is already {item.status}.")
    item.status = "rejected"
    item.needs_review = False
    item.acknowledged = True
    return model, f"Rejected {item_id}."


def _summarize_validation(report) -> str:
    """Human-readable summary of a ValidationReport."""
    n_errors = len(report.errors)
    n_warnings = len(report.warnings)
    n_pass = len(report.passes)
    n_skip = len(report.skipped)
    ok_str = "PASS" if report.ok else "FAIL"
    return (
        f"Validation {ok_str} — "
        f"{n_pass} passed, {n_errors} errors, {n_warnings} warnings, {n_skip} skipped"
    )


def main(args: argparse.Namespace | None = None) -> None:
    """CLI entry point. Accepts an argparse.Namespace or parses sys.argv."""
    if args is None:
        parser = _build_argparser()
        args = parser.parse_args()

    model_path = pathlib.Path(args.model)
    if not model_path.exists():
        print(f"Error: model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)

    raw = model_path.read_text()
    model = BuildingModel.from_json(raw)

    if args.auto_triage:
        model.auto_triage = True

    # --- Confirm or Reject ---
    if args.confirm is not None or args.reject is not None:
        if args.confirm is not None and args.reject is not None:
            print(
                "Error: specify only one of --confirm or --reject, not both.",
                file=sys.stderr,
            )
            sys.exit(1)

        is_confirm = args.confirm is not None

        try:
            if is_confirm:
                assert args.confirm is not None
                model, msg = _confirm_item(model, args.confirm)
            else:
                assert args.reject is not None
                model, msg = _reject_item(model, args.reject)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        # Save updated model
        model_path.write_text(model.to_json())
        print(msg)
        print(f"Model saved: {model_path}")

        # Re-run validation
        report = run_checks(model)
        print(_summarize_validation(report))
        return

    # --- List (non-interactive) ---
    if args.list:
        model = BuildingModel.from_json(pathlib.Path(args.model).read_text())
        items = [item for item in model.review_queue if args.show_all or item.status == "open"]
        items.sort(key=lambda i: (i.provenance.sheet_id if i.provenance else "", i.kind))
        if not items:
            print("No pending review items.")
            sys.exit(0)
        if args.format == "json":
            import json

            print(
                json.dumps(
                    [_review_item_to_dict(item) for item in items],
                    indent=2,
                )
            )
        else:
            for item in items:
                sheet = item.provenance.sheet_id if item.provenance else "?"
                print(
                    f"[{item.id}] {item.kind} | sheet={sheet} "
                    f"| confidence={item.confidence:.0%} | {item.description}"
                )
        sys.exit(0)

    # --- Interactive List ---
    list_review_items(args.model, show_all=args.show_all)


def _review_item_to_dict(item: ReviewItem) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "sheet": item.provenance.sheet_id if item.provenance else None,
        "confidence": item.confidence,
        "description": item.description,
        "status": item.status if item.status else None,
    }


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matchline review",
        description="Review queue: list open review items, confirm or reject decisions.",
    )
    parser.add_argument("model", help="BuildingModel JSON file path")
    parser.add_argument(
        "--confirm",
        metavar="ID",
        help="Confirm a review item (marks confirmed, re-runs validation)",
    )
    parser.add_argument(
        "--reject",
        metavar="ID",
        help="Reject a review item (marks rejected, re-runs validation)",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Also show confirmed and rejected items",
    )
    parser.add_argument(
        "--auto-triage",
        action="store_true",
        default=False,
        help="Enable auto-triage for review items (sets model.auto_triage=True)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List review items to stdout without prompting (exits 0 immediately)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for --list (default: text)",
    )
    return parser


if __name__ == "__main__":
    main()
