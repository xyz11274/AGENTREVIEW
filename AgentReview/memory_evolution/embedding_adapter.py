"""Pinned BGE-M3 dense embedding adapter used by formal M★ runs."""

from typing import List


class BgeM3Embedding:
    def __init__(self, model_name: str, revision: str):
        if not revision:
            raise ValueError("BGE-M3 requires an immutable model revision")
        self.model_name = model_name
        self.revision = revision
        self._tokenizer = None
        self._model = None
        self._device = None

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

    def embed(self, texts: List[str]) -> List[List[float]]:
        self._load()
        import torch
        import torch.nn.functional as functional

        output = []
        batch_size = 8
        with torch.inference_mode():
            for start in range(0, len(texts), batch_size):
                batch = [str(value) for value in texts[start : start + batch_size]]
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
                output.extend(dense.cpu().float().tolist())
        return output


_INSTANCES = {}


def create_bge_m3(model_name: str = "BAAI/bge-m3", revision: str = ""):
    key = (model_name, revision)
    if key not in _INSTANCES:
        _INSTANCES[key] = BgeM3Embedding(model_name, revision)
    return _INSTANCES[key]
