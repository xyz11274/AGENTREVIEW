#!/usr/bin/env bash
set -euo pipefail

# Local vLLM accepts a dummy key, but the OpenAI SDK requires a non-empty value.
: "${OPENAI_API_KEY:?Set OPENAI_API_KEY (dummy is valid for local vLLM).}"
OPENAI_BASE_URL=${OPENAI_BASE_URL:-${OPENAI_API_BASE:-}}
: "${OPENAI_BASE_URL:?Set OPENAI_BASE_URL to one local vLLM replica.}"
export OPENAI_BASE_URL

: "${S2_API_KEY:?Set S2_API_KEY before running this script.}"
# If you use AzureOpenAI API, you need to set the following
#export AZURE_ENDPOINT=...  # Format: https://<your-endpoint>.openai.azure.com/
#export AZURE_DEPLOYMENT=...  # Your Azure OpenAI deployment here
#export AZURE_OPENAI_KEY=... # Your Azure OpenAI key here

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
  --model_name "${MODEL_NAME}" \
  --keyword_model_name "${MODEL_NAME}" \
  --max_retrieved_refs 8 \
  --reviewer_workers 5 \
  --api_min_interval_seconds 0 \
  --review_max_tokens 1200 \
  --memory_mode mstar_program \
  --memory_program_dir "${MSTAR_PROGRAM_DIR}" \
  --memory_embedding_adapter "${MSTAR_EMBEDDING_ADAPTER}" \
  --memory_embedding_revision "${MSTAR_EMBEDDING_REVISION}" \
  --overwrite
