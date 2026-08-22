import hashlib
import math
import unittest

from memory_evolution.subset_selection import (
    build_static_validation_set,
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


if __name__ == "__main__":
    unittest.main()
