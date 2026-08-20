import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from memory_evolution.cli import build_parser
from memory_evolution.embedding_adapter import BgeM3Embedding
from memory_evolution.search import EvolutionConfig
from memory_evolution.toolkit import InMemoryVectorCollection


class MockBgeEmbedding(BgeM3Embedding):
    def __init__(
        self,
        cache_path: Path,
        *,
        revision: str = "revision-a",
        cache_enabled: bool = True,
        fail_batch: int = 0,
    ):
        super().__init__(
            "BAAI/bge-m3",
            revision,
            cache_path=str(cache_path),
            cache_enabled=cache_enabled,
            expected_dimension=4,
        )
        self.load_calls = 0
        self.batch_calls = []
        self.fail_batch = fail_batch

    def _load(self):
        self.load_calls += 1
        self._model = object()

    @staticmethod
    def vector(text: str):
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [
            (int.from_bytes(digest[index : index + 2], "big") - 32768) / 32768
            for index in range(0, 8, 2)
        ]

    def _embed_uncached_batch(self, batch):
        self.batch_calls.append(list(batch))
        if self.fail_batch and len(self.batch_calls) == self.fail_batch:
            raise RuntimeError("simulated interruption")
        return [self.vector(text) for text in batch]


class EmbeddingCacheTest(unittest.TestCase):
    def test_second_embed_does_not_call_underlying_bge(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            first = MockBgeEmbedding(cache)
            expected = first.embed(["same text"])
            self.assertEqual(first.load_calls, 1)
            self.assertEqual(first.batch_calls, [["same text"]])

            second = MockBgeEmbedding(cache)
            self.assertEqual(second.embed(["same text"]), expected)
            self.assertEqual(second.load_calls, 0)
            self.assertEqual(second.batch_calls, [])

    def test_different_model_revision_does_not_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            MockBgeEmbedding(cache, revision="revision-a").embed(["paper"])
            changed = MockBgeEmbedding(cache, revision="revision-b")
            changed.embed(["paper"])
            self.assertEqual(changed.load_calls, 1)
            self.assertEqual(changed.batch_calls, [["paper"]])

    def test_different_embedding_semantics_does_not_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            MockBgeEmbedding(cache).embed(["paper"])
            connection = sqlite3.connect(str(cache))
            connection.execute(
                "UPDATE embeddings SET semantics_version = ?",
                ("obsolete-semantics",),
            )
            connection.commit()
            connection.close()

            changed = MockBgeEmbedding(cache)
            changed.embed(["paper"])
            self.assertEqual(changed.load_calls, 1)
            self.assertEqual(changed.batch_calls, [["paper"]])

    def test_one_character_change_is_a_cache_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            embedding = MockBgeEmbedding(cache)
            embedding.embed(["paper"])
            embedding.embed(["papers"])
            self.assertEqual(embedding.batch_calls, [["paper"], ["papers"]])

    def test_result_order_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            texts = ["beta", "alpha", "gamma"]
            embedding = MockBgeEmbedding(cache)
            self.assertEqual(
                embedding.embed(texts),
                [embedding.vector(text) for text in texts],
            )

    def test_duplicate_text_semantics_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            texts = ["beta", "alpha", "beta", "gamma", "alpha"]
            embedding = MockBgeEmbedding(cache)
            first = embedding.embed(texts)
            self.assertEqual(first, [embedding.vector(text) for text in texts])
            self.assertEqual(
                embedding.batch_calls,
                [["beta", "alpha", "gamma"]],
            )

            second = MockBgeEmbedding(cache)
            self.assertEqual(second.embed(texts), first)
            self.assertEqual(second.batch_calls, [])

    def test_partial_batches_are_committed_and_reused_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            texts = [f"paper-{index}" for index in range(10)]
            interrupted = MockBgeEmbedding(cache, fail_batch=2)
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                interrupted.embed(texts)
            self.assertEqual(len(interrupted.batch_calls[0]), 8)

            resumed = MockBgeEmbedding(cache)
            result = resumed.embed(texts)
            self.assertEqual(result, [resumed.vector(text) for text in texts])
            self.assertEqual(resumed.batch_calls, [["paper-8", "paper-9"]])

    def test_corrupt_or_wrong_dimension_entry_is_never_used(self):
        for corruption in ("dimension", "payload"):
            with (
                self.subTest(corruption=corruption),
                tempfile.TemporaryDirectory() as directory,
            ):
                cache = Path(directory) / "embedding.sqlite3"
                MockBgeEmbedding(cache).embed(["paper"])
                connection = sqlite3.connect(str(cache))
                if corruption == "dimension":
                    connection.execute("UPDATE embeddings SET dimension = 3")
                else:
                    connection.execute(
                        "UPDATE embeddings SET vector_float32 = ?", (b"broken",)
                    )
                connection.commit()
                connection.close()

                repaired = MockBgeEmbedding(cache)
                self.assertEqual(
                    repaired.embed(["paper"]), [repaired.vector("paper")]
                )
                self.assertEqual(repaired.batch_calls, [["paper"]])

    def test_cache_toggle_preserves_vectors_and_retrieval_ranking(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "embedding.sqlite3"
            texts = ["alpha", "beta", "gamma"]
            uncached = MockBgeEmbedding(cache, cache_enabled=False)
            cached = MockBgeEmbedding(cache, cache_enabled=True)
            uncached_vectors = uncached.embed(texts)
            cached_vectors = cached.embed(texts)
            self.assertEqual(cached_vectors, uncached_vectors)

            query_uncached = uncached.embed(["alpha"])[0]
            query_cached = MockBgeEmbedding(cache).embed(["alpha"])[0]
            self.assertEqual(query_cached, query_uncached)

            rankings = []
            for vectors, query in (
                (uncached_vectors, query_uncached),
                (cached_vectors, query_cached),
            ):
                collection = InMemoryVectorCollection("fixture")
                collection.add(texts, vectors)
                rankings.append(
                    [item["text"] for item in collection.query(query, top_k=3)]
                )
            self.assertEqual(rankings[0], rankings[1])

    def test_cache_path_is_excluded_from_formal_resume_identity(self):
        parser = build_parser()

        def identity_config(cache_path: str):
            args = parser.parse_args(
                [
                    "evolve-task",
                    "--task-id",
                    "QUAL",
                    "--data-root",
                    "data/formal",
                    "--output",
                    "runs/formal",
                    "--embedding-cache",
                    cache_path,
                ]
            )
            return asdict(EvolutionConfig.from_value(vars(args)))

        first = identity_config("/cache/location-a.sqlite3")
        second = identity_config("/different/location-b.sqlite3")
        self.assertEqual(first, second)
        self.assertNotIn("embedding_cache", first)
        self.assertNotIn("disable_embedding_cache", first)


if __name__ == "__main__":
    unittest.main()
