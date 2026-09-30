"""Pinned DeepSeek text tokenizer; API usage remains the billing authority."""

from pathlib import Path
from functools import lru_cache

from .io import file_sha256
from .vendor.deepseek_v41.encoding import encode_messages
from agentreview.reviewer_runtime.memory_budget import _tokenizer

ROOT = Path(__file__).parent / "vendor/deepseek_v41"
REVISION = "dba1be0a40aa45a94ad051997016db3960a90277"
TOKENIZER_SHA256 = "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b"
ENCODER_SHA256 = "502bdaec8a3fd88ebc24c4721a7038fbe42f2063c664638127056107920035c1"


def tokenizer_identity():
    return {"method": "deepseek-v4.1-local-estimate", "source_revision": REVISION,
            "encoder_sha256": ENCODER_SHA256,
            "tokenizer": {"path": str(ROOT / "tokenizer.json"), "sha256": TOKENIZER_SHA256}}


def text_tokenizer():
    path = ROOT / "tokenizer.json"
    stat = path.stat()
    return _tokenizer(str(path), TOKENIZER_SHA256, stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=1)
def _verify_encoder(size, mtime_ns):
    if file_sha256(ROOT / "encoding.py") != ENCODER_SHA256:
        raise ValueError("DeepSeek message encoder differs from its pinned upstream source")


def count_chat_tokens(messages, config):
    stat = (ROOT / "encoding.py").stat()
    _verify_encoder(stat.st_size, stat.st_mtime_ns)
    prompt = encode_messages(messages, thinking_mode="thinking" if config.enable_thinking else "chat",
                             reasoning_effort=config.reasoning_effort)
    count = len(text_tokenizer().encode(prompt, add_special_tokens=False).ids)
    return {"input_tokens": count, "max_model_len": config.max_model_len,
            "method": "deepseek-v4.1-local-estimate"}


def available_output_tokens(tokens, maximum):
    """Use the available model context without trimming any input text."""
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("Output capacity requires a positive integer")
    remaining = tokens["max_model_len"] - tokens["input_tokens"]
    if remaining <= 0:
        raise ValueError("Complete request exhausts model context; input is never truncated")
    return min(maximum, remaining)
