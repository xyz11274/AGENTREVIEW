"""Pinned dense-embedding backends with fingerprinted exact-text caches."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import multiprocessing
import re
import sqlite3
import struct
import threading
import gc
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


BGE_M3_SEMANTICS_VERSION = "bge-m3-cls-l2norm-maxlen8192-v1"
# Compatibility alias used by historical tests and cache inspection tools.
EMBEDDING_SEMANTICS_VERSION = BGE_M3_SEMANTICS_VERSION
BGE_M3_EMBEDDING_DIMENSION = 1024
QWEN3_EMBEDDING_DIMENSION = 4096
QWEN3_MAX_LENGTH = 32768
QWEN3_MODEL_ID = "Qwen/Qwen3-Embedding-8B"
EPISODE_SERIALIZATION_VERSION = "agentreview-episode-text-v1"
MODEL_BATCH_SIZE = 8
QWEN3_MODEL_BATCH_SIZE = 1
PROGRESS_INTERVAL = 256
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EMBEDDING_CACHE = PROJECT_ROOT / "cache" / "embedding_cache.sqlite3"
DEFAULT_QWEN3_EMBEDDING_CACHE = (
    PROJECT_ROOT / "cache" / "qwen3_embedding_8b_cache.sqlite3"
)
CACHE_SCHEMA_VERSION = "embedding-cache-sqlite-v1"
_IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def embedding_fingerprint(specification: Dict[str, Any]) -> str:
    """Return the complete embedding specification's stable SHA-256 identity."""
    return hashlib.sha256(_canonical_json(specification).encode("utf-8")).hexdigest()


def is_immutable_model_revision(value: str) -> bool:
    return bool(_IMMUTABLE_REVISION.fullmatch(str(value or "").strip()))


class CachedDenseEmbedding:
    """Shared cache and validation logic for immutable dense embedders."""

    provider = "dense_embedding"
    batch_size = MODEL_BATCH_SIZE

    def __init__(
        self,
        model_name: str,
        revision: str,
        *,
        cache_path: str,
        cache_enabled: bool,
        expected_dimension: int,
        semantics_version: str,
        specification: Dict[str, Any],
        fail_on_cache_miss: bool = False,
    ):
        self.model_name = str(model_name)
        self.revision = str(revision)
        self.cache_enabled = bool(cache_enabled)
        self.fail_on_cache_miss = bool(fail_on_cache_miss)
        self.cache_path = Path(cache_path).expanduser().resolve()
        self.expected_dimension = int(expected_dimension)
        if self.expected_dimension <= 0:
            raise ValueError("Expected embedding dimension must be positive")
        self.embedding_spec = dict(specification)
        self.embedding_fingerprint = embedding_fingerprint(self.embedding_spec)
        self.semantics_version = str(semantics_version)
        self._tokenizer = None
        self._model = None
        self._device = None
        self._lock = threading.Lock()
        self._truncation_stats = {
            "total_count": 0,
            "truncated_count": 0,
            "max_token_count_before_truncation": 0,
            "max_length": int(self.embedding_spec.get("max_length") or 0),
        }

    @property
    def truncation_stats(self) -> Dict[str, int]:
        return dict(self._truncation_stats)

    def _load(self) -> None:
        raise NotImplementedError

    def _embed_uncached_batch(self, batch: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def _connect_cache(self) -> sqlite3.Connection:
        connection = None
        try:
            if self.fail_on_cache_miss:
                if not self.cache_path.is_file():
                    raise FileNotFoundError(
                        f"Formal embedding cache is missing: {self.cache_path}"
                    )
                connection = sqlite3.connect(
                    f"file:{self.cache_path}?mode=ro&immutable=1",
                    uri=True,
                    timeout=30.0,
                )
            else:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(str(self.cache_path), timeout=30.0)
            connection.execute("PRAGMA busy_timeout=30000")
            if self.fail_on_cache_miss:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
            if self.fail_on_cache_miss:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                required = {"cache_metadata", "embeddings"}
                if not required.issubset(tables):
                    raise RuntimeError(
                        "Formal embedding cache schema is incomplete: "
                        f"missing={sorted(required - tables)}"
                    )
            else:
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
                if self.fail_on_cache_miss:
                    raise RuntimeError(
                        "Formal embedding cache has no schema_version metadata"
                    )
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
                f"{self.provider} embedding dimension mismatch: "
                f"observed={len(values)}, expected={self.expected_dimension}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{self.provider} embedding contains a non-finite value")
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
            or hashlib.sha256(payload).hexdigest() != str(expected_vector_sha256)
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
                    self.semantics_version,
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
                if not self.fail_on_cache_miss:
                    connection.execute(
                        """
                        DELETE FROM embeddings
                        WHERE model_name = ? AND model_revision = ?
                          AND semantics_version = ? AND text_sha256 = ?
                        """,
                        (
                            self.model_name,
                            self.revision,
                            self.semantics_version,
                            text_sha256,
                        ),
                    )
        if invalid and not self.fail_on_cache_miss:
            connection.commit()
        return cached, invalid

    @staticmethod
    def _write_cache_batch(connection: sqlite3.Connection, rows) -> None:
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

    def _cache_row(self, text: str, raw_vector) -> Tuple[List[float], tuple]:
        text_sha256, text_byte_length = self._text_identity(text)
        vector, payload, vector_sha256 = self._canonical_float32_vector(raw_vector)
        return vector, (
            self.model_name,
            self.revision,
            self.semantics_version,
            text_sha256,
            text_byte_length,
            self.expected_dimension,
            payload,
            vector_sha256,
        )

    def write_vectors_to_cache(
        self, texts: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        if self.fail_on_cache_miss:
            raise RuntimeError("Formal embedding cache is read-only")
        if len(texts) != len(vectors):
            raise ValueError("Embedding cache write requires aligned texts and vectors")
        rows = [
            self._cache_row(str(text), vector)[1]
            for text, vector in zip(texts, vectors)
        ]
        connection = self._connect_cache()
        try:
            self._write_cache_batch(connection, rows)
        finally:
            connection.close()

    def _embed_without_cache(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        self._load()
        output = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            raw = self._embed_uncached_batch(batch)
            if len(raw) != len(batch):
                raise RuntimeError(
                    f"{self.provider} returned the wrong number of embeddings"
                )
            output.extend(self._canonical_float32_vector(vector)[0] for vector in raw)
        return output

    def embed(self, texts: List[str]) -> List[List[float]]:
        normalized = [str(value) for value in texts]
        with self._lock:
            if not self.cache_enabled:
                print(
                    f"[EMBED CACHE] provider={self.provider} disabled "
                    f"requested={len(normalized)}",
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
                    f"[EMBED CACHE] provider={self.provider} "
                    f"fingerprint={self.embedding_fingerprint[:12]} "
                    f"requested={len(normalized)} hits={hit_count} misses={miss_count}",
                    flush=True,
                )
                if invalid:
                    print(
                        f"[EMBED CACHE] invalid={invalid} treated_as_miss={invalid}",
                        flush=True,
                    )
                if miss_count == 0:
                    return [cached[text_sha256] for text_sha256, _ in identities]
                if self.fail_on_cache_miss:
                    raise RuntimeError(
                        "Formal embedding cache miss: "
                        f"requested={len(normalized)} hits={hit_count} "
                        f"misses={miss_count} invalid={invalid}; online model "
                        "loading and cache mutation are disabled"
                    )

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
                for start in range(0, len(missing), self.batch_size):
                    batch = missing[start : start + self.batch_size]
                    raw_vectors = self._embed_uncached_batch(
                        [text for text, _, _ in batch]
                    )
                    if len(raw_vectors) != len(batch):
                        raise RuntimeError(
                            f"{self.provider} returned the wrong number of embeddings"
                        )
                    cache_rows = []
                    for (text, text_sha256, _), raw_vector in zip(batch, raw_vectors):
                        vector, row = self._cache_row(text, raw_vector)
                        cached[text_sha256] = vector
                        cache_rows.append(row)
                    self._write_cache_batch(connection, cache_rows)
                    committed += len(cache_rows)
                    resolved_count = sum(
                        1 for text_sha256, _ in identities if text_sha256 in cached
                    )
                    if resolved_count >= next_progress or committed == len(missing):
                        print(
                            f"[EMBED CACHE] progress={resolved_count}/{len(normalized)} "
                            f"committed={committed}",
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


class BgeM3Embedding(CachedDenseEmbedding):
    provider = "bge_m3"

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
        super().__init__(
            model_name,
            revision,
            cache_path=str(cache_path or DEFAULT_EMBEDDING_CACHE),
            cache_enabled=cache_enabled,
            expected_dimension=expected_dimension,
            semantics_version=BGE_M3_SEMANTICS_VERSION,
            specification={
                "provider": "bge_m3",
                "model_id": str(model_name),
                "model_revision": str(revision),
                "dimension": int(expected_dimension),
                "max_length": 8192,
                "normalize_embeddings": True,
                "pooling": "cls",
                "serialization_version": EPISODE_SERIALIZATION_VERSION,
            },
        )

    def _load(self) -> None:
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
            encoded = {key: value.to(self._device) for key, value in encoded.items()}
            hidden = self._model(**encoded).last_hidden_state[:, 0]
            dense = functional.normalize(hidden, p=2, dim=1)
            return dense.cpu().float().tolist()


class Qwen3Embedding(CachedDenseEmbedding):
    """Model-native Qwen3-Embedding-8B document-style encoder."""

    provider = "qwen3_embedding"
    batch_size = QWEN3_MODEL_BATCH_SIZE

    def __init__(
        self,
        model_name: str,
        revision: str,
        *,
        cache_path: str = "",
        cache_enabled: bool = True,
        device: str = "",
        expected_dimension: int = QWEN3_EMBEDDING_DIMENSION,
        max_length: int = QWEN3_MAX_LENGTH,
        batch_size: int = QWEN3_MODEL_BATCH_SIZE,
        fail_on_cache_miss: bool = False,
    ):
        if str(model_name) != QWEN3_MODEL_ID:
            raise ValueError(
                f"Formal Qwen embedding requires the dedicated {QWEN3_MODEL_ID} model"
            )
        if not is_immutable_model_revision(revision):
            raise ValueError(
                "Qwen3-Embedding-8B requires an immutable resolved commit SHA"
            )
        self.requested_device = str(device or "")
        self.batch_size = max(1, int(batch_size))
        spec = {
            "provider": self.provider,
            "model_id": str(model_name),
            "model_revision": str(revision),
            "dimension": int(expected_dimension),
            "max_length": int(max_length),
            "normalize_embeddings": True,
            "pooling": "last_token",
            "serialization_version": EPISODE_SERIALIZATION_VERSION,
        }
        fingerprint = embedding_fingerprint(spec)
        super().__init__(
            model_name,
            revision,
            cache_path=str(cache_path or DEFAULT_QWEN3_EMBEDDING_CACHE),
            cache_enabled=cache_enabled,
            expected_dimension=expected_dimension,
            semantics_version=f"qwen3e8b:{fingerprint}",
            specification=spec,
            fail_on_cache_miss=fail_on_cache_miss,
        )
        self.max_length = int(max_length)
        self.attention_implementation = ""
        self.runtime_dtype = ""

    @staticmethod
    def _check_dependencies() -> None:
        from importlib.metadata import PackageNotFoundError, version

        from packaging.version import Version

        required = {
            "transformers": Version("4.51.0"),
            "sentence-transformers": Version("2.7.0"),
        }
        failures = []
        for package, minimum in required.items():
            try:
                installed = Version(version(package))
            except PackageNotFoundError:
                failures.append(f"{package}>={minimum} (missing)")
                continue
            if installed < minimum:
                failures.append(f"{package}>={minimum} (installed {installed})")
        if failures:
            raise RuntimeError(
                "Qwen3-Embedding-8B dependency requirement failed: "
                + ", ".join(failures)
            )

    @staticmethod
    def _disable_broken_optional_torchvision() -> None:
        try:
            import torchvision  # noqa: F401
        except (ImportError, RuntimeError):
            import transformers.utils.import_utils as import_utils

            import_utils._torchvision_available = False

    def _load(self) -> None:
        if self._model is not None:
            return
        self._check_dependencies()
        self._disable_broken_optional_torchvision()

        import torch
        from sentence_transformers import SentenceTransformer

        device = self.requested_device or (
            "cuda:0" if torch.cuda.is_available() else "cpu"
        )
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(f"Requested embedding device is unavailable: {device}")
        use_bfloat16 = device.startswith("cuda") and torch.cuda.is_bf16_supported()
        dtype = torch.bfloat16 if use_bfloat16 else torch.float32
        self.runtime_dtype = "bfloat16" if use_bfloat16 else "float32"
        candidates = ["sdpa", "eager"]
        if device.startswith("cuda") and importlib.util.find_spec("flash_attn"):
            candidates.insert(0, "flash_attention_2")
        failures = []
        for attention in candidates:
            model = None
            try:
                model = SentenceTransformer(
                    self.model_name,
                    revision=self.revision,
                    device=device,
                    trust_remote_code=True,
                    model_kwargs={
                        "attn_implementation": attention,
                        "dtype": dtype,
                    },
                    tokenizer_kwargs={"padding_side": "left"},
                )
                model.max_seq_length = self.max_length
                model.eval()
                observed_dimension = int(model.get_sentence_embedding_dimension())
                if observed_dimension != self.expected_dimension:
                    raise RuntimeError(
                        "Qwen3 embedding dimension mismatch after model load: "
                        f"observed={observed_dimension}, "
                        f"expected={self.expected_dimension}"
                    )
                if str(model.tokenizer.padding_side) != "left":
                    raise RuntimeError("Qwen3 tokenizer padding_side must be left")
                self._model = model
                self._tokenizer = model.tokenizer
                self._device = device
                self.attention_implementation = attention
                print(
                    f"[QWEN3 EMBEDDING] device={device} dtype={self.runtime_dtype} "
                    f"attention={attention} max_length={self.max_length}",
                    flush=True,
                )
                return
            except torch.cuda.OutOfMemoryError as exc:
                model = None
                gc.collect()
                torch.cuda.empty_cache()
                raise RuntimeError(
                    f"Qwen3-Embedding-8B has insufficient memory on {device}"
                ) from exc
            except Exception as exc:
                failures.append(f"{attention}: {type(exc).__name__}: {exc}")
                model = None
                gc.collect()
                if device.startswith("cuda"):
                    torch.cuda.empty_cache()
        raise RuntimeError(
            "Qwen3-Embedding-8B failed to load with supported attention "
            "implementations: " + " | ".join(failures)
        )

    def audit_token_lengths(self, texts: Iterable[str]) -> Dict[str, int]:
        if self._tokenizer is None:
            self._check_dependencies()
            self._disable_broken_optional_torchvision()
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                revision=self.revision,
                trust_remote_code=True,
                padding_side="left",
            )
        values = [str(text) for text in texts]
        total = 0
        truncated = 0
        maximum = 0
        for start in range(0, len(values), 64):
            batch = values[start : start + 64]
            encoded = self._tokenizer(
                batch,
                add_special_tokens=True,
                padding=False,
                truncation=False,
            )
            lengths = [len(input_ids) for input_ids in encoded["input_ids"]]
            total += len(lengths)
            truncated += sum(length > self.max_length for length in lengths)
            maximum = max([maximum, *lengths])
        self._truncation_stats = {
            "total_count": total,
            "truncated_count": truncated,
            "max_token_count_before_truncation": maximum,
            "max_length": self.max_length,
        }
        return self.truncation_stats

    def _embed_uncached_batch(self, batch: List[str]) -> List[List[float]]:
        import numpy as np
        import torch

        # No prompt_name or retrieval instruction is supplied: every episode and
        # validation item uses the same symmetric document-style representation.
        with torch.inference_mode():
            vectors = self._model.encode(
                batch,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        values = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if np.any(~np.isfinite(values)) or np.any(norms <= 0):
            raise ValueError("qwen3_embedding produced an invalid vector")
        values = values / norms
        return values.tolist()


_INSTANCES: Dict[tuple, CachedDenseEmbedding] = {}


def create_bge_m3(
    model_name: str = "BAAI/bge-m3",
    revision: str = "",
    cache_path: str = "",
    cache_enabled: bool = True,
    **_: Any,
):
    resolved_cache_path = str(
        Path(cache_path or DEFAULT_EMBEDDING_CACHE).expanduser().resolve()
    )
    key = ("bge_m3", model_name, revision, bool(cache_enabled), resolved_cache_path)
    if key not in _INSTANCES:
        _INSTANCES[key] = BgeM3Embedding(
            model_name,
            revision,
            cache_path=resolved_cache_path,
            cache_enabled=cache_enabled,
        )
    return _INSTANCES[key]


def create_qwen3_embedding(
    model_name: str = QWEN3_MODEL_ID,
    revision: str = "",
    cache_path: str = "",
    cache_enabled: bool = True,
    device: str = "",
    batch_size: int = QWEN3_MODEL_BATCH_SIZE,
    fail_on_cache_miss: bool = False,
    **_: Any,
):
    resolved_cache_path = str(
        Path(cache_path or DEFAULT_QWEN3_EMBEDDING_CACHE).expanduser().resolve()
    )
    key = (
        "qwen3_embedding",
        model_name,
        revision,
        bool(cache_enabled),
        resolved_cache_path,
        str(device),
        int(batch_size),
        bool(fail_on_cache_miss),
    )
    if key not in _INSTANCES:
        _INSTANCES[key] = Qwen3Embedding(
            model_name,
            revision,
            cache_path=resolved_cache_path,
            cache_enabled=cache_enabled,
            device=device,
            batch_size=batch_size,
            fail_on_cache_miss=fail_on_cache_miss,
        )
    return _INSTANCES[key]


def _qwen3_device_worker(payload):
    model_name, revision, device, batch_size, indexed_texts = payload
    embedder = Qwen3Embedding(
        model_name,
        revision,
        cache_enabled=False,
        device=f"cuda:{device}",
        batch_size=batch_size,
    )
    texts = [text for _, text in indexed_texts]
    vectors = embedder.embed(texts)
    return list(zip((index for index, _ in indexed_texts), vectors))


def embed_qwen3_on_devices(
    texts: Sequence[str],
    *,
    revision: str,
    devices: Sequence[int],
    model_name: str = QWEN3_MODEL_ID,
    cache_path: str = "",
    batch_size: int = QWEN3_MODEL_BATCH_SIZE,
) -> List[List[float]]:
    """Encode deterministic round-robin shards with one model per CUDA device."""
    normalized = [str(text) for text in texts]
    device_values = [int(device) for device in devices]
    if not device_values:
        raise ValueError("At least one embedding device is required")
    if len(set(device_values)) != len(device_values):
        raise ValueError("Embedding devices must be unique")
    if len(device_values) == 1:
        embedder = create_qwen3_embedding(
            model_name=model_name,
            revision=revision,
            cache_path=cache_path,
            device=f"cuda:{device_values[0]}",
            batch_size=batch_size,
        )
        return embedder.embed(normalized)

    shards = [[] for _ in device_values]
    for index, text in enumerate(normalized):
        shards[index % len(device_values)].append((index, text))
    payloads = [
        (model_name, revision, device, batch_size, shard)
        for device, shard in zip(device_values, shards)
        if shard
    ]
    context = multiprocessing.get_context("spawn")
    with context.Pool(processes=len(payloads)) as pool:
        worker_results = pool.map(_qwen3_device_worker, payloads)
    merged: List[List[float] | None] = [None] * len(normalized)
    for worker_result in worker_results:
        for index, vector in worker_result:
            merged[index] = vector
    if any(vector is None for vector in merged):
        raise RuntimeError("Multi-device Qwen embedding merge lost an input")
    result = [vector for vector in merged if vector is not None]
    cache = create_qwen3_embedding(
        model_name=model_name,
        revision=revision,
        cache_path=cache_path,
        device=f"cuda:{device_values[0]}",
        batch_size=batch_size,
    )
    cache.write_vectors_to_cache(normalized, result)
    return result
