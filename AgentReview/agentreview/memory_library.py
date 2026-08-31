import json
import re
from pathlib import Path

from agentreview.reviewer_identity import load_reviewer_suite


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def tokenize_text(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize_text(text))


def infer_reviewer_dimension(reviewer_name: str | None) -> str | None:
    if not reviewer_name:
        return None
    match = re.search(r"Reviewer\s+([A-Z]+)", reviewer_name)
    if match:
        return match.group(1)
    if "Meta" in reviewer_name:
        return "META"
    return None


def clip_text(text: str, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."


def extract_reviews_from_messages(messages: list[dict]) -> dict[str, str]:
    reviews = {}
    for message in messages:
        agent_name = message.get("agent_name")
        if agent_name and (
            agent_name.startswith("Reviewer ") or agent_name == "Final Meta-Reviewer"
        ):
            reviews[agent_name] = message.get("content", "")
    return reviews


def build_memory_record(
    sample_id: str,
    paper_title: str,
    paper_md_path: str,
    review_dir: str,
    retrieved_papers: list[dict],
    references_text: str,
    source_anchors: list[dict],
    reviews: dict[str, str],
    keyword_model_name: str | None,
    review_model_name: str,
    reviewer_suite_identity_sha256: str,
) -> dict:
    return {
        "sample_id": sample_id,
        "paper_title": paper_title,
        "paper_md_path": paper_md_path,
        "review_dir": review_dir,
        "keyword_model_name": keyword_model_name,
        "review_model_name": review_model_name,
        "reviewer_suite_identity_sha256": reviewer_suite_identity_sha256,
        "retrieved_papers": retrieved_papers,
        "references_text": references_text,
        "source_anchors": source_anchors,
        "reviews": reviews,
        "reviewer_dimensions": sorted(reviews.keys()),
        "title_normalized": normalize_text(paper_title),
    }


def update_memory_library(output_dir: str, memory_record: dict) -> dict[str, str]:
    library_root = Path(output_dir) / "single_paper_review"
    library_root.mkdir(parents=True, exist_ok=True)

    jsonl_path = library_root / "memory_library.jsonl"
    index_path = library_root / "memory_index.json"

    records = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))

    suite_identity = load_reviewer_suite().suite_identity_sha256
    records = [
        record
        for record in records
        if record.get("reviewer_suite_identity_sha256") == suite_identity
        and record.get("sample_id") != memory_record["sample_id"]
    ]
    records.append(memory_record)
    records.sort(key=lambda item: item["sample_id"])

    jsonl_path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
        + ("\n" if records else ""),
        encoding="utf-8",
    )

    index = {
        "by_sample_id": {},
        "by_title_normalized": {},
        "by_reviewer": {},
    }

    for record in records:
        sample_id = record["sample_id"]
        index["by_sample_id"][sample_id] = record["review_dir"]
        title_key = record.get("title_normalized") or normalize_text(
            record.get("paper_title", "")
        )
        index["by_title_normalized"].setdefault(title_key, []).append(sample_id)
        for reviewer_name in record.get("reviews", {}).keys():
            index["by_reviewer"].setdefault(reviewer_name, []).append(sample_id)

    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "memory_library_jsonl": str(jsonl_path),
        "memory_index_json": str(index_path),
    }


def memory_library_path(output_dir: str) -> Path:
    return Path(output_dir) / "single_paper_review" / "memory_library.jsonl"


def load_memory_records(output_dir: str) -> list[dict]:
    jsonl_path = memory_library_path(output_dir)
    if not jsonl_path.exists():
        return []

    records = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            record = json.loads(line)
            if (
                record.get("reviewer_suite_identity_sha256")
                == load_reviewer_suite().suite_identity_sha256
            ):
                records.append(record)
    return records


def _record_search_text(record: dict, reviewer_name: str | None) -> str:
    parts = [
        record.get("paper_title", ""),
        record.get("references_text", ""),
    ]
    retrieved_titles = [
        paper.get("title", "") for paper in record.get("retrieved_papers", [])
    ]
    parts.extend(retrieved_titles[:10])

    reviews = record.get("reviews", {})
    if reviewer_name and reviewer_name in reviews:
        parts.append(reviews[reviewer_name])
    else:
        parts.extend(reviews.values())
    return "\n".join(part for part in parts if part)


def _score_record(
    record: dict,
    query_tokens: list[str],
    reviewer_name: str | None,
    dimension: str | None,
) -> float:
    if not query_tokens:
        return 0.0

    record_tokens = set(tokenize_text(_record_search_text(record, reviewer_name)))
    if not record_tokens:
        return 0.0

    query_set = set(query_tokens)
    overlap = len(query_set & record_tokens)
    score = overlap / max(len(query_set), 1)

    title_tokens = set(tokenize_text(record.get("paper_title", "")))
    score += 0.25 * (len(query_set & title_tokens) / max(len(query_set), 1))

    reviews = record.get("reviews", {})
    if reviewer_name and reviewer_name in reviews:
        score += 0.2
    elif dimension and any(name.endswith(dimension) for name in reviews.keys()):
        score += 0.1

    if record.get("retrieved_papers"):
        score += 0.05
    return score


def retrieve_memory_context(
    output_dir: str,
    paper_title: str,
    reviewer_name: str | None = None,
    dimension: str | None = None,
    exclude_sample_id: str | None = None,
    top_k: int = 3,
    max_chars: int = 5000,
) -> dict:
    records = load_memory_records(output_dir)
    dimension = dimension or infer_reviewer_dimension(reviewer_name)
    query_tokens = tokenize_text(paper_title)

    scored: list[tuple[float, dict]] = []
    for record in records:
        if exclude_sample_id and record.get("sample_id") == exclude_sample_id:
            continue
        score = _score_record(record, query_tokens, reviewer_name, dimension)
        if score > 0:
            scored.append((score, record))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = scored[: max(0, top_k)]
    entries = []

    for score, record in selected:
        reviews = record.get("reviews", {})
        review_text = ""
        source_reviewer = reviewer_name
        if reviewer_name and reviewer_name in reviews:
            review_text = reviews[reviewer_name]
        elif dimension:
            matching_review = next(
                (
                    (name, text)
                    for name, text in reviews.items()
                    if name.endswith(dimension)
                ),
                None,
            )
            if matching_review:
                source_reviewer, review_text = matching_review
        elif reviews:
            source_reviewer, review_text = next(iter(reviews.items()))

        entries.append(
            {
                "sample_id": record.get("sample_id"),
                "paper_title": record.get("paper_title"),
                "reviewer_name": source_reviewer,
                "score": round(score, 4),
                "review_excerpt": clip_text(review_text, 1200),
                "retrieved_reference_titles": [
                    paper.get("title")
                    for paper in record.get("retrieved_papers", [])[:5]
                    if paper.get("title")
                ],
            }
        )

    formatted = format_memory_context(
        paper_title=paper_title,
        reviewer_name=reviewer_name,
        dimension=dimension,
        entries=entries,
        max_chars=max_chars,
    )

    return {
        "paper_title": paper_title,
        "reviewer_name": reviewer_name,
        "dimension": dimension,
        "top_k": top_k,
        "num_available_records": len(records),
        "num_selected_records": len(entries),
        "entries": entries,
        "formatted_context": formatted,
    }


def format_memory_context(
    paper_title: str,
    reviewer_name: str | None,
    dimension: str | None,
    entries: list[dict],
    max_chars: int = 5000,
) -> str:
    role_label = reviewer_name or f"{dimension or 'general'} reviewer"
    lines = [
        "=== REVIEW MEMORY CONTEXT ===",
        f"Target paper: {paper_title}",
        f"Target reviewer: {role_label}",
        "",
        "Use this memory as prior review experience, not as ground truth. Transfer only critique patterns that are supported by the current paper evidence anchors.",
    ]

    if not entries:
        lines.extend(
            [
                "",
                "No relevant prior memory records were found. Proceed using only the current paper, retrieved references, rubric, and source anchors.",
                "=== END REVIEW MEMORY CONTEXT ===",
            ]
        )
        return "\n".join(lines)

    for idx, entry in enumerate(entries, start=1):
        lines.extend(
            [
                "",
                f"[Memory {idx}] sample_id={entry.get('sample_id')} score={entry.get('score')}",
                f"Prior paper: {entry.get('paper_title')}",
                f"Prior reviewer: {entry.get('reviewer_name')}",
                "Relevant prior review excerpt:",
                entry.get("review_excerpt") or "(empty)",
            ]
        )
        ref_titles = entry.get("retrieved_reference_titles", [])
        if ref_titles:
            lines.append("Related reference titles from that run:")
            lines.extend(f"- {title}" for title in ref_titles)

    lines.append("=== END REVIEW MEMORY CONTEXT ===")
    return clip_text("\n".join(lines), max_chars)
