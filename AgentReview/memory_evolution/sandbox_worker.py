"""JSON-RPC worker for one capability-confined MemoryProgram process.

This file intentionally imports only the Python standard library. It is executed
inside bubblewrap and has no project, network, environment, or writable host path.
"""

import hashlib
import builtins
import collections
import dataclasses
import datetime
import heapq
import itertools
import json
import math
import re
import sqlite3
import statistics
import sys
import textwrap
import time
import traceback
import types
import typing
from dataclasses import MISSING, fields


_WIRE_OUT = sys.stdout
sys.stdout = sys.stderr
_next_call_id = 0


def _send(value):
    _WIRE_OUT.write(json.dumps(value, ensure_ascii=False) + "\n")
    _WIRE_OUT.flush()


def _receive():
    line = sys.stdin.readline()
    if not line:
        raise EOFError("sandbox parent closed the command channel")
    return json.loads(line)


def _tool_call(name, *args, **kwargs):
    global _next_call_id
    _next_call_id += 1
    call_id = _next_call_id
    _send(
        {
            "type": "tool_call",
            "id": call_id,
            "name": name,
            "args": args,
            "kwargs": kwargs,
        }
    )
    response = _receive()
    if response.get("type") != "tool_result" or response.get("id") != call_id:
        raise RuntimeError("invalid toolkit response")
    if not response.get("ok"):
        raise RuntimeError(response.get("error") or f"tool call failed: {name}")
    return response.get("value")


def _cosine(left, right):
    size = min(len(left), len(right))
    if size <= 0:
        return 0.0
    dot = sum(float(left[i]) * float(right[i]) for i in range(size))
    left_norm = math.sqrt(sum(float(left[i]) ** 2 for i in range(size)))
    right_norm = math.sqrt(sum(float(right[i]) ** 2 for i in range(size)))
    return 0.0 if not left_norm or not right_norm else dot / (left_norm * right_norm)


class VectorCollection:
    def __init__(self, name):
        self.name = str(name)
        self.records = []
        self.ids = set()

    def add(
        self,
        texts=None,
        embeddings=None,
        metadatas=None,
        ids=None,
        documents=None,
    ):
        texts = texts if texts is not None else documents
        texts = list(texts or [])
        embeddings = list(embeddings or [])
        if texts and not embeddings:
            embeddings = _tool_call("embed", [str(text) for text in texts])
        metadatas = metadatas or [{} for _ in texts]
        ids = ids or [str(len(self.records) + i) for i in range(len(texts))]
        if not (len(texts) == len(embeddings) == len(metadatas) == len(ids)):
            raise ValueError("vector collection arguments must have equal lengths")
        for record_id, text, embedding, metadata in zip(
            ids, texts, embeddings, metadatas
        ):
            record_id = str(record_id)
            if record_id in self.ids:
                continue
            self.ids.add(record_id)
            self.records.append(
                {
                    "id": record_id,
                    "text": str(text),
                    "embedding": [float(value) for value in embedding],
                    "metadata": dict(metadata),
                }
            )

    def query(
        self,
        query_embedding=None,
        top_k=5,
        query_embeddings=None,
        query_texts=None,
        n_results=None,
        where=None,
        include=None,
        **kwargs,
    ):
        chroma_style = query_embeddings is not None or query_texts is not None
        if chroma_style:
            values = list(query_embeddings or [])
            if not values and query_texts:
                values = _tool_call(
                    "embed", [str(text) for text in query_texts]
                )
            query_embedding = values[0] if values else []
            top_k = n_results if n_results is not None else top_k
        scored = []
        for record in self.records:
            if where and any(
                record["metadata"].get(key) != value
                for key, value in dict(where).items()
            ):
                continue
            scored.append(
                {
                    "id": record["id"],
                    "text": record["text"],
                    "metadata": record["metadata"],
                    "score": _cosine(query_embedding, record["embedding"]),
                }
            )
        scored.sort(key=lambda value: value["score"], reverse=True)
        selected = scored[: max(0, int(top_k))]
        if not chroma_style:
            return selected
        return {
            "ids": [[item["id"] for item in selected]],
            "documents": [[item["text"] for item in selected]],
            "metadatas": [[item["metadata"] for item in selected]],
            "distances": [[1.0 - item["score"] for item in selected]],
        }

    def __len__(self):
        return len(self.records)


class ChromaClient:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, **kwargs):
        name = str(name)
        if name not in self.collections:
            self.collections[name] = VectorCollection(name)
        return self.collections[name]

    def create_collection(self, name, **kwargs):
        return self.get_or_create_collection(name, **kwargs)

    def get_collection(self, name, **kwargs):
        return self.collections[str(name)]

    def delete_collection(self, name):
        self.collections.pop(str(name), None)

    def list_collections(self):
        return list(self.collections.values())


class _ChromaModule:
    @staticmethod
    def EphemeralClient(*args, **kwargs):
        return ChromaClient()


class _Logger:
    def debug(self, message):
        _tool_call("log", "candidate_debug", str(message))


class CapabilityToolkit:
    def __init__(self):
        self.collections = {}
        self.db = sqlite3.connect(":memory:")
        self.chroma = ChromaClient()
        self.logger = _Logger()

    def llm_completion(self, messages, **kwargs):
        return str(_tool_call("llm_completion", messages, **kwargs))

    def embed(self, texts):
        return _tool_call("embed", [str(text) for text in texts])

    def create_vector_collection(self, name):
        return self.chroma.get_or_create_collection(name)

    def retrieve_s2(self, paper_id, limit=10):
        return _tool_call("retrieve_s2", str(paper_id), int(limit))

    def log(self, event, payload=None):
        _tool_call("log", str(event), payload)


def _snapshot_value(value, toolkit):
    if value is toolkit:
        return {"__mstar_type__": "toolkit"}
    if value is toolkit.db:
        return {"__mstar_type__": "sqlite_connection"}
    if isinstance(value, VectorCollection):
        return {"__mstar_type__": "collection", "name": value.name}
    if isinstance(value, set):
        return {
            "__mstar_type__": "set",
            "items": [_snapshot_value(item, toolkit) for item in sorted(value, key=repr)],
        }
    if isinstance(value, tuple):
        return {
            "__mstar_type__": "tuple",
            "items": [_snapshot_value(item, toolkit) for item in value],
        }
    if isinstance(value, list):
        return [_snapshot_value(item, toolkit) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _snapshot_value(item, toolkit) for key, item in value.items()
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(
        "MemoryProgram state is not checkpoint-serializable: "
        + type(value).__name__
    )


def _restore_value(value, toolkit):
    if isinstance(value, list):
        return [_restore_value(item, toolkit) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get("__mstar_type__")
    if marker == "toolkit":
        return toolkit
    if marker == "sqlite_connection":
        return toolkit.db
    if marker == "collection":
        return toolkit.chroma.get_or_create_collection(value["name"])
    if marker == "set":
        return {_restore_value(item, toolkit) for item in value.get("items", [])}
    if marker == "tuple":
        return tuple(_restore_value(item, toolkit) for item in value.get("items", []))
    return {key: _restore_value(item, toolkit) for key, item in value.items()}


def _snapshot_memory(knowledge_base, toolkit):
    return {
        "snapshot_version": "mstar-memory-snapshot-v1",
        "knowledge_base_state": {
            key: _snapshot_value(value, toolkit)
            for key, value in knowledge_base.__dict__.items()
        },
        "sqlite_dump": list(toolkit.db.iterdump()),
        "collections": {
            name: {
                "records": collection.records,
                "ids": sorted(collection.ids),
            }
            for name, collection in toolkit.chroma.collections.items()
        },
    }


def _restore_memory(knowledge_base, toolkit, snapshot):
    if snapshot.get("snapshot_version") != "mstar-memory-snapshot-v1":
        raise ValueError("Unsupported or legacy Memory snapshot")
    old_db = toolkit.db
    new_db = sqlite3.connect(":memory:")
    script = "\n".join(str(line) for line in snapshot.get("sqlite_dump") or [])
    if script.strip():
        new_db.executescript(script)
    toolkit.db = new_db
    toolkit.chroma = ChromaClient()
    for name, payload in dict(snapshot.get("collections") or {}).items():
        collection = toolkit.chroma.get_or_create_collection(name)
        collection.records = list(payload.get("records") or [])
        collection.ids = set(payload.get("ids") or [])
    state = {
        key: _restore_value(value, toolkit)
        for key, value in dict(snapshot.get("knowledge_base_state") or {}).items()
    }
    knowledge_base.__dict__.clear()
    knowledge_base.__dict__.update(state)
    try:
        old_db.close()
    except Exception:
        pass


class _SafeSQLite:
    @staticmethod
    def connect(database, *args, **kwargs):
        if database != ":memory:":
            raise PermissionError("MemoryProgram SQLite is restricted to ':memory:'")
        return sqlite3.connect(database, *args, **kwargs)


_SAFE_MODULES = {
    "collections": collections,
    "dataclasses": dataclasses,
    "datetime": datetime,
    "hashlib": hashlib,
    "heapq": heapq,
    "itertools": itertools,
    "json": json,
    "math": math,
    "re": re,
    "sqlite3": _SafeSQLite,
    "statistics": statistics,
    "textwrap": textwrap,
    "time": time,
    "typing": typing,
    "chromadb": _ChromaModule,
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level:
        raise ImportError("relative imports are disabled in MemoryPrograms")
    root = str(name).split(".", 1)[0]
    if root not in _SAFE_MODULES:
        raise ImportError(f"MemoryProgram import is not allowed: {name}")
    return _SAFE_MODULES[root]


def _load_program(path):
    source = builtins.open(path, "r", encoding="utf-8").read()
    code = builtins.compile(source, path, "exec")
    safe_builtins = dict(vars(builtins))
    for name in (
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "input",
        "locals",
        "open",
        "vars",
    ):
        safe_builtins.pop(name, None)
    safe_builtins["__import__"] = _safe_import
    module = types.ModuleType("candidate_memory_program")
    module.__dict__["__builtins__"] = safe_builtins
    module.__dict__["__file__"] = "<isolated-memory-program>"
    sys.modules[module.__name__] = module
    builtins.exec(code, module.__dict__)
    return module


def _run(program_path):
    program = _load_program(program_path)
    toolkit = CapabilityToolkit()
    knowledge_base = program.KnowledgeBase(toolkit)
    _send({"type": "ready"})
    while True:
        command = _receive()
        command_id = command.get("id")
        name = command.get("command")
        try:
            if name == "close":
                _send({"type": "result", "id": command_id, "ok": True, "value": None})
                return
            if name == "write":
                item = program.KnowledgeItem(**dict(command.get("item") or {}))
                knowledge_base.write(item, str(command.get("raw_text") or ""))
                value = None
            elif name == "read":
                query = program.Query(**dict(command.get("query") or {}))
                value = knowledge_base.read(query)
                if not isinstance(value, str):
                    raise TypeError("KnowledgeBase.read() must return str")
            elif name == "schemas":
                def schema(dataclass_type):
                    values = []
                    for field in fields(dataclass_type):
                        type_name = getattr(field.type, "__name__", str(field.type))
                        has_default = field.default is not MISSING
                        has_factory = field.default_factory is not MISSING
                        default = field.default if has_default else None
                        if has_factory:
                            try:
                                default = field.default_factory()
                            except Exception:
                                default = None
                        if default is not None and not isinstance(
                            default, (str, int, float, bool, list, dict)
                        ):
                            default = None
                        values.append(
                            {
                                "name": field.name,
                                "type": type_name,
                                "has_default": has_default,
                                "has_factory": has_factory,
                                "default": default,
                            }
                        )
                    return values

                value = {
                    "knowledge_item": schema(program.KnowledgeItem),
                    "query": schema(program.Query),
                    "instructions": {
                        "knowledge_item": program.INSTRUCTION_KNOWLEDGE_ITEM,
                        "query": program.INSTRUCTION_QUERY,
                        "response": program.INSTRUCTION_RESPONSE,
                        "always_on": program.ALWAYS_ON_KNOWLEDGE,
                    },
                }
            elif name == "snapshot":
                value = _snapshot_memory(knowledge_base, toolkit)
            elif name == "restore":
                _restore_memory(
                    knowledge_base,
                    toolkit,
                    dict(command.get("snapshot") or {}),
                )
                value = None
            else:
                raise ValueError(f"unknown sandbox command: {name}")
            _send({"type": "result", "id": command_id, "ok": True, "value": value})
        except BaseException as exc:
            _send(
                {
                    "type": "result",
                    "id": command_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=12),
                }
            )


if __name__ == "__main__":
    try:
        _run(sys.argv[1])
    except BaseException as exc:
        _send(
            {
                "type": "fatal",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=12),
            }
        )
        raise
