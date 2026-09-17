"""Fail-closed transport and completeness checks around unchanged CoCo code."""

from __future__ import annotations

import asyncio
import json
import math
import urllib.request
from pathlib import Path

from .io import atomic_write_json, read_json, value_sha256
from .llm import validate_completion
from .request_admission import async_request_slot
from .execution_telemetry import span


def audited_client_class(base):
    class AuditedClient(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.client.max_retries = 0
            self.audit_tasks = {}
            self.wire_attempts = {}

        def _cache_load(self, key):
            audit = self.cache_dir / f"{key}.audit.json"
            if not audit.is_file():
                return None
            receipt = read_json(audit)
            if receipt.get("status") != "complete":
                return None
            self.audit_tasks[key] = receipt
            return super()._cache_load(key)

        def _token_budget(self, payload):
            base_url = self.base_url.rstrip("/").removesuffix("/v1")
            body = {"model": payload["model"], "messages": payload["messages"],
                    "add_generation_prompt": True,
                    "chat_template_kwargs": payload.get("extra_body", {}).get("chat_template_kwargs", {})}
            req = urllib.request.Request(base_url + "/tokenize", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as response:
                tokens = json.load(response)
            count, capacity = tokens.get("count"), tokens.get("max_model_len")
            output = payload.get("max_tokens", payload.get("max_completion_tokens"))
            if (type(count) is not int or type(capacity) is not int or type(output) is not int
                    or output <= 0 or count + output > capacity):
                raise ValueError(f"CoCo complete request exceeds context: input={count}, output={output}, capacity={capacity}")
            return {"input_tokens": count, "output_budget": output, "max_model_len": capacity}

        async def _create_completion_async(self, api_payload):
            digest = value_sha256(api_payload)
            directory = self.cache_dir / "wire" / digest
            attempts = self.wire_attempts.setdefault(digest, [])
            if len(attempts) >= 3:
                raise RuntimeError("CoCo request exhausted three transport attempts")
            attempt = len(attempts) + 1
            record = {"attempt": attempt, "payload": api_payload, "status": "reserved"}
            attempts.append(record)
            atomic_write_json(directory / f"attempt-{attempt}.request.json", record)
            try:
                record["tokens"] = await asyncio.to_thread(self._token_budget, api_payload)
                async with async_request_slot(self.base_url):
                    with span("coco_model_request", service=self.base_url, request_digest=digest):
                        response = await super()._create_completion_async(api_payload)
                record["completion"] = response.model_dump(mode="json")
                validate_completion(response, component="original_coco")
                record["status"] = "complete"
                return response
            except Exception as exc:
                record.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
                raise
            finally:
                atomic_write_json(directory / f"attempt-{attempt}.result.json", record)

        async def get_response(self, key, messages, api_payload):
            content, history = await super().get_response(key, messages, api_payload)
            attempts = self.wire_attempts.get(value_sha256(api_payload), [])
            successful = [item for item in attempts if item["status"] == "complete"]
            if not successful or not isinstance(content, str) or "[LLM Error]" in content:
                raise RuntimeError("CoCo request has no complete model response")
            receipt = {"status": "complete", "messages": messages, "payload": api_payload,
                       "completion": successful[-1]["completion"], "tokens": successful[-1]["tokens"],
                       "wire_request": value_sha256(api_payload)}
            self.audit_tasks[key] = receipt
            atomic_write_json(self.cache_dir / f"{key}.audit.json", receipt)
            return content, history

        def get_result(self, key, clear_cache=False):
            result = super().get_result(key, clear_cache=clear_cache)
            if key not in self.audit_tasks or result.get("status") != "done":
                raise RuntimeError(f"CoCo task {key} has no complete transport receipt")
            return result

    return AuditedClient


def audit_split(module, inputs, outputs, tasks):
    if len(inputs) != len(outputs):
        raise ValueError("CoCo Split lost an input review")
    evidence = []
    for source, result in zip(inputs, outputs):
        texts, prompt, _ = module.build_prompt(source["text"])
        responses = [task["completion"]["choices"][0]["message"]["content"] for task in tasks.values()
                     if task["messages"][-1]["content"] == prompt]
        coverage = [module.compute_coverage(module.parse_llm_output(raw), len(texts))[0] for raw in responses]
        if not coverage or max(coverage) != 1.0:
            raise ValueError("CoCo Split exhausted responses without full part coverage; fallback is not admissible")
        if result.get("sentence_texts") != texts or not result.get("opinions"):
            raise ValueError("CoCo Split requires original sentence texts and nonempty opinions")
        if any(not opinion or any(type(i) is not int or not 0 <= i < len(texts) for i in opinion)
               for opinion in result["opinions"]):
            raise ValueError("CoCo Split contains invalid opinion indices")
        evidence.append({"paper_id": source["id"], "sentence_count": len(texts),
                         "opinion_count": len(result["opinions"]), "raw_coverage": coverage})
    return evidence


def audit_classify(module, inputs, outputs, tasks):
    allowed, _ = module.classify_categories()
    _, main = module.classify_reviews_prompt()
    if len(inputs) != len(outputs):
        raise ValueError("CoCo Classify lost an input review")
    evidence = []
    for source, result in zip(inputs, outputs):
        opinions = source["opinions"]
        categories = result.get("category", [])
        normalized_na = 0
        if len(categories) != len(opinions) or any(result.get(k) != v for k, v in source.items()):
            raise ValueError("CoCo Classify must preserve every opinion and supply every classification")
        for opinion, labels in zip(opinions, categories):
            comment = "\n".join(source["sentence_texts"][i] for i in opinion).strip()
            prompt = main.strip() + "\n\n\nReviewer Comment:\n" + comment
            valid = []
            normalized = False
            for task in sorted(tasks.values(), key=lambda value: value.get("payload", {}).get("presence_penalty", 0)):
                if task["messages"][-1]["content"] != prompt:
                    continue
                raw = task["completion"]["choices"][0]["message"]["content"]
                parsed, okay = module.parse_labels_from_llm(raw, allowed)
                # Match upstream is_retry_ok and labels_candidate exactly. An
                # accepted no-category response normalizes to N/A; the fallback
                # with zero accepted responses is the unresolved failure.
                if raw and okay and "</think>" in raw:
                    valid.append(parsed if parsed else ["N/A"])
                    normalized = normalized or not parsed
            expected = (["N/A"] if any(module._is_na_labelset(item) for item in valid)
                        else min(valid, key=len)) if valid else None
            if labels != expected:
                raise ValueError("CoCo Classify has an unresolved opinion or classification without valid original-model evidence")
            normalized_na += int(normalized and labels == ["N/A"])
        evidence.append({"paper_id": source["id"], "opinion_count": len(opinions),
                         "classified_count": len(categories), "original_na_normalizations": normalized_na})
    return evidence


def audit_judge(evaluation, paper_ids, human_refs, ai_categories, category, paper):
    evidence = []
    for pid in paper_ids:
        expected = (set(human_refs[pid]["category_correct"]) & set(ai_categories[pid])) - {"N/A"}
        actual = category[pid]["category_scores"]
        if set(actual) != expected:
            raise ValueError(f"CoCo Judge has unresolved required categories for {pid}: {sorted(expected - set(actual))}")
        scores = [paper[pid]["judge_scores"], *(row["judge_scores"] for row in actual.values())]
        for row in scores:
            if row.get("success") is not True:
                raise ValueError("CoCo Judge requires every scheduled result to succeed")
            # X is upstream's legitimate non-applicable score. Missing values are not X.
            if row.get("claim_extraction") not in {"claim", "normal"}:
                raise ValueError("CoCo Judge requires its claim_extraction decision")
            for metric in (*evaluation.table_judge_metric_map.values(), "actionability"):
                value = row.get(metric)
                if (value == "X" and metric in {"verifiability", "actionability"}) or (type(value) in (int, float) and math.isfinite(value) and 1 <= value <= 5):
                    continue
                raise ValueError(f"CoCo Judge has missing/invalid required metric {metric}: {value!r}")
        evidence.append({"paper_id": pid, "required_categories": sorted(expected),
                         "completed_categories": sorted(actual), "paper_judge_complete": True})
    return evidence
