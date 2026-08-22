# Local Qwen3.8-27B-FP8 OpenAI-compatible runtime

## Provider resolution and isolation

Generation follows this chain:

```text
memory_evolution.cli evolve-task
  -> task / reflector / judge MemoryToolkit
  -> agentreview.backends.openai.OpenAIChat
  -> openai.OpenAI(base_url=<one explicit endpoint>, max_retries=0)
  -> local vLLM /chat/completions
  -> qwen3.8-27b-fp8
```

The endpoint resolution order is `--openai-base-url`, `OPENAI_BASE_URL`, then
the legacy compatibility alias `OPENAI_API_BASE`. There is no provider or cloud
default. Formal runs accept a loopback/private endpoint and bind one process to
one URL. CoCo remains independent at `127.0.0.1:13099` (ReviewSplit) and
`127.0.0.1:13098` (ReviewClassify).

`--temperature 0.15` is the canonical Evolution parent-selection softmax
temperature. LLM sampling is independently configured by `--llm-temperature`
and defaults to `0.0`.

`reasoning_effort` is a top-level chat request field only when
`--reasoning-effort-supported` is explicitly set. With
`--no-reasoning-effort-supported`, the configured effort is recorded and logged
but is not sent. A rejected supported declaration fails; the backend never
retries after changing/removing that field. `reasoning_content`, `reasoning`,
and leading `<think>...</think>` text are excluded from the business review.

## Environment

Set the deployment-specific values once. `OPENAI_API_KEY=dummy` is sufficient
for a vLLM server that does not enforce authentication.

```bash
cd /data0/xyj/shengao/AgentReview_0811/AgentReview
export PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
export OPENAI_API_KEY=dummy
export QWEN_MODEL_SOURCE='/absolute/model/path-or-repo-id'
export QWEN_MODEL_REVISION='exact-snapshot-or-weights-identity'
export VLLM_VERSION='exact-vllm-version'
export COCO_ROOT='/absolute/pinned/CoCoReviewBench-at-2580687ee5d4ad2ac4b9730afb8f1dfda22e40db'
```

The API key is never included in run config, checkpoint, runtime metadata, or
experiment identity. `QWEN_MODEL_SOURCE`, `QWEN_MODEL_REVISION`, vLLM version,
model names, sampling, and reasoning capability are logical identity. The base
URL is runtime provenance only.

## Replica preflight

Replica A:

```bash
"$PY" -m memory_evolution.api_preflight \
  --openai-base-url http://127.0.0.1:8000/v1 \
  --model-name qwen3.8-27b-fp8 \
  --max-tokens 256 \
  --reasoning-effort medium \
  --no-reasoning-effort-supported
```

Replica B:

```bash
"$PY" -m memory_evolution.api_preflight \
  --openai-base-url http://127.0.0.1:8001/v1 \
  --model-name qwen3.8-27b-fp8 \
  --max-tokens 256 \
  --reasoning-effort medium \
  --no-reasoning-effort-supported
```

Each preflight requires `/models` success, the exact served model name, and a
non-empty SDK-parsed `message.content` from `/chat/completions` using the
declared `max_tokens` and reasoning request shape.

## Shared QUAL arguments

The following shell array fixes the formal temporal v3/Qwen cache/CoCo/model
identity while preserving all canonical Evolution parameters:

```bash
COMMON_QUAL_ARGS=(
  --task-id QUAL
  --data-root "$PWD/data/evolution_coco_v2_20260819"
  --iterations 21
  --static-size 25
  --rotating-size 5
  --temperature 0.15
  --episode-budget 125
  --selection-repeats 3
  --selection-top-k 3
  --max-fix-attempts 3
  --parent-policy softmax
  --mutation-scope full
  --task-model-name qwen3.8-27b-fp8
  --reflector-model-name qwen3.8-27b-fp8
  --judge-model-name qwen3.8-27b-fp8
  --generation-model-source "$QWEN_MODEL_SOURCE"
  --generation-model-revision "$QWEN_MODEL_REVISION"
  --vllm-version "$VLLM_VERSION"
  --llm-temperature 0.0
  --reasoning-effort medium
  --no-reasoning-effort-supported
  --max-tokens 8192
  --retry-max-tokens 16384
  --api-request-timeout-seconds 600
  --api-connect-timeout-seconds 30
  --api-read-timeout-seconds 600
  --api-write-timeout-seconds 60
  --api-pool-timeout-seconds 30
  --api-max-retries 2
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter
  --review-split-revision e405b49e472d1c4ddbf226eae7aded032a2d5c8a
  --review-classify-revision 8607b97b893bf64be0de848609863093c7ecc095
  --embedding-adapter memory_evolution.embedding_adapter:create_qwen3_embedding
  --embedding-model-name Qwen/Qwen3-Embedding-8B
  --embedding-revision 1d8ad4ca9b3dd8059ad90a75d4983776a23d44af
  --embedding-cache "$PWD/cache/qwen3_embedding_8b_cache.sqlite3"
  --embedding-cache-audit "$PWD/cache/qwen3_embedding_8b_cache.audit.json"
  --formal-experiment-manifest "$PWD/configs/formal_temporal_v3_qwen3e8b.json"
  --s2-retrieval-pool "$PWD/configs/frozen_s2_temporal_v2.jsonl"
  --asset-manifest "$PWD/configs/asset_manifest_v2.json"
  --temporal-split-manifest "$PWD/configs/coco_temporal_split_v2/manifest.json"
  --coco-root "$COCO_ROOT"
)
```

## QUAL smoke on replica A

Smoke is non-reportable and performs one mutation, but this command explicitly
uses the formal v3 assets and read-only Qwen cache. A miss cannot load the
embedding model or write a vector.

```bash
SMOKE_OUTPUT="$PWD/runs/local_qwen_qual_smoke_seed0"
test ! -e "$SMOKE_OUTPUT"
"$PY" -m memory_evolution.cli evolve-task \
  "${COMMON_QUAL_ARGS[@]}" \
  --output "$SMOKE_OUTPUT" \
  --seed 0 \
  --openai-base-url http://127.0.0.1:8000/v1 \
  --embedding-cache-read-only \
  --smoke
```

## Two independent formal Evolution runs

These output directories are disjoint. There is no endpoint list, round-robin,
failover, or cross-run state sharing.

```bash
OUTPUT_A="$PWD/runs/formal_qwen_qual_seed0_replica_a"
OUTPUT_B="$PWD/runs/formal_qwen_qual_seed1_replica_b"
test ! -e "$OUTPUT_A"
test ! -e "$OUTPUT_B"

"$PY" -m memory_evolution.cli evolve-task \
  "${COMMON_QUAL_ARGS[@]}" \
  --output "$OUTPUT_A" \
  --seed 0 \
  --openai-base-url http://127.0.0.1:8000/v1 &
PID_A=$!

"$PY" -m memory_evolution.cli evolve-task \
  "${COMMON_QUAL_ARGS[@]}" \
  --output "$OUTPUT_B" \
  --seed 1 \
  --openai-base-url http://127.0.0.1:8001/v1 &
PID_B=$!

wait "$PID_A"
wait "$PID_B"
```

## Resume across replicas

Rerun seed 0 with the same output and logical configuration, changing only:

```bash
--openai-base-url http://127.0.0.1:8001/v1
```

Resume is accepted because the controller/checkpoint identity includes the
logical model, revision, vLLM version, generation/reasoning configuration,
formal protocol, embedding identity, frozen assets, data, search configuration,
and seed, but excludes base URL, API key, port, and physical GPU IDs.
`llm_runtime.json` retains both endpoints in `base_url_history`. The operator is
responsible for ensuring both services really use identical weights, revision,
vLLM parameters, served name, and chat template; changing any recorded logical
field is rejected.
