"""MNIST evaluation: Section 3 of the paper.

Claims: 93.89% test accuracy, 0.195 s training (60k), 5.10 us/image inference.
"""

import json
import time
from pathlib import Path

import numpy as np

from jesse import WisardClassifier, com_normalize, make_tuple_indices


def main(data_dir: str = "data", out_path: str = "mnist_results.json"):
    """Run the Section 3 MNIST evaluation (needs data/mnist_X.npy + mnist_y.npy)."""
    data = Path(data_dir)
    _mnist_x = data / "mnist_X.npy"
    _mnist_y = data / "mnist_y.npy"
    if not _mnist_x.exists() or not _mnist_y.exists():
        raise FileNotFoundError(
            f"MNIST data not found in {data.resolve()}; expected mnist_X.npy and "
            "mnist_y.npy. This demo is not part of the test suite (see AGENTS.md Never-list)."
        )
    X = np.load(str(data / "mnist_X.npy")).astype(np.float64)  # (70000, 784)
    y = np.load(str(data / "mnist_y.npy")).astype(np.int64)
    Xtr, ytr = X[:60000].reshape(-1, 28, 28), y[:60000]  # standard split order
    Xte, yte = X[60000:].reshape(-1, 28, 28), y[60000:]
    print(f"train {Xtr.shape} test {Xte.shape}", flush=True)

    tidx = make_tuple_indices(28, 28, seed=42)
    clf = WisardClassifier(10, tuple_idx=tidx)

    t0 = time.perf_counter()
    clf.fit(Xtr, ytr)
    t_train = time.perf_counter() - t0
    print(f"train time: {t_train:.3f}s ({len(Xtr) / t_train:,.0f} img/s)", flush=True)

    results = {"train_time_s": t_train, "n_train": len(Xtr), "n_test": len(Xte)}

    # latency on a warm subset, per-image
    for name, fn in [("sum", clf.predict_sum), ("logodds", clf.predict_logodds)]:
        fn(Xte[:50])  # warm
        t0 = time.perf_counter()
        pred = fn(Xte)
        t_inf = time.perf_counter() - t0
        acc = float((pred == yte).mean())
        print(
            f"{name}: acc={acc * 100:.2f}%  infer={t_inf / len(Xte) * 1e6:.2f} us/img", flush=True
        )
        results[name] = {"acc": acc, "us_per_img": t_inf / len(Xte) * 1e6}

    # COM-normalized variant (Sections 4.1/6.3) on a 2k subset for speed
    sub = 2000
    Xtr_c = np.stack([com_normalize(im) for im in Xtr[:20000]])
    Xte_c = np.stack([com_normalize(im) for im in Xte[:sub]])
    clf2 = WisardClassifier(10, tuple_idx=tidx)
    clf2.fit(Xtr_c, ytr[:20000])
    acc_c = float((clf2.predict_sum(Xte_c) == yte[:sub]).mean())
    print(f"sum+COMnorm (20k train / 2k test): acc={acc_c * 100:.2f}%", flush=True)
    results["sum_comnorm_20k"] = {"acc": acc_c}

    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
