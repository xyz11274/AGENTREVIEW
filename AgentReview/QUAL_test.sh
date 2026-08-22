cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python

: "${OPENAI_API_KEY:?Export OPENAI_API_KEY before running this script}"
OPENAI_BASE_URL=${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}
: "${OPENAI_BASE_URL:?Export OPENAI_BASE_URL for the local vLLM replica}"
export OPENAI_BASE_URL
: "${OPENREVIEW_ROOT:?Set OPENREVIEW_ROOT to the external OpenReview data root}"
: "${COCOREVIEWBENCH_ROOT:?Set COCOREVIEWBENCH_ROOT to the external CoCo root}"

$PY -m unittest discover -s tests -v

$PY -m memory_evolution.cli prepare-specialists \
  --openreview-root "$OPENREVIEW_ROOT" \
  --coco-root "$COCOREVIEWBENCH_ROOT" \
  --temporal-split-manifest configs/coco_temporal_split_v2/manifest.json \
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
  --openai-base-url "$OPENAI_BASE_URL" \
  --smoke \
  --seed 0
