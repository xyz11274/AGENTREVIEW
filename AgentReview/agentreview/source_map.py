import json
from pathlib import Path


def load_content_list(paper_md_path: str) -> list[dict]:
    paper_dir = Path(paper_md_path).resolve().parent
    content_list_path = paper_dir / "content_list.json"
    if not content_list_path.exists():
        return []
    return json.loads(content_list_path.read_text(encoding="utf-8"))


def extract_title_from_content_list(content_list: list[dict]) -> str | None:
    for item in content_list:
        if item.get("type") == "text" and item.get("text_level") == 1:
            text = " ".join(
                part.strip()
                for part in item.get("text", "").splitlines()
                if part.strip()
            )
            if text and text.upper() not in {"ABSTRACT", "INTRODUCTION"}:
                return text
    return None


def build_source_anchors(
    content_list: list[dict],
    max_anchors: int = 80,
    max_text_chars: int = 500,
    include_types: list[str] | None = None,
) -> list[dict]:
    anchors: list[dict] = []
    allowed_types = set(include_types or ["text", "image"])

    for item in content_list:
        item_type = item.get("type")
        if item_type not in allowed_types or item_type not in {"text", "image"}:
            continue

        text = ""
        if item_type == "text":
            text = item.get("text", "")
        elif item_type == "image":
            caption_lines = item.get("image_caption", []) or []
            text = " ".join(caption_lines)

        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if not text:
            continue

        anchors.append(
            {
                "source_id": f"SRC-{len(anchors) + 1:03d}",
                "type": item_type,
                "page_idx": item.get("page_idx"),
                "bbox": item.get("bbox"),
                "text_level": item.get("text_level"),
                "text": text[:max_text_chars],
            }
        )

        if len(anchors) >= max_anchors:
            break

    return anchors


def format_source_anchors(anchors: list[dict]) -> str:
    if not anchors:
        return "No structured source anchors available."

    lines = ["Source anchors with extracted pages or character intervals in the complete paper:"]
    for anchor in anchors:
        location = (
            f"chars=[{anchor['char_start']},{anchor['char_end']})"
            if "char_start" in anchor and "char_end" in anchor
            else f"page={anchor.get('page_idx') if anchor.get('page_idx') is not None else 'unknown'}"
        )
        lines.append(
            f"{anchor['source_id']} | {location} | type={anchor.get('type')} | text={anchor.get('text')}"
        )
    return "\n".join(lines)
