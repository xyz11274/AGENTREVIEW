#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$PROJECT_DIR"

: "${OPENAI_API_KEY:?OPENAI_API_KEY is not set. Export it before running this script.}"
: "${OPENAI_API_BASE:?OPENAI_API_BASE is not set. Export it before running this script.}"

PYTHON_BIN=${PYTHON_BIN:-/data0/xyj/miniconda3/envs/env_shengao/bin/python}
: "${OPENREVIEW_ROOT:?Set OPENREVIEW_ROOT to the external OpenReview data root}"
: "${COCOREVIEWBENCH_ROOT:?Set COCOREVIEWBENCH_ROOT to the external CoCo root}"
PILOT_DATA_ROOT=${PILOT_DATA_ROOT:-"$PROJECT_DIR/data/pilot_full"}
PILOT_RUN_ROOT=${PILOT_RUN_ROOT:-"$PROJECT_DIR/runs/pilot_full"}
PILOT_ITERATIONS=${PILOT_ITERATIONS:-2}
PILOT_STATIC_SIZE=${PILOT_STATIC_SIZE:-2}
PILOT_ROTATING_SIZE=${PILOT_ROTATING_SIZE:-2}
PILOT_EPISODE_BUDGET=${PILOT_EPISODE_BUDGET:-10}
PILOT_SELECTION_REPEATS=${PILOT_SELECTION_REPEATS:-2}
PILOT_SELECTION_TOP_K=${PILOT_SELECTION_TOP_K:-2}
PILOT_EXPERIMENT_SEEDS=${PILOT_EXPERIMENT_SEEDS:-0}
PILOT_PRIMARY_SEED=${PILOT_PRIMARY_SEED:-0}
PILOT_FOLDS=${PILOT_FOLDS:-3}
PILOT_API_REQUEST_TIMEOUT_SECONDS=${PILOT_API_REQUEST_TIMEOUT_SECONDS:-120}
PILOT_API_CONNECT_TIMEOUT_SECONDS=${PILOT_API_CONNECT_TIMEOUT_SECONDS:-15}
PILOT_API_MIN_INTERVAL_SECONDS=${PILOT_API_MIN_INTERVAL_SECONDS:-2}

export AGENTREVIEW_API_MIN_INTERVAL_SECONDS="$PILOT_API_MIN_INTERVAL_SECONDS"

if [ -e "$PILOT_RUN_ROOT" ]; then
    echo "Run directory already exists; choose a fresh PILOT_RUN_ROOT:" >&2
    echo "  $PILOT_RUN_ROOT" >&2
    exit 2
fi

if [ ! -f "$PILOT_DATA_ROOT/manifest.json" ]; then
    echo "[PILOT] Preparing small specialist datasets..."
    "$PYTHON_BIN" -m memory_evolution.cli prepare-specialists \
        --openreview-root "$OPENREVIEW_ROOT" \
        --coco-root "$COCOREVIEWBENCH_ROOT" \
        --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
        --output-dir "$PILOT_DATA_ROOT" \
        --max-train-families 30 \
        --validation-families 20 \
        --meta-validation-families 6 \
        --max-paper-chars 12000 \
        --seed 0
else
    echo "[PILOT] Reusing prepared data: $PILOT_DATA_ROOT"
fi

echo "[PILOT] Evolving QUAL/CLAR/SIGN/ORIG/POL..."
echo "[PILOT] API timeouts: connect=${PILOT_API_CONNECT_TIMEOUT_SECONDS}s read=${PILOT_API_REQUEST_TIMEOUT_SECONDS}s"
"$PYTHON_BIN" -m memory_evolution.cli evolve-specialists \
    --data-root "$PILOT_DATA_ROOT" \
    --output "$PILOT_RUN_ROOT" \
    --experiment-seeds "$PILOT_EXPERIMENT_SEEDS" \
    --primary-seed "$PILOT_PRIMARY_SEED" \
    --pilot \
    --iterations "$PILOT_ITERATIONS" \
    --static-size "$PILOT_STATIC_SIZE" \
    --rotating-size "$PILOT_ROTATING_SIZE" \
    --episode-budget "$PILOT_EPISODE_BUDGET" \
    --selection-repeats "$PILOT_SELECTION_REPEATS" \
    --selection-top-k "$PILOT_SELECTION_TOP_K" \
    --api-request-timeout-seconds "$PILOT_API_REQUEST_TIMEOUT_SECONDS" \
    --api-connect-timeout-seconds "$PILOT_API_CONNECT_TIMEOUT_SECONDS"

META_INPUTS="$PILOT_DATA_ROOT/meta_inputs.jsonl"
echo "[PILOT] Generating leakage-safe specialist inputs for META..."
"$PYTHON_BIN" -m memory_evolution.cli cache-meta-inputs \
    --data-root "$PILOT_DATA_ROOT" \
    --program-root "$PILOT_RUN_ROOT" \
    --output "$META_INPUTS" \
    --folds "$PILOT_FOLDS" \
    --pilot \
    --api-request-timeout-seconds "$PILOT_API_REQUEST_TIMEOUT_SECONDS" \
    --api-connect-timeout-seconds "$PILOT_API_CONNECT_TIMEOUT_SECONDS"

echo "[PILOT] Preparing META evolution data..."
"$PYTHON_BIN" -m memory_evolution.cli prepare-meta \
    --openreview-root "$OPENREVIEW_ROOT" \
    --specialist-outputs "$META_INPUTS" \
    --output-dir "$PILOT_DATA_ROOT"

echo "[PILOT] Evolving META..."
"$PYTHON_BIN" -m memory_evolution.cli evolve-meta \
    --data-root "$PILOT_DATA_ROOT" \
    --output "$PILOT_RUN_ROOT" \
    --experiment-seeds "$PILOT_EXPERIMENT_SEEDS" \
    --primary-seed "$PILOT_PRIMARY_SEED" \
    --pilot \
    --iterations "$PILOT_ITERATIONS" \
    --static-size "$PILOT_STATIC_SIZE" \
    --rotating-size "$PILOT_ROTATING_SIZE" \
    --episode-budget "$PILOT_EPISODE_BUDGET" \
    --selection-repeats "$PILOT_SELECTION_REPEATS" \
    --selection-top-k "$PILOT_SELECTION_TOP_K" \
    --api-request-timeout-seconds "$PILOT_API_REQUEST_TIMEOUT_SECONDS" \
    --api-connect-timeout-seconds "$PILOT_API_CONNECT_TIMEOUT_SECONDS"

echo "[PILOT] Evolution summary:"
"$PYTHON_BIN" -m memory_evolution.pilot_report \
    --run-root "$PILOT_RUN_ROOT" \
    --seed "$PILOT_PRIMARY_SEED"

echo "[PILOT] Complete: $PILOT_RUN_ROOT"
