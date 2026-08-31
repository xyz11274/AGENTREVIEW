#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_DIR"

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY (dummy is valid for local vLLM)}"
OPENAI_BASE_URL=${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}
: "${OPENAI_BASE_URL:?Set OPENAI_BASE_URL to the process-bound local vLLM endpoint}"
export OPENAI_BASE_URL

: "${OPENREVIEW_ROOT:?Set OPENREVIEW_ROOT to the external OpenReview data root}"
: "${COCOREVIEWBENCH_ROOT:?Set COCOREVIEWBENCH_ROOT to the external CoCo root}"

PYTHON_BIN=${PYTHON_BIN:-/data0/xyj/miniconda3/envs/env_shengao/bin/python}
SMOKE_DATA_ROOT=${SMOKE_DATA_ROOT:-"$PROJECT_DIR/data/specialist_smoke"}
SMOKE_RUN_ROOT=${SMOKE_RUN_ROOT:-"$PROJECT_DIR/runs/specialist_smoke"}
TEMPORAL_MANIFEST="$PROJECT_DIR/configs/coco_temporal_split_v2/manifest.json"
FROZEN_S2_POOL="$PROJECT_DIR/configs/frozen_s2_temporal_v2.jsonl"

if [ -e "$SMOKE_RUN_ROOT" ]; then
  echo "Smoke run directory already exists; choose a fresh SMOKE_RUN_ROOT:" >&2
  echo "  $SMOKE_RUN_ROOT" >&2
  exit 2
fi

if [ -f "$SMOKE_DATA_ROOT/manifest.json" ]; then
  echo "[SMOKE] Reusing prepared temporal-v2 data: $SMOKE_DATA_ROOT"
else
  echo "[SMOKE] Preparing temporal-v2 specialist data..."
  "$PYTHON_BIN" -m memory_evolution.cli prepare-specialists \
    --openreview-root "$OPENREVIEW_ROOT" \
    --coco-root "$COCOREVIEWBENCH_ROOT" \
    --temporal-split-manifest "$TEMPORAL_MANIFEST" \
    --s2-cache "$FROZEN_S2_POOL" \
    --output-dir "$SMOKE_DATA_ROOT" \
    --max-train-families 10 \
    --validation-families 5 \
    --meta-validation-families 1 \
    --max-paper-chars 12000 \
    --seed 0
fi

"$PYTHON_BIN" -m memory_evolution.cli evolve-specialists \
  --data-root "$SMOKE_DATA_ROOT" \
  --output "$SMOKE_RUN_ROOT" \
  --experiment-seeds 0 \
  --primary-seed 0 \
  --openai-base-url "$OPENAI_BASE_URL" \
  --s2-retrieval-pool "$FROZEN_S2_POOL" \
  --smoke

echo "[SMOKE] Complete: $SMOKE_RUN_ROOT"
