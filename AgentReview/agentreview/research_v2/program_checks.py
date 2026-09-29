"""Static capability checks for an evolved M★ Program source."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

DEFAULT_IMPORT_WHITELIST = {
    "collections",
    "dataclasses",
    "hashlib",
    "heapq",
    "itertools",
    "json",
    "math",
    "re",
    "sqlite3",
    "statistics",
    "textwrap",
    "typing",
}
REQUIRED_CLASSES = {"KnowledgeItem", "Query", "KnowledgeBase"}
REQUIRED_INSTRUCTIONS = {
    "INSTRUCTION_KNOWLEDGE_ITEM",
    "INSTRUCTION_QUERY",
    "INSTRUCTION_RESPONSE",
    "ALWAYS_ON_KNOWLEDGE",
}
REQUIRED_PLANNING_QUERY_FIELDS = {"query_text", "reviewer_dimension"}
REVIEW_PLAN_PROTOCOL = "evolvable-review-plan-v1"
REVIEW_PLAN_FUNCTION = "plan_review"
REVIEW_PLAN_TOOLKIT_CAPABILITIES = {"retrieve_current_s2"}
TOOLKIT_INTERFACES = {
    "llm_completion", "embed", "create_vector_collection", "retrieve_current_s2",
    "log", "db", "chroma", "logger", "collections", "embedding_restore_cache",
    "toolkit_embedding_sources", "is_toolkit_embedding",
    "count_tokens", "memory_token_limit", "select_memory_entries",
}
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


def _ast_sha256(nodes: list[ast.AST]) -> str:
    normalized = "\n".join(
        ast.dump(node, annotate_fields=True, include_attributes=False) for node in nodes
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _bound_import_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}
    return {alias.asname or alias.name for alias in node.names}


def _referenced_imports(
    nodes: list[ast.AST], imports: list[ast.Import | ast.ImportFrom]
) -> list[ast.Import | ast.ImportFrom]:
    loaded = {
        node.id
        for root in nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    return [node for node in imports if _bound_import_names(node).intersection(loaded)]


def _parent_nodes(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _argument_access_errors(
    plan: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str]:
    """Keep candidate-visible planning inputs at their declared capability seam."""
    parents = _parent_nodes(plan)
    errors: list[str] = []
    for node in ast.walk(plan):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        parent = parents.get(node)
        if node.id == "toolkit":
            direct_capability = (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in REVIEW_PLAN_TOOLKIT_CAPABILITIES
            )
            capability_call = (
                direct_capability
                and isinstance(parents.get(parent), ast.Call)
                and parents[parent].func is parent
            )
            if not capability_call:
                errors.append(
                    "plan_review may use toolkit only through a direct "
                    "retrieve_current_s2(...) call"
                )
        elif node.id == "query":
            canonical_field = (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in REQUIRED_PLANNING_QUERY_FIELDS
            )
            if not canonical_field:
                errors.append(
                    "plan_review may use query only through canonical query_text "
                    "and reviewer_dimension fields"
                )
    return errors


def _plan_signature_error(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    arguments = node.args
    positional = [*arguments.posonlyargs, *arguments.args]
    if [value.arg for value in positional] != ["toolkit", "query", "memory_context"]:
        return "plan_review signature must be (toolkit, query, memory_context)"
    if (
        arguments.vararg is not None
        or arguments.kwarg is not None
        or arguments.kwonlyargs
        or arguments.defaults
        or arguments.kw_defaults
    ):
        return "plan_review signature must be (toolkit, query, memory_context)"
    if isinstance(node, ast.AsyncFunctionDef):
        return "plan_review must be a synchronous function"
    if node.decorator_list:
        return "plan_review decorators are not allowed"
    return None


def _text_slice_errors(tree):
    """Permit proven collection slices, reject unbounded text/unknown slices.

    This is a program quality contract, not the sandbox security boundary.
    Unknown collection expressions can be made explicit with list(...).
    """
    errors = []
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        assignments = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments.setdefault(target.id, []).append(node.value)

        def sequence(node, seen=frozenset()):
            if isinstance(node, (ast.List, ast.Tuple, ast.ListComp)):
                return True
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
                return sequence(node.value, seen)
            if isinstance(node, ast.Call):
                return (isinstance(node.func, ast.Name) and node.func.id in {"list", "tuple", "sorted"}) or (
                    isinstance(node.func, ast.Attribute) and node.func.attr in {"query", "select_memory_entries", "split", "splitlines", "fetchall"})
            if isinstance(node, ast.Name) and node.id not in seen:
                values = assignments.get(node.id, [])
                return bool(values) and all(sequence(v, seen | {node.id}) for v in values)
            return False

        for node in ast.walk(function):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice) and not sequence(node.value):
                errors.append(f"Text or unknown slice at line {node.lineno}: select complete entries with toolkit.select_memory_entries; never truncate material")
    return errors


def static_check(code: str, import_whitelist: set[str] | None = None) -> list[str]:
    whitelist = import_whitelist or DEFAULT_IMPORT_WHITELIST
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"AST parse failed: {exc}"]
    errors: list[str] = _text_slice_errors(tree)
    class_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    classes = [node.name for node in class_nodes]
    top_level_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    if (
        sorted(classes) != sorted(REQUIRED_CLASSES)
        or top_level_classes != REQUIRED_CLASSES
    ):
        errors.append(
            "Program must define exactly KnowledgeItem, Query, and KnowledgeBase "
            f"classes; got {sorted(classes)}"
        )
    plan_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == REVIEW_PLAN_FUNCTION
    ]
    if len(plan_nodes) != 1:
        errors.append(
            "Program must define exactly one top-level "
            "plan_review(toolkit, query, memory_context) function"
        )
    else:
        signature_error = _plan_signature_error(plan_nodes[0])
        if signature_error:
            errors.append(signature_error)
        errors.extend(_argument_access_errors(plan_nodes[0]))
        forbidden_query_fields = {
            node.attr
            for node in ast.walk(plan_nodes[0])
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "query"
            and node.attr not in REQUIRED_PLANNING_QUERY_FIELDS
        }
        if forbidden_query_fields:
            errors.append(
                "plan_review may read only canonical query_text and "
                "reviewer_dimension fields; got "
                f"{sorted(forbidden_query_fields)}"
            )
        coupled_instructions = {
            node.id
            for node in ast.walk(plan_nodes[0])
            if isinstance(node, ast.Name) and node.id in REQUIRED_INSTRUCTIONS
        }
        if coupled_instructions:
            errors.append(
                "plan_review must be independent from Memory instruction constants: "
                f"{sorted(coupled_instructions)}"
            )
        coupled_memory_types = {
            node.id
            for node in ast.walk(plan_nodes[0])
            if isinstance(node, ast.Name) and node.id in REQUIRED_CLASSES
        }
        if coupled_memory_types:
            errors.append(
                "plan_review must be independent from Memory classes: "
                f"{sorted(coupled_memory_types)}"
            )
    top_level_functions = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name != REVIEW_PLAN_FUNCTION
    ]
    if top_level_functions:
        errors.append(
            "Program top-level helper functions are not component-separable: "
            f"{sorted(top_level_functions)}"
        )
    assigned_names: set[str] = set()
    unexpected_statements: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {
                target.id for target in node.targets if isinstance(target, ast.Name)
            }
            if len(names) != len(node.targets):
                unexpected_statements.append(type(node).__name__)
            assigned_names.update(names)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                assigned_names.add(node.target.id)
            else:
                unexpected_statements.append(type(node).__name__)
        elif isinstance(node, ast.Expr):
            if not isinstance(node.value, ast.Constant) or not isinstance(
                node.value.value, str
            ):
                unexpected_statements.append(type(node).__name__)
        elif isinstance(
            node,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            continue
        else:
            unexpected_statements.append(type(node).__name__)
    if assigned_names != REQUIRED_INSTRUCTIONS:
        errors.append(
            "Program top-level constants must be exactly the four Memory instructions; "
            f"got {sorted(assigned_names)}"
        )
    if unexpected_statements:
        errors.append(
            "Program contains unsupported top-level statements: "
            f"{sorted(set(unexpected_statements))}"
        )
    memory_component_nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.ClassDef) and node.name in REQUIRED_CLASSES)
        or (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, ast.Name) and target.id in REQUIRED_INSTRUCTIONS
                for target in (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
            )
        )
    ]
    memory_policy_references = {
        node.id
        for root in memory_component_nodes
        for node in ast.walk(root)
        if isinstance(node, ast.Name) and node.id == REVIEW_PLAN_FUNCTION
    }
    if memory_policy_references:
        errors.append(
            "Memory classes and instructions must be independent from plan_review"
        )
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in {
            "KnowledgeItem",
            "Query",
        }:
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                annotation = ast.unparse(statement.annotation).replace(" ", "")
                if annotation not in ALLOWED_FIELD_TYPES:
                    errors.append(
                        f"{node.name} field type is not JSON-protocol compatible: {annotation}"
                    )
    query_node = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Query"
        ),
        None,
    )
    if query_node is not None:
        query_fields = {
            node.target.id
            for node in query_node.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        missing_query_fields = sorted(REQUIRED_PLANNING_QUERY_FIELDS - query_fields)
        if missing_query_fields:
            errors.append(
                f"Query must preserve canonical planning fields: {missing_query_fields}"
            )
    imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    policy_imports = _referenced_imports(plan_nodes, imports)
    memory_imports = _referenced_imports(memory_component_nodes, imports)
    referenced_import_ids = {id(node) for node in (*policy_imports, *memory_imports)}
    for node in imports:
        if any(alias.name == "*" for alias in node.names):
            errors.append("Star imports are not allowed")
        if id(node) not in referenced_import_ids:
            errors.append(
                "Every import must be referenced by the Memory or S2 policy component: "
                + ast.unparse(node)
            )
        collisions = _bound_import_names(node).intersection(
            REQUIRED_CLASSES | REQUIRED_INSTRUCTIONS | {REVIEW_PLAN_FUNCTION}
        )
        if collisions:
            errors.append(
                "Import bindings collide with Program interface names: "
                f"{sorted(collisions)}"
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
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_ATTRIBUTES
            ):
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
            if isinstance(node.func, ast.Name) and node.func.id == "retrieve_s2":
                errors.append(
                    "Candidate Program may not call host-only capability: retrieve_s2"
                )
        elif isinstance(node, ast.Attribute):
            direct_toolkit = (
                isinstance(node.value, ast.Name) and node.value.id == "toolkit"
            ) or (
                isinstance(node.value, ast.Attribute) and node.value.attr == "toolkit"
            )
            if direct_toolkit and node.attr not in TOOLKIT_INTERFACES:
                errors.append(
                    f"Unknown toolkit interface: {node.attr}; use llm_completion(messages, **kwargs) "
                    "for model calls and embed(texts) for embeddings"
                )
            if node.attr == "retrieve_s2":
                errors.append(
                    "Candidate Program may not access host-only capability: retrieve_s2"
                )
            if node.attr.startswith("__"):
                errors.append(f"Dunder attribute access is not allowed: {node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(
                f"Global state declaration is not allowed at line {node.lineno}"
            )
    return sorted(set(errors))


def program_capability_manifest(
    path: str | Path | None = None,
    code: str | None = None,
) -> dict[str, str]:
    """Return deterministic whole-program, policy, and Memory identities."""
    if (path is None) == (code is None):
        raise ValueError("Provide exactly one of path or code")
    if path is not None:
        source_bytes = Path(path).expanduser().resolve().read_bytes()
        source = source_bytes.decode("utf-8")
    else:
        source = str(code)
        source_bytes = source.encode("utf-8")
    errors = static_check(source)
    if errors:
        raise ValueError(f"Program capability static check failed: {errors}")
    tree = ast.parse(source)
    plan_nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == REVIEW_PLAN_FUNCTION
    ]
    if len(plan_nodes) != 1:
        raise ValueError("Program must define exactly one synchronous plan_review")
    by_name = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in REQUIRED_CLASSES
    }
    if set(by_name) != REQUIRED_CLASSES:
        raise ValueError(
            "Program must define KnowledgeItem, Query, and KnowledgeBase classes"
        )
    memory_nodes = [
        by_name[name] for name in ("KnowledgeItem", "Query", "KnowledgeBase")
    ]
    instruction_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id in REQUIRED_INSTRUCTIONS
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    policy_nodes = [*_referenced_imports(plan_nodes, imports), *plan_nodes]
    memory_component_nodes = [
        *_referenced_imports([*instruction_nodes, *memory_nodes], imports),
        *instruction_nodes,
        *memory_nodes,
    ]
    return {
        "interface_version": REVIEW_PLAN_PROTOCOL,
        "program_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "policy_ast_sha256": _ast_sha256(policy_nodes),
        "memory_ast_sha256": _ast_sha256(memory_component_nodes),
    }
