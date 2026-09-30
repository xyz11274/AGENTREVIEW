"""Small stable constants shared by Research Core v2 stages."""

from pathlib import Path
from .config import ModelConfig

ROLES = ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}
SPLIT_SIZES = {
    "canonical_review_corpus": 125,
    "evolution_static": 25,
    "evolution_rotating_pool": 125,
}
SPLIT_SEED = 0
TEMPERATURE = 0.0
ENABLE_THINKING = False
REVIEW_MAX_TOKENS = ModelConfig().max_output_tokens
SUMMARIZER_MAX_TOKENS = ModelConfig().max_output_tokens
META_MAX_TOKENS = ModelConfig().max_output_tokens
DEFAULT_ARTIFACT_ROOT = Path("artifacts/research_v2")
SCHEMA_VERSION = "research-core-v2-artifact-v1"
