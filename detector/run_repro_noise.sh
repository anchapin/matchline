#!/usr/bin/env bash
# #516: measure run-to-run variance at FIXED seed and FIXED epoch count.
#
# Why this exists: #500's between-arm delta can only be judged against a noise
# floor, and no unconfounded floor has ever been measured on this hardware. The
# two runs cited on #509 (0.05485 and 0.21352 mAP50) differed in BOTH epochs
# (1 vs 50) and seed (seedtest_s0 is seed 0, seedtest_s1 is seed 1), so they
# measure an LR-schedule change, not run-to-run noise. Everything here is held
# constant except the repeat index.
#
# Naming: repro_seed0_r{0,1,2}. The old seedtest_s0/_s1 scheme was misread as
# "run A / run B" when it meant "seed 0 / seed 1"; _seed0_ and _r<i> cannot be
# confused. --seed is recorded in each run's args.yaml by train.py.
#
# GPU contention: this competes with the #500 matrix for the single device. Do
# NOT start it while the matrix is running -- queue it after the last row lands.
# The script refuses to start if a matrix run looks live (see the guard below).
#
# Usage:  bash detector/run_repro_noise.sh [EPOCHS] [REPEATS]
# Report: detector/.venv-det-rocm/bin/python detector/report_ab_stats.py

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/detector/.venv-det-rocm"
PY="$VENV/bin/python"
RUNS="${MATCHLINE_RUNS:-$HOME/workspace/datasets/detector_runs}"
EPOCHS="${1:-10}"
REPEATS="${2:-3}"

# Held constant across repeats -- identical to run_ab_eca.sh's controlled block,
# so the floor is measured under the same conditions as the matrix it judges.
ARM=yolo11n_baseline
SEED=0
BATCH=16
IMGSZ=640
WORKERS=6
DEVICE=0
PRETRAINED=yolo11n.pt
DATA=configs/cubicasa.yaml

if [[ ! -x "$PY" ]]; then
  echo "FATAL: $PY missing. See detector/requirements-detector-rocm.txt for the recreate recipe." >&2
  exit 1
fi

# --- GPU contention guard ---------------------------------------------------
# A matrix run whose results.csv was written in the last 5 minutes is assumed
# live. Override with MATCHLINE_FORCE=1 if you know the matrix is finished.
if [[ "${MATCHLINE_FORCE:-0}" != "1" ]]; then
  live="$(find "$RUNS" -maxdepth 2 -name results.csv -newermt '-5 minutes' \
            -path '*yolo11n_*' 2>/dev/null | head -1)"
  if [[ -n "$live" ]]; then
    echo "REFUSING: a matrix run looks live ($live modified <5 min ago)." >&2
    echo "  #516 must not contend with the #500 matrix for device $DEVICE." >&2
    echo "  Re-run after the last row lands, or set MATCHLINE_FORCE=1." >&2
    exit 2
  fi
fi

echo "[repro] arm=$ARM seed=$SEED epochs=$EPOCHS repeats=$REPEATS device=$DEVICE"
echo "[repro] CAVEAT: a floor measured at $EPOCHS epochs is not automatically"
echo "[repro] the floor at 50. Report the epoch count alongside the number."

for i in $(seq 0 $((REPEATS - 1))); do
  name="repro_seed${SEED}_r${i}"

  # Fresh run directories: #514 -- $RUNS/<name>/ collides across invocations, so
  # a pre-existing directory is an error here, not something to append into.
  if [[ -d "$RUNS/$name" ]]; then
    echo "[repro] SKIP $name (directory exists; delete it to re-measure)"
    continue
  fi

  echo "[repro] === $name : start $(date -Is) ==="
  ( cd "$REPO/detector" && "$PY" train.py \
      --model "configs/${ARM}.yaml" \
      --data "$DATA" \
      --epochs "$EPOCHS" \
      --batch "$BATCH" \
      --imgsz "$IMGSZ" \
      --workers "$WORKERS" \
      --device "$DEVICE" \
      --pretrained "$PRETRAINED" \
      --seed "$SEED" \
      --name "$name" ) 2>&1 \
    | tr '\r' '\n' \
    | grep -E "aligned transfer|CUDA:|epochs completed|Traceback|KeyError|Error|error:" \
    | tee -a "$RUNS/repro_$name.log"
  echo "[repro] === $name : done $(date -Is) (exit ${PIPESTATUS[0]}) ==="
done

echo "[repro] ===== all repeats finished $(date -Is) ====="
echo "[repro] Now report the spread:"
echo "  $PY $REPO/detector/report_ab_stats.py --runs $RUNS"
