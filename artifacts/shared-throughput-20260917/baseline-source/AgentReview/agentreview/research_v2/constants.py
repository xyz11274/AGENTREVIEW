"""Small stable constants shared by Research Core v2 stages."""

from pathlib import Path

ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}
SPLIT_SIZES = {
    "canonical_review_corpus": 125,
    "evolution_static": 25,
    "evolution_rotating_pool": 125,
}
SPLIT_SEED = 0
MODEL_SOURCE = "Qwen/Qwen3.8-27B"
MODEL_REVISION = "1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0"
MODEL_ALIAS = "qwen3.8-27b"
MODEL_PRECISION = "BF16"
TEMPERATURE = 0.0
ENABLE_THINKING = False
REVIEW_MAX_TOKENS = 8192
REFLECTOR_MAX_TOKENS = 8192
EVALUATOR_MAX_TOKENS = 4096
SUMMARIZER_MAX_TOKENS = 4096
META_MAX_TOKENS = 4096
DEFAULT_ARTIFACT_ROOT = Path("artifacts/research_v2")
SCHEMA_VERSION = "research-core-v2-artifact-v1"
