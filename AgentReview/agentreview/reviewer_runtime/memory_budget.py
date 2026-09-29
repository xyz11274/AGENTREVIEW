"""Host-owned token accounting and selection of indivisible experience entries."""
from functools import lru_cache
import hashlib
from pathlib import Path

MEMORY_CONTRACT = "complete-experience-token-budget-v2"


@lru_cache(maxsize=4)
def _tokenizer(path, digest, size, mtime_ns):
    from tokenizers import Tokenizer
    source = Path(path)
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("Memory tokenizer differs from its frozen identity")
    tokenizer = Tokenizer.from_file(str(source))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def budget_options(runtime):
    runtime = runtime or {}
    identity = runtime.get("tokenizer")
    if identity:
        path = Path(identity["path"]).resolve()
        stat = path.stat()
        tokenizer = _tokenizer(str(path), identity["sha256"], stat.st_size, stat.st_mtime_ns)
        counter = lambda text: len(tokenizer.encode(text, add_special_tokens=False).ids)
    else:
        # Offline fixtures / legacy entry points only. UTF-8 bytes are a
        # conservative upper bound, explicitly recorded rather than called BPE.
        counter = lambda text: len(text.encode("utf-8"))
    return {"memory_token_counter": counter,
            "memory_tokenizer_identity": identity or {"provider": "utf8-byte-upper-bound"},
            "memory_context_tokens": runtime.get("context_max_tokens", 4096),
            "memory_summary_input_tokens": runtime.get("summary_input_max_tokens", 32768)}


class MemoryBudgetError(ValueError):
    failure_category = "candidate"


def select_complete_entries(entries, *, limit, count_tokens):
    if not isinstance(entries, list) or not all(isinstance(x, str) for x in entries):
        raise TypeError("Memory budget requires a list of complete text entries")
    selected, trace = [], []
    for index, entry in enumerate(entries):
        fits = count_tokens("\n\n".join([*selected, entry])) <= limit
        trace.append({"index": index, "sha256": hashlib.sha256(entry.encode()).hexdigest(),
                      "tokens": count_tokens(entry), "selected": fits})
        if fits:
            selected.append(entry)
    return selected, trace
