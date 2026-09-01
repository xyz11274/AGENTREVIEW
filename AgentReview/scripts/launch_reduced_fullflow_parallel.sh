#!/usr/bin/env bash
set -Eeuo pipefail

set +u
source /data/xyj/envs/env_shengao/bin/activate
if [[ -f /data/xyj/envs/qwen38_serve/bin/activate ]]; then
  source /data/xyj/envs/qwen38_serve/bin/activate
else
  source /data/xyj/envs/qwen38serve/bin/activate
fi
set -u

export CUDA_VISIBLE_DEVICES="${PARALLEL_EMBEDDING_GPU:-6}"
export OPENAI_API_KEY=dummy
export COCO_SPLIT_API_KEY=dummy
export COCO_CLASSIFY_API_KEY=dummy
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
# Four role workers share one CoCo adapter. Keep inner classification serial so
# the process-wide request fan-out remains bounded at approximately four.
export COCO_CLASSIFY_WORKERS="${PARALLEL_COCO_CLASSIFY_WORKERS:-1}"

PROJECT_ROOT=/data/xyj/shengao/AgentReview_0901_parallel/AgentReview
SOURCE_ROOT=/data/xyj/shengao/AgentReview_0831/AgentReview
PYTHON_BIN=/data/xyj/envs/env_shengao/bin/python
COCO_ROOT=/data/xyj/shengao/CoCoReviewBench_adapter_2580687

# Reduced inputs and frozen selection assets are reused read-only. The 0831
# reduced-v2 run root and runtime embedding cache are reused only because their
# formal identities are compatible; missing/uncommitted work is recomputed.
DATA_ROOT="$SOURCE_ROOT/data/0901_mstar_reduced_fullflow_a300_b50_v2"
TEST_ROOT="$SOURCE_ROOT/data/0901_mstar_formal_d_official_v2"
SELECTION_MANIFEST="$SOURCE_ROOT/runs/0901_mstar_full_reduced_runtime/reduced_selection_manifest_v2.json"
SELECTION_CACHE="$SOURCE_ROOT/cache/qwen3_embedding_8b_cache.sqlite3"
SELECTION_AUDIT="$SOURCE_ROOT/cache/qwen3_embedding_8b_cache.audit.json"
ASSET_MANIFEST="$SOURCE_ROOT/runs/0901_mstar_full_reduced_runtime/asset_manifest_v2.runtime.json"

RUN_ROOT="${PARALLEL_RUN_ROOT:-$SOURCE_ROOT/runs/0901_mstar_full_reduced_v2}"
BASELINE_ROOT="${PARALLEL_BASELINE_ROOT:-$SOURCE_ROOT/runs/0901_mstar_full_reduced_baseline_v2}"
RUNTIME_ROOT="${PARALLEL_RUNTIME_ROOT:-$PROJECT_ROOT/runs/0901_mstar_full_reduced_parallel_runtime}"
RUNTIME_CACHE="${PARALLEL_RUNTIME_CACHE:-$SOURCE_ROOT/runs/0901_mstar_full_reduced_runtime/qwen3_runtime_embedding.sqlite3}"
BASELINE_REPORT="$RUN_ROOT/d100_original_seed_coco_report.json"
EVOLVED_REPORT="$RUN_ROOT/d100_evolved_winner_coco_report.json"
SUMMARY="${PARALLEL_SUMMARY:-$PROJECT_ROOT/artifacts/reduced_fullflow_parallel_summary.json}"
LOG="$RUNTIME_ROOT/reduced_fullflow_parallel.log"
STAGE_LOG="$RUNTIME_ROOT/stages.tsv"

TEMPORAL_MANIFEST="$PROJECT_ROOT/configs/coco_temporal_split_v2/manifest.json"
EXPERIMENT_MANIFEST="$PROJECT_ROOT/configs/formal_temporal_v3_qwen3e8b.json"
S2_POOL="$PROJECT_ROOT/configs/frozen_s2_temporal_v2.jsonl"

mkdir -p "$RUNTIME_ROOT" "$PROJECT_ROOT/artifacts"
cd "$PROJECT_ROOT"
exec > >(tee -a "$LOG") 2>&1

stage() {
  printf '%s\t%s\n' "$(date --iso-8601=seconds)" "$1" | tee -a "$STAGE_LOG"
}

stage PRECHECK_START
for port in 8000 13099 13098; do
  curl -fsS --max-time 10 "http://127.0.0.1:${port}/v1/models" >/dev/null
done
qwen_context="$(
  curl -fsS http://127.0.0.1:8000/v1/models |
    jq -r '.data[] | select(.id == "qwen3.8-27b") | .max_model_len'
)"
[[ "$qwen_context" =~ ^[0-9]+$ ]] && (( qwen_context >= 65536 ))
ps -eo args= | rg -q -- '--tensor-parallel-size(=| )4'
ps -eo args= | rg -q -- '--max-num-seqs(=| )4'
[[ "$COCO_CLASSIFY_WORKERS" == "1" ]]

if ps -eo args= | rg 'memory_evolution\.cli evolve-specialists' | \
  rg -F -- "--output $RUN_ROOT" >/dev/null; then
  echo "Parallel Specialist evolution is already active; refusing duplicate launch." >&2
  exit 1
fi
for required in \
  "$DATA_ROOT/manifest.json" \
  "$TEST_ROOT/manifest.json" \
  "$SELECTION_MANIFEST" \
  "$ASSET_MANIFEST" \
  "$EXPERIMENT_MANIFEST" \
  "$S2_POOL" \
  "$SELECTION_CACHE" \
  "$SELECTION_AUDIT"; do
  test -f "$required"
done
jq -e '
  .formal_ready == true and
  .selection_seed_label == "20260901_reduced_fullflow" and
  .train_family_count == 300 and
  .validation_family_count == 50 and
  .meta_validation_family_count == 0 and
  ([.tasks[] | .episode_count] | all(. == 300)) and
  ([.tasks[] | .validation_count] | all(. == 50))
' "$DATA_ROOT/manifest.json" >/dev/null
jq -e '
  .seed_label == "20260901_reduced_fullflow" and
  .counts == {"A":300,"B":50,"C":0,"D":100} and
  ([.tasks[] | length] | length == 5 and all(. == 100))
' "$SELECTION_MANIFEST" >/dev/null

"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path

from memory_evolution.cli import _validate_formal_embedding_cache_audit
from memory_evolution.embedding_adapter import create_qwen3_embedding

project_root = Path("/data/xyj/shengao/AgentReview_0901_parallel/AgentReview")
source_root = Path("/data/xyj/shengao/AgentReview_0831/AgentReview")
data_root = source_root / "data/0901_mstar_reduced_fullflow_a300_b50_v2"
cache = source_root / "cache/qwen3_embedding_8b_cache.sqlite3"
audit = source_root / "cache/qwen3_embedding_8b_cache.audit.json"
with (project_root / "configs/formal_temporal_v3_qwen3e8b.json").open(
    encoding="utf-8"
) as handle:
    embedding = dict(json.load(handle)["embedding"])
fingerprint = embedding.pop("fingerprint")
count, _ = _validate_formal_embedding_cache_audit(
    str(cache), str(audit), embedding, fingerprint
)
if count != 12705:
    raise SystemExit(f"unexpected frozen embedding count: {count}")
client = create_qwen3_embedding(
    model_name=embedding["model_id"],
    revision=embedding["model_revision"],
    cache_path=str(cache),
    cache_enabled=True,
    fail_on_cache_miss=True,
    cache_namespace="selection",
)
for task_id in ("QUAL", "CLAR", "SIGN", "ORIG", "POL"):
    for filename, text_key in (
        ("episodes.jsonl", "text"),
        ("validation.jsonl", "question"),
    ):
        texts = [
            json.loads(line)[text_key]
            for line in (data_root / task_id / filename)
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]
        client.embed(texts)
PY
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/finalize_formal_readiness.py" \
  --check-only \
  --asset-manifest "$ASSET_MANIFEST" \
  --formal-experiment-manifest "$EXPERIMENT_MANIFEST" \
  --temporal-split-manifest "$TEMPORAL_MANIFEST" \
  --s2-pool "$S2_POOL" \
  --data-root "$DATA_ROOT" \
  --coco-root "$COCO_ROOT" \
  > "$RUNTIME_ROOT/formal_readiness_gate.json"
stage PRECHECK_COMPLETE

if [[ "${PARALLEL_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  stage PREFLIGHT_ONLY_COMPLETE
  exit 0
fi

COMMON_RUNTIME_ARGS=(
  --openai-base-url http://127.0.0.1:8000/v1
  --task-model-name qwen3.8-27b
  --reflector-model-name qwen3.8-27b
  --judge-model-name qwen3.8-27b
  --generation-model-source /data/xyj/models/Qwen3.8-27B
  --generation-model-revision 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
  --vllm-version 0.27.1
  --llm-temperature 0.0
  --reasoning-effort medium
  --no-reasoning-effort-supported
  --max-tokens 16384
  --retry-max-tokens 32768
  --api-request-timeout-seconds 600
  --api-connect-timeout-seconds 30
  --api-read-timeout-seconds 600
  --api-write-timeout-seconds 60
  --api-pool-timeout-seconds 30
  --api-max-retries 2
  --write-timeout 60
  --read-timeout 60
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter
  --review-split-revision e405b49e472d1c4ddbf226eae7aded032a2d5c8a
  --review-classify-revision 8607b97b893bf64be0de848609863093c7ecc095
  --coco-root "$COCO_ROOT"
  --embedding-adapter memory_evolution.embedding_adapter:create_qwen3_embedding
  --embedding-model-name Qwen/Qwen3-Embedding-8B
  --embedding-revision 1d8ad4ca9b3dd8059ad90a75d4983776a23d44af
  --embedding-cache "$SELECTION_CACHE"
  --embedding-cache-audit "$SELECTION_AUDIT"
  --embedding-cache-read-only
  --runtime-embedding-cache "$RUNTIME_CACHE"
  --embedding-device cuda:0
  --embedding-batch-size 4
  --s2-retrieval-pool "$S2_POOL"
  --asset-manifest "$ASSET_MANIFEST"
  --formal-experiment-manifest "$EXPERIMENT_MANIFEST"
  --temporal-split-manifest "$TEMPORAL_MANIFEST"
)

if [[ ! -f "$RUN_ROOT/suite_manifest.json" ]]; then
  stage SPECIALIST_EVOLUTION_START
  "$PYTHON_BIN" -m memory_evolution.cli evolve-specialists \
    --data-root "$DATA_ROOT" \
    --output "$RUN_ROOT" \
    --experiment-seeds 0 \
    --primary-seed 0 \
    --role-workers 4 \
    --iterations 8 \
    --static-size 16 \
    --rotating-size 4 \
    --temperature 0.15 \
    --episode-budget 32 \
    --selection-repeats 0 \
    --selection-top-k 1 \
    --max-fix-attempts 3 \
    --parent-policy softmax \
    --mutation-scope full \
    --sandbox-memory-mb 8192 \
    "${COMMON_RUNTIME_ARGS[@]}"
  stage SPECIALIST_EVOLUTION_COMPLETE
else
  stage SPECIALIST_EVOLUTION_ALREADY_COMPLETE
fi

test -f "$RUN_ROOT/suite_manifest.json"
test ! -e "$RUN_ROOT/meta_manifest.json"
stage META_DISABLED

if [[ ! -f "$BASELINE_ROOT/suite_manifest.json" ]]; then
  stage ORIGINAL_SEED_BASELINE_SELECTION_START
  "$PYTHON_BIN" "$PROJECT_ROOT/scripts/build_original_seed_suite.py" \
    --evolved-run-root "$RUN_ROOT" \
    --output "$BASELINE_ROOT" \
    --seed 0
  stage ORIGINAL_SEED_BASELINE_SELECTION_COMPLETE
fi

if [[ ! -f "$BASELINE_REPORT" ]]; then
  stage ORIGINAL_SEED_D100_START
  "$PYTHON_BIN" -m memory_evolution.cli final-evaluate-suite \
    --specialists-only \
    --specialist-test-selection-manifest "$SELECTION_MANIFEST" \
    --evaluation-pipeline-depth 2 \
    --data-root "$DATA_ROOT" \
    --test-data-root "$TEST_ROOT" \
    --program-root "$BASELINE_ROOT" \
    --output "$BASELINE_REPORT" \
    "${COMMON_RUNTIME_ARGS[@]}"
  stage ORIGINAL_SEED_D100_COMPLETE
fi

if [[ ! -f "$EVOLVED_REPORT" ]]; then
  stage EVOLVED_WINNER_D100_START
  "$PYTHON_BIN" -m memory_evolution.cli final-evaluate-suite \
    --specialists-only \
    --specialist-test-selection-manifest "$SELECTION_MANIFEST" \
    --evaluation-pipeline-depth 2 \
    --data-root "$DATA_ROOT" \
    --test-data-root "$TEST_ROOT" \
    --program-root "$RUN_ROOT" \
    --output "$EVOLVED_REPORT" \
    "${COMMON_RUNTIME_ARGS[@]}"
  stage EVOLVED_WINNER_D100_COMPLETE
fi

if [[ ! -f "$SUMMARY" ]]; then
  stage SUMMARY_START
  "$PYTHON_BIN" "$PROJECT_ROOT/scripts/summarize_reduced_fullflow.py" \
    --evolved-run-root "$RUN_ROOT" \
    --baseline-suite-root "$BASELINE_ROOT" \
    --baseline-report "$BASELINE_REPORT" \
    --evolved-report "$EVOLVED_REPORT" \
    --selection-manifest "$SELECTION_MANIFEST" \
    --output "$SUMMARY"
  stage SUMMARY_COMPLETE
fi

stage FULLFLOW_COMPLETE
