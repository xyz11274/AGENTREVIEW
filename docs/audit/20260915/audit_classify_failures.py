#!/usr/bin/env python3
"""Replay historical classify caches; no model calls or historical file changes.

Run with the same historical evaluation Python used by recover_paper_scores.py.
The sole output is classify_failure_audit.json beside this script. This script
records counts, artifact paths, and parse outcomes without raw review text.
"""

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def main():
    root = Path("/data0/xyj/shengao")
    coco = root / "CoCoReviewBench"
    base = root / "AgentReview_0831/artifacts/research_v2/preliminary_common902"
    replay_path = base / "build_inputs.py"
    sys.path.insert(0, str(coco))
    spec = importlib.util.spec_from_file_location("historical_cache_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    from src.utils import english_word_count, split_text_into_chunks
    _, prompt_body = replay.classify_reviews_prompt()
    allowed, _ = replay.classify_categories()
    manifest = json.loads((base / "manifest.json").read_text())

    @lru_cache(maxsize=30000)
    def parse(content):
        return replay.parse_labels_from_llm(content, allowed)

    arms = {}
    for arm, config in replay.ARMS.items():
        records = list(map(json.loads, config["input"].read_text().splitlines()))
        reasons = Counter()
        failed_ids = []
        failed_opinions = 0
        failed_single_chunk = 0
        all_attempts_more_than_three = 0
        opinion_count = 0
        examples = []
        for line_number, record in enumerate(records, 1):
            paper_failed = False
            for index, opinion in enumerate(record["opinions"]):
                opinion_count += 1
                comment = "\n".join(record["sentence_texts"][i] for i in opinion)
                prompt = replay.build_classify_request_prompt(prompt_body, comment)
                attempts = []
                succeeded = False
                for penalty in (0.0, 0.5, 1.0):
                    key = replay.cache_key(prompt=prompt, base_url=config["base_url"],
                                           cache_version=config["cache_version"], penalty=penalty)
                    content = replay.load_cache_content(config["cache"], key)
                    labels, format_ok = parse(content) if content else (set(), False)
                    failures = []
                    if content is None:
                        failures.append("missing_or_empty_cache")
                    else:
                        if format_ok is False:
                            failures.append("duplicate_or_unrecognized_labels")
                        if len(labels) > 3:
                            failures.append("more_than_3_labels")
                        if "N/A" in labels and len(labels) > 1:
                            failures.append("mixed_NA")
                    attempts.append({"presence_penalty": penalty, "reasons": failures,
                                     "parsed_label_count": len(labels), "format_ok": format_ok,
                                     "cache_path": str(config["cache"] / f"{key}.json")})
                    if format_ok and 1 <= len(labels) <= 3 and ("N/A" not in labels or len(labels) == 1):
                        succeeded = True
                        break
                # N/A is an accepted fallback under the historical standard;
                # later attempts can change labels but cannot make it a failure.
                if succeeded:
                    continue
                paper_failed = True
                failed_opinions += 1
                failed_single_chunk += len(opinion) == 1
                failure_union = {reason for attempt in attempts for reason in attempt["reasons"]}
                reasons["+".join(sorted(failure_union))] += 1
                all_attempts_more_than_three += all("more_than_3_labels" in attempt["reasons"] for attempt in attempts)
                if len(examples) < 3:
                    examples.append({"paper_id": record["id"], "source_jsonl_line": line_number,
                                     "opinion_index": index, "part_indices": opinion,
                                     "character_count": len(comment), "english_words": english_word_count(comment),
                                     "deterministic_chunk_count_on_replay": len(split_text_into_chunks(comment)),
                                     "attempts": attempts})
            if paper_failed:
                failed_ids.append(record["id"])
        expected = manifest["arm_stats"][arm]
        matches = failed_opinions == expected["failed_opinions"] and len(failed_ids) == expected["failed_papers"]
        if matches is False:
            raise RuntimeError(f"Replayed failures differ from the historical manifest: {arm}")
        arms[arm] = {"source": str(config["input"]), "source_sha256": replay.sha256_file(config["input"]),
                     "paper_count": len(records), "opinion_count": opinion_count,
                     "failed_papers": len(failed_ids), "failed_opinions": failed_opinions,
                     "failed_single_chunk_opinions": failed_single_chunk,
                     "all_three_attempts_more_than_three_labels": all_attempts_more_than_three,
                     "failure_reason_union": dict(reasons), "historical_manifest_counts_match": matches,
                     "failed_paper_ids": failed_ids, "examples": examples}
        print(arm, "failed papers:", len(failed_ids), "failed opinions:", failed_opinions, flush=True)
    sources = [Path(__file__).resolve(), replay_path, coco / "src/utils.py",
               coco / "src/evaluation/_2_split.py", coco / "src/evaluation/_3_classify.py",
               base / "manifest.json"]
    report = {"protocol": "historical-coco-classify-cache-failure-diagnostic-v1",
              "created_at": datetime.now(timezone.utc).isoformat(), "model_calls": 0,
              "historical_artifacts_modified": False,
              "thresholds": {"chunk_max_english_words": 150, "accepted_label_count_min": 1,
                             "accepted_label_count_max": 3, "presence_penalties": [0.0, 0.5, 1.0]},
              "interpretation": "Dominant failure is more than three recognized labels on one pre-split chunk. This is a review segmentation and classification contract mismatch; this replay supplies no evidence of tokenizer failure.",
              "limitations": "Missing or empty cache does not identify the original transport or generation failure.",
              "source_bindings": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              "arms": arms}
    output = Path(__file__).with_name("classify_failure_audit.json")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
