"""Single-file V4A patch application used by M★ Reflector."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AppliedPatch:
    code: str
    title: str
    details: list[str]
    raw_response: str


def apply_v4a_patch(source: str, response: str) -> AppliedPatch:
    begin = response.find("*** Begin Patch")
    end = response.find("*** End Patch", begin + 1)
    if begin < 0 or end < 0:
        raise ValueError("Reflector response is missing V4A patch markers")
    patch_lines = response[begin:end].splitlines()
    updates = [
        index
        for index, line in enumerate(patch_lines)
        if line.startswith("*** Update File:")
    ]
    if len(updates) != 1:
        raise ValueError("V4A patch must contain exactly one Update File block")
    filename = patch_lines[updates[0]].split(":", 1)[1].strip()
    if filename != "program.py":
        raise ValueError("V4A patch may update only program.py")
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in patch_lines[updates[0] + 1 :]:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if line.startswith("*** "):
            raise ValueError(f"Unsupported V4A operation: {line}")
        if current is None:
            if line.strip():
                raise ValueError("V4A change appeared before the first hunk")
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise ValueError(f"Invalid V4A hunk line: {line!r}")
        current.append(line)
    if not hunks or any(not hunk for hunk in hunks):
        raise ValueError("V4A patch must contain at least one non-empty hunk")
    source_lines = source.splitlines()
    trailing_newline = source.endswith("\n")
    cursor = 0
    for hunk in hunks:
        old = [line[1:] for line in hunk if line[0] in {" ", "-"}]
        new = [line[1:] for line in hunk if line[0] in {" ", "+"}]
        if not old:
            raise ValueError("V4A hunk requires unchanged or removed context")
        matches = [
            index
            for index in range(cursor, len(source_lines) - len(old) + 1)
            if source_lines[index : index + len(old)] == old
        ]
        if len(matches) != 1:
            raise ValueError(
                f"V4A hunk context matched {len(matches)} locations, expected one"
            )
        start = matches[0]
        source_lines[start : start + len(old)] = new
        cursor = start + len(new)
    commit = response[:begin].strip()
    title_match = re.search(r"(?m)^Title:\s*(.+)$", commit)
    details = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^-\s+(.+)$", commit)
    ]
    title = title_match.group(1).strip() if title_match else "V4A MemoryProgram mutation"
    code = "\n".join(source_lines) + ("\n" if trailing_newline else "")
    return AppliedPatch(code=code, title=title, details=details, raw_response=response)
