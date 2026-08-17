cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python

export OPENAI_API_KEY="sk-B0bnEYxbAt1egxDdEweM6WP8DgJxtYmStTuS0KMsUXKWbQTe"
export OPENAI_API_BASE="https://yunwu.ai/v1"

$PY -m unittest discover -s tests -v

$PY -m memory_evolution.cli prepare-specialists \
  --openreview-root /data0/xyj/shengao/OpenReview \
  --output-dir data/pilot \
  --max-train-families 30 \
  --validation-families 6 \
  --meta-validation-families 6 \
  --max-paper-chars 12000 \
  --seed 0

$PY -m memory_evolution.cli evolve-task \
  --task-id QUAL \
  --data-root data/pilot \
  --output runs/pilot_qual \
  --smoke \
  --seed 0
