"""Small file-format helpers used by the optional OpenScholar bridge."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


def load_jsonlines(file: str | Path) -> list[Any]:
    """Load non-empty JSON Lines records and surface malformed input."""
    records: list[Any] = []
    with Path(file).open(encoding="utf-8") as jsonl_file:
        for line in jsonl_file:
            if line.strip():
                records.append(json.loads(line))
    return records


def save_file_jsonl(data: Iterable[Any], fp: str | Path) -> None:
    """Write one JSON value per line using UTF-8."""
    output_path = Path(fp)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as writer:
        for item in data:
            writer.write(json.dumps(item, ensure_ascii=False))
            writer.write("\n")


def extract_titles(text: str) -> list[str]:
    """Extract numbered titles from the References section."""
    marker = "References:"
    if marker in text:
        reference_text = text.split(marker, 1)[1]
        return re.findall(r"\[\d+\]\s(.+)", reference_text)
    return []


def save_tsv_dict(
    data: Iterable[Mapping[str, Any]], fp: str | Path, fields: Sequence[str]
) -> None:
    """Write dictionaries as a UTF-8 TSV file, creating its parent directory."""
    output_path = Path(fp)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(data)
