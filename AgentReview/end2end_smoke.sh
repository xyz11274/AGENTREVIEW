cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python

: "${OPENAI_API_KEY:?Set OPENAI_API_KEY (dummy is valid for local vLLM)}"
OPENAI_BASE_URL=${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}
: "${OPENAI_BASE_URL:?Set OPENAI_BASE_URL to the process-bound local vLLM endpoint}"
export OPENAI_BASE_URL

$PY -m memory_evolution.cli evolve-specialists \
  --data-root data/pilot \
  --output runs/pilot_suite \
  --experiment-seeds 0 \
  --primary-seed 0 \
  --openai-base-url "$OPENAI_BASE_URL" \
  --smoke
