"""Transport evidence and artifact checks around unchanged official CoCo code.

Original parsing, recovery and score applicability belong to CoCo. Diagnostics
must not turn an upstream-accepted recovery into a failed evaluation.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path

from .io import atomic_write_json, read_json, value_sha256
from .request_admission import async_request_slot
from .execution_telemetry import span, RequestTrace
from .runtime_limits import request_deadline, remaining_seconds, RequestWallTimeout


def transport_diagnostics(client):
    """Observe upstream attempts without changing retry, cache or score semantics."""
    from .development_coco import failure_kind
    groups = list(client.wire_attempts.values())
    attempts = [attempt for group in groups for attempt in group]
    failures = {}
    for attempt in attempts:
        if attempt.get("status") != "failed":
            continue
        error = attempt.get("error", {})
        key = failure_kind(error.get("type", ""), error.get("message", ""))
        failures[key] = failures.get(key, 0) + 1
    return {"attempts": len(attempts), "repeated_attempts": sum(max(0, len(group) - 1) for group in groups),
            "failed_attempts": sum(failures.values()), "failure_kinds": failures}


def audited_client_class(base):
    class AuditedClient(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.response_model = self.model_name
            self.audit_tasks = {}
            self.wire_attempts = {}

        def _cache_load(self, key):
            audit = self.cache_dir / f"{key}.audit.json"
            if not audit.is_file():
                return None
            receipt = read_json(audit)
            if receipt.get("status") != "complete":
                return None
            cached = super()._cache_load(key)
            if cached is None:
                return None
            completion = receipt.get("completion")
            if completion is not None and completion.get("model") != self.response_model:
                raise ValueError("CoCo response receipt belongs to a different configured model")
            if receipt["upstream_content"] != cached[0]:
                raise ValueError("CoCo cache differs from its recorded upstream result")
            self.audit_tasks[key] = receipt
            return cached

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

        async def _perform_completion(self, api_payload):
            with request_deadline() as deadline:
                try:
                    async with asyncio.timeout(remaining_seconds(deadline)):
                        return await super()._create_completion_async(api_payload)
                except TimeoutError as exc:
                    if isinstance(exc, RequestWallTimeout):
                        raise
                    raise RequestWallTimeout('CoCo request wall-clock timeout exhausted') from exc

        async def _create_completion_async(self, api_payload):
            digest = value_sha256(api_payload)
            directory = self.cache_dir / "wire" / digest
            attempts = self.wire_attempts.setdefault(digest, [])
            attempt = len(attempts) + 1
            record = {"attempt": attempt, "payload": api_payload, "status": "reserved"}
            attempts.append(record)
            atomic_write_json(directory / f"attempt-{attempt}.request.json", record)
            try:
                record["tokens"] = await asyncio.to_thread(self._token_budget, api_payload)
                with RequestTrace(directory / f'attempt-{attempt}.progress.json', request_id=digest,
                                  component='coco:' + self.model_name, attempt=attempt,
                                  sdk_max_retries=self.client.max_retries,
                                  input_tokens=record['tokens']['input_tokens'],
                                  output_budget=record['tokens']['output_budget']) as trace:
                    async with async_request_slot(self.base_url):
                        with span("coco_model_request", service=self.base_url, request_digest=digest):
                            response = await self._perform_completion(api_payload)
                    completion = response.model_dump(mode="json")
                    trace.update(response_id=completion.get('id'), usage=completion.get('usage'),
                        finish_reasons={str(c['index']): c.get('finish_reason') for c in completion.get('choices', [])},
                        content_chars=sum(len(c.get('message', {}).get('content') or '') for c in completion.get('choices', [])))
                record["completion"] = response.model_dump(mode="json")
                if response.model != self.response_model:
                    raise ValueError("CoCo endpoint returned a different configured model")
                record["status"] = "complete"
                return response
            except Exception as exc:
                record.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
                raise
            finally:
                atomic_write_json(directory / f"attempt-{attempt}.result.json", record)

        async def get_response(self, key, messages, api_payload):
            # Preserve the author's retries, extraction, empty/error responses,
            # token accounting and cache writes. Recording observes that result.
            result = await super().get_response(key, messages, api_payload)
            if key in self.audit_tasks:
                return result
            attempts = self.wire_attempts.get(value_sha256(api_payload), [])
            successful = [item for item in attempts if item["status"] == "complete"]
            last = successful[-1] if successful else (attempts[-1] if attempts else {})
            receipt = {"status": "complete", "messages": messages, "payload": api_payload,
                       "upstream_content": result[0], "completion": last.get("completion"),
                       "tokens": last.get("tokens"), "transport_status": last.get("status"),
                       "transport_error": last.get("error"), "expected_model": self.response_model,
                       "wire_request": value_sha256(api_payload)}
            self.audit_tasks[key] = receipt
            atomic_write_json(self.cache_dir / f"{key}.audit.json", receipt)
            return result

    return AuditedClient


def audit_split(module, inputs, outputs, tasks):
    if len(inputs) != len(outputs):
        raise ValueError("CoCo Split lost an input review")
    evidence = []
    for source, result in zip(inputs, outputs):
        texts, prompt, _ = module.build_prompt(source["text"])
        responses = [task["upstream_content"] if "upstream_content" in task
                     else task["completion"]["choices"][0]["message"]["content"] for task in tasks.values()
                     if task["messages"][-1]["content"] == prompt]
        coverage = [module.compute_coverage(module.parse_llm_output(raw), len(texts))[0] for raw in responses]
        if not coverage:
            raise ValueError("CoCo Split has no recorded model response")
        if result.get("sentence_texts") != texts or not result.get("opinions"):
            raise ValueError("CoCo Split requires original sentence texts and nonempty opinions")
        if any(not opinion or any(type(i) is not int or not 0 <= i < len(texts) for i in opinion)
               for opinion in result["opinions"]):
            raise ValueError("CoCo Split contains invalid opinion indices")
        covered = {i for opinion in result["opinions"] for i in opinion}
        if covered != set(range(len(texts))):
            raise ValueError("CoCo Split artifact lost parts after upstream recovery")
        evidence.append({"paper_id": source["id"], "sentence_count": len(texts),
                         "opinion_count": len(result["opinions"]), "raw_coverage": coverage,
                         "output_coverage": 1.0, "upstream_recovery_used": max(coverage) != 1.0})
    return evidence


def audit_classify(module, inputs, outputs, tasks):
    if len(inputs) != len(outputs):
        raise ValueError("CoCo Classify lost an input review")
    evidence = []
    for source, result in zip(inputs, outputs):
        opinions = source["opinions"]
        categories = result.get("category", [])
        if len(categories) != len(opinions) or any(result.get(k) != v for k, v in source.items()):
            raise ValueError("CoCo Classify must preserve every opinion and supply every classification")
        # CoCo owns retry selection and N/A recovery. Do not reimplement that
        # algorithm here or require a raw response to meet extra format rules.
        evidence.append({"paper_id": source["id"], "opinion_count": len(opinions),
                         "classified_count": len(categories),
                         "na_opinion_count": sum(labels == ["N/A"] for labels in categories)})
    return evidence


def audit_judge(evaluation, paper_ids, human_refs, ai_categories, category, paper):
    evidence = []
    for pid in paper_ids:
        expected = (set(human_refs[pid]["category_correct"]) & set(ai_categories[pid])) - {"N/A"}
        actual = category[pid]["category_scores"]
        # Original summary functions decide which parsed metrics apply and
        # which failed judgments are omitted. Preserve those values and counts.
        evidence.append({"paper_id": pid, "required_categories": sorted(expected),
                         "completed_categories": sorted(actual),
                         "paper_judge_complete": paper[pid]["judge_scores"].get("success") is True})
    return evidence
