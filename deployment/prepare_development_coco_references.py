"""Prepare the original 150 development discussions for upstream CoCo curation.

This is a CPU-only, resumable stage. It preserves source rows and calls upstream
formatting/prompt functions unchanged. It does not manufacture curation labels,
call an inference service, modify the original splits, or create scoring assets.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "artifacts/paired-benchmark-20260917"
OUT = ROOT / "reference-curation"
COCO = REPO.parent / "CoCoReviewBench"
SOURCE = REPO.parent / "OpenReview/train.jsonl"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def binding(path):
    return {"path": str(path), "sha256": digest(path.read_bytes())}


def read(path):
    return json.loads(path.read_text())


def read_jsonl(path):
    # JSON strings may contain literal U+2028/U+0085; str.splitlines() would
    # incorrectly treat those as record boundaries.
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def immutable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Prepared artifact changed; preserve and diagnose: {path}")
    else:
        with path.open("xb") as stream:
            stream.write(data)
    return binding(path)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def jsonl_bytes(rows):
    return b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows)


def checkpoint(value):
    path = OUT / "progress.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(json_bytes(value))
    tmp.replace(path)


def stat_identity(path):
    value = path.stat()
    return {"path": str(path), "bytes": value.st_size, "mtime_ns": value.st_mtime_ns,
            "ctime_ns": value.st_ctime_ns, "inode": value.st_ino, "device": value.st_dev}


def upstream(name, relative):
    path = COCO / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_source(partitions):
    """One streaming pass performs both source hashing and exact-row extraction."""
    cached = OUT / "source-receipt.json"
    before = stat_identity(SOURCE)
    if cached.exists():
        receipt = read(cached)
        if receipt["source_stat"] != before or receipt["partitions"] != partitions:
            raise ValueError("Source or original split changed since extraction")
        for ref in receipt["outputs"].values():
            if binding(Path(ref["path"])) != ref:
                raise ValueError("Previously extracted source rows changed")
        return receipt
    wanted = {pid for ids in partitions.values() for pid in ids}
    selected, lines, h = {}, {}, hashlib.sha256()
    records = 0
    with SOURCE.open("rb") as stream:
        for line_no, raw in enumerate(stream, 1):
            h.update(raw)
            if not raw.strip():
                continue
            row = json.loads(raw)
            records += 1
            pid = row["id"]
            if pid in wanted:
                if pid in selected:
                    raise ValueError(f"Duplicate source ID: {pid}")
                selected[pid] = raw.rstrip(b"\r\n") + b"\n"
                lines[pid] = line_no
    if before != stat_identity(SOURCE):
        raise ValueError("Source changed during extraction")
    if set(selected) != wanted:
        raise ValueError(f"Missing original IDs: {sorted(wanted - set(selected))}")
    outputs = {split: immutable(OUT / "sources" / f"{split}.jsonl",
                               b"".join(selected[pid] for pid in ids))
               for split, ids in partitions.items()}
    receipt = {"source_stat": before, "source_sha256": h.hexdigest(),
               "source_record_count": records, "partitions": partitions,
               "source_line_numbers": lines, "outputs": outputs,
               "selection_rule": "Exact original frozen IDs and order; no resampling"}
    immutable(cached, json_bytes(receipt))
    return receipt


def check_raw_against_frozen_clean(raw, clean, upstream_split):
    """Check identity and require text differences to reproduce upstream cleanup."""
    for key in ("id", "content", "conference", "metareview", "PDF_path", "MD_path"):
        if raw.get(key) != clean.get(key):
            raise ValueError(f"{raw['id']}: raw/clean mismatch in {key}")
    if len(raw["reviews"]) != len(clean["reviews"]):
        raise ValueError(f"{raw['id']}: review thread count mismatch")
    fields, cleaned_fields = 0, 0
    for raw_thread, clean_thread in zip(raw["reviews"], clean["reviews"]):
        if len(raw_thread) != len(clean_thread):
            raise ValueError(f"{raw['id']}: discussion turn count mismatch")
        for (role, payload), (clean_role, clean_payload) in zip(raw_thread, clean_thread):
            if role != clean_role:
                raise ValueError(f"{raw['id']}: speaker identity mismatch")
            if {k: v for k, v in payload.items() if k != "value"} != {
                    k: v for k, v in clean_payload.items() if k != "value"}:
                raise ValueError(f"{raw['id']}: reviewer metadata mismatch")
            if payload["value"].keys() != clean_payload["value"].keys():
                raise ValueError(f"{raw['id']}: review field mismatch")
            for field, text in payload["value"].items():
                ids = clean_payload["value"][field]
                if not isinstance(text, str) or any(type(i) is not int or
                        not 0 <= i < len(clean["sentence_texts"]) for i in ids):
                    raise ValueError(f"{raw['id']}: invalid text or sentence indices")
                restored = "\n".join(clean["sentence_texts"][i] for i in ids)
                if re.sub(r"\s+", "", restored) != re.sub(r"\s+", "", text):
                    expected = "\n".join(upstream_split(text, min_words=0, strict_commas=False))
                    if re.sub(r"\s+", "", restored) != re.sub(r"\s+", "", expected):
                        raise ValueError(f"{raw['id']}: unexplained review content mismatch in {field}")
                    cleaned_fields += 1
                fields += 1
    return fields, cleaned_fields


def prepare():
    started = time.monotonic()
    progress = OUT / "progress.json"
    if progress.exists() and read(progress).get("status") == "failed":
        failure = read(progress)
        immutable(OUT / "preparation-failures" / f"{digest(json_bytes(failure))}.json", json_bytes(failure))
    checkpoint({"status": "preparing_source", "started_at": datetime.datetime.now().astimezone().isoformat(),
                "llm_requests_submitted": 0, "scoring_ready": False})
    partitions = {split: [r["paper_id"] for r in read(ROOT / "inputs" / split / "manifest.json")["records"]]
                  for split in ("build", "static", "rotating")}
    assert {k: len(v) for k, v in partitions.items()} == {"build": 125, "static": 25, "rotating": 125}
    source = extract_source(partitions)
    sys.path[:0] = [str(COCO / "src/benchmark"), str(COCO)]
    split_module = upstream("curation_split", "src/benchmark/_1_split_reviews.py")
    merge_module = upstream("curation_merge", "src/benchmark/_7_merge_benchmark.py")
    human_module = upstream("curation_human", "src/evaluation/_0_human_review.py")
    source_audit = read(ROOT / "audit/openreview-source-check.json")
    if source_audit.get("source_matches_original_freeze") is not True:
        raise ValueError("Clean source must match the original frozen source")
    outputs, evidence = {}, []
    from transformers import AutoTokenizer
    tokenizer_root = REPO.parent.parent / "models/Qwen3.8-27B"
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_root, local_files_only=True)
    for split in ("static", "rotating"):
        rows = read_jsonl(Path(source["outputs"][split]["path"]))
        clean_path = ROOT / "inputs/development-human-source" / f"{split}.jsonl"
        if binding(clean_path) != source_audit["partitions"][split]["source"]:
            raise ValueError(f"{split}: verified clean source changed")
        clean_list = read_jsonl(clean_path)
        if len(clean_list) != len(partitions[split]) or {r["id"] for r in clean_list} != set(partitions[split]):
            raise ValueError(f"{split}: duplicate, extra, or missing clean source ID")
        clean_rows = {r["id"]: r for r in clean_list}
        indexed, all_human, requests = [], [], []
        for raw in rows:
            pid, clean = raw["id"], clean_rows[raw["id"]]
            field_count, cleaned_count = check_raw_against_frozen_clean(raw, clean, split_module.split_text_into_chunks)
            skeleton = {"id": pid, "sentence_texts": clean["sentence_texts"],
                        "reviews": merge_module.convert_pairs(copy.deepcopy(clean["reviews"]))}
            merge_module.normalize_reviewer_roles(skeleton)
            reviewer_texts = human_module.collect_reviewer_texts({pid: skeleton}).get(pid, {})
            if len(reviewer_texts) < 2:
                raise ValueError(f"{pid}: upstream leave-one-out requires at least two scored reviewers")
            full_text = human_module.collect_all_reviewer_role_texts({pid: skeleton})[pid]
            indexed.append(skeleton)
            all_human.append({"id": pid, "text": full_text})
            pairs = list(split_module.reviews_to_text_pairs(raw["reviews"]))
            texts, roles, prompt, last = split_module.build_prompt(pairs)
            if not texts or set(texts) != set(roles) or last != len(texts):
                raise ValueError(f"{pid}: malformed upstream discussion prompt")
            system = split_module.split_reviews_system_prompt(len(texts))
            messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            # Count both modes without choosing a curation model or altering the
            # upstream output budget; actual wire requests must be checked again.
            token_counts = {str(thinking).lower(): len(tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True, enable_thinking=thinking))
                for thinking in (False, True)}
            requests.append({"id": pid, "messages": messages, "part_texts": texts,
                             "part_roles": roles, "part_count": len(texts),
                             "upstream_generation": {"temperature": 0.6, "max_completion_tokens": 16384,
                                                     "reasoning_effort": "medium"},
                             "local_qwen_token_counts_by_thinking": token_counts,
                             "required_context_with_full_output": max(token_counts.values()) + 16384,
                             "status": "prepared_not_submitted"})
            evidence.append({"paper_id": pid, "split": split, "discussion_threads": len(raw["reviews"]),
                             "discussion_turns": sum(map(len, raw["reviews"])),
                             "verified_text_fields": field_count, "sentence_count": len(clean["sentence_texts"]),
                             "fields_with_upstream_reproduced_cleanup": cleaned_count,
                             "scored_reviewers": list(reviewer_texts), "split_parts": len(texts),
                             "required_context_with_full_output": requests[-1]["required_context_with_full_output"]})
        outputs[split] = {
            "raw_discussions": source["outputs"][split],
            "indexed_human_source": immutable(OUT / "prepared" / split / "indexed-human-source.jsonl", jsonl_bytes(indexed)),
            "all_human_text": immutable(OUT / "prepared" / split / "all-human-text.jsonl", jsonl_bytes(all_human)),
            "discussion_split_requests": immutable(OUT / "prepared" / split / "discussion-split.requests.jsonl", jsonl_bytes(requests)),
            "frozen_clean_source": binding(clean_path),
        }
        checkpoint({"status": "preparing", "prepared_papers": len(evidence),
                    "llm_requests_submitted": 0, "scoring_ready": False})
    stages = [
        {"stage": "source_and_prompt_preparation", "status": "complete", "papers": 150},
        {"stage": "discussion_split", "upstream": "src/benchmark/_1_split_reviews.py", "status": "waiting_for_model_service"},
        {"stage": "block_classify", "upstream": "src/benchmark/_2_classify_reviews.py", "status": "waiting_for_discussion_split"},
        {"stage": "sentence_alignment_and_refinement", "upstream": ["src/benchmark/_3_preprocess_file.py", "src/benchmark/_3_refine_split_and_classify.py"], "status": "pending"},
        {"stage": "discussion_clustering", "upstream": "src/benchmark/_4_review_clustering.py", "status": "pending"},
        {"stage": "reviewer_conflict_detection_and_validation", "upstream": ["src/benchmark/_5_conflict_reviews.py", "src/benchmark/_5_validate_conflict_reviews.py"], "status": "pending"},
        {"stage": "author_refutation_detection_and_validation", "upstream": ["src/benchmark/_6_author_conflict.py", "src/benchmark/_6_validate_author_conflict.py"], "status": "pending"},
        {"stage": "original_merge_and_reference_integrity", "upstream": ["src/benchmark/_7_postprocess_benchmark.py", "src/benchmark/_7_merge_benchmark.py"], "status": "pending"},
        {"stage": "complete_human_split_classify_and_reviewer_selection", "status": "pending"},
    ]
    implementation_paths = [COCO / "src/utils.py", COCO / "src/benchmark/prompt_registry.py",
                            *(COCO / f"src/benchmark/{name}" for name in (
                                "_1_split_reviews.py", "_7_merge_benchmark.py")),
                            COCO / "src/evaluation/_0_human_review.py"]
    receipt = {"protocol": "paired-development-coco-source-preparation-v1", "status": "complete",
               "scoring_ready": False, "source_receipt": binding(OUT / "source-receipt.json"),
               "implementation": {str(p.relative_to(COCO)): binding(p) for p in implementation_paths},
               "tokenizer": binding(tokenizer_root / "tokenizer.json"), "outputs": outputs,
               "records": evidence, "pipeline": stages, "llm_requests_submitted": 0,
               "raw_source_coverage": {k: len(v) for k, v in partitions.items()},
               "limitations": ["Prepared human text and prompts are not validated CoCo reference assets.",
                               "No missing conflict/refutation annotation is filled with correct.",
                               "Curation model configuration and service availability are required for the next stage.",
                               "Original text is preserved; sentence splitting uses unchanged upstream semantics."]}
    immutable(OUT / "preparation-receipt.json", json_bytes(receipt))
    checkpoint({"status": "waiting_for_model_service", "cpu_preparation": "complete",
                "prepared_papers": len(evidence), "llm_requests_submitted": 0,
                "scoring_ready": False, "elapsed_seconds": time.monotonic() - started,
                "receipt": binding(OUT / "preparation-receipt.json")})
    print(json.dumps({"prepared": len(evidence), "verified_fields": sum(r["verified_text_fields"] for r in evidence),
                      "discussion_parts": sum(r["split_parts"] for r in evidence),
                      "maximum_split_context": max(r["required_context_with_full_output"] for r in evidence),
                      "receipt": str(OUT / "preparation-receipt.json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        prepare()
    except Exception as exc:
        checkpoint({"status": "failed", "error_type": type(exc).__name__, "error": str(exc),
                    "llm_requests_submitted": 0, "scoring_ready": False})
        raise
