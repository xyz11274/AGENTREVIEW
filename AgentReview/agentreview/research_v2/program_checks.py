"""Static capability checks for an evolved M★ Program source."""

from __future__ import annotations

import ast

DEFAULT_IMPORT_WHITELIST = {
    "collections",
    "dataclasses",
    "datetime",
    "hashlib",
    "heapq",
    "itertools",
    "json",
    "math",
    "re",
    "sqlite3",
    "statistics",
    "textwrap",
    "time",
    "typing",
}
REQUIRED_CLASSES = {"KnowledgeItem", "Query", "KnowledgeBase"}
FORBIDDEN_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "locals",
    "open",
    "setattr",
    "vars",
}
FORBIDDEN_ATTRIBUTES = {
    "Popen",
    "call",
    "check_call",
    "check_output",
    "connect",
    "popen",
    "request",
    "run",
    "system",
    "urlopen",
}
ALLOWED_FIELD_TYPES = {
    "bool",
    "float",
    "int",
    "list[str]",
    "List[str]",
    "Optional[str]",
    "str",
}


def static_check(code: str, import_whitelist: set[str] | None = None) -> list[str]:
    whitelist = import_whitelist or DEFAULT_IMPORT_WHITELIST
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"AST parse failed: {exc}"]
    errors: list[str] = []
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    if classes != REQUIRED_CLASSES:
        errors.append(
            "Program must define exactly KnowledgeItem, Query, and KnowledgeBase "
            f"classes; got {sorted(classes)}"
        )
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in {"KnowledgeItem", "Query"}:
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                annotation = ast.unparse(statement.annotation).replace(" ", "")
                if annotation not in ALLOWED_FIELD_TYPES:
                    errors.append(
                        f"{node.name} field type is not JSON-protocol compatible: {annotation}"
                    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in whitelist:
                    errors.append(f"Import is not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in whitelist:
                errors.append(f"Import is not allowed: {node.module or '<relative>'}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                errors.append(f"Forbidden call: {node.func.id}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in FORBIDDEN_ATTRIBUTES:
                sqlite_memory_connect = (
                    node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "sqlite3"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == ":memory:"
                )
                if not sqlite_memory_connect:
                    errors.append(f"Forbidden call attribute: {node.func.attr}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"Dunder attribute access is not allowed: {node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(f"Global state declaration is not allowed at line {node.lineno}")
    return sorted(set(errors))
