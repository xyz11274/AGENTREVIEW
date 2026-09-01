import hashlib
import math
import unittest

from memory_evolution.subset_selection import (
    build_static_validation_set,
    sample_rotating_validation_set,
    select_episode_subset,
)


class Deterministic4096Embedding:
    def embed(self, texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            values = [
                ((digest[index % len(digest)] + index) % 251) / 125.0 - 1.0
                for index in range(4096)
            ]
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append([value / norm for value in values])
        return vectors


class QwenSubsetSelectionTest(unittest.TestCase):
    def test_4096_dimension_selection_is_reproducible(self):
        client = Deterministic4096Embedding()
        validation = [
            {"id": f"v-{index}", "question": f"validation {index}"}
            for index in range(12)
        ]
        episodes = [
            {"id": f"e-{index}", "text": f"episode {index}"} for index in range(20)
        ]
        static_first = build_static_validation_set(
            validation, client, n_static=5, seed=7, allow_random_fallback=False
        )
        static_second = build_static_validation_set(
            validation, client, n_static=5, seed=7, allow_random_fallback=False
        )
        self.assertEqual(
            [item["id"] for item in static_first],
            [item["id"] for item in static_second],
        )
        selected_first = select_episode_subset(
            episodes,
            static_first,
            client,
            budget=6,
            allow_random_fallback=False,
        )
        selected_second = select_episode_subset(
            episodes,
            static_first,
            client,
            budget=6,
            allow_random_fallback=False,
        )
        self.assertEqual(
            [item["id"] for item in selected_first],
            [item["id"] for item in selected_second],
        )

    def test_selection_rejects_mixed_embedding_dimensions(self):
        class MixedEmbedding:
            def embed(self, texts):
                return [[1.0, 0.0], [1.0, 0.0, 0.0]][: len(texts)]

        with self.assertRaisesRegex(RuntimeError, "frozen embedding adapter"):
            build_static_validation_set(
                [
                    {"id": "a", "question": "a"},
                    {"id": "b", "question": "b"},
                    {"id": "c", "question": "c"},
                ],
                MixedEmbedding(),
                n_static=2,
                allow_random_fallback=False,
            )

    def test_static_selection_uses_centroid_nearest_cluster_members(self):
        class AxisEmbedding:
            def embed(self, texts):
                mapping = {
                    "left-a": [1.0, 0.2],
                    "left-center": [1.0, 0.0],
                    "left-b": [1.0, -0.2],
                    "right-a": [0.2, 1.0],
                    "right-center": [0.0, 1.0],
                    "right-b": [-0.2, 1.0],
                }
                return [mapping[text] for text in texts]

        values = [
            {"id": text, "question": text}
            for text in (
                "left-a",
                "left-center",
                "left-b",
                "right-a",
                "right-center",
                "right-b",
            )
        ]
        selected = build_static_validation_set(
            values,
            AxisEmbedding(),
            n_static=2,
            seed=0,
            allow_random_fallback=False,
        )
        self.assertEqual(
            {value["id"] for value in selected},
            {"left-center", "right-center"},
        )

    def test_rotating_selection_is_seeded_kmeans_not_random_sampling(self):
        client = Deterministic4096Embedding()
        values = [
            {"id": f"v-{index}", "question": f"rotating {index}"}
            for index in range(12)
        ]
        first = sample_rotating_validation_set(
            values,
            n_rotating=5,
            seed=9,
            embedding_client=client,
            allow_random_fallback=False,
        )
        second = sample_rotating_validation_set(
            values,
            n_rotating=5,
            seed=9,
            embedding_client=client,
            allow_random_fallback=False,
        )
        self.assertEqual(
            [value["id"] for value in first],
            [value["id"] for value in second],
        )
        self.assertEqual(len({value["id"] for value in first}), 5)


if __name__ == "__main__":
    unittest.main()
