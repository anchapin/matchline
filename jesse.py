"""Jesse-Vision prototype: zero-weight n-tuple (WiSARD) classifier.

Implements the core of https://jesse.solidsf.com/paper ("Zero-Weight Optical
Recognition ... via One-Hot Pixel String Tuple Addressing and Empirical
Bayesian Beliefs") from the paper text alone. numpy only, no torch.

Paper spec (Section 2):
  B(x,y) = (I[I(x,y) > 35], I[I(x,y) > 120])            (thermometer encoding)
  a_k    = sum_{s=0}^{n-1} B[tau_{k,s}] * 2^s in [0, 2^n - 1]
  K = 160 tuples, n = 10 bits, neighborhoods of radius 4 around centers.
  160 * 1024 = 163,840 integer slots per class; training = one streaming pass
  incrementing counters (no gradients, no floats).
"""

from __future__ import annotations

import re

import numpy as np

# --- Paper Section 2: thresholds ------------------------------------------------
THRESH_LOW = 35
THRESH_HIGH = 120

# --- Paper Section 2: tuple geometry ---------------------------------------------
K_TUPLES = 160
N_BITS = 10
NEIGHBORHOOD_RADIUS = 4
SLOT_COUNT = 1 << N_BITS  # 1024


def thermometer_encode(img: np.ndarray) -> np.ndarray:
    """Section 2, Eq. B(x,y): 2-bit thermometer encoding of a grayscale image.

    Returns flat bit vector of length H*W*2 (28*28*2 = 1568 for MNIST).
    """
    img = np.asarray(img, dtype=np.float64)
    low = (img > THRESH_LOW).astype(np.uint8).ravel()
    high = (img > THRESH_HIGH).astype(np.uint8).ravel()
    return np.concatenate([low, high])


def thermometer_encode_batch(images: np.ndarray) -> np.ndarray:
    """Vectorized Section 2 encoding: (N, H, W) -> (N, 2*H*W) uint8 bits.

    Bit-identical to stacking thermometer_encode per image; one pass, no
    Python loop.
    """
    images = np.asarray(images)
    n = images.shape[0]
    low = (images > THRESH_LOW).astype(np.uint8).reshape(n, -1)
    high = (images > THRESH_HIGH).astype(np.uint8).reshape(n, -1)
    return np.concatenate([low, high], axis=1)


def make_tuple_indices(h: int, w: int, seed: int = 42) -> np.ndarray:
    """Section 2: K=160 tuples of n=10 bits sampled in localized radius-4
    neighborhoods.

    [UNSPECIFIED in paper: exact center placement and intra-neighborhood sampling
    rule.] Best-guess choice: 160 centers on a regular 10x16 grid spanning the
    field; each tuple samples 10 (pixel, threshold-channel) pairs uniformly
    without replacement from the 9x9 neighborhood (both threshold channels),
    clipped at image borders, with a fixed RNG seed for determinism.

    Returns int array of shape (K, n) indexing into the flat thermometer vector.
    """
    rng = np.random.default_rng(seed)
    rows = np.linspace(NEIGHBORHOOD_RADIUS // 2 + 1, h - 2, 10)
    cols = np.linspace(NEIGHBORHOOD_RADIUS // 2 + 1, w - 2, 16)
    centers = [(r, c) for r in rows for c in cols]
    assert len(centers) == K_TUPLES
    idx = np.zeros((K_TUPLES, N_BITS), dtype=np.int64)
    for k, (cr, cc) in enumerate(centers):
        r0 = max(int(round(cr)) - NEIGHBORHOOD_RADIUS, 0)
        r1 = min(int(round(cr)) + NEIGHBORHOOD_RADIUS, h - 1)
        c0 = max(int(round(cc)) - NEIGHBORHOOD_RADIUS, 0)
        c1 = min(int(round(cc)) + NEIGHBORHOOD_RADIUS, w - 1)
        cand = []
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                base = r * w + c
                cand.append(base)  # low-threshold channel bit
                cand.append(base + h * w)  # high-threshold channel bit
        cand = np.asarray(cand, dtype=np.int64)
        idx[k] = rng.choice(cand, size=N_BITS, replace=False)
    return idx


_POWERS = (1 << np.arange(N_BITS)).astype(np.int64)  # 2^s weights


def tuple_addresses(bitvecs: np.ndarray, tuple_idx: np.ndarray) -> np.ndarray:
    """Section 2, Eq. a_k = sum_s B[tau_{k,s}] * 2^s.

    bitvecs: (N, D) binary; tuple_idx: (K, n). Returns (N, K) addresses.
    einsum fuses the gather+weighted-sum without materializing an int64
    (N, K, n) temporary.
    """
    return np.einsum("nki,i->nk", bitvecs[:, tuple_idx], _POWERS).astype(np.int64)


class WisardClassifier:
    """Per-class discriminators: RAM_c[k, a] integer counters (Sec. 2).

    fit(): one streaming pass, RAM_c[k, a_k] += 1 per training sample.
    predict(): argmax over class response scores.
    """

    def __init__(
        self,
        n_classes: int,
        tuple_idx: np.ndarray | None = None,
        h: int = 28,
        w: int = 28,
        seed: int = 42,
    ):
        self.n_classes = n_classes
        self.h, self.w = h, w
        self.tuple_idx = tuple_idx if tuple_idx is not None else make_tuple_indices(h, w, seed)
        k = self.tuple_idx.shape[0]
        self.ram = np.zeros((n_classes, k, SLOT_COUNT), dtype=np.int64)

    # -- training -----------------------------------------------------------
    def fit(self, images: np.ndarray, labels: np.ndarray) -> None:
        """Single streaming pass. images: (N,H,W) grayscale 0..255.

        Vectorized: one np.add.at per class replaces the per-tuple bincount
        loop. Counts are identical (verified against bincount in tests).
        """
        bits = thermometer_encode_batch(images)
        addrs = tuple_addresses(bits, self.tuple_idx)  # (N, K)
        k = self.tuple_idx.shape[0]
        rows = np.arange(k)[:, None]  # (K, 1) broadcasts against (K, M)
        for c in range(self.n_classes):
            a = addrs[labels == c]  # (M, K)
            if len(a):
                np.add.at(self.ram[c], (rows, a.T), 1)

    # -- inference ----------------------------------------------------------
    def _responses(self, images: np.ndarray) -> np.ndarray:
        """Raw counter values per class/tuple, shape (C, N, K).

        Vectorized: single advanced-index gather over the whole (C, K, S)
        RAM with vals[c, n, k] = ram[c, k, addr[n, k]]. Bit-identical to the
        old per-class loop; no Python loops at all.
        """
        bits = thermometer_encode_batch(images)
        addrs = tuple_addresses(bits, self.tuple_idx)  # (N, K)
        c = self.n_classes
        k = self.tuple_idx.shape[0]
        return self.ram[
            np.arange(c)[:, None, None],
            np.arange(k)[None, None, :],
            addrs[None, :, :],
        ]

    def _gather_addrs(self, images: np.ndarray) -> np.ndarray:
        """Shared front-end: (N,H,W) -> (N, K) tuple addresses."""
        bits = thermometer_encode_batch(images)
        return tuple_addresses(bits, self.tuple_idx)

    def predict_sum(self, images: np.ndarray) -> np.ndarray:
        """Variant A: plain WiSARD response score_c = sum_k RAM_c[k, a_k].

        [UNSPECIFIED: paper says 'Empirical Bayesian Beliefs' / 'empirical
        log-odds emission E(t,d)' but gives no formula. Plain sum is the
        canonical WiSARD response and a special case of bleaching with b=0.]

        Per-class accumulation avoids materializing the full (C, N, K)
        response tensor; integer arithmetic is bit-identical to the
        tensor formulation.
        """
        addrs = self._gather_addrs(images)  # (N, K)
        n = len(images)
        k = self.tuple_idx.shape[0]
        kk = np.arange(k)
        scores = np.zeros((self.n_classes, n), dtype=np.int64)
        for c in range(self.n_classes):
            # (K, N) orientation: no transpose copy needed
            scores[c] = self.ram[c][kk[:, None], addrs.T].sum(axis=0)
        return scores.argmax(axis=0)

    def predict_logodds(self, images: np.ndarray, alpha: float = 1.0) -> np.ndarray:
        """Variant B: empirical log-odds vs. class-average (my reading of the
        paper's 'empirical log-odds emission' / 'Bayesian beliefs').

        E_c = sum_k log( (RAM_c[k,a_k] + a) / (mean_{c'} RAM_{c'}[k,a_k] + a) )

        Reformulated as sum_k log(RAM_c+a) - sum_k log(mean+a): the second
        term is class-independent. Per-class accumulation keeps only (N, K)
        temporaries instead of the full (C, N, K) tensor.
        """
        addrs = self._gather_addrs(images)  # (N, K)
        n = len(images)
        k = self.tuple_idx.shape[0]
        c = self.n_classes
        kk = np.arange(k)
        log_ram = np.zeros((c, n))  # [c, n] = sum_k log(ram_c[k,a]+alpha)
        sum_ram = np.zeros((n, k))  # [n, k] = sum_c ram_c[k,a]
        for cc in range(c):
            # (N, K) orientation: contiguous reduction axis for the log-sum
            g = self.ram[cc][kk[:, None], addrs.T].T
            sum_ram += g
            log_ram[cc] = np.log(g + alpha).sum(axis=1)
        bg = np.log(sum_ram / c + alpha).sum(axis=1)  # (N,) class-indep. term
        return (log_ram - bg[None, :]).argmax(axis=0)

    def predict_bleach(self, images: np.ndarray, b: int = 0) -> np.ndarray:
        """Variant C: bleaching. score_c = #{k : RAM_c[k,a_k] > b}; ties broken
        by raising b. [UNSPECIFIED: bleaching not described in paper text.]
        """
        vals = self._responses(images)
        n = len(images)
        out = np.full(n, -1)
        pending = np.arange(n)
        while len(pending):
            v = vals[:, pending, :]  # (C, P, K)
            scores = (v > b).sum(axis=2)  # (C, P)
            best = scores.argmax(axis=0)
            top = scores.max(axis=0)
            # unique winner?
            nunique = (scores == top[None, :]).sum(axis=0)
            decided = pending[nunique == 1]
            out[decided] = best[nunique == 1]
            pending = pending[nunique > 1]
            b += 1
            if b > vals.max():
                out[pending] = (
                    best[(scores == top[None, :]).argmax(axis=0)][nunique > 1]
                    if len(pending)
                    else out[pending]
                )
                break
        return out


# --- Section 4.1 / 6.3: center-of-mass normalization --------------------------------
def com_normalize(img: np.ndarray, size: int = 28) -> np.ndarray:
    """Shift image so its intensity center of mass sits at (size/2, size/2).

    c_x = sum x*I / sum I, c_y likewise; (dx,dy) = (size/2 - c_x, size/2 - c_y).
    Integer shift via np.roll (paper implies continuous; integer is the
    zero-weight-friendly choice). [UNSPECIFIED: interpolation vs integer shift.]
    """
    img = np.asarray(img, dtype=np.float64)
    total = img.sum()
    if total == 0:
        return img
    yy, xx = np.mgrid[0 : img.shape[0], 0 : img.shape[1]]
    cx = (xx * img).sum() / total
    cy = (yy * img).sum() / total
    dx = int(round(size / 2 - cx))
    dy = int(round(size / 2 - cy))
    return np.roll(np.roll(img, dy, axis=0), dx, axis=1)


# --- Section 9.2: Zhang-Suen thinning ------------------------------------------------
def zhang_suen(binary: np.ndarray) -> np.ndarray:
    """1-pixel skeletonization. binary: 0/1 foreground array.

    Sub-iteration 1: 2<=B(p1)<=6, A(p1)=1, p2*p4*p6=0, p4*p6*p8=0
    Sub-iteration 2: 2<=B(p1)<=6, A(p1)=1, p2*p4*p8=0, p2*p6*p8=0
    Neighbor order (clockwise from north): p2=N, p3=NE, p4=E, p5=SE,
    p6=S, p7=SW, p8=W, p9=NW. [Paper gives conditions but not the neighbor
    indexing; this is the standard Zhang-Suen convention.]
    """
    img = (np.asarray(binary) > 0).astype(np.uint8)
    img = np.pad(img, 1)
    changed = True
    while changed:
        changed = False
        for step in (1, 2):
            p2 = img[:-2, 1:-1]
            p3 = img[:-2, 2:]
            p4 = img[1:-1, 2:]
            p5 = img[2:, 2:]
            p6 = img[2:, 1:-1]
            p7 = img[2:, :-2]
            p8 = img[1:-1, :-2]
            p9 = img[:-2, :-2]
            nbrs = np.stack([p2, p3, p4, p5, p6, p7, p8, p9], axis=0)
            B = nbrs.sum(axis=0)
            seq = np.concatenate([nbrs, nbrs[:1]], axis=0)  # p2..p9,p2
            A = ((seq[:-1] == 0) & (seq[1:] == 1)).sum(axis=0)
            if step == 1:
                cond = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            kill = (img[1:-1, 1:-1] == 1) & (B >= 2) & (B <= 6) & (A == 1) & cond
            if kill.any():
                img[1:-1, 1:-1][kill] = 0
                changed = True
    return img[1:-1, 1:-1]


# --- Section 9.2: topological invariants ----------------------------------------------
def skeleton_invariants(skel: np.ndarray) -> dict:
    """d(p) = sum of active 8-neighbors; endpoints d=1, T-junctions d=3,
    cross d>=4; holes b_1 via background flood fill; chi = 1 - b_1 for a
    single connected foreground component."""
    from collections import deque

    sk = (np.asarray(skel) > 0).astype(np.uint8)
    h, w = sk.shape
    padded = np.pad(sk, 1)
    # 8-connectivity degree keeps digital curves (e.g. Bresenham circles)
    # continuous; diagonal-only contacts would read as endpoints under
    # 4-connectivity.
    deg = (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    )
    deg = deg * sk
    endpoints = int(((deg == 1) & (sk == 1)).sum())
    # Merge 8-connected clusters of junction pixels (deg>=3) into single
    # junction nodes; classify by number of curve branches leaving the
    # cluster. [UNSPECIFIED in paper: no junction-counting convention given;
    # this is the convention that reproduces Table 9.4 for simple glyphs.]
    is_j = (deg >= 3) & (sk == 1)
    seen = np.zeros_like(is_j, bool)
    t_junctions = x_junctions = 0
    for r in range(h):
        for c in range(w):
            if is_j[r, c] and not seen[r, c]:
                q = deque([(r, c)])
                seen[r, c] = True
                cluster = []
                while q:
                    y, x = q.popleft()
                    cluster.append((y, x))
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < h and 0 <= nx < w and is_j[ny, nx] and not seen[ny, nx]:
                                seen[ny, nx] = True
                                q.append((ny, nx))
                cset = set(cluster)
                branches = 0
                for y, x in cluster:
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < h and 0 <= nx < w and sk[ny, nx] and (ny, nx) not in cset:
                                branches += 1
                # each branch counted from its cluster pixel; a branch shared
                # by 2 cluster pixels double-counts -> use distinct directions
                # per cluster instead
                dirs = set()
                for y, x in cluster:
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < h and 0 <= nx < w and sk[ny, nx] and (ny, nx) not in cset:
                                dirs.add((np.sign(dy), np.sign(dx)))
                nb = len(dirs)
                if nb == 3:
                    t_junctions += 1
                elif nb >= 4:
                    x_junctions += 1
    # holes: background components not touching the border.
    # Foreground is 8-connected, so background must be 4-connected
    # (connectivity duality); otherwise bg leaks through diagonal gaps.
    bg = (sk == 0).astype(np.uint8)
    seen = np.zeros_like(bg, bool)
    holes = 0
    for r in range(h):
        for c in range(w):
            if bg[r, c] and not seen[r, c]:
                q = deque([(r, c)])
                seen[r, c] = True
                touches_border = False
                while q:
                    y, x = q.popleft()
                    if y == 0 or x == 0 or y == h - 1 or x == w - 1:
                        touches_border = True
                    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                if not touches_border:
                    holes += 1
    return {
        "endpoints": endpoints,
        "t_junctions": t_junctions,
        "x_junctions": x_junctions,
        "holes": holes,
        "chi": 1 - holes,
    }


def sliding_window_tag_extract(
    image: np.ndarray,
    detections: list,
    window_px: int = 80,
    stride_px: int = 20,
    ocr_lang: str = "eng",
) -> dict[int, str]:
    """Extract schedule tag text adjacent to each detected symbol.

    For each detection bbox, crops a window to the LEFT of the symbol
    (where schedule tags typically appear on drawings) and runs OCR.
    Returns {detection_index: tag_str}.

    Returns empty string for detections where no tag was read.
    """
    import pytesseract

    tags = {}
    for i, det in enumerate(detections):
        x0, y0, x1, y1 = det.bbox

        # Crop: left of symbol, same height, up to window_px wide
        tag_x0 = max(0, int(x0) - window_px)
        tag_y0 = max(0, int(y0))
        tag_x1 = max(0, int(x0) - 2)  # 2px gap from symbol edge
        tag_y1 = min(image.shape[1], int(y1))

        if tag_x1 <= tag_x0 or tag_y1 <= tag_y0:
            tags[i] = ""
            continue

        crop = image[tag_y0:tag_y1, tag_x0:tag_x1]
        if crop.size == 0:
            tags[i] = ""
            continue

        text = pytesseract.image_to_string(crop, lang=ocr_lang, config="--psm 6").strip()
        # Normalize: uppercase, remove spaces, extract tag pattern
        m = re.search(r"[A-Z]\d*-\d+|[A-Z]\d+", text.upper())
        tags[i] = m.group(0) if m else ""
    return tags


def tag_detections(
    detections: list,
    image: np.ndarray,
) -> list:
    """Fill .tag field on Detection objects using sliding-window OCR.

    Modifies detections in place and also returns them.
    """
    tags = sliding_window_tag_extract(image, detections)
    for i, det in enumerate(detections):
        det.tag = tags.get(i, "")
    return detections
