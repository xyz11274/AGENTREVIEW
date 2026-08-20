"""Pinned BGE-M3 dense embedding adapter with a persistent exact-text cache."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Dict, List, Tuple


EMBEDDING_SEMANTICS_VERSION = "bge-m3-cls-l2norm-maxlen8192-v1"
BGE_M3_EMBEDDING_DIMENSION = 1024
MODEL_BATCH_SIZE = 8
PROGRESS_INTERVAL = 256
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EMBEDDING_CACHE = PROJECT_ROOT / "cache" / "embedding_cache.sqlite3"
CACHE_SCHEMA_VERSION = "embedding-cache-sqlite-v1"


class BgeM3Embedding:
    def __init__(
        self,
        model_name: str,
        revision: str,
        *,
        cache_path: str = "",
        cache_enabled: bool = True,
        expected_dimension: int = BGE_M3_EMBEDDING_DIMENSION,
    ):
        if not revision:
            raise ValueError("BGE-M3 requires an immutable model revision")
        self.model_name = model_name
        self.revision = revision
        self.cache_enabled = bool(cache_enabled)
        self.cache_path = Path(
            cache_path or DEFAULT_EMBEDDING_CACHE
        ).expanduser().resolve()
        self.expected_dimension = int(expected_dimension)
        if self.expected_dimension <= 0:
            raise ValueError("BGE-M3 expected embedding dimension must be positive")
        self._tokenizer = None
        self._model = None
        self._device = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_name,
            revision=self.revision,
            trust_remote_code=True,
        )
        self._model = AutoModel.from_pretrained(
            self.model_name,
            revision=self.revision,
            trust_remote_code=True,
        ).to(self._device)
        self._model.eval()

    def _embed_uncached_batch(self, batch: List[str]) -> List[List[float]]:
        import torch
        import torch.nn.functional as functional

        with torch.inference_mode():
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=8192,
                return_tensors="pt",
            )
            encoded = {
                key: value.to(self._device) for key, value in encoded.items()
            }
            hidden = self._model(**encoded).last_hidden_state[:, 0]
            dense = functional.normalize(hidden, p=2, dim=1)
            return dense.cpu().float().tolist()

    def _connect_cache(self) -> sqlite3.Connection:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        connection = None
        try:
            connection = sqlite3.connect(str(self.cache_path), timeout=30.0)
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    model_name TEXT NOT NULL,
                    model_revision TEXT NOT NULL,
                    semantics_version TEXT NOT NULL,
                    text_sha256 TEXT NOT NULL,
                    text_byte_length INTEGER NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_float32 BLOB NOT NULL,
                    vector_sha256 TEXT NOT NULL,
                    PRIMARY KEY (
                        model_name,
                        model_revision,
                        semantics_version,
                        text_sha256
                    )
                )
                """
            )
            row = connection.execute(
                "SELECT value FROM cache_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO cache_metadata(key, value) VALUES (?, ?)",
                    ("schema_version", CACHE_SCHEMA_VERSION),
                )
                connection.commit()
            elif str(row[0]) != CACHE_SCHEMA_VERSION:
                raise RuntimeError(
                    "Embedding cache schema mismatch: "
                    f"observed={row[0]!r}, expected={CACHE_SCHEMA_VERSION!r}"
                )
            return connection
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise RuntimeError(
                f"Embedding cache is unreadable or corrupt: {self.cache_path}: {exc}"
            ) from exc
        except Exception:
            if connection is not None:
                connection.close()
            raise

    @staticmethod
    def _text_identity(text: str) -> Tuple[str, int]:
        payload = text.encode("utf-8")
        return hashlib.sha256(payload).hexdigest(), len(payload)

    def _canonical_float32_vector(self, vector) -> Tuple[List[float], bytes, str]:
        values = [float(value) for value in vector]
        if len(values) != self.expected_dimension:
            raise ValueError(
                "BGE-M3 embedding dimension mismatch: "
                f"observed={len(values)}, expected={self.expected_dimension}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("BGE-M3 embedding contains a non-finite value")
        payload = struct.pack(f"<{self.expected_dimension}f", *values)
        canonical = list(struct.unpack(f"<{self.expected_dimension}f", payload))
        return canonical, payload, hashlib.sha256(payload).hexdigest()

    def _decode_cached_vector(
        self,
        row,
        *,
        expected_text_bytes: int,
    ) -> List[float] | None:
        if row is None:
            return None
        text_byte_length, dimension, payload, expected_vector_sha256 = row
        payload = bytes(payload)
        if (
            int(text_byte_length) != expected_text_bytes
            or int(dimension) != self.expected_dimension
            or len(payload) != self.expected_dimension * 4
            or hashlib.sha256(payload).hexdigest()
            != str(expected_vector_sha256)
        ):
            return None
        try:
            vector = list(struct.unpack(f"<{self.expected_dimension}f", payload))
        except struct.error:
            return None
        if not all(math.isfinite(value) for value in vector):
            return None
        return vector

    def _read_cache(
        self,
        connection: sqlite3.Connection,
        identities: List[Tuple[str, int]],
    ) -> Tuple[Dict[str, List[float]], int]:
        cached: Dict[str, List[float]] = {}
        invalid = 0
        for text_sha256, text_byte_length in dict.fromkeys(identities):
            row = connection.execute(
                """
                SELECT text_byte_length, dimension, vector_float32, vector_sha256
                FROM embeddings
                WHERE model_name = ? AND model_revision = ?
                  AND semantics_version = ? AND text_sha256 = ?
                """,
                (
                    self.model_name,
                    self.revision,
                    EMBEDDING_SEMANTICS_VERSION,
                    text_sha256,
                ),
            ).fetchone()
            vector = self._decode_cached_vector(
                row, expected_text_bytes=text_byte_length
            )
            if vector is not None:
                cached[text_sha256] = vector
            elif row is not None:
                invalid += 1
                connection.execute(
                    """
                    DELETE FROM embeddings
                    WHERE model_name = ? AND model_revision = ?
                      AND semantics_version = ? AND text_sha256 = ?
                    """,
                    (
                        self.model_name,
                        self.revision,
                        EMBEDDING_SEMANTICS_VERSION,
                        text_sha256,
                    ),
                )
        if invalid:
            connection.commit()
        return cached, invalid

    def _write_cache_batch(
        self,
        connection: sqlite3.Connection,
        rows,
    ) -> None:
        connection.executemany(
            """
            INSERT OR REPLACE INTO embeddings (
                model_name, model_revision, semantics_version, text_sha256,
                text_byte_length, dimension, vector_float32, vector_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()

    def _embed_without_cache(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        self._load()
        output = []
        for start in range(0, len(texts), MODEL_BATCH_SIZE):
            batch = texts[start : start + MODEL_BATCH_SIZE]
            raw = self._embed_uncached_batch(batch)
            if len(raw) != len(batch):
                raise RuntimeError("BGE-M3 returned the wrong number of embeddings")
            output.extend(
                self._canonical_float32_vector(vector)[0] for vector in raw
            )
        return output

    def embed(self, texts: List[str]) -> List[List[float]]:
        normalized = [str(value) for value in texts]
        with self._lock:
            if not self.cache_enabled:
                print(
                    f"[EMBED CACHE] disabled requested={len(normalized)}",
                    flush=True,
                )
                return self._embed_without_cache(normalized)

            connection = self._connect_cache()
            try:
                identities = [self._text_identity(text) for text in normalized]
                cached, invalid = self._read_cache(connection, identities)
                hit_count = sum(
                    1 for text_sha256, _ in identities if text_sha256 in cached
                )
                miss_count = len(normalized) - hit_count
                print(
                    f"[EMBED CACHE] requested={len(normalized)} "
                    f"hits={hit_count} misses={miss_count}",
                    flush=True,
                )
                if invalid:
                    print(
                        f"[EMBED CACHE] invalid={invalid} treated_as_miss={invalid}",
                        flush=True,
                    )
                if miss_count == 0:
                    return [cached[text_sha256] for text_sha256, _ in identities]

                missing = []
                seen_missing = set()
                for text, (text_sha256, text_byte_length) in zip(
                    normalized, identities
                ):
                    if text_sha256 in cached or text_sha256 in seen_missing:
                        continue
                    seen_missing.add(text_sha256)
                    missing.append((text, text_sha256, text_byte_length))

                self._load()
                committed = 0
                next_progress = PROGRESS_INTERVAL
                for start in range(0, len(missing), MODEL_BATCH_SIZE):
                    batch = missing[start : start + MODEL_BATCH_SIZE]
                    raw_vectors = self._embed_uncached_batch(
                        [text for text, _, _ in batch]
                    )
                    if len(raw_vectors) != len(batch):
                        raise RuntimeError(
                            "BGE-M3 returned the wrong number of embeddings"
                        )
                    cache_rows = []
                    for (_, text_sha256, text_byte_length), raw_vector in zip(
                        batch, raw_vectors
                    ):
                        vector, payload, vector_sha256 = (
                            self._canonical_float32_vector(raw_vector)
                        )
                        cached[text_sha256] = vector
                        cache_rows.append(
                            (
                                self.model_name,
                                self.revision,
                                EMBEDDING_SEMANTICS_VERSION,
                                text_sha256,
                                text_byte_length,
                                self.expected_dimension,
                                payload,
                                vector_sha256,
                            )
                        )
                    self._write_cache_batch(connection, cache_rows)
                    committed += len(cache_rows)
                    resolved_count = sum(
                        1
                        for text_sha256, _ in identities
                        if text_sha256 in cached
                    )
                    if resolved_count >= next_progress or committed == len(missing):
                        print(
                            f"[EMBED CACHE] progress={resolved_count}/"
                            f"{len(normalized)}",
                            flush=True,
                        )
                        print(
                            f"[EMBED CACHE] committed={committed}",
                            flush=True,
                        )
                        while next_progress <= resolved_count:
                            next_progress += PROGRESS_INTERVAL
                return [cached[text_sha256] for text_sha256, _ in identities]
            except sqlite3.DatabaseError as exc:
                raise RuntimeError(
                    f"Embedding cache operation failed: {self.cache_path}: {exc}"
                ) from exc
            finally:
                connection.close()


_INSTANCES = {}


def create_bge_m3(
    model_name: str = "BAAI/bge-m3",
    revision: str = "",
    cache_path: str = "",
    cache_enabled: bool = True,
):
    resolved_cache_path = str(
        Path(cache_path or DEFAULT_EMBEDDING_CACHE).expanduser().resolve()
    )
    key = (model_name, revision, bool(cache_enabled), resolved_cache_path)
    if key not in _INSTANCES:
        _INSTANCES[key] = BgeM3Embedding(
            model_name,
            revision,
            cache_path=resolved_cache_path,
            cache_enabled=cache_enabled,
        )
    return _INSTANCES[key]
