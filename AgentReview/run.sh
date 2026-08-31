#!/usr/bin/env bash
set -euo pipefail

: "${OPENAI_CLIENT_TYPE:=openai}"
case "${OPENAI_CLIENT_TYPE}" in
  openai)
    # Local vLLM accepts a dummy key; the SDK receives a non-empty value.
    : "${OPENAI_API_KEY:?Set OPENAI_API_KEY (dummy is valid for local vLLM).}"
    OPENAI_BASE_URL=${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}
    : "${OPENAI_BASE_URL:?Set OPENAI_BASE_URL to one OpenAI-compatible endpoint.}"
    export OPENAI_BASE_URL
    ;;
  azure_openai)
    : "${AZURE_OPENAI_KEY:?Set AZURE_OPENAI_KEY.}"
    : "${AZURE_ENDPOINT:?Set AZURE_ENDPOINT.}"
    : "${AZURE_DEPLOYMENT:?Set AZURE_DEPLOYMENT.}"
    ;;
  *)
    echo "OPENAI_CLIENT_TYPE must be openai or azure_openai" >&2
    exit 2
    ;;
esac

export HF_ENDPOINT=https://hf-mirror.com
export AGENTREVIEW_API_MIN_INTERVAL_SECONDS=0

: "${PYTHON_BIN:=/data0/xyj/miniconda3/envs/env_shengao/bin/python}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
: "${PAPER_MD_DIR:=$PROJECT_ROOT/test_data}"
: "${REVIEW_OUTPUT_DIR:=$PROJECT_ROOT/outputs}"
: "${MSTAR_PROGRAM_DIR:?Set MSTAR_PROGRAM_DIR to the promoted six-program suite.}"
: "${MSTAR_EMBEDDING_ADAPTER:=memory_evolution.embedding_adapter:create_qwen3_embedding}"
: "${MSTAR_EMBEDDING_REVISION:?Set the immutable Qwen3-Embedding-8B model revision.}"
: "${MODEL_NAME:=qwen3.8-27b-fp8}"

"${PYTHON_BIN}" run_single_paper_review_cli.py \
  --paper_md_dir "${PAPER_MD_DIR}" \
  --output_dir "${REVIEW_OUTPUT_DIR}" \
  --openai_client_type "${OPENAI_CLIENT_TYPE}" \
  --model_name "${MODEL_NAME}" \
  --reviewer_workers 5 \
  --api_min_interval_seconds 0 \
  --review_max_tokens 1200 \
  --memory_mode mstar_program \
  --memory_program_dir "${MSTAR_PROGRAM_DIR}" \
  --memory_embedding_adapter "${MSTAR_EMBEDDING_ADAPTER}" \
  --memory_embedding_revision "${MSTAR_EMBEDDING_REVISION}" \
  --overwrite
