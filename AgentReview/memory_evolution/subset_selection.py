import math
import random
import warnings
from typing import Any, List


def _question(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("question", ""))
    return str(getattr(item, "question", ""))


def _episode_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _embed(client, texts: List[str]) -> List[List[float]]:
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
    return [list(map(float, vector)) for vector in vectors]


def _cosine(left: List[float], right: List[float]) -> float:
    size = min(len(left), len(right))
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(value * value for value in left[:size]))
    right_norm = math.sqrt(sum(value * value for value in right[:size]))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _mean(vectors: List[List[float]]) -> List[float]:
    if not vectors:
        return []
    dimensions = min(len(vector) for vector in vectors)
    return [
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(dimensions)
    ]


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

    centroid_indices = rng.sample(range(len(values)), n_static)
    centroids = [vectors[index][:] for index in centroid_indices]
    assignments = [0] * len(values)
    for _ in range(30):
        new_assignments = [
            max(range(n_static), key=lambda index: _cosine(vector, centroids[index]))
            for vector in vectors
        ]
        if new_assignments == assignments:
            break
        assignments = new_assignments
        for cluster in range(n_static):
            members = [
                vector
                for vector, assignment in zip(vectors, assignments)
                if assignment == cluster
            ]
            if members:
                centroids[cluster] = _mean(members)

    selected_indices = []
    for cluster, centroid in enumerate(centroids):
        members = [
            index for index, assignment in enumerate(assignments) if assignment == cluster
        ]
        candidates = members or [
            index for index in range(len(values)) if index not in selected_indices
        ]
        selected = rng.choice(candidates)
        if selected not in selected_indices:
            selected_indices.append(selected)
    for index in range(len(values)):
        if len(selected_indices) >= n_static:
            break
        if index not in selected_indices:
            selected_indices.append(index)
    return [values[index] for index in selected_indices[:n_static]]


def sample_rotating_validation_set(
    remaining_val_items, n_rotating: int = 5, seed=None
):
    values = list(remaining_val_items)
    if n_rotating <= 0:
        return []
    if n_rotating >= len(values):
        return values
    return random.Random(seed).sample(values, n_rotating)


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
        for episode_index in remaining:
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
