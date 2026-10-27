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
    matchline review <model.json> --confirm-kind <kind>  # confirm every open item of a kind
    matchline review <model.json> --reject-kind <kind>   # reject every open item of a kind

Batch triage (#1294)
--------------------
``--confirm-kind`` / ``--reject-kind`` (optionally narrowed with
``--confirm-sheet`` / ``--reject-sheet``) decide a whole class of review
items at once: every OPEN item matching the filter is confirmed or rejected
through the same path the single-item ``--confirm`` / ``--reject`` flags use
(same state, same revision log entry, same correction history). The
decisions are recorded in a decisions file in the report's format
(``matchline.review_decisions/1``), written beside the model as
``review_decisions.json`` unless ``--decisions-out`` says otherwise, so the
batch can be replayed with ``matchline review <model.json> --apply``.

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

# Per-task model file names (relative to review_classifier/). The shipped
# models are .npz; the security guard below rejects any .pkl in this directory.
_TASK_MODEL_NAMES: dict[str, str] = {
    "route_to_review": "trained_model_route_to_review.npz",
    "schedule_match": "trained_model_schedule_match.npz",
    "extraction_type": "trained_model_extraction_type.npz",
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
    "elevation_conflict": "route_to_review",
    "window_reconciliation": "route_to_review",
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
    npz_path = pathlib.Path(__file__).parent / "review_classifier" / model_file
    if npz_path.exists():
        try:
            return TypedDecider.from_npz(npz_path)
        except (OSError, EOFError):
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

    Items are ordered worst-first (#996): open items before resolved ones,
    then highest urgency, then lowest confidence. This matches the HTML
    review page and the matchline-ui summary.

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

    # Worst-first (#996): open first, highest urgency, lowest confidence.
    # Same ordering the HTML review page renders (#749).
    import review.review_report as review_report

    items = review_report.worst_first(items)

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
            except (ValueError, TypeError):
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


def _log_review_override(
    model: BuildingModel, item_id: str, action: str, corrections_path=None
) -> None:
    """Capture a review decision as friction feedback (#1038).

    The review decision is a correction of a detection, so it lands in the
    local feedback log (beside the corrections history) as an override
    event: the item's kind when that token is generic enough for the
    telemetry exclusion list, "review_item" otherwise. Capture failure
    never blocks the review.
    """
    from review.feedback_capture import FeedbackCaptureError, FeedbackLog

    item = next((i for i in model.review_queue if i.id == item_id), None)
    kind = item.kind if item is not None else "unknown"
    # the exclusion list refuses "confirm" (substring "firm"), so the action
    # token maps to accept/reject, same 1:1 vocabulary, both exclusion-safe
    action_token = {"confirm": "accept", "reject": "reject"}[action]
    log_path = (
        pathlib.Path(corrections_path).parent / "feedback.jsonl"
        if corrections_path
        else pathlib.Path("feedback.jsonl")
    )
    try:
        log = FeedbackLog(log_path)
        try:
            log.log_override(kind, action_token)
        except FeedbackCaptureError:
            log.log_override("review_item", action_token)
    except (FeedbackCaptureError, OSError):
        pass


def _decide(
    model: BuildingModel,
    item_id: str,
    action: str,
    corrections_path=None,
    emitter=None,
) -> tuple[BuildingModel, str]:
    """Same state and revision log entry as the HTML review report (#749)."""
    from review.review_report import decide

    done = {"confirm": "Confirmed", "reject": "Rejected"}[action]
    changed = decide(model, item_id, action, corrections_path=corrections_path)
    if changed:
        _log_review_override(model, item_id, action, corrections_path)
        if emitter is not None:
            # the same 1:1 vocabulary the feedback log uses (#1126)
            emitter.record_correction({"confirm": "accept", "reject": "reject"}[action])
    return (
        model,
        f"{done} {item_id}."
        if changed
        else f"{item_id} is already {done.lower()}; nothing changed.",
    )


def _confirm_item(
    model: BuildingModel, item_id: str, corrections_path=None, emitter=None
) -> tuple[BuildingModel, str]:
    """Mark a review item as confirmed. Returns (updated_model, message)."""
    return _decide(model, item_id, "confirm", corrections_path=corrections_path, emitter=emitter)


def _reject_item(
    model: BuildingModel, item_id: str, corrections_path=None, emitter=None
) -> tuple[BuildingModel, str]:
    """Mark a review item as rejected. Returns (updated_model, message)."""
    return _decide(model, item_id, "reject", corrections_path=corrections_path, emitter=emitter)


def _batch_request(args) -> tuple[str, str | None, str | None] | None:
    """The batch triage request in ``args``, or None when there is none.

    Returns ``(action, kind, sheet)`` with ``action`` either ``"confirm"``
    or ``"reject"``. Raises ValueError when both actions are asked for.
    """
    confirm_kind = getattr(args, "confirm_kind", None)
    reject_kind = getattr(args, "reject_kind", None)
    confirm_sheet = getattr(args, "confirm_sheet", None)
    reject_sheet = getattr(args, "reject_sheet", None)
    given = (confirm_kind, reject_kind, confirm_sheet, reject_sheet)
    if not any(v is not None for v in given):
        return None
    if (confirm_kind is not None or confirm_sheet is not None) and (
        reject_kind is not None or reject_sheet is not None
    ):
        raise ValueError(
            "specify only one of --confirm-kind/--confirm-sheet or "
            "--reject-kind/--reject-sheet, not both."
        )
    action = "confirm" if (confirm_kind is not None or confirm_sheet is not None) else "reject"
    kind = confirm_kind if confirm_kind is not None else reject_kind
    sheet = confirm_sheet if confirm_sheet is not None else reject_sheet
    return action, kind, sheet


def _batch_items(
    model: BuildingModel,
    kind: str | None = None,
    sheet: str | None = None,
) -> list[ReviewItem]:
    """Every OPEN review item matching the batch filters, worst-first.

    The same order the queue is listed in (open first, highest urgency,
    lowest confidence), so a batch run reads like the list it came from.
    """
    import review.review_report as review_report

    out: list[ReviewItem] = []
    for item in review_report.worst_first(model.review_queue):
        if item.status != "open":
            continue
        if kind and item.kind != kind:
            continue
        if sheet:
            prov = item.provenance
            if prov is None or prov.sheet_id != sheet:
                continue
        out.append(item)
    return out


def _write_review_telemetry(emitter, dest) -> None:
    """Write the consent-gated telemetry payload beside the run's outputs (#1126).

    The payload lands next to feedback.jsonl (the model's directory) and is
    written only when the reviewer granted consent; emission failure never
    blocks the review.
    """
    if not emitter.enabled:
        return
    from telemetry_emitter import EmitterBuildError

    try:
        payload = emitter.build_payload()
    except EmitterBuildError as e:
        print(f"warning: telemetry payload skipped: {e}", file=sys.stderr)
        return
    if payload is not None:
        import json

        (pathlib.Path(dest) / "telemetry.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


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

    from telemetry_emitter import TelemetryEmitter

    # consent comes from the --telemetry flag only; the review session has no
    # config plumbing, and telemetry_local_only has nothing to win over here
    emitter = TelemetryEmitter(consent=bool(getattr(args, "telemetry", False)))

    raw = model_path.read_text()
    model = BuildingModel.from_json(raw)

    if args.auto_triage:
        model.auto_triage = True

    # --- Batch triage request (#1294), resolved once -----------------------
    try:
        batch = _batch_request(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Correction history modes (#997) take no other action ---------------
    history_modes = [
        getattr(args, "undo", None) is not None,
        bool(getattr(args, "revert_to_auto", None)),
        bool(getattr(args, "corrections", None)),
    ]
    if any(history_modes) and (
        sum(history_modes) > 1
        or args.confirm is not None
        or args.reject is not None
        or getattr(args, "apply", None)
        or getattr(args, "report", None)
        or getattr(args, "list", None)
        or getattr(args, "bulk_confirm", False)
        or getattr(args, "bulk_reject", False)
        or batch is not None
    ):
        print(
            "Error: --undo, --revert-to-auto and --corrections take no other action.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Apply a decisions file from the HTML review report (#749) ---
    if getattr(args, "apply", None):
        import json

        import review.review_report as review_report

        try:
            doc = json.loads(pathlib.Path(args.apply).read_text())
            dest_apply = pathlib.Path(getattr(args, "out", None) or model_path)
            summary = review_report.apply_decisions(
                model,
                doc,
                review_report.sha256_text(raw),
                corrections_path=dest_apply,
            )
        except (OSError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        dest = pathlib.Path(getattr(args, "out", None) or model_path)
        dest.write_text(model.to_json(), encoding="utf-8")
        for w in summary["warnings"]:
            print(f"Warning: {w}", file=sys.stderr)
        print(review_report.summarize(summary))
        print(f"Model saved: {dest}")
        print(_summarize_validation(run_checks(model)))
        return

    # --- Write the HTML review report (#749) ---
    if getattr(args, "report", None):
        import review.review_report as review_report

        d = review_report.write_review(pathlib.Path(args.report), model)
        print(f"Review report: {d / 'review.html'}")
        return

    # --- Bulk confirm/reject with filters (#996) ---
    if getattr(args, "bulk_confirm", False) or getattr(args, "bulk_reject", False):
        if getattr(args, "bulk_confirm", False) and getattr(args, "bulk_reject", False):
            print(
                "Error: specify only one of --bulk-confirm or --bulk-reject.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.confirm is not None or args.reject is not None:
            print(
                "Error: --bulk-confirm/--bulk-reject take no --confirm/--reject.",
                file=sys.stderr,
            )
            sys.exit(1)

        is_confirm = getattr(args, "bulk_confirm", False)
        import review.review_report as review_report

        lvl_idx = review_report._level_index(model)
        matched = [
            item
            for item in review_report.worst_first(model.review_queue)
            if item.status == "open"
            and (not args.kind or item.kind == args.kind)
            and (args.conf_above is None or item.confidence >= args.conf_above)
            and (args.conf_below is None or item.confidence <= args.conf_below)
            and (not args.level or review_report._item_level(model, item, lvl_idx) == args.level)
        ]
        if not matched:
            print("Error: no open review items match the bulk filters.", file=sys.stderr)
            sys.exit(1)

        changed = 0
        try:
            for item in matched:
                if is_confirm:
                    model, msg = _confirm_item(
                        model, item.id, corrections_path=model_path, emitter=emitter
                    )
                else:
                    model, msg = _reject_item(
                        model, item.id, corrections_path=model_path, emitter=emitter
                    )
                changed += 1
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        model_path.write_text(model.to_json(), encoding="utf-8")
        action = "confirmed" if is_confirm else "rejected"
        print(f"Bulk {action}: {len(matched)} item(s) matched the filters, {changed} changed.")
        print(_summarize_validation(run_checks(model)))
        _write_review_telemetry(emitter, model_path.parent)
        return

    # --- Batch triage by filter (#1294) ---
    if batch is not None:
        action, kind, sheet = batch
        if args.confirm is not None or args.reject is not None:
            print(
                "Error: --confirm-kind/--reject-kind take no --confirm/--reject.",
                file=sys.stderr,
            )
            sys.exit(1)
        if getattr(args, "bulk_confirm", False) or getattr(args, "bulk_reject", False):
            print(
                "Error: --confirm-kind/--reject-kind take no --bulk-confirm/--bulk-reject.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not kind and not sheet:
            print(
                "Error: --confirm-kind/--reject-kind need a filter "
                "(--confirm-kind/--reject-kind or --confirm-sheet/--reject-sheet).",
                file=sys.stderr,
            )
            sys.exit(1)

        import review.review_report as review_report

        matched = _batch_items(model, kind=kind, sheet=sheet)
        decisions_path = pathlib.Path(
            getattr(args, "decisions_out", None) or (model_path.parent / "review_decisions.json")
        )
        if not matched:
            print("No open review items match the batch filters; nothing changed.")
            print(f"No decisions written to {decisions_path}.")
            return

        # The decisions file records the queue as it was BEFORE this batch
        # (automatic states, model hash) plus the decisions taken, exactly
        # like the file the HTML review page exports.
        doc = review_report.template(model, review_report.sha256_text(model.to_json()))

        changed = 0
        try:
            for item in matched:
                if action == "confirm":
                    model, _msg = _confirm_item(
                        model, item.id, corrections_path=model_path, emitter=emitter
                    )
                else:
                    model, _msg = _reject_item(
                        model, item.id, corrections_path=model_path, emitter=emitter
                    )
                changed += 1
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        model_path.write_text(model.to_json(), encoding="utf-8")
        doc["decisions"] = [{"id": item.id, "action": action} for item in matched]
        review_report.write_decisions(decisions_path, doc)

        verb = "confirmed" if action == "confirm" else "rejected"
        print(f"Batch {verb}: {len(matched)} item(s) matched the filters, {changed} changed.")
        for item in matched:
            print(f"  {item.id} {item.kind} {verb}")
        print(f"Model saved: {model_path}")
        print(f"Decisions written: {decisions_path}")
        print(_summarize_validation(run_checks(model)))
        _write_review_telemetry(emitter, model_path.parent)
        return

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
                model, msg = _confirm_item(
                    model, args.confirm, corrections_path=model_path, emitter=emitter
                )
            else:
                assert args.reject is not None
                model, msg = _reject_item(
                    model, args.reject, corrections_path=model_path, emitter=emitter
                )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        # Save updated model
        model_path.write_text(model.to_json(), encoding="utf-8")
        print(msg)
        print(f"Model saved: {model_path}")

        # Re-run validation
        report = run_checks(model)
        print(_summarize_validation(report))
        _write_review_telemetry(emitter, model_path.parent)
        return

    # --- Correction history (#997): undo, revert-to-automatic, listing ---
    import review.corrections as corrections_mod

    if any(history_modes):
        if getattr(args, "undo", None) is not None:
            if args.undo < 1:
                print("Error: --undo needs a positive number of corrections.", file=sys.stderr)
                sys.exit(1)
            try:
                undone = corrections_mod.undo(model, model_path, args.undo)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            model_path.write_text(model.to_json(), encoding="utf-8")
            for c in undone:
                field = f" ({c['field']} back to {c['before']!r})" if c.get("field") else ""
                print(f"Undid [{c['seq']}] {c['action']} {c['item_id']}{field}")
            if not undone:
                print("Nothing to undo.")
            print(f"Model saved: {model_path}")
            print(_summarize_validation(run_checks(model)))
            return

        if getattr(args, "revert_to_auto", None):
            try:
                r = corrections_mod.revert_to_automatic(model, model_path, args.revert_to_auto)
            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
            model_path.write_text(model.to_json(), encoding="utf-8")
            print(
                f"Reverted {r['field']} to automatic output "
                f"({r['from']!r} -> {r['to']!r}); {r['reverted']} correction(s) marked undone."
            )
            print(f"Model saved: {model_path}")
            print(_summarize_validation(run_checks(model)))
            return

        try:
            doc = corrections_mod.load(model_path)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        cs = doc["corrections"]
        n_undone = sum(1 for c in cs if c.get("undone"))
        if not cs:
            print("No corrections recorded.")
            return
        print(
            f"Correction history: {len(cs)} accepted, {n_undone} undone "
            f"({corrections_mod.path_for(model_path)})"
        )
        for c in cs:
            mark = " [undone]" if c.get("undone") else ""
            field = f" {c['field']} {c['before']!r} -> {c['after']!r}" if c.get("field") else ""
            row = f"  [{c['seq']}] {c['ts']} {c['action']} {c['item_id']} ({c['source']})"
            print(f"{row}{field}{mark}")
        return

    # --- List (non-interactive) ---
    if args.list:
        model = BuildingModel.from_json(pathlib.Path(args.model).read_text())
        items = [item for item in model.review_queue if args.show_all or item.status == "open"]
        list_kind = getattr(args, "list_kind", None)
        list_sheet = getattr(args, "list_sheet", None)
        if list_kind:
            items = [i for i in items if i.kind == list_kind]
        if list_sheet:
            items = [
                i for i in items if i.provenance is not None and i.provenance.sheet_id == list_sheet
            ]
        items.sort(key=lambda i: (i.provenance.sheet_id if i.provenance else "", i.kind))
        if not items:
            print("No pending review items.")
            sys.exit(0)
        if args.format == "json":
            import json

            import review.review_report as review_report

            lvl_idx = review_report._level_index(model)
            print(
                json.dumps(
                    [
                        _review_item_to_dict(item, review_report._item_level(model, item, lvl_idx))
                        for item in items
                    ],
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


def _review_item_to_dict(item: ReviewItem, level: str | None = None) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "sheet": item.provenance.sheet_id if item.provenance else None,
        "confidence": item.confidence,
        "description": item.description,
        "status": item.status if item.status else None,
        "level": level,
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
    parser.add_argument(
        "--apply",
        metavar="DECISIONS",
        help="Replay a decisions.json exported from the HTML review report",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="With --apply: write the reviewed model here instead of in place",
    )
    parser.add_argument(
        "--report",
        metavar="DIR",
        help="Write the HTML review report to DIR/review (model, decisions template, page)",
    )
    parser.add_argument(
        "--telemetry",
        action="store_true",
        help="grant consent for this review session's anonymous telemetry payload (#1003); "
        "off by default. The payload lands beside the model as telemetry.json; "
        "nothing is transmitted",
    )
    parser.add_argument(
        "--undo",
        metavar="N",
        type=int,
        help="Undo the last N accepted corrections (corrections.json, #997)",
    )
    parser.add_argument(
        "--revert-to-auto",
        metavar="KEY",
        help="Put one field back to the automatic output; KEY is KIND:ID:FIELD (#997)",
    )
    parser.add_argument(
        "--corrections",
        action="store_true",
        help="Print the correction history beside the model (#997)",
    )
    parser.add_argument(
        "--bulk-confirm",
        action="store_true",
        help="Confirm every OPEN item matching the bulk filters (#996)",
    )
    parser.add_argument(
        "--bulk-reject",
        action="store_true",
        help="Reject every OPEN item matching the bulk filters (#996)",
    )
    parser.add_argument(
        "--kind",
        metavar="KIND",
        help="Bulk filter: review-item kind, exact match",
    )
    parser.add_argument(
        "--conf-above",
        metavar="MIN",
        type=float,
        help="Bulk filter: confidence >= MIN",
    )
    parser.add_argument(
        "--conf-below",
        metavar="MAX",
        type=float,
        help="Bulk filter: confidence <= MAX",
    )
    parser.add_argument(
        "--level",
        metavar="LEVEL",
        help="Bulk filter: level id (floor) the item's target belongs to",
    )
    parser.add_argument(
        "--confirm-kind",
        metavar="KIND",
        help="Batch: confirm every OPEN item of this kind and record the decisions (#1294)",
    )
    parser.add_argument(
        "--reject-kind",
        metavar="KIND",
        help="Batch: reject every OPEN item of this kind and record the decisions (#1294)",
    )
    parser.add_argument(
        "--confirm-sheet",
        metavar="SHEET",
        help="Batch: confirm every OPEN item from this sheet and record the decisions (#1294)",
    )
    parser.add_argument(
        "--reject-sheet",
        metavar="SHEET",
        help="Batch: reject every OPEN item from this sheet and record the decisions (#1294)",
    )
    parser.add_argument(
        "--decisions-out",
        metavar="PATH",
        help="With a batch confirm/reject: write the decisions file here "
        "(default: review_decisions.json beside the model)",
    )
    parser.add_argument(
        "--list-kind",
        metavar="KIND",
        help="With --list: only list items of this kind",
    )
    parser.add_argument(
        "--list-sheet",
        metavar="SHEET",
        help="With --list: only list items from this sheet",
    )
    return parser


if __name__ == "__main__":
    main()
