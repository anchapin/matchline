#!/usr/bin/env bash
# #500 A/B: ECA channel attention on the YOLO11n backbone (FloorYOLO).
#
# Runs the full matrix — 2 arms x 3 seeds — and appends one summary row per run
# to ab_summary.csv. Everything except --model and --seed is held constant
# across arms, because the comparison is only meaningful if seed, split, epochs,
# batch, imgsz and initialisation are identical:
#
#   --pretrained yolo11n.pt   both arms start from the same COCO weights. This is
#                             the fairness-critical flag. Without it the arms get
#                             different inits and the A/B measures init, not ECA.
#                             For the ECA arm this also routes through
#                             eca.transfer_aligned (98.8% of params copied; the
#                             51 mismatched tensors are the nc 80 -> 2 Detect
#                             head), because plain YOLO.load() matches by
#                             parameter name and would match almost nothing.
#
# Three seeds, not one: a single run cannot support a +/-1pt mAP50 claim. Per-seed
# spread is reported so a between-arm delta can be compared against it.
#
# Resumable: a run whose results.csv already has EPOCHS rows is skipped, so this
# can be re-invoked after an interruption.
#
# Usage:  bash detector/run_ab_eca.sh [EPOCHS]
# Logs:   ~/workspace/datasets/detector_runs/<arm>_s<seed>/

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/detector/.venv-det-rocm"
PY="$VENV/bin/python"
RUNS="${MATCHLINE_RUNS:-$HOME/workspace/datasets/detector_runs}"
SUMMARY="$RUNS/ab_summary.csv"
EPOCHS="${1:-50}"

# --- the controlled constants. Change these only by changing BOTH arms. ---
BATCH=16
IMGSZ=640
WORKERS=6
DEVICE=0
PRETRAINED=yolo11n.pt
DATA=configs/cubicasa.yaml
SEEDS=(0 1 2)
ARMS=(yolo11n_baseline yolo11n_eca)

# ROCm torch lives in .venv-det-rocm; the CPU .venv-det must not be used here.
if [[ ! -x "$PY" ]]; then
  echo "FATAL: $PY missing. Build it with:" >&2
  echo "  python3 -m venv detector/.venv-det-rocm" >&2
  echo "  detector/.venv-det-rocm/bin/pip install torch==2.14.0+rocm7.2 torchvision==0.29.0+rocm7.2 \\" >&2
  echo "      --index-url https://download.pytorch.org/whl/rocm7.2" >&2
  echo "  detector/.venv-det-rocm/bin/pip install 'ultralytics>=8.4.155,<8.5'" >&2
  exit 1
fi

echo "[ab] epochs=$EPOCHS batch=$BATCH imgsz=$IMGSZ device=$DEVICE seeds=${SEEDS[*]} arms=${ARMS[*]}"
echo "[ab] summary -> $SUMMARY"

header_needed=1
[[ -f "$SUMMARY" ]] && header_needed=0

for seed in "${SEEDS[@]}"; do
  for arm in "${ARMS[@]}"; do
    name="${arm}_s${seed}"
    csv="$RUNS/$name/results.csv"

    if [[ -f "$csv" ]] && [[ "$(($(wc -l < "$csv") - 1))" -ge "$EPOCHS" ]]; then
      echo "[ab] skip $name (already $EPOCHS epochs)"
    else
      echo "[ab] === $name : start $(date -Is) ==="
      ( cd "$REPO/detector" && "$PY" train.py \
          --model "configs/${arm}.yaml" \
          --data "$DATA" \
          --epochs "$EPOCHS" \
          --batch "$BATCH" \
          --imgsz "$IMGSZ" \
          --workers "$WORKERS" \
          --device "$DEVICE" \
          --pretrained "$PRETRAINED" \
          --seed "$seed" \
          --name "$name" ) 2>&1 \
        | tr '\r' '\n' \
        | grep -E "aligned transfer|CUDA:|epochs completed|Traceback|KeyError|Error|error:" \
        | tee -a "$RUNS/ab_$name.log"
      echo "[ab] === $name : done $(date -Is) (exit ${PIPESTATUS[0]}) ==="

      # Append the final-epoch metrics for this run (only newly completed runs).
      if [[ -f "$csv" ]]; then
        if [[ $header_needed -eq 1 ]]; then
          echo "arm,seed,epochs,mAP50,mAP50_95,train_box_loss" > "$SUMMARY"
          header_needed=0
        fi
        "$PY" - "$csv" "$arm" "$seed" "$EPOCHS" >> "$SUMMARY" <<'PYEOF'
import csv, sys
path, arm, seed, epochs = sys.argv[1:5]
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
if not rows:
    sys.exit(f"no rows in {path}")
r = rows[-1]
print(f"{arm},{seed},{len(rows)},{r['metrics/mAP50(B)']},{r['metrics/mAP50-95(B)']},{r['train/box_loss']}")
PYEOF
      else
        echo "[ab] WARN $name produced no results.csv" >&2
      fi
    fi
  done
done

echo "[ab] ===== summary ====="
column -s, -t "$SUMMARY" 2>/dev/null || cat "$SUMMARY"
echo "[ab] all runs finished $(date -Is)"
