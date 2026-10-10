"""Frozen dense embedding construction for role-local memory."""
from .config import ResearchConfig

def embedding_client(config: ResearchConfig, *, remote_service=None):
    if config.embedding_provider != "qwen3_embedding":
        raise ValueError(f"Unsupported formal embedding provider: {config.embedding_provider}")
    if remote_service is not None:
        from .embedding_remote import RemoteFP32Embedding
        return RemoteFP32Embedding(model=config.embedding_model, revision=config.embedding_revision,
                                   cache_path=config.embedding_cache_path, service=remote_service)
    from memory_evolution.embedding_adapter import create_qwen3_embedding

    return create_qwen3_embedding(
        model_name=config.embedding_model,
        revision=config.embedding_revision,
        cache_path=config.embedding_cache_path,
        device=config.embedding_device,
        cache_namespace="research_v2_runtime_v1",
    )

