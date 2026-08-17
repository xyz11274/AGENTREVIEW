cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python

export OPENAI_API_KEY="sk-B0bnEYxbAt1egxDdEweM6WP8DgJxtYmStTuS0KMsUXKWbQTe"
export OPENAI_API_BASE="https://yunwu.ai/v1"



$PY -m memory_evolution.cli evolve-specialists \
  --data-root data/pilot \
  --output runs/pilot_suite \
  --experiment-seeds 0 \
  --primary-seed 0 \
  --smoke
