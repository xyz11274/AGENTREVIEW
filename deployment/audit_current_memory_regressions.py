"""CPU-only reproductions of the historical feedback and truncation symptoms."""
from pathlib import Path
import json
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "AgentReview"))
from agentreview.research_v2.memory import _deterministic_item, _experience_source
from agentreview.research_v2.seeds import vector_search, llm_summarizer
from agentreview.research_v2 import development_coco_worker as worker
from agentreview.reviewer_runtime.toolkit import MemoryToolkit


class Collection:
    def __init__(self):
        self.texts = []

    def add(self, texts, embeddings, metadatas, ids):
        self.texts.extend(texts)

    def query(self, embedding, top_k):
        return [{"id": "fixture", "score": 1, "metadata": {"role": "QUAL"},
                 "text": "Review experience with a complete conditional statement."}]


class Toolkit(MemoryToolkit):
    def __init__(self):
        super().__init__()
        self.collection = Collection()
        self.summary = "A" * 1497 + "important_complete_correction_at_end."

    def create_vector_collection(self, name):
        return self.collection

    def embed(self, texts):
        return [[1.0] for _ in texts]

    def log(self, *args):
        pass

    def llm_completion(self, *args, **kwargs):
        return self.summary


def main():
    checks = []
    review = {"review_id": "fixture", "role": "QUAL", "review_text": "Initial incorrect claim.",
              "paper_title": "Synthetic regression fixture"}
    evaluation = {"review_quality": 2, "factual_evidence_issues": ["CORRECTION_SENTINEL"],
                  "major_missing_points": [], "improvement_suggestions": []}
    raw = _experience_source(review, evaluation)
    assert "CORRECTION_SENTINEL" in raw
    for program in (vector_search, llm_summarizer):
        toolkit = Toolkit()
        item = _deterministic_item(program, review=review, role="QUAL")
        program.KnowledgeBase(toolkit).write(item, raw)
        retained = "CORRECTION_SENTINEL" in "\n".join(toolkit.collection.texts)
        checks.append({"name": program.__name__.rsplit(".", 1)[-1] + "_feedback_retention",
                       "passed": retained, "feedback_supplied_by_host": True,
                       "feedback_present_in_written_memory": retained})
    toolkit = Toolkit()
    observed = llm_summarizer.KnowledgeBase(toolkit).read(llm_summarizer.Query("query", "QUAL"))
    checks.append({"name": "summarizer_preserves_complete_returned_text", "passed": observed == toolkit.summary,
                   "returned_by_model_chars": len(toolkit.summary), "delivered_chars": len(observed),
                   "delivered_tail": observed[-12:]})
    with tempfile.TemporaryDirectory() as folder:
        worker._load_standard(REPO.parent / "CoCoReviewBench", "_4_evaluation")
        client = worker._client({"services": {"judge": {
            "base_url": "http://127.0.0.1:18001/v1", "model": "qwen3.8-27b"}}}, "judge", {"output_root": folder})
        try:
            payloads = {}
            for effort in ("none", "low", "medium", "high"):
                _, _, payload = client._prepare_request("fixture", temperature=0, reasoning_effort=effort)
                payloads[effort] = {"chat_template_kwargs": payload["extra_body"]["chat_template_kwargs"],
                                    "reasoning_effort_sent": payload.get("reasoning_effort"),
                                    "max_tokens": payload["max_tokens"]}
        finally:
            client.shutdown()
    result = {"protocol": "current-memory-regression-reproduction-v1", "model_calls": 0,
              "checks": checks, "unresolved_symptoms": sum(not x["passed"] for x in checks),
              "upstream_coco_effort_wire_mapping": payloads,
              "scope": "Real host item/source constructors and real seed read/write methods, deterministic tool fixtures; no model quality claim"}
    output = REPO / "artifacts/coco-paired-resplit-20260917/audit/memory-fix-reproduction.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    assert result["unresolved_symptoms"] == 0, "Memory regression remains unresolved"


if __name__ == "__main__":
    main()
