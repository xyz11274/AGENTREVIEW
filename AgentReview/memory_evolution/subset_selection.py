import math
import random
import warnings
from typing import Any


def _question(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("question", ""))
    return str(getattr(item, "question", ""))


def _episode_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _embed(client, texts: list[str]) -> list[list[float]]:
    if client is None:
        raise RuntimeError("Embedding client is unavailable")
    if hasattr(client, "embed"):
        vectors = client.embed(texts)
    elif hasattr(client, "embed_documents"):
        vectors = client.embed_documents(texts)
    elif callable(client):
        vectors = client(texts)
    else:
        raise TypeError("Embedding client must provide embed() or be callable")
    output = [list(map(float, vector)) for vector in vectors]
    if len(output) != len(texts):
        raise ValueError(
            "Embedding result count must match the input text count: "
            f"vectors={len(output)}, texts={len(texts)}"
        )
    if output:
        dimension = len(output[0])
        if dimension <= 0:
            raise ValueError("Embedding vectors must have a positive dimension")
        for index, vector in enumerate(output):
            if len(vector) != dimension:
                raise ValueError(
                    "Embedding vectors must share one dimension: "
                    f"vector[0]={dimension}, vector[{index}]={len(vector)}"
                )
            if not all(math.isfinite(value) for value in vector):
                raise ValueError(f"Embedding vector {index} must contain finite values")
    return output


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(
            "Cosine similarity requires equal embedding dimensions: "
            f"left={len(left)}, right={len(right)}"
        )
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _mean(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise ValueError("Mean embedding requires equal vector dimensions")
    return [
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(dimensions)
    ]


def _kmeans_representative_indices(
    vectors: list[list[float]],
    clusters: int,
    *,
    seed: int,
    max_iterations: int = 50,
) -> list[int]:
    """Return the centroid-nearest representative of each cosine k-means cluster."""
    if clusters <= 0 or not vectors:
        return []
    if clusters >= len(vectors):
        return list(range(len(vectors)))

    rng = random.Random(seed)
    centroids = [vectors[index][:] for index in rng.sample(range(len(vectors)), clusters)]
    assignments: list[int] | None = None
    for _ in range(max_iterations):
        next_assignments = [
            max(
                range(clusters),
                key=lambda index: (_cosine(vector, centroids[index]), -index),
            )
            for vector in vectors
        ]
        if next_assignments == assignments:
            break
        assignments = next_assignments
        for cluster in range(clusters):
            members = [
                vector
                for vector, assignment in zip(vectors, assignments)
                if assignment == cluster
            ]
            if members:
                centroids[cluster] = _mean(members)

    assignments = assignments or [0] * len(vectors)
    selected: list[int] = []
    for cluster, centroid in enumerate(centroids):
        members = [
            index
            for index, assignment in enumerate(assignments)
            if assignment == cluster and index not in selected
        ]
        if members:
            selected.append(
                max(members, key=lambda index: (_cosine(vectors[index], centroid), -index))
            )

    # Empty clusters are possible in Lloyd's algorithm. Fill them deterministically
    # with the point least represented by the already selected centroids.
    while len(selected) < clusters:
        remaining = [index for index in range(len(vectors)) if index not in selected]
        if not selected:
            selected.append(remaining[0])
            continue
        selected.append(
            min(
                remaining,
                key=lambda index: (
                    max(_cosine(vectors[index], vectors[value]) for value in selected),
                    index,
                ),
            )
        )
    return selected


def build_static_validation_set(
    val_items,
    embedding_client,
    n_static: int = 25,
    seed: int = 0,
    allow_random_fallback: bool = True,
):
    values = list(val_items)
    if n_static <= 0 or not values:
        return []
    if n_static >= len(values):
        return values
    rng = random.Random(seed)
    try:
        vectors = _embed(embedding_client, [_question(item) for item in values])
    except Exception as exc:
        if not allow_random_fallback:
            raise RuntimeError(
                "Formal static selection requires the frozen embedding adapter"
            ) from exc
        warnings.warn(
            f"Embedding unavailable for static validation selection; using random sample: {exc}",
            RuntimeWarning,
        )
        return rng.sample(values, n_static)

    selected_indices = _kmeans_representative_indices(
        vectors,
        n_static,
        seed=seed,
    )
    return [values[index] for index in selected_indices[:n_static]]


def sample_rotating_validation_set(
    remaining_val_items,
    n_rotating: int = 5,
    seed: int | None = None,
    embedding_client=None,
    allow_random_fallback: bool = True,
):
    values = list(remaining_val_items)
    if n_rotating <= 0:
        return []
    if n_rotating >= len(values):
        return values
    resolved_seed = int(seed or 0)
    try:
        vectors = _embed(embedding_client, [_question(item) for item in values])
    except Exception as exc:
        if not allow_random_fallback:
            raise RuntimeError(
                "Formal rotating selection requires the frozen embedding adapter"
            ) from exc
        warnings.warn(
            "Embedding unavailable for rotating validation selection; using "
            f"random sample: {exc}",
            RuntimeWarning,
        )
        return random.Random(resolved_seed).sample(values, n_rotating)
    indices = _kmeans_representative_indices(
        vectors,
        n_rotating,
        seed=resolved_seed,
    )
    return [values[index] for index in indices]


def select_episode_subset(
    episodes,
    validation_items,
    embedding_client,
    budget: int,
    allow_random_fallback: bool = True,
):
    episode_values = list(episodes)
    validation_values = list(validation_items)
    budget = max(0, min(int(budget), len(episode_values)))
    if budget == 0 or not episode_values:
        return []
    rng = random.Random(0)
    try:
        episode_vectors = _embed(
            embedding_client, [_episode_text(item) for item in episode_values]
        )
        validation_vectors = _embed(
            embedding_client, [_question(item) for item in validation_values]
        )
    except Exception as exc:
        if not allow_random_fallback:
            raise RuntimeError(
                "Formal facility-location selection requires the frozen "
                "embedding adapter"
            ) from exc
        warnings.warn(
            f"Embedding unavailable for episode selection; using random sample: {exc}",
            RuntimeWarning,
        )
        return rng.sample(episode_values, budget)
    if not validation_vectors:
        return rng.sample(episode_values, budget)

    similarities = [
        [_cosine(validation, episode) for episode in episode_vectors]
        for validation in validation_vectors
    ]
    nearest = [-1.0] * len(validation_vectors)
    selected = []
    remaining = set(range(len(episode_values)))
    while remaining and len(selected) < budget:
        best_index = None
        best_gain = float("-inf")
        for episode_index in sorted(remaining):
            gain = sum(
                max(nearest[val_index], similarities[val_index][episode_index])
                - nearest[val_index]
                for val_index in range(len(validation_vectors))
            )
            if gain > best_gain:
                best_gain = gain
                best_index = episode_index
        selected.append(best_index)
        remaining.remove(best_index)
        nearest = [
            max(nearest[val_index], similarities[val_index][best_index])
            for val_index in range(len(validation_vectors))
        ]
    return [episode_values[index] for index in selected]
