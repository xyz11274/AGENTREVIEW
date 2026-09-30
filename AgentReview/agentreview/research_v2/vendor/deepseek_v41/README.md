# DeepSeek-V4.1 token accounting assets

Unmodified upstream files from `deepseek-ai/DeepSeek-V4.1-Flash`, revision
`dba1be0a40aa45a94ad051997016db3960a90277` (MIT, see LICENSE):

- `encoding/encoding.py` → `encoding.py`
- `tokenizer.json`
- `LICENSE`

Source: https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/tree/dba1be0a40aa45a94ad051997016db3960a90277

These assets count text locally; they do not load model weights or perform local
generation. Ark may add provider-side prompt framing. Local chat counts are
labelled estimates; response `usage` is retained as the provider's actual count.
The tokenizer is also used for complete memory-entry selection. No network
download or remote code loading occurs at runtime.
