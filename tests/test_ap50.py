"""AP50 metric math (#1119): pure Python, no torch required."""

from detector.ap50 import average_precision, mean_average_precision


def test_perfect_detector_is_1():
    assert average_precision([(1.0, True), (0.9, True)], num_gt=2) == 1.0


def test_all_false_positives_is_0():
    assert average_precision([(0.9, False)], num_gt=2) == 0.0


def test_no_ground_truth_is_none():
    assert average_precision([(0.9, True)], num_gt=0) is None


def test_ground_truth_without_predictions_is_0():
    assert average_precision([], num_gt=3) == 0.0


def test_hand_computed_case_is_five_sixths():
    # T, F, T with 2 gt: thresholds give (P,R) = (1.0, 0.5), (0.5, 0.5),
    # (2/3, 1.0); the all-point envelope is 1.0 up to recall 0.5 and 2/3
    # from there to 1.0, so AP = 1/2 + 1/3 = 5/6.
    ap = average_precision([(0.9, True), (0.8, False), (0.7, True)], num_gt=2)
    assert ap is not None and abs(ap - 5 / 6) < 1e-9


def test_confidence_ordering_matters():
    # Same counts, different confidence order: TP-first holds precision
    # at 1.0 across the whole recall range (AP 1.0); FP-first caps the
    # envelope at 0.5 (AP 0.5), so the ordering genuinely changes the score.
    good = average_precision([(0.9, True), (0.8, True), (0.7, False), (0.6, False)], num_gt=2)
    bad = average_precision([(0.9, False), (0.8, False), (0.7, True), (0.6, True)], num_gt=2)
    assert good == 1.0 and bad == 0.5


def test_ties_keep_caller_order():
    # Stable sort: equal confidences keep the caller's order, which makes
    # the metric deterministic across runs for a given detection list.
    # With one gt, TP-first holds precision at 1.0 (AP 1.0); FP-first can
    # only reach precision 0.5 (AP 0.5), since the envelope cannot exceed
    # the best precision actually reached.
    a = average_precision([(0.5, True), (0.5, False)], num_gt=1)
    b = average_precision([(0.5, False), (0.5, True)], num_gt=1)
    assert a == 1.0 and b == 0.5


def test_mean_skips_undefined_classes():
    per_class = {"door": {"ap50": 0.75}, "window": {"ap50": None}}
    assert mean_average_precision(per_class) == 0.75
    assert mean_average_precision({"door": {"ap50": None}}) is None
