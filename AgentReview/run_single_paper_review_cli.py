import importlib
import hashlib
import json
import os
import re
import shlex
import sys
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from agentreview.agent import Player
    from agentreview.arguments import parse_args
    from agentreview.backends.openai import EmptyModelResponseError
    from agentreview.const import AGENTREVIEW_LOGO
    from agentreview.memory_library import (
        build_memory_record,
        extract_reviews_from_messages,
        infer_reviewer_dimension,
        load_memory_records,
        retrieve_memory_context,
        update_memory_library,
    )
    from agentreview.message import Message
    from agentreview.paper_review_player import Reviewer
    from agentreview.paper_review_player_md import MarkdownPaperExtractorPlayer
    from agentreview.paper_context import (
        DEFAULT_PAPER_CONTEXT_MAX_CHARS,
        clip_text as canonical_clip_text,
    )
    from agentreview.role_descriptions import get_paper_extractor_config
    from agentreview.single_paper_review_roles import (
        META_REVIEWER_NAME,
        REVIEWER_SPECS,
        build_meta_reviewer_config,
        build_specialist_reviewer_configs,
    )
    from agentreview.utility.utils import project_setup
    from agentreview.reviewer_runtime.memory_runtime import MemoryHarnessAdapter
    from agentreview.reviewer_runtime.episodes import build_reviewer_memory_episode_text
    from agentreview.reviewer_runtime.models import Episode
    from agentreview.reviewer_runtime.task_agent import build_task_agent
    from agentreview.reviewer_runtime.program_loader import (
        MemoryProgramLoader,
        resolve_program_path,
        verify_promoted_program,
    )
    from agentreview.reviewer_runtime.artifacts import (
        CandidateReviewRecord,
        CandidateReviewWriter,
    )
    from agentreview.reviewer_runtime.tasks import reviewer_identity
    from agentreview.reviewer_runtime.toolkit import (
        MemoryToolkit,
        frozen_s2_pool_version,
        load_frozen_s2_pool,
    )
    from agentreview.history_checkpoint import (
        canonical_sha256,
        load_checkpoint as load_history_checkpoint,
        save_checkpoint as save_history_checkpoint,
    )
except ModuleNotFoundError as exc:
    missing_name = getattr(exc, "name", "unknown dependency")
    raise SystemExit(
        "Failed to start AgentReview because a required Python dependency is missing: "
        f"{missing_name}. Install dependencies first with "
        "`pip install -r requirements.txt` from this AgentReview directory."
    ) from exc


def build_single_paper_experiment_setting() -> Dict:
    return {
        "paper_id": None,
        "paper_decision": None,
        "players": {
            "Paper Extractor": [{}],
            "Reviewer": [
                {
                    "name": spec["name"],
                    "review_dimension": spec["dimension"],
                    "focus": spec["focus"],
                }
                for spec in REVIEWER_SPECS
            ],
            "Meta Reviewer": [{"name": META_REVIEWER_NAME}],
        },
        "global_settings": {
            "provides_numeric_rating": ["reviewer", "meta_reviewer"],
            "persons_aware_of_authors_identities": []
        }
    }


def build_single_paper_players(args: Namespace):
    os.environ["AGENTREVIEW_API_MIN_INTERVAL_SECONDS"] = str(
        getattr(args, "api_min_interval_seconds", 2.0)
    )
    players = []

    extractor_cfg = get_paper_extractor_config()
    players.append(
        MarkdownPaperExtractorPlayer(
            paper_md_path=args.paper_md_path,
            args=args,
            **extractor_cfg,
        )
    )

    for reviewer_cfg in build_specialist_reviewer_configs():
        reviewer_cfg["backend"]["model"] = args.model_name
        reviewer_cfg["backend"]["temperature"] = getattr(
            args, "review_temperature", 0.3
        )
        reviewer_cfg["backend"]["max_tokens"] = getattr(args, "review_max_tokens", 8192)
        reviewer_cfg["backend"]["retry_max_tokens"] = getattr(
            args, "review_retry_max_tokens", 16384
        )
        reviewer_reasoning_effort = getattr(
            args, "reviewer_reasoning_effort", "low"
        )
        reviewer_cfg["backend"]["reasoning_effort"] = (
            None
            if reviewer_reasoning_effort == "none"
            else reviewer_reasoning_effort
        )
        reviewer_cfg["backend"]["request_timeout_seconds"] = getattr(
            args, "api_request_timeout_seconds", 600.0
        )
        reviewer_cfg["backend"]["connect_timeout_seconds"] = getattr(
            args, "api_connect_timeout_seconds", 30.0
        )
        players.append(
            Reviewer(
                data_dir=getattr(args, "data_dir", None),
                conference=getattr(args, "conference", None),
                args=args,
                **reviewer_cfg,
            )
        )

    meta_cfg = build_meta_reviewer_config()
    meta_cfg["backend"]["model"] = args.model_name
    meta_cfg["backend"]["temperature"] = getattr(
        args, "review_temperature", 0.3
    )
    meta_cfg["backend"]["max_tokens"] = getattr(
        args, "meta_review_max_tokens", 4096
    )
    meta_cfg["backend"]["request_timeout_seconds"] = getattr(
        args, "api_request_timeout_seconds", 600.0
    )
    meta_cfg["backend"]["connect_timeout_seconds"] = getattr(
        args, "api_connect_timeout_seconds", 30.0
    )
    players.append(
        Player(
            data_dir=getattr(args, "data_dir", None),
            conference=getattr(args, "conference", None),
            args=args,
            **meta_cfg,
        )
    )

    return players


def discover_markdown_papers(args: Namespace) -> List[Dict[str, str]]:
    papers: List[Dict[str, str]] = []

    if args.paper_md_path and getattr(args, "frozen_context_bundle_path", None):
        md_path = Path(args.paper_md_path).resolve()
        bundle_path = Path(args.frozen_context_bundle_path).expanduser().resolve()
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        expected_md = (
            bundle_path.parent / bundle["paper_markdown_relative_path"]
        ).resolve()
        if md_path != expected_md:
            raise RuntimeError("Single-paper frozen bundle Markdown mismatch")
        if (
            hashlib.sha256(md_path.read_bytes()).hexdigest()
            != bundle["paper_markdown_sha256"]
        ):
            raise RuntimeError("Single-paper frozen Markdown checksum mismatch")
        return [
            {
                "sample_id": str(bundle["paper_id"]),
                "paper_md_path": str(md_path),
                "paper_dir": str(md_path.parent),
                "frozen_context_bundle_path": str(bundle_path),
            }
        ]

    if getattr(args, "paper_context_manifest", None):
        manifest_path = Path(args.paper_context_manifest).expanduser().resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("contains_human_review_data") is not False:
            raise RuntimeError(
                "Frozen paper-context manifest must be marked human-review-free"
            )
        requested_split = getattr(
            args, "paper_context_split", "effectiveness_validation"
        )
        for item in manifest.get("items") or []:
            if item.get("split") != requested_split:
                continue
            bundle_path = Path(item["bundle_path"]).expanduser().resolve()
            if hashlib.sha256(bundle_path.read_bytes()).hexdigest() != item.get(
                "bundle_sha256"
            ):
                raise RuntimeError(f"Frozen bundle checksum mismatch: {bundle_path}")
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            md_path = (
                bundle_path.parent / bundle["paper_markdown_relative_path"]
            ).resolve()
            if hashlib.sha256(md_path.read_bytes()).hexdigest() != item.get(
                "paper_markdown_sha256"
            ):
                raise RuntimeError(f"Frozen Markdown checksum mismatch: {md_path}")
            papers.append(
                {
                    "sample_id": str(item["paper_id"]),
                    "paper_md_path": str(md_path),
                    "paper_dir": str(md_path.parent),
                    "frozen_context_bundle_path": str(bundle_path),
                }
            )
        if not papers:
            raise FileNotFoundError(
                f"No {requested_split!r} bundles in {manifest_path}"
            )
        if args.batch_limit is not None:
            papers = papers[: args.batch_limit]
        return papers

    if args.paper_md_path:
        md_path = Path(args.paper_md_path).resolve()
        sample_id = md_path.parent.name if md_path.name.startswith("md_content") else md_path.stem
        papers.append({
            "sample_id": sample_id,
            "paper_md_path": str(md_path),
            "paper_dir": str(md_path.parent),
        })
        return papers

    if not args.paper_md_dir:
        raise ValueError("Please provide either --paper_md_path or --paper_md_dir.")

    md_root = Path(args.paper_md_dir).resolve()
    if not md_root.exists():
        raise FileNotFoundError(f"Markdown directory not found: {md_root}")

    if args.paper_md_glob == "*/md_content_no_appendix.md":
        for paper_dir in sorted(path for path in md_root.iterdir() if path.is_dir()):
            candidates = [
                paper_dir / "md_content_no_appendix.md",
                paper_dir / "md_content.md",
            ]
            chosen = next((candidate for candidate in candidates if candidate.is_file()), None)
            if chosen is None:
                continue
            papers.append({
                "sample_id": paper_dir.name,
                "paper_md_path": str(chosen.resolve()),
                "paper_dir": str(paper_dir.resolve()),
            })
    else:
        matched_paths = sorted(md_root.glob(args.paper_md_glob))
        for path in matched_paths:
            if not path.is_file():
                continue
            sample_id = path.parent.name
            papers.append({
                "sample_id": sample_id,
                "paper_md_path": str(path.resolve()),
                "paper_dir": str(path.parent.resolve()),
            })

    if not papers:
        raise FileNotFoundError(
            f"No Markdown papers found under {md_root} with pattern '{args.paper_md_glob}'."
        )

    if args.batch_limit is not None:
        papers = papers[:args.batch_limit]

    return papers


def save_structured_outputs(
    args: Namespace,
    paper_info: Dict[str, str],
    extractor,
    messages: List[Message],
    reviewer_memory_contexts: Dict[str, Dict] = None,
) -> Dict:
    output_dir = Path(args.output_dir) / "single_paper_review" / paper_info["sample_id"]
    output_dir.mkdir(parents=True, exist_ok=True)

    serialized_messages = [
        {
            "agent_name": message.agent_name,
            "content": message.content,
            "turn": message.turn,
            "timestamp": str(message.timestamp),
            "visible_to": message.visible_to,
            "msg_type": message.msg_type,
        }
        for message in messages
    ]

    extraction_memory = deepcopy(getattr(extractor, "last_memory", {}) or {})
    reviews = {
        reviewer_name: normalize_source_anchors(
            review_text,
            extraction_memory.get("source_anchors", []),
        )
        for reviewer_name, review_text in extract_reviews_from_messages(
            serialized_messages
        ).items()
    }
    extraction_memory.update({
        "sample_id": paper_info["sample_id"],
        "paper_dir": paper_info["paper_dir"],
        "paper_md_path": paper_info["paper_md_path"],
        "review_model_name": args.model_name,
        "review_temperature": getattr(args, "review_temperature", 0.3),
        "replicate_id": getattr(args, "replicate_id", ""),
        "run_seed": getattr(args, "run_seed", None),
        "seed_control_available": False,
        "generation_config": {
            "model": args.model_name,
            "temperature": getattr(args, "review_temperature", 0.3),
            "reasoning_effort": getattr(args, "reviewer_reasoning_effort", None),
            "review_max_tokens": getattr(args, "review_max_tokens", None),
            "retry_max_tokens": getattr(args, "review_retry_max_tokens", None),
        },
        "reviewer_names": list(reviews.keys()),
        "expected_reviewer_names": (
            EXPECTED_REVIEWER_NAMES
            if getattr(args, "enable_meta", False)
            else SPECIALIST_REVIEWER_NAMES
        ),
        "meta_enabled": bool(getattr(args, "enable_meta", False)),
        "reviewer_memory_contexts": reviewer_memory_contexts or {},
        "memory_retrieval_enabled": (
            not getattr(args, "disable_memory_retrieval", False)
            and getattr(args, "memory_mode", "existing") != "none"
        ),
        "memory_mode": (
            "none"
            if getattr(args, "disable_memory_retrieval", False)
            else getattr(args, "memory_mode", "existing")
        ),
    })

    reviews_path = output_dir / "reviews.json"
    reviews_path.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")

    candidate_reviews_path = output_dir / "candidate_reviews.jsonl"
    candidate_reviews_path.write_text("", encoding="utf-8")
    candidate_writer = CandidateReviewWriter(candidate_reviews_path)
    memory_mode = str(extraction_memory.get("memory_mode") or "existing")
    for reviewer_name, review_text in reviews.items():
        task_id = infer_reviewer_dimension(reviewer_name) or "META"
        context = dict((reviewer_memory_contexts or {}).get(reviewer_name) or {})
        program_path = str(context.get("program_path") or "")
        if program_path:
            program = MemoryProgramLoader.identity(program_path, task_id)
            program_identity = program.to_dict()
            program_sha256 = program.sha256
        else:
            logical = f"legacy-{memory_mode}-memory"
            program_identity = {
                "logical_identity": logical,
                "source_kind": "legacy_memory_mode",
            }
            program_sha256 = hashlib.sha256(logical.encode("utf-8")).hexdigest()
        candidate_writer.append(
            CandidateReviewRecord(
                paper_id=str(paper_info["sample_id"]),
                task_id=task_id,
                review_text=review_text,
                reviewer_identity=reviewer_identity(task_id),
                memory_program_identity=program_identity,
                memory_program_sha256=program_sha256,
                model=str(args.model_name),
                revision=str(
                    getattr(args, "model_revision", "")
                    or "legacy-backend-unspecified"
                ),
                run_identity=(
                    f"legacy:{getattr(args, 'replicate_id', '')}:"
                    f"{paper_info['sample_id']}"
                ),
            )
        )

    memory_path = output_dir / "memory.json"
    memory_path.write_text(json.dumps(extraction_memory, ensure_ascii=False, indent=2), encoding="utf-8")

    memory_record = build_memory_record(
        sample_id=paper_info["sample_id"],
        paper_title=extraction_memory.get("paper_title", ""),
        paper_md_path=paper_info["paper_md_path"],
        review_dir=str(output_dir),
        retrieved_papers=extraction_memory.get("retrieved_papers", []),
        references_text=extraction_memory.get("references_text", ""),
        source_anchors=extraction_memory.get("source_anchors", []),
        reviews=reviews,
        keyword_model_name=extraction_memory.get("keyword_model_name"),
        review_model_name=args.model_name,
    )
    return {
        "review_dir": str(output_dir),
        "memory_path": str(memory_path),
        "reviews_path": str(reviews_path),
        "candidate_reviews_path": str(candidate_reviews_path),
        "memory_record": memory_record,
    }


def commit_global_memory(args: Namespace, memory_record: Dict) -> Dict[str, str]:
    return update_memory_library(args.output_dir, memory_record)


def validate_generated_outputs(result: Dict[str, str]):
    review_dir = Path(result["review_dir"])
    reviews = json.loads((review_dir / "reviews.json").read_text(encoding="utf-8"))
    memory = json.loads((review_dir / "memory.json").read_text(encoding="utf-8"))
    timing = json.loads((review_dir / "timing.json").read_text(encoding="utf-8"))
    history = json.loads((review_dir / "review_history.json").read_text(encoding="utf-8"))

    expected_reviewers = memory.get("expected_reviewer_names") or EXPECTED_REVIEWER_NAMES
    missing_reviewers = [name for name in expected_reviewers if name not in reviews]
    if missing_reviewers:
        raise ValueError(f"Incomplete review set for {result['sample_id']}: missing {missing_reviewers}")

    for reviewer_name, review_text in reviews.items():
        has_score = bool(re.search(r"(Dimension score|Final score|Score \(1-10\))\s*[:：]", review_text, flags=re.IGNORECASE))
        if not has_score:
            raise ValueError(f"Missing score in {reviewer_name} for {result['sample_id']}")
        validate_review_source_anchors(
            review_text,
            memory.get("source_anchors", []),
            reviewer_name,
        )

    if not memory.get("source_anchors"):
        raise ValueError(f"Missing source anchors in memory for {result['sample_id']}")
    if (memory.get("s2_usage") or {}).get("default_injection") is True:
        raise ValueError(f"S2 was injected by default for {result['sample_id']}")
    if not timing.get("duration_text"):
        raise ValueError(f"Missing timing duration for {result['sample_id']}")
    if "timing" not in history or not history["timing"].get("duration_text"):
        raise ValueError(f"Missing timing block in review history for {result['sample_id']}")


def load_completed_result_if_valid(args: Namespace, paper_info: Dict[str, str]):
    if not getattr(args, "reuse_completed_specialists", False):
        return None
    review_dir = (
        Path(args.output_dir)
        / "single_paper_review"
        / paper_info["sample_id"]
    )
    required = [
        review_dir / "reviews.json",
        review_dir / "memory.json",
        review_dir / "review_history.json",
        review_dir / "timing.json",
    ]
    if not all(path.is_file() for path in required):
        return None
    try:
        timing = json.loads((review_dir / "timing.json").read_text(encoding="utf-8"))
        if Path(timing.get("paper_md_path", "")).resolve() != Path(
            paper_info["paper_md_path"]
        ).resolve():
            return None
        result = {
            "sample_id": paper_info["sample_id"],
            "paper_md_path": paper_info["paper_md_path"],
            "review_dir": str(review_dir),
            "review_history_path": str(review_dir / "review_history.json"),
            "timing_path": str(review_dir / "timing.json"),
            "memory_path": str(review_dir / "memory.json"),
            "reviews_path": str(review_dir / "reviews.json"),
            "duration_seconds": timing.get("duration_seconds", 0),
            "duration_text": timing.get("duration_text", ""),
            "started_at_local": timing.get("started_at_local", ""),
            "ended_at_local": timing.get("ended_at_local", ""),
        }
        validate_generated_outputs(result)
    except Exception as exc:
        print(
            f"[Resume] Existing output for {paper_info['sample_id']} is not reusable: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return None
    return result


EXPECTED_REVIEWER_NAMES = [
    "Reviewer QUAL",
    "Reviewer CLAR",
    "Reviewer SIGN",
    "Reviewer ORIG",
    "Reviewer POL",
    "Final Meta-Reviewer",
]
SPECIALIST_REVIEWER_NAMES = [spec["name"] for spec in REVIEWER_SPECS]

STRICT_SOURCE_ANCHOR_PATTERN = re.compile(r"SRC-(\d+)\(page=(\d+)\)")
SOURCE_ANCHOR_CANDIDATE_PATTERN = re.compile(
    r"SRC-[A-Za-z0-9_-]+(?:\([^\n)]*\))?",
    flags=re.IGNORECASE,
)


class SourceAnchorValidationError(ValueError):
    def __init__(self, reviewer_name: str, errors: List[str]):
        self.reviewer_name = reviewer_name
        self.errors = list(errors)
        super().__init__(
            f"{reviewer_name} failed source anchor validation: "
            + "; ".join(self.errors)
        )


def allowed_source_anchor_map(source_anchors: List[Dict]) -> Dict[str, int]:
    """Return the exact numeric source IDs and pages a reviewer may copy."""
    allowed = {}
    for anchor in source_anchors or []:
        source_id = str(anchor.get("source_id", ""))
        if not re.fullmatch(r"SRC-\d+", source_id):
            continue
        try:
            page = int(anchor.get("page_idx"))
        except (TypeError, ValueError):
            continue
        if page < 0:
            continue
        allowed[source_id] = page
    return allowed


def allowed_source_anchor_tokens(source_anchors: List[Dict]) -> List[str]:
    return [
        f"{source_id}(page={page})"
        for source_id, page in allowed_source_anchor_map(source_anchors).items()
    ]


def validate_review_source_anchors(
    review_text: str,
    source_anchors: List[Dict],
    reviewer_name: str = "Reviewer",
) -> List[str]:
    """Validate every SRC reference against the extractor-provided ID/page pair."""
    allowed = allowed_source_anchor_map(source_anchors)
    candidates = SOURCE_ANCHOR_CANDIDATE_PATTERN.findall(review_text or "")
    errors = []
    valid_tokens = []
    if not candidates:
        errors.append("no source anchor was provided")
    for token in candidates:
        match = STRICT_SOURCE_ANCHOR_PATTERN.fullmatch(token)
        if not match:
            errors.append(
                f"invalid format {token!r}; expected SRC-<digits>(page=<digits>)"
            )
            continue
        source_id = f"SRC-{match.group(1)}"
        page = int(match.group(2))
        if source_id not in allowed:
            errors.append(f"unknown anchor ID {source_id!r}")
            continue
        if allowed[source_id] != page:
            errors.append(
                f"wrong page for {source_id!r}: got {page}, expected {allowed[source_id]}"
            )
            continue
        valid_tokens.append(token)
    if errors:
        raise SourceAnchorValidationError(
            reviewer_name,
            list(dict.fromkeys(errors)),
        )
    return valid_tokens


def build_anchor_repair_request(
    reviewer_name: str,
    previous_review: str,
    validation_error: SourceAnchorValidationError,
    source_anchors: List[Dict],
) -> str:
    allowed_tokens = allowed_source_anchor_tokens(source_anchors)
    return (
        "Your previous review failed mandatory Source Anchor validation.\n\n"
        f"Validation errors: {'; '.join(validation_error.errors)}\n\n"
        "Return the complete corrected review only. Preserve the original review "
        "conclusions, scores, confidence, recommendation, strengths, weaknesses, "
        "and wording. Change only invalid or missing Source Anchor citations as "
        "needed.\n\n"
        "Every citation must be copied character-for-character from the allowed "
        "tokens below. The only legal format is SRC-<digits>(page=<digits>). "
        "Do not invent mnemonic names such as SRC-ABST, SRC-ABSTRACT, SRC-INTRO, "
        "SRC-EXP, SRC-EXPERIMENTS, SRC-METHOD, SRC-AUTHORS, or any other named "
        "anchor. Do not use an unlisted numeric ID such as SRC-999.\n\n"
        "Allowed Source Anchors:\n"
        + "\n".join(f"- {token}" for token in allowed_tokens)
        + "\n\nPrevious review to repair:\n"
        + previous_review
    )


def validate_complete_specialist_reviews(
    messages: List[Message], source_anchors: List[Dict]
) -> None:
    by_name = {
        message.agent_name: message
        for message in messages
        if message.agent_name.startswith("Reviewer ")
    }
    expected = [spec["name"] for spec in REVIEWER_SPECS]
    missing = [name for name in expected if name not in by_name]
    if missing:
        raise RuntimeError(
            "Specialist validation gate failed; Meta Reviewer will not run. "
            f"Missing validated specialists: {missing}"
        )
    for reviewer_name in expected:
        validate_review_source_anchors(
            by_name[reviewer_name].content,
            source_anchors,
            reviewer_name,
        )


def validate_review_payload(
    sample_id: str,
    extraction_memory: Dict,
    messages: List[Message],
    duration_text: str,
    expected_reviewer_names: List[str] = None,
) -> None:
    """Validate all non-file review state before any final artifact is written."""
    serialized_messages = [
        {
            "agent_name": message.agent_name,
            "content": message.content,
        }
        for message in messages
    ]
    reviews = extract_reviews_from_messages(serialized_messages)
    expected = list(expected_reviewer_names or EXPECTED_REVIEWER_NAMES)
    missing_reviewers = [name for name in expected if name not in reviews]
    if missing_reviewers:
        raise ValueError(
            f"Incomplete review set for {sample_id}: missing {missing_reviewers}"
        )

    source_anchors = extraction_memory.get("source_anchors", [])
    if not source_anchors:
        raise ValueError(f"Missing source anchors in memory for {sample_id}")
    for reviewer_name in expected:
        review_text = reviews[reviewer_name]
        has_score = bool(
            re.search(
                r"(Dimension score|Final score|Score \(1-10\))\s*[:：]",
                review_text,
                flags=re.IGNORECASE,
            )
        )
        if not has_score:
            raise ValueError(f"Missing score in {reviewer_name} for {sample_id}")
        validate_review_source_anchors(
            review_text,
            source_anchors,
            reviewer_name,
        )

    if (extraction_memory.get("s2_usage") or {}).get("default_injection") is True:
        raise ValueError(f"S2 was injected by default for {sample_id}")
    if not duration_text:
        raise ValueError(f"Missing timing duration for {sample_id}")


def source_anchor_page_map(source_anchors: List[Dict]) -> Dict[str, int]:
    pages = {}
    for anchor in source_anchors or []:
        source_id = str(anchor.get("source_id", "")).upper()
        if not re.fullmatch(r"SRC-\d{3}", source_id):
            continue
        page = anchor.get("page_idx")
        if page is None:
            continue
        try:
            pages[source_id] = int(page)
        except (TypeError, ValueError):
            continue
    return pages


def _page_from_hint(page_hint: str):
    if not page_hint:
        return None
    match = re.search(r"\d+", page_hint)
    if not match:
        return None
    return int(match.group(0))


def _canonical_source_anchor(source_number: str, pages: Dict[str, int], page_hint: str = None) -> str:
    source_id = f"SRC-{int(source_number):03d}"
    page = pages.get(source_id)
    if page is None:
        page = _page_from_hint(page_hint)
    if page is None:
        return source_id
    return f"{source_id}(page={page})"


def normalize_source_anchors(text: str, source_anchors: List[Dict]) -> str:
    """Normalize common model citation variants to SRC-xxx(page=y)."""
    if not text:
        return text
    pages = source_anchor_page_map(source_anchors)

    def replace_range(match):
        start = int(match.group(1))
        end = int(match.group(2))
        page_hint = match.group(3)
        if end < start or end - start > 25:
            return match.group(0)
        return ", ".join(
            _canonical_source_anchor(f"{value:03d}", pages, page_hint)
            for value in range(start, end + 1)
        )

    normalized = re.sub(
        r"SRC-(\d{3})\s*[–—-]\s*(?:SRC-)?(\d{3})(?:\s*\(\s*page\s*=\s*([^)]+)\))?",
        replace_range,
        text,
        flags=re.IGNORECASE,
    )

    def replace_single(match):
        return _canonical_source_anchor(match.group(1), pages, match.group(2))

    normalized = re.sub(
        r"SRC-(\d{3})(?:\s*\(\s*page\s*=\s*([^)]+)\))?",
        replace_single,
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def normalize_review_messages(messages: List[Message], source_anchors: List[Dict]) -> List[Message]:
    reviewer_names = set(EXPECTED_REVIEWER_NAMES)
    for message in messages:
        if message.agent_name in reviewer_names:
            message.content = normalize_source_anchors(
                message.content,
                source_anchors,
            )
    return messages


def has_strict_source_anchor(text: str) -> bool:
    return bool(re.search(r"SRC-\d{3}\(page=\d+\)", text or ""))


def has_any_source_anchor(text: str) -> bool:
    return bool(re.search(r"SRC-\d{3}", text or "", flags=re.IGNORECASE))


def _memory_embedding_factory(args: Namespace):
    if getattr(args, "memory_embedding_mode", "frozen") == "deterministic_hash":
        if getattr(args, "memory_embedding_adapter", None) or getattr(
            args, "memory_embedding_revision", None
        ):
            raise ValueError(
                "deterministic_hash mode cannot be combined with an embedding "
                "adapter or revision"
            )
        return lambda: None
    embedding_adapter = getattr(args, "memory_embedding_adapter", None)
    embedding_revision = getattr(args, "memory_embedding_revision", None)
    if not embedding_adapter or not embedding_revision:
        raise ValueError(
            "--memory_mode=mstar_program requires --memory_embedding_adapter "
            "and --memory_embedding_revision from the formal evolution run"
        )
    module_name, separator, factory_name = embedding_adapter.partition(":")
    if not separator:
        raise ValueError("--memory_embedding_adapter must use module:factory syntax")
    factory = getattr(importlib.import_module(module_name), factory_name)

    def configured_factory():
        try:
            return factory(
                model_name=getattr(
                    args, "memory_embedding_model_name", "Qwen/Qwen3-Embedding-8B"
                ),
                revision=embedding_revision,
            )
        except TypeError:
            return factory()

    return configured_factory


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_precompiled_memory(args: Namespace, task_id: str, program_path: Path):
    cache_root_value = getattr(args, "memory_knowledge_cache_dir", None)
    if not cache_root_value:
        return None, None
    cache_root = Path(cache_root_value).expanduser().resolve()
    manifest_path = cache_root / "cache_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"KnowledgeItem cache manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = (manifest.get("tasks") or {}).get(task_id) or {}
    expected_program_sha = str(entry.get("program_sha256") or "")
    actual_program_sha = _sha256_path(program_path)
    if actual_program_sha != expected_program_sha:
        raise RuntimeError(
            f"{task_id} program/cache mismatch: "
            f"expected={expected_program_sha}, actual={actual_program_sha}"
        )
    cache_path = (cache_root / str(entry.get("cache_file") or "")).resolve()
    if cache_root not in cache_path.parents or not cache_path.is_file():
        raise FileNotFoundError(f"Invalid {task_id} cache file: {cache_path}")
    if _sha256_path(cache_path) != entry.get("cache_sha256"):
        raise RuntimeError(f"{task_id} KnowledgeItem cache checksum mismatch")
    values = []
    with cache_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if value.get("task_id") != task_id:
                raise RuntimeError(
                    f"Wrong task in {cache_path}:{line_number}: {value.get('task_id')}"
                )
            values.append(value)
    if len(values) != int(entry.get("item_count", -1)):
        raise RuntimeError(f"{task_id} KnowledgeItem cache count mismatch")
    return values, {
        "cache_manifest": str(manifest_path),
        "cache_manifest_sha256": _sha256_path(manifest_path),
        "cache_path": str(cache_path),
        "cache_sha256": entry["cache_sha256"],
    }


def _mstar_toolkit(args: Namespace, backend, embedding_factory) -> MemoryToolkit:
    s2_pool, s2_sha256 = load_frozen_s2_pool(
        getattr(args, "s2_retrieval_pool", None)
    )
    return MemoryToolkit(
        llm_client=backend,
        llm_model=getattr(args, "model_name", None),
        embedding_client=embedding_factory(),
        embedding_model=getattr(
            args, "memory_embedding_model_name", "Qwen/Qwen3-Embedding-8B"
        ),
        s2_retrieval_pool=s2_pool,
        s2_retrieval_pool_sha256=s2_sha256,
    )


def _mstar_memory_contexts(
    args: Namespace,
    paper_info: Dict[str, str],
    extractor,
    players: List[Player],
) -> Dict[str, Dict]:
    if not getattr(args, "memory_program_dir", None):
        raise ValueError("--memory_mode=mstar_program requires --memory_program_dir")
    embedding_factory = _memory_embedding_factory(args)
    backend = next(
        player.backend for player in players if player.name.startswith("Reviewer ")
    )
    memory_source_output_dir = (
        getattr(args, "memory_source_output_dir", None) or args.output_dir
    )
    use_precompiled = bool(getattr(args, "memory_knowledge_cache_dir", None))
    records = (
        []
        if use_precompiled
        else [
            record
            for record in load_memory_records(memory_source_output_dir)
            if record.get("sample_id") != paper_info["sample_id"]
        ]
    )

    reviewer_episodes: Dict[str, List[Episode]] = {}
    for record in records:
        sample_id = str(record.get("sample_id", ""))
        for reviewer_name, review in sorted((record.get("reviews") or {}).items()):
            if not review:
                continue
            dimension = infer_reviewer_dimension(reviewer_name) or "GENERAL"
            reviewer_episodes.setdefault(reviewer_name, []).append(
                Episode(
                    id=f"{sample_id}::{reviewer_name}",
                    text=build_reviewer_memory_episode_text(
                        record, reviewer_name, review
                    ),
                    metadata={
                        "source": "agentreview_memory_library",
                        "sample_id": sample_id,
                        "paper_title": record.get("paper_title", ""),
                        "reviewer_name": reviewer_name,
                        "reviewer_dimension": dimension,
                    },
                )
            )
    total_reviewer_episodes = sum(len(items) for items in reviewer_episodes.values())

    def answer_with_independent_reviewer_memory(
        reviewer_name: str,
        dimension: str,
        question: str,
    ) -> Dict[str, object]:
        toolkit = _mstar_toolkit(args, backend, embedding_factory)
        program_path = resolve_program_path(args.memory_program_dir, dimension)
        precompiled, cache_provenance = _load_precompiled_memory(
            args, dimension, program_path
        )
        if precompiled is None:
            verify_promoted_program(
                args.memory_program_dir,
                dimension,
                program_path,
                getattr(
                    args,
                    "memory_embedding_model_name",
                    "Qwen/Qwen3-Embedding-8B",
                ),
                getattr(args, "memory_embedding_revision", ""),
                str(
                    getattr(toolkit.embedding_client, "embedding_fingerprint", "")
                    or ""
                ),
            )
        adapter = MemoryHarnessAdapter(
            str(program_path),
            toolkit,
            build_task_agent(toolkit, dimension),
        )
        episodes = list(reviewer_episodes.get(reviewer_name, []))
        if not episodes:
            episodes = [
                episode
                for name, values in reviewer_episodes.items()
                if (infer_reviewer_dimension(name) or "GENERAL") == dimension
                for episode in values
            ]
        try:
            if precompiled is not None:
                adapter.ingest_precompiled(precompiled)
            else:
                adapter.ingest_episodes(episodes)
            retrieved = adapter.retrieve(question)
            context = (
                "MemoryProgram operational guidance (lower priority than the "
                "frozen reviewer rubric):\n"
                f"{adapter.program.INSTRUCTION_RESPONSE}\n"
                f"{adapter.program.ALWAYS_ON_KNOWLEDGE}\n\n"
                "Retrieved memory:\n"
                f"{retrieved}"
            )
        finally:
            adapter.close()
        return {
            "context": context,
            "num_selected_records": (
                len(precompiled) if precompiled is not None else len(episodes)
            ),
            "program_path": str(program_path),
            "cache_provenance": cache_provenance,
            "resource_usage": toolkit.resource_snapshot(),
        }

    extraction_memory = getattr(extractor, "last_memory", {}) or {}
    paper_title = extraction_memory.get("paper_title") or paper_info["sample_id"]
    contexts = {}
    reviewer_targets = [
        (spec["name"], spec["dimension"]) for spec in REVIEWER_SPECS
    ]
    for reviewer_name, dimension in reviewer_targets:
        question = (
            f"Paper id: {paper_info['sample_id']}\n"
            f"What prior review experience is useful for reviewing '{paper_title}' "
            f"as {reviewer_name} in the {dimension} dimension?"
        )
        memory_result = answer_with_independent_reviewer_memory(
            reviewer_name,
            dimension,
            question,
        )
        answer = str(memory_result["context"])
        formatted = (
            "=== EVOLVED MEMORY PROGRAM CONTEXT ===\n"
            + answer[: getattr(args, "memory_max_chars", 5000)]
            + "\n=== END EVOLVED MEMORY PROGRAM CONTEXT ==="
        )
        contexts[reviewer_name] = {
            "paper_title": paper_title,
            "reviewer_name": reviewer_name,
            "dimension": dimension,
            "num_available_records": int(memory_result["num_selected_records"])
            if use_precompiled
            else total_reviewer_episodes,
            "num_selected_records": int(memory_result["num_selected_records"]),
            "entries": [],
            "formatted_context": formatted,
            "memory_mode": "mstar_program",
            "program_path": memory_result["program_path"],
            "memory_source_output_dir": memory_source_output_dir,
            "memory_episode_granularity": "reviewer_level",
            "memory_scope": "per_reviewer_independent_kb",
            "knowledge_cache": memory_result["cache_provenance"],
            "resource_usage": memory_result["resource_usage"],
        }
    return contexts


def _mstar_meta_memory_context(
    args: Namespace,
    paper_info: Dict[str, str],
    players: List[Player],
    specialist_messages: List[Message],
) -> Dict[str, object]:
    """Run the sixth program only after all five specialist outputs are frozen."""
    backend = next(
        player.backend for player in players if player.name == META_REVIEWER_NAME
    )
    embedding_factory = _memory_embedding_factory(args)
    toolkit = _mstar_toolkit(args, backend, embedding_factory)
    program_path = resolve_program_path(args.memory_program_dir, "META")
    precompiled, cache_provenance = _load_precompiled_memory(
        args, "META", program_path
    )
    if precompiled is None:
        verify_promoted_program(
            args.memory_program_dir,
            "META",
            program_path,
            getattr(
                args,
                "memory_embedding_model_name",
                "Qwen/Qwen3-Embedding-8B",
            ),
            getattr(args, "memory_embedding_revision", ""),
            str(
                getattr(toolkit.embedding_client, "embedding_fingerprint", "")
                or ""
            ),
        )
    adapter = MemoryHarnessAdapter(
        str(program_path),
        toolkit,
        build_task_agent(toolkit, "META"),
    )
    memory_source_output_dir = (
        getattr(args, "memory_source_output_dir", None) or args.output_dir
    )
    records = (
        []
        if precompiled is not None
        else [
            record
            for record in load_memory_records(memory_source_output_dir)
            if record.get("sample_id") != paper_info["sample_id"]
        ]
    )
    episodes = []
    for record in records:
        review = (record.get("reviews") or {}).get(META_REVIEWER_NAME)
        if not review:
            continue
        episodes.append(
            Episode(
                id=f"{record.get('sample_id', '')}::{META_REVIEWER_NAME}",
                text=build_reviewer_memory_episode_text(
                    record, META_REVIEWER_NAME, review
                ),
                metadata={
                    "source": "agentreview_memory_library",
                    "sample_id": record.get("sample_id", ""),
                    "reviewer_name": META_REVIEWER_NAME,
                    "reviewer_dimension": "META",
                },
            )
        )
    by_dimension = {
        infer_reviewer_dimension(message.agent_name): message.content
        for message in specialist_messages
    }
    question = (
        f"Review task: META\nPaper id: {paper_info['sample_id']}\n\n"
        + "\n\n".join(
            f"=== {task_id} ===\n{by_dimension[task_id]}"
            for task_id in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
        )
        + "\n\nReturn the complete final meta-review."
    )
    try:
        if precompiled is not None:
            adapter.ingest_precompiled(precompiled)
        else:
            adapter.ingest_episodes(episodes)
        retrieved = adapter.retrieve(question)
        answer = (
            "MemoryProgram operational guidance (lower priority than the "
            "fixed meta-reviewer prompt):\n"
            f"{adapter.program.INSTRUCTION_RESPONSE}\n"
            f"{adapter.program.ALWAYS_ON_KNOWLEDGE}\n\n"
            "Retrieved memory:\n"
            f"{retrieved}"
        )
    finally:
        adapter.close()
    formatted = (
        "=== EVOLVED MEMORY PROGRAM CONTEXT ===\n"
        + answer[: getattr(args, "memory_max_chars", 5000)]
        + "\n=== END EVOLVED MEMORY PROGRAM CONTEXT ==="
    )
    return {
        "paper_title": paper_info["sample_id"],
        "reviewer_name": META_REVIEWER_NAME,
        "dimension": "META",
        "num_available_records": (
            len(precompiled) if precompiled is not None else len(records)
        ),
        "num_selected_records": (
            len(precompiled) if precompiled is not None else len(episodes)
        ),
        "entries": [],
        "formatted_context": formatted,
        "memory_mode": "mstar_program",
        "program_path": str(program_path),
        "memory_source_output_dir": memory_source_output_dir,
        "memory_episode_granularity": "meta_review_level",
        "memory_scope": "post_specialist_meta_kb",
        "knowledge_cache": cache_provenance,
        "resource_usage": toolkit.resource_snapshot(),
    }


def build_reviewer_memory_contexts(
    args: Namespace,
    paper_info: Dict[str, str],
    extractor,
    players: List[Player] = None,
) -> Dict[str, Dict]:
    memory_mode = getattr(args, "memory_mode", "existing")
    if getattr(args, "disable_memory_retrieval", False):
        memory_mode = "none"
    if memory_mode == "none":
        return {}
    if memory_mode == "mstar_program":
        return _mstar_memory_contexts(args, paper_info, extractor, players or [])

    extraction_memory = getattr(extractor, "last_memory", {}) or {}
    paper_title = extraction_memory.get("paper_title") or paper_info["sample_id"]

    contexts: Dict[str, Dict] = {}
    for spec in REVIEWER_SPECS:
        reviewer_name = spec["name"]
        contexts[reviewer_name] = retrieve_memory_context(
            output_dir=args.output_dir,
            paper_title=paper_title,
            reviewer_name=reviewer_name,
            dimension=spec["dimension"],
            exclude_sample_id=paper_info["sample_id"],
            top_k=getattr(args, "memory_top_k", 3),
            max_chars=getattr(args, "memory_max_chars", 5000),
        )

    contexts[META_REVIEWER_NAME] = retrieve_memory_context(
        output_dir=args.output_dir,
        paper_title=paper_title,
        reviewer_name=META_REVIEWER_NAME,
        dimension=infer_reviewer_dimension(META_REVIEWER_NAME),
        exclude_sample_id=paper_info["sample_id"],
        top_k=getattr(args, "memory_top_k", 3),
        max_chars=getattr(args, "memory_max_chars", 5000),
    )
    return contexts


def memory_message_for(reviewer_name: str, reviewer_memory_contexts: Dict[str, Dict]) -> Message:
    context = reviewer_memory_contexts.get(reviewer_name, {})
    content = context.get("formatted_context")
    if not content:
        content = (
            "=== REVIEW MEMORY CONTEXT ===\n"
            "No memory retrieval context is available for this run.\n"
            "=== END REVIEW MEMORY CONTEXT ==="
        )
    return Message(agent_name="Memory Librarian", content=content, turn=0, visible_to=[reviewer_name])


def write_specialist_checkpoint(
    checkpoint_path: Path,
    reviews: Dict[str, str],
    checkpoint_metadata: Dict[str, str] = None,
    failures: Dict[str, str] = None,
) -> None:
    if checkpoint_path is None:
        return
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **(checkpoint_metadata or {}),
        "reviews": reviews,
        "failures": failures or {},
        "updated_at_local": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime()
        ),
    }
    temporary_path = checkpoint_path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(checkpoint_path)


def call_role_with_anchor_repair(
    player: Player,
    observation: List[Message],
    source_anchors: List[Dict],
) -> str:
    """Call one review role, validating immediately and repairing at most once."""

    def invoke(messages: List[Message]) -> str:
        return player(messages)

    content = invoke(observation)
    try:
        validate_review_source_anchors(content, source_anchors, player.name)
        return content
    except SourceAnchorValidationError as first_error:
        initial_error = first_error
        print(
            f"[Anchor] {player.name}: invalid first response; requesting one repair: "
            f"{'; '.join(first_error.errors)}",
            flush=True,
        )

    repair_observation = list(observation) + [
        Message(
            agent_name=player.name,
            content=content,
            turn=1,
            visible_to=[player.name],
        ),
        Message(
            agent_name="Source Anchor Validator",
            content=build_anchor_repair_request(
                player.name,
                content,
                initial_error,
                source_anchors,
            ),
            turn=2,
            visible_to=[player.name],
        ),
    ]
    repaired_content = invoke(repair_observation)
    try:
        validate_review_source_anchors(
            repaired_content,
            source_anchors,
            player.name,
        )
    except SourceAnchorValidationError as repair_error:
        raise SourceAnchorValidationError(
            player.name,
            ["repair attempt remained invalid", *repair_error.errors],
        ) from repair_error
    print(f"[Anchor] {player.name}: repaired response passed", flush=True)
    return repaired_content


def run_parallel_reviews(
    players: List[Player],
    extractor_message: Message,
    reviewer_memory_contexts: Dict[str, Dict],
    max_workers: int = 1,
    existing_messages: List[Message] = None,
    checkpoint_path: Path = None,
    checkpoint_metadata: Dict[str, str] = None,
    paper_max_chars: int = DEFAULT_PAPER_CONTEXT_MAX_CHARS,
    resume_command: str = None,
    source_anchors: List[Dict] = None,
) -> List[Message]:
    reviewer_players = [player for player in players if player.name.startswith("Reviewer ")]
    existing_by_name = {}
    for message in existing_messages or []:
        if not message.agent_name.startswith("Reviewer ") or not message.content:
            continue
        try:
            validate_review_source_anchors(
                message.content,
                source_anchors,
                message.agent_name,
            )
        except SourceAnchorValidationError as exc:
            print(
                f"[Review] Ignoring invalid specialist checkpoint for "
                f"{message.agent_name}: {exc}",
                flush=True,
            )
            continue
        existing_by_name[message.agent_name] = message.content
    reviewer_players = [
        player for player in reviewer_players if player.name not in existing_by_name
    ]
    max_workers = max(1, min(max_workers, len(reviewer_players) or 1))
    started = time.monotonic()

    def call_reviewer(player: Player):
        role_context = extractor_message.content.replace(
            "reviewer_role: SPECIALIST",
            f"reviewer_role: {infer_reviewer_dimension(player.name) or player.name}",
            1,
        )
        clipped_content = clip_context(
            role_context,
            paper_max_chars,
        )
        print(
            f"[Review] {player.name}: paper_context_chars="
            f"{len(extractor_message.content)}->{len(clipped_content)}",
            flush=True,
        )
        reviewer_paper_message = Message(
            agent_name=extractor_message.agent_name,
            content=clipped_content,
            turn=extractor_message.turn,
        )
        observation = [
            reviewer_paper_message,
            memory_message_for(player.name, reviewer_memory_contexts),
        ]
        content = call_role_with_anchor_repair(
            player,
            observation,
            source_anchors,
        )
        return player.name, content

    results = list(existing_by_name.items())
    failures = []
    failure_records = {}
    if existing_by_name:
        print(
            f"[Review] Reusing {len(existing_by_name)} specialist checkpoint(s); "
            f"requesting {len(reviewer_players)} missing reviewer(s)",
            flush=True,
        )
    if not reviewer_players:
        name_to_order = {
            spec["name"]: idx for idx, spec in enumerate(REVIEWER_SPECS)
        }
        return [
            Message(agent_name=name, content=content, turn=1)
            for name, content in sorted(
                results, key=lambda item: name_to_order[item[0]]
            )
        ]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(call_reviewer, player): player.name
            for player in reviewer_players
        }
        for future in as_completed(futures):
            reviewer_name = futures[future]
            try:
                result = future.result()
                results.append(result)
                failure_records.pop(reviewer_name, None)
                write_specialist_checkpoint(
                    checkpoint_path,
                    dict(results),
                    checkpoint_metadata,
                    failure_records,
                )
                print(
                    f"[Review] Checkpoint saved: {result[0]} "
                    f"({len(results)}/{len(REVIEWER_SPECS)} specialists available)",
                    flush=True,
                )
            except Exception as exc:
                failures.append((reviewer_name, exc))
                failure_records[reviewer_name] = (
                    f"{type(exc).__name__}: {exc}"
                )
                write_specialist_checkpoint(
                    checkpoint_path,
                    dict(results),
                    checkpoint_metadata,
                    failure_records,
                )

    if failures:
        successful_names = [
            name for name, _ in sorted(
                results,
                key=lambda item: {
                    spec["name"]: idx
                    for idx, spec in enumerate(REVIEWER_SPECS)
                }[item[0]],
            )
        ]
        failed_names = [name for name, _ in failures]
        print(
            f"[Review] Partial specialist run saved: "
            f"successful={successful_names}, failed={failed_names}, "
            f"checkpoint={checkpoint_path or 'disabled'}",
            flush=True,
        )
        if resume_command:
            print(
                f"[Review] Resume only failed reviewers with:\n"
                f"{resume_command}",
                flush=True,
            )
        failure_text = "; ".join(
            f"{name}: {type(exc).__name__}: {exc}"
            for name, exc in failures
        )
        failure_error = RuntimeError(
            f"Specialist reviewer failure(s): {failure_text}"
        )
        raise failure_error

    messages: List[Message] = []
    name_to_order = {spec["name"]: idx for idx, spec in enumerate(REVIEWER_SPECS)}
    for reviewer_name, content in sorted(results, key=lambda item: name_to_order[item[0]]):
        messages.append(Message(agent_name=reviewer_name, content=content, turn=1))

    print(
        f"[Review] Specialist stage completed: {len(messages)}/"
        f"{len(REVIEWER_SPECS)} reviews in {time.monotonic() - started:.1f}s",
        flush=True,
    )
    return messages


def load_reusable_specialist_messages(
    args: Namespace,
    paper_info: Dict[str, str],
    source_anchors: List[Dict] = None,
) -> List[Message]:
    if not getattr(args, "reuse_completed_specialists", False):
        return []
    review_dir = (
        Path(args.output_dir)
        / "single_paper_review"
        / paper_info["sample_id"]
    )
    history_path = review_dir / "review_history.json"
    timing_path = review_dir / "timing.json"
    if not history_path.is_file() or not timing_path.is_file():
        return []
    try:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        if Path(timing.get("paper_md_path", "")).resolve() != Path(
            paper_info["paper_md_path"]
        ).resolve():
            return []
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    by_name = {
        item.get("agent_name"): item
        for item in history.get("messages", [])
        if item.get("agent_name", "").startswith("Reviewer ")
    }
    messages = []
    for spec in REVIEWER_SPECS:
        item = by_name.get(spec["name"])
        content = (item or {}).get("content", "")
        if not content:
            return []
        if not re.search(
            r"Dimension score\s*\(1-10\)\s*[:：]",
            content,
            flags=re.IGNORECASE,
        ):
            return []
        try:
            validate_review_source_anchors(
                content,
                source_anchors,
                spec["name"],
            )
        except SourceAnchorValidationError:
            return []
        messages.append(
            Message(
                agent_name=spec["name"],
                content=content,
                turn=1,
            )
        )
    return messages


def specialist_checkpoint_path(
    args: Namespace, paper_info: Dict[str, str]
) -> Path:
    return (
        Path(args.output_dir)
        / "single_paper_review"
        / paper_info["sample_id"]
        / "specialist_checkpoint.json"
    )


def runtime_condition_fingerprint(args: Namespace, paper_info: Dict[str, str]) -> str:
    condition = {
        "paper_md_path": str(Path(paper_info["paper_md_path"]).resolve()),
        "frozen_context_bundle_path": paper_info.get(
            "frozen_context_bundle_path"
        ),
        "model_name": getattr(args, "model_name", None),
        "review_temperature": getattr(args, "review_temperature", 0.3),
        "reviewer_reasoning_effort": getattr(
            args, "reviewer_reasoning_effort", None
        ),
        "review_max_tokens": getattr(args, "review_max_tokens", None),
        "review_retry_max_tokens": getattr(
            args, "review_retry_max_tokens", None
        ),
        "memory_mode": getattr(args, "memory_mode", None),
        "memory_program_dir": getattr(args, "memory_program_dir", None),
        "memory_knowledge_cache_dir": getattr(
            args, "memory_knowledge_cache_dir", None
        ),
        "memory_embedding_mode": getattr(args, "memory_embedding_mode", None),
        "replicate_id": getattr(args, "replicate_id", ""),
        "run_seed": getattr(args, "run_seed", None),
        "seed_control_available": False,
    }
    payload = json.dumps(
        condition, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_specialist_checkpoint(
    args: Namespace,
    paper_info: Dict[str, str],
    source_anchors: List[Dict] = None,
) -> List[Message]:
    if getattr(args, "strict_history_resume", False):
        # An interrupted Historical paper is rerun as a whole. Only the batch
        # prefix checkpoint, never a per-paper partial, is authoritative.
        return []
    if not getattr(args, "reuse_completed_specialists", False):
        return []
    path = specialist_checkpoint_path(args, paper_info)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    checkpoint_paper = payload.get("paper_md_path")
    if checkpoint_paper and Path(checkpoint_paper).resolve() != Path(
        paper_info["paper_md_path"]
    ).resolve():
        return []
    checkpoint_model = payload.get("model_name")
    if checkpoint_model and checkpoint_model != getattr(args, "model_name", None):
        return []
    checkpoint_condition = payload.get("runtime_condition_sha256")
    strict_condition = bool(
        paper_info.get("frozen_context_bundle_path")
        or getattr(args, "memory_knowledge_cache_dir", None)
    )
    if (
        checkpoint_condition
        and checkpoint_condition != runtime_condition_fingerprint(args, paper_info)
    ) or (strict_condition and not checkpoint_condition):
        return []
    reviews = payload.get("reviews")
    if not isinstance(reviews, dict):
        return []

    expected_names = {spec["name"] for spec in REVIEWER_SPECS}
    messages = []
    for reviewer_name, content in reviews.items():
        if reviewer_name not in expected_names or not isinstance(content, str):
            continue
        if not re.search(
            r"Dimension score\s*\(1-10\)\s*[:：]",
            content,
            flags=re.IGNORECASE,
        ):
            continue
        try:
            validate_review_source_anchors(
                content,
                source_anchors,
                reviewer_name,
            )
        except SourceAnchorValidationError:
            continue
        messages.append(
            Message(agent_name=reviewer_name, content=content, turn=1)
        )
    return messages


def clip_context(text: str, max_chars: int) -> str:
    return canonical_clip_text(text, max_chars, marker="Paper context clipped")


def build_specialist_resume_command(
    args: Namespace, paper_info: Dict[str, str]
) -> str:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--paper_md_path",
        paper_info["paper_md_path"],
        "--output_dir",
        args.output_dir,
        "--model_name",
        args.model_name,
        "--review_temperature",
        str(getattr(args, "review_temperature", 0.3)),
        "--keyword_model_name",
        str(getattr(args, "keyword_model_name", args.model_name)),
        "--max_retrieved_refs",
        str(getattr(args, "max_retrieved_refs", 8)),
        "--reviewer_workers",
        str(getattr(args, "reviewer_workers", 5)),
        "--api_min_interval_seconds",
        str(getattr(args, "api_min_interval_seconds", 0)),
        "--api_request_timeout_seconds",
        str(getattr(args, "api_request_timeout_seconds", 600)),
        "--api_connect_timeout_seconds",
        str(getattr(args, "api_connect_timeout_seconds", 30)),
        "--review_max_tokens",
        str(getattr(args, "review_max_tokens", 8192)),
        "--review_retry_max_tokens",
        str(getattr(args, "review_retry_max_tokens", 16384)),
        "--reviewer_reasoning_effort",
        str(getattr(args, "reviewer_reasoning_effort", "low")),
        "--paper_context_max_chars",
        str(
            getattr(
                args,
                "paper_context_max_chars",
                DEFAULT_PAPER_CONTEXT_MAX_CHARS,
            )
        ),
        "--meta_review_max_tokens",
        str(getattr(args, "meta_review_max_tokens", 4096)),
        "--memory_mode",
        str(getattr(args, "memory_mode", "existing")),
        "--memory_top_k",
        str(getattr(args, "memory_top_k", 3)),
        "--memory_max_chars",
        str(getattr(args, "memory_max_chars", 5000)),
        "--reuse_completed_specialists",
        "--overwrite",
    ]
    if getattr(args, "disable_memory_retrieval", False):
        command.append("--disable_memory_retrieval")
    if getattr(args, "disable_memory_writeback", False):
        command.append("--disable_memory_writeback")
    if getattr(args, "enable_meta", False):
        command.append("--enable_meta")
    if getattr(args, "s2_retrieval_pool", None):
        command.extend(["--s2_retrieval_pool", args.s2_retrieval_pool])
    if paper_info.get("frozen_context_bundle_path"):
        command.extend(
            [
                "--frozen_context_bundle_path",
                paper_info["frozen_context_bundle_path"],
            ]
        )
    if getattr(args, "memory_program_dir", None):
        command.extend(
            ["--memory_program_dir", args.memory_program_dir]
        )
    if getattr(args, "memory_embedding_adapter", None):
        command.extend(
            ["--memory_embedding_adapter", args.memory_embedding_adapter]
        )
    if getattr(args, "memory_embedding_model_name", None):
        command.extend(
            [
                "--memory_embedding_model_name",
                args.memory_embedding_model_name,
            ]
        )
    if getattr(args, "memory_embedding_revision", None):
        command.extend(
            [
                "--memory_embedding_revision",
                args.memory_embedding_revision,
            ]
        )
    if getattr(args, "memory_source_output_dir", None):
        command.extend(
            [
                "--memory_source_output_dir",
                args.memory_source_output_dir,
            ]
        )
    if getattr(args, "memory_knowledge_cache_dir", None):
        command.extend(
            [
                "--memory_knowledge_cache_dir",
                args.memory_knowledge_cache_dir,
            ]
        )
    command.extend(
        [
            "--memory_embedding_mode",
            str(getattr(args, "memory_embedding_mode", "frozen")),
        ]
    )
    return shlex.join(command)


def build_review_history(experiment_setting: Dict, messages: List[Message]) -> Dict:
    return {
        "experiment_setting": experiment_setting,
        "messages": [
            {
                "agent_name": message.agent_name,
                "content": message.content,
                "turn": message.turn,
                "timestamp": str(message.timestamp),
                "visible_to": message.visible_to,
                "msg_type": message.msg_type,
            }
            for message in messages
        ],
    }


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def print_paper_banner(kind: str, index: int, total: int, sample_id: str, paper_md_path: str = "", duration_text: str = ""):
    if kind == "start":
        print("\n" + "=" * 96)
        print(f"[START {index}/{total}] {sample_id}")
        print(f"Paper: {paper_md_path}")
        print("=" * 96)
    elif kind == "end":
        print("-" * 96)
        print(f"[END   {index}/{total}] {sample_id} | Duration: {duration_text}")
        print("-" * 96 + "\n")


def run_one_paper(base_args: Namespace, paper_info: Dict[str, str]) -> Dict[str, str]:
    args = deepcopy(base_args)
    args.paper_md_path = paper_info["paper_md_path"]
    args.paper_md_output_name = paper_info["sample_id"]
    args.frozen_context_bundle_path = paper_info.get(
        "frozen_context_bundle_path"
    )
    args.task = "single_paper_review"
    started_at_epoch = time.time()
    started_at_local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_at_epoch))

    experiment_setting = build_single_paper_experiment_setting()
    players = build_single_paper_players(args)
    extractor = next(player for player in players if player.name == "Paper Extractor")
    meta_enabled = bool(getattr(args, "enable_meta", False))
    meta_reviewer = (
        next(player for player in players if player.name == META_REVIEWER_NAME)
        if meta_enabled
        else None
    )

    print(f"Running extractor for {paper_info['sample_id']}")
    extractor_content = extractor([])
    extractor_message = Message(agent_name="Paper Extractor", content=extractor_content, turn=0)
    source_anchors = (getattr(extractor, "last_memory", {}) or {}).get(
        "source_anchors", []
    )

    reviewer_memory_contexts = build_reviewer_memory_contexts(
        args, paper_info, extractor, players
    )
    memory_mode = getattr(args, "memory_mode", "existing")
    if getattr(args, "disable_memory_retrieval", False):
        memory_mode = "none"
    if memory_mode == "none":
        print(f"Memory retrieval disabled for {paper_info['sample_id']}")
    else:
        selected_counts = {
            name: context.get("num_selected_records", 0)
            for name, context in reviewer_memory_contexts.items()
        }
        print(f"Prepared reviewer memory contexts for {paper_info['sample_id']}: {selected_counts}")

    checkpoint_path = specialist_checkpoint_path(args, paper_info)
    resume_command = build_specialist_resume_command(args, paper_info)
    checkpoint_metadata = {
        "paper_md_path": paper_info["paper_md_path"],
        "model_name": args.model_name,
        "memory_mode": memory_mode,
        "memory_program_dir": getattr(args, "memory_program_dir", None),
        "memory_embedding_model_name": getattr(
            args, "memory_embedding_model_name", None
        ),
        "memory_embedding_revision": getattr(
            args, "memory_embedding_revision", None
        ),
        "runtime_condition_sha256": runtime_condition_fingerprint(
            args, paper_info
        ),
    }
    specialist_messages = load_reusable_specialist_messages(
        args,
        paper_info,
        source_anchors,
    )
    if specialist_messages:
        print(
            "[Review] Reusing 5 completed specialist reviews from the "
            "incomplete previous run",
            flush=True,
        )
    else:
        specialist_messages = load_specialist_checkpoint(
            args,
            paper_info,
            source_anchors,
        )
        reviewer_workers = getattr(args, "reviewer_workers", 5)
        print(
            f"[Review] Starting 5 specialist reviewers "
            f"(parallelism={reviewer_workers})",
            flush=True,
        )
        specialist_messages = run_parallel_reviews(
            players,
            extractor_message,
            reviewer_memory_contexts,
            max_workers=reviewer_workers,
            existing_messages=specialist_messages,
            checkpoint_path=checkpoint_path,
            checkpoint_metadata=checkpoint_metadata,
            paper_max_chars=getattr(
                args,
                "paper_context_max_chars",
                DEFAULT_PAPER_CONTEXT_MAX_CHARS,
            ),
            resume_command=resume_command,
            source_anchors=source_anchors,
        )

    validate_complete_specialist_reviews(specialist_messages, source_anchors)
    if meta_enabled and memory_mode == "mstar_program":
        reviewer_memory_contexts[META_REVIEWER_NAME] = _mstar_meta_memory_context(
            args,
            paper_info,
            players,
            specialist_messages,
        )
    write_specialist_checkpoint(
        checkpoint_path,
        {
            message.agent_name: message.content
            for message in specialist_messages
        },
        checkpoint_metadata,
    )
    meta_message = None
    if meta_enabled:
        print("[Review] Starting opt-in legacy final meta-reviewer", flush=True)
        meta_paper_context = Message(
            agent_name=extractor_message.agent_name,
            content=clip_context(
                extractor_message.content.replace(
                    "reviewer_role: SPECIALIST", "reviewer_role: META", 1
                ),
                getattr(
                    args,
                    "paper_context_max_chars",
                    DEFAULT_PAPER_CONTEXT_MAX_CHARS,
                ),
            ),
            turn=extractor_message.turn,
        )
        meta_observation = [
            meta_paper_context,
            memory_message_for(META_REVIEWER_NAME, reviewer_memory_contexts),
        ] + specialist_messages
        try:
            try:
                meta_content = call_role_with_anchor_repair(
                    meta_reviewer,
                    meta_observation,
                    source_anchors,
                )
            except EmptyModelResponseError:
                print(
                    "[Review] Final Meta-Reviewer: empty response; retrying with "
                    "specialist reviews only",
                    flush=True,
                )
                compact_meta_observation = [
                    memory_message_for(META_REVIEWER_NAME, reviewer_memory_contexts),
                ] + specialist_messages
                meta_content = call_role_with_anchor_repair(
                    meta_reviewer,
                    compact_meta_observation,
                    source_anchors,
                )
        except Exception:
            raise
        meta_message = Message(
            agent_name=META_REVIEWER_NAME,
            content=meta_content,
            turn=2,
        )

    memory_messages = [
        memory_message_for(name, reviewer_memory_contexts)
        for name in (
            [spec["name"] for spec in REVIEWER_SPECS]
            + ([META_REVIEWER_NAME] if meta_enabled else [])
        )
    ]
    final_messages = specialist_messages + ([meta_message] if meta_message else [])
    all_messages = normalize_review_messages(
        [extractor_message] + memory_messages + final_messages,
        getattr(extractor, "last_memory", {}).get("source_anchors", []),
    )

    output_dir = Path(args.output_dir) / "single_paper_review" / paper_info["sample_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    review_history_path = output_dir / "review_history.json"
    if review_history_path.exists() and (not args.overwrite):
        raise Exception(f"History already exists: {review_history_path} (use --overwrite to replace)")
    ended_at_epoch = time.time()
    ended_at_local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended_at_epoch))
    duration_seconds = ended_at_epoch - started_at_epoch
    duration_text = format_duration(duration_seconds)
    validate_review_payload(
        paper_info["sample_id"],
        getattr(extractor, "last_memory", {}) or {},
        all_messages,
        duration_text,
        EXPECTED_REVIEWER_NAMES if meta_enabled else SPECIALIST_REVIEWER_NAMES,
    )

    review_history = build_review_history(experiment_setting, all_messages)
    review_history["memory_programs"] = {
        reviewer_name: context.get("program_path")
        for reviewer_name, context in reviewer_memory_contexts.items()
        if context.get("program_path")
    }
    review_history["timing"] = {
        "started_at_local": started_at_local,
        "ended_at_local": ended_at_local,
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
    }
    review_history_path.write_text(
        json.dumps(review_history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    saved_paths = save_structured_outputs(
        args=args,
        paper_info=paper_info,
        extractor=extractor,
        messages=all_messages,
        reviewer_memory_contexts=reviewer_memory_contexts,
    )
    memory_record = saved_paths.pop("memory_record")

    timing_path = output_dir / "timing.json"
    timing_path.write_text(
        json.dumps(
            {
                "sample_id": paper_info["sample_id"],
                "paper_md_path": paper_info["paper_md_path"],
                "started_at_local": started_at_local,
                "ended_at_local": ended_at_local,
                "duration_seconds": duration_seconds,
                "duration_text": duration_text,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    result = {
        "sample_id": paper_info["sample_id"],
        "paper_md_path": paper_info["paper_md_path"],
        "review_history_path": str(review_history_path),
        "timing_path": str(timing_path),
        "started_at_local": started_at_local,
        "ended_at_local": ended_at_local,
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
        **saved_paths,
    }
    validate_generated_outputs(result)
    if not getattr(args, "disable_memory_writeback", False):
        result.update(commit_global_memory(args, memory_record))
    else:
        result["memory_writeback_disabled"] = True
    validate_generated_outputs(result)
    checkpoint_path.unlink(missing_ok=True)
    return result


def _strict_history_state(args: Namespace, papers: List[Dict[str, str]]):
    checkpoint_path = (
        Path(args.output_dir)
        / "single_paper_review"
        / "history_replay_checkpoint.json"
    )
    manifest_path = Path(args.paper_context_manifest).expanduser().resolve()
    program_root = Path(args.memory_program_dir).expanduser().resolve()
    program_hashes = {
        str(path.relative_to(program_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(program_root.rglob("*.py"))
    }
    s2_path = Path(args.s2_retrieval_pool).expanduser().resolve()
    identity = {
        "candidate_id": program_root.name,
        "lineage": program_root.parent.name,
        "replicate_id": str(args.replicate_id),
        "program_sha256": canonical_sha256(program_hashes),
        "historical_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "experiment_config_sha256": runtime_condition_fingerprint(args, papers[0]),
        "s2_asset_sha256": hashlib.sha256(s2_path.read_bytes()).hexdigest(),
        "s2_asset_version": frozen_s2_pool_version(s2_path),
        "run_seed": args.run_seed,
        "seed_control_available": False,
        "generation_config": {
            "model": args.model_name,
            "temperature": args.review_temperature,
            "reasoning_effort": args.reviewer_reasoning_effort,
            "review_max_tokens": args.review_max_tokens,
            "retry_max_tokens": args.review_retry_max_tokens,
        },
    }
    frozen_ids = [paper["sample_id"] for paper in papers]
    checkpoint = load_history_checkpoint(
        str(checkpoint_path),
        expected_identity=identity,
        frozen_ids=frozen_ids,
    )
    completed = list(checkpoint.get("completed_paper_ids") or []) if checkpoint else []
    library_path = Path(args.output_dir) / "single_paper_review" / "memory_library.jsonl"
    if not checkpoint:
        stale_paper_dirs = [
            paper_id
            for paper_id in frozen_ids
            if (
                Path(args.output_dir) / "single_paper_review" / paper_id
            ).is_dir()
            and any(
                (Path(args.output_dir) / "single_paper_review" / paper_id).iterdir()
            )
        ]
        if stale_paper_dirs:
            raise RuntimeError(
                "Formal fresh run has Historical outputs but no identity-bound "
                "checkpoint: " + ", ".join(stale_paper_dirs[:10])
            )
    if checkpoint:
        snapshot = checkpoint["memory_snapshot"]
        expected_text = str(snapshot.get("memory_library_jsonl") or "")
        actual_text = library_path.read_text(encoding="utf-8") if library_path.is_file() else ""
        if actual_text != expected_text:
            library_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = library_path.with_suffix(".tmp")
            temporary.write_text(expected_text, encoding="utf-8")
            os.replace(temporary, library_path)
    # The immediate next paper may contain partial crash artifacts and is
    # deliberately rerun from scratch. Any still-later paper proves a
    # non-prefix history directory and is a formal protocol error.
    for paper_id in frozen_ids[len(completed) + 1 :]:
        paper_root = Path(args.output_dir) / "single_paper_review" / paper_id
        if paper_root.is_dir() and any(paper_root.iterdir()):
            raise RuntimeError(
                "History directory contains a record beyond the legal prefix: "
                + paper_id
            )
    return checkpoint_path, identity, frozen_ids, completed, library_path


def main(args: Namespace):
    print(AGENTREVIEW_LOGO)

    papers = discover_markdown_papers(args)
    print(f"Discovered {len(papers)} markdown paper(s) for review.")

    strict_state = None
    if getattr(args, "strict_history_resume", False):
        if not papers:
            raise RuntimeError("Strict Historical replay requires a non-empty manifest")
        strict_state = _strict_history_state(args, papers)
        # Only the checkpoint prefix is authoritative. The immediate next
        # paper is an uncommitted crash window and must be regenerated in full,
        # even when partial per-paper artifacts already exist.
        args.overwrite = True

    results = []
    for index, paper_info in enumerate(papers, start=1):
        print_paper_banner(
            kind="start",
            index=index,
            total=len(papers),
            sample_id=paper_info["sample_id"],
            paper_md_path=paper_info["paper_md_path"],
        )
        completed_result = None
        if strict_state and index <= len(strict_state[3]):
            reuse_before = getattr(args, "reuse_completed_specialists", False)
            args.reuse_completed_specialists = True
            completed_result = load_completed_result_if_valid(args, paper_info)
            args.reuse_completed_specialists = reuse_before
            if completed_result is None:
                raise RuntimeError(
                    "Checkpoint prefix output is missing or invalid: "
                    + paper_info["sample_id"]
                )
        elif not strict_state:
            completed_result = load_completed_result_if_valid(args, paper_info)
        if completed_result:
            print(
                f"[Resume] Skipping completed valid paper: {paper_info['sample_id']}",
                flush=True,
            )
            results.append(completed_result)
            print_paper_banner(
                kind="end",
                index=index,
                total=len(papers),
                sample_id=paper_info["sample_id"],
                duration_text=completed_result["duration_text"],
            )
            continue
        result = run_one_paper(args, paper_info)
        validate_generated_outputs(result)
        results.append(result)
        if strict_state:
            checkpoint_path, identity, frozen_ids, completed, library_path = strict_state
            completed.append(paper_info["sample_id"])
            library_text = (
                library_path.read_text(encoding="utf-8")
                if library_path.is_file()
                else ""
            )
            save_history_checkpoint(
                str(checkpoint_path),
                run_identity=identity,
                frozen_ids=frozen_ids,
                completed_ids=completed,
                memory_snapshot={
                    "snapshot_version": "review-library-snapshot-v1",
                    "memory_library_jsonl": library_text,
                },
                paper_record={
                    "paper_id": paper_info["sample_id"],
                    "completed": True,
                    "review_dir": result["review_dir"],
                },
            )
        print(f"Saved outputs to {result['review_dir']}")
        print_paper_banner(
            kind="end",
            index=index,
            total=len(papers),
            sample_id=paper_info["sample_id"],
            duration_text=result["duration_text"],
        )

    manifest_path = Path(args.output_dir) / "single_paper_review" / "batch_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved batch manifest to {manifest_path}")


if __name__ == "__main__":
    project_setup()
    main(parse_args())
