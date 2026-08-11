"""Deterministic Python/Prompt change analysis for MemoryProgram candidates."""

from __future__ import annotations

import ast
import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


PROMPT_NAMES = (
    "INSTRUCTION_KNOWLEDGE_ITEM",
    "INSTRUCTION_QUERY",
    "INSTRUCTION_RESPONSE",
    "ALWAYS_ON_KNOWLEDGE",
)
SCHEMA_NAMES = ("KnowledgeItem", "Query")
CORE_METHODS = ("write", "read")


def _unparse(node: Optional[ast.AST]) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return ""


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is not None:
        return segment
    lines = source.splitlines()
    start = max(0, int(getattr(node, "lineno", 1)) - 1)
    end = int(getattr(node, "end_lineno", start + 1))
    return "\n".join(lines[start:end])


def _call_name(node: ast.Call) -> str:
    value = node.func
    parts: List[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _calls(node: ast.AST) -> Set[str]:
    return {
        name
        for name in (_call_name(value) for value in ast.walk(node) if isinstance(value, ast.Call))
        if name
    }


def _branch_counts(node: ast.AST) -> Dict[str, int]:
    types = {
        "if": ast.If,
        "for": (ast.For, ast.AsyncFor),
        "while": ast.While,
        "try": ast.Try,
        "with": (ast.With, ast.AsyncWith),
    }
    return {
        label: sum(isinstance(value, node_type) for value in ast.walk(node))
        for label, node_type in types.items()
    }


def _function_signature(node: ast.FunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    args = _unparse(node.args)
    returns = f" -> {_unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args}){returns}"


def _field_value(node: ast.AST) -> str:
    if isinstance(node, ast.Call) and _call_name(node).endswith("field"):
        return _unparse(node)
    return _unparse(node)


def _class_fields(node: ast.ClassDef) -> Dict[str, Dict[str, str]]:
    fields: Dict[str, Dict[str, str]] = {}
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields[item.target.id] = {
                "name": item.target.id,
                "annotation": _unparse(item.annotation),
                "default": _field_value(item.value) if item.value else "",
            }
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    fields[target.id] = {
                        "name": target.id,
                        "annotation": "",
                        "default": _field_value(item.value),
                    }
    return fields


def _imports(tree: ast.Module) -> Set[str]:
    values = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            values.update(
                f"{module}.{alias.name}".strip(".") for alias in node.names
            )
    return values


def analyze_program(source: str) -> Dict[str, Any]:
    """Extract structural facts from a Python candidate without executing it."""

    result: Dict[str, Any] = {
        "parse_error": None,
        "prompts": {},
        "schemas": {},
        "methods": {},
        "helpers": {},
        "imports": [],
    }
    try:
        tree = ast.parse(source or "")
    except SyntaxError as exc:
        result["parse_error"] = (
            f"SyntaxError: {exc.msg} (line {exc.lineno}, column {exc.offset})"
        )
        return result

    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    for node in tree.body:
        name: Optional[str] = None
        value: Optional[ast.AST] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name, value = target.id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        if name in PROMPT_NAMES and value is not None:
            try:
                literal = ast.literal_eval(value)
            except (ValueError, TypeError, SyntaxError):
                literal = None
            if isinstance(literal, str):
                result["prompts"][name] = literal

    for name in SCHEMA_NAMES:
        class_node = classes.get(name)
        if class_node:
            result["schemas"][name] = _class_fields(class_node)

    knowledge_base = classes.get("KnowledgeBase")
    if knowledge_base:
        for node in knowledge_base.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            detail = {
                "name": node.name,
                "signature": _function_signature(node),
                "source": _source_segment(source, node),
                "calls": sorted(_calls(node)),
                "branches": _branch_counts(node),
            }
            if node.name in CORE_METHODS:
                result["methods"][node.name] = detail
            else:
                result["helpers"][f"KnowledgeBase.{node.name}"] = detail

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["helpers"][node.name] = {
                "name": node.name,
                "signature": _function_signature(node),
                "source": _source_segment(source, node),
                "calls": sorted(_calls(node)),
                "branches": _branch_counts(node),
            }
    result["imports"] = sorted(_imports(tree))
    return result


def _status(before: Any, after: Any) -> str:
    if before is None and after is not None:
        return "added"
    if before is not None and after is None:
        return "removed"
    return "unchanged" if before == after else "modified"


def _set_delta(before: Iterable[str], after: Iterable[str]) -> Dict[str, List[str]]:
    before_set, after_set = set(before), set(after)
    return {
        "added": sorted(after_set - before_set),
        "removed": sorted(before_set - after_set),
    }


def _schema_changes(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    class_names = sorted(set(before) | set(after))
    for class_name in class_names:
        before_fields = before.get(class_name, {})
        after_fields = after.get(class_name, {})
        for field_name in sorted(set(before_fields) | set(after_fields)):
            old = before_fields.get(field_name)
            new = after_fields.get(field_name)
            status = _status(old, new)
            if status != "unchanged":
                changes.append(
                    {
                        "class": class_name,
                        "field": field_name,
                        "status": status,
                        "before": old,
                        "after": new,
                    }
                )
    return changes


def _function_change(
    name: str, before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    status = _status(
        before.get("source") if before else None,
        after.get("source") if after else None,
    )
    return {
        "name": name,
        "status": status,
        "signature_before": before.get("signature", "") if before else "",
        "signature_after": after.get("signature", "") if after else "",
        "calls": _set_delta(
            before.get("calls", []) if before else [],
            after.get("calls", []) if after else [],
        ),
        "branches_before": before.get("branches", {}) if before else {},
        "branches_after": after.get("branches", {}) if after else {},
        "source_before": before.get("source", "") if before else "",
        "source_after": after.get("source", "") if after else "",
    }


def _function_changes(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[Dict[str, Any]]:
    values = []
    for name in sorted(set(before) | set(after)):
        change = _function_change(name, before.get(name), after.get(name))
        if change["status"] != "unchanged":
            values.append(change)
    return values


def _unified_diff(before: str, after: str, from_name: str, to_name: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_name,
            tofile=to_name,
            n=3,
        )
    )


def compare_programs(
    before_source: str,
    after_source: str,
    *,
    before_name: str = "before.py",
    after_name: str = "after.py",
) -> Dict[str, Any]:
    """Compare two candidates using AST facts plus a complete text diff."""

    before = analyze_program(before_source)
    after = analyze_program(after_source)
    prompts = []
    for name in PROMPT_NAMES:
        old = before["prompts"].get(name)
        new = after["prompts"].get(name)
        prompts.append(
            {
                "name": name,
                "status": _status(old, new),
                "before": old or "",
                "after": new or "",
            }
        )
    core_methods = []
    for name in CORE_METHODS:
        change = _function_change(
            f"KnowledgeBase.{name}",
            before["methods"].get(name),
            after["methods"].get(name),
        )
        if change["status"] != "unchanged":
            core_methods.append(change)
    helper_changes = _function_changes(before["helpers"], after["helpers"])
    import_changes = _set_delta(before["imports"], after["imports"])
    raw_diff = _unified_diff(
        before_source, after_source, from_name=before_name, to_name=after_name
    )
    return {
        "before_name": before_name,
        "after_name": after_name,
        "parse_errors": {
            "before": before["parse_error"],
            "after": after["parse_error"],
        },
        "prompts": prompts,
        "schema": _schema_changes(before["schemas"], after["schemas"]),
        "write": [
            change
            for change in core_methods
            if change["name"] == "KnowledgeBase.write"
        ],
        "read": [
            change
            for change in core_methods
            if change["name"] == "KnowledgeBase.read"
        ],
        "helpers": helper_changes,
        "imports": import_changes,
        "raw_diff": raw_diff or "两个程序的文本内容相同。",
    }


def changed_sections(comparison: Dict[str, Any]) -> List[str]:
    """Return stable Chinese labels for sections containing actual changes."""

    labels: List[str] = []
    if any(item["status"] != "unchanged" for item in comparison.get("prompts", [])):
        labels.append("Prompt")
    if comparison.get("schema"):
        labels.append("Schema")
    if comparison.get("write"):
        labels.append("Write")
    if comparison.get("read"):
        labels.append("Read")
    imports = comparison.get("imports") or {}
    if comparison.get("helpers") or imports.get("added") or imports.get("removed"):
        labels.append("辅助代码")
    if any(comparison.get("parse_errors", {}).values()) and not labels:
        labels.append("文本变化")
    return labels


def tokenize_for_diff(text: str) -> List[str]:
    """Tokenize prose while preserving whitespace and punctuation."""

    return re.findall(r"\s+|[\w]+|[^\w\s]", text, flags=re.UNICODE)


def word_diff(before: str, after: str) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Return tagged before/after token streams for side-by-side rendering."""

    old_tokens = tokenize_for_diff(before)
    new_tokens = tokenize_for_diff(after)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    old_result: List[Tuple[str, str]] = []
    new_result: List[Tuple[str, str]] = []
    for opcode, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if opcode == "equal":
            old_result.extend(("equal", token) for token in old_tokens[old_start:old_end])
            new_result.extend(("equal", token) for token in new_tokens[new_start:new_end])
        elif opcode == "delete":
            old_result.extend(("delete", token) for token in old_tokens[old_start:old_end])
        elif opcode == "insert":
            new_result.extend(("insert", token) for token in new_tokens[new_start:new_end])
        else:
            old_result.extend(("delete", token) for token in old_tokens[old_start:old_end])
            new_result.extend(("insert", token) for token in new_tokens[new_start:new_end])
    return old_result, new_result
