import ast
import signal
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .sandbox import SandboxSession
from .toolkit import MemoryToolkit


DEFAULT_IMPORT_WHITELIST = {
    "dataclasses",
    "typing",
    "math",
    "re",
    "json",
    "sqlite3",
    "hashlib",
    "collections",
    "heapq",
    "time",
    "statistics",
    "itertools",
    "datetime",
    "textwrap",
    "chromadb",
}
REQUIRED_SYMBOLS = {
    "INSTRUCTION_KNOWLEDGE_ITEM",
    "INSTRUCTION_QUERY",
    "INSTRUCTION_RESPONSE",
    "ALWAYS_ON_KNOWLEDGE",
    "KnowledgeItem",
    "Query",
    "KnowledgeBase",
}
FORBIDDEN_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
}
FORBIDDEN_ATTRIBUTES = {
    "system",
    "popen",
    "Popen",
    "run",
    "call",
    "check_call",
    "check_output",
    "urlopen",
    "request",
    "connect",
}


class ProgramTimeoutError(TimeoutError):
    pass


@contextmanager
def time_limit(seconds: float, operation: str):
    seconds = float(seconds)
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def handler(signum, frame):
        raise ProgramTimeoutError(f"{operation} exceeded {seconds:.2f} seconds")

    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


@dataclass
class QualityResult:
    passed: bool
    static_errors: List[str] = field(default_factory=list)
    compile_errors: List[str] = field(default_factory=list)
    smoke_errors: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[str]:
        return self.static_errors + self.compile_errors + self.smoke_errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def static_check(
    code: str, import_whitelist: Optional[set] = None
) -> List[str]:
    whitelist = import_whitelist or DEFAULT_IMPORT_WHITELIST
    errors: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"AST parse failed: {exc}"]

    top_level_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    expected_classes = {"KnowledgeItem", "Query", "KnowledgeBase"}
    if top_level_classes != expected_classes:
        errors.append(
            "Program must define exactly KnowledgeItem, Query, and KnowledgeBase "
            f"classes; got {sorted(top_level_classes)}"
        )
    allowed_field_types = {
        "str",
        "int",
        "float",
        "bool",
        "list[str]",
        "List[str]",
        "Optional[str]",
    }
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name not in {
            "KnowledgeItem",
            "Query",
        }:
            continue
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign):
                annotation = ast.unparse(statement.annotation).replace(" ", "")
                if annotation not in allowed_field_types:
                    errors.append(
                        f"{node.name} field type is not JSON-protocol compatible: "
                        f"{annotation}"
                    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in whitelist:
                    errors.append(f"Import is not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in whitelist:
                errors.append(f"Import is not allowed: {node.module or '<relative>'}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                errors.append(f"Forbidden call: {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                attribute = node.func.attr
                if attribute in FORBIDDEN_ATTRIBUTES:
                    if attribute == "connect":
                        if (
                            isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "sqlite3"
                            and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and node.args[0].value == ":memory:"
                        ):
                            continue
                    errors.append(f"Forbidden call attribute: {attribute}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"Dunder attribute access is not allowed: {node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            errors.append(f"Global state declaration is not allowed at line {node.lineno}")

    return sorted(set(errors))


def _sample_value(field_name: str, annotation: Any) -> Any:
    annotation_name = getattr(annotation, "__name__", str(annotation)).lower()
    if "bool" in annotation_name:
        return False
    if "int" in annotation_name:
        return 1
    if "float" in annotation_name:
        return 1.0
    if "list" in annotation_name:
        return []
    if "dict" in annotation_name:
        return {}
    return f"sample {field_name}"


def _instantiate(dataclass_type):
    kwargs = {}
    for item in fields(dataclass_type):
        if item.default is MISSING and item.default_factory is MISSING:
            kwargs[item.name] = _sample_value(item.name, item.type)
    return dataclass_type(**kwargs)


def _default_smoke_toolkit() -> MemoryToolkit:
    class FakeLLM:
        def llm_completion(self, messages, **kwargs):
            return "mock summary"

    return MemoryToolkit(llm_client=FakeLLM())


def compile_and_smoke_check(
    program_path: str,
    toolkit: Optional[MemoryToolkit] = None,
    import_timeout: float = 10.0,
    write_timeout: float = 60.0,
    read_timeout: float = 60.0,
    sandbox_memory_mb: int = 1024,
):
    compile_errors: List[str] = []
    smoke_errors: List[str] = []
    session = None
    try:
        active_toolkit = toolkit or _default_smoke_toolkit()
        session = SandboxSession(
            program_path,
            active_toolkit,
            timeout_seconds=max(import_timeout, write_timeout, read_timeout),
            memory_mb=sandbox_memory_mb,
            cpu_seconds=max(1, int(max(write_timeout, read_timeout))),
        )
        program = session.program_interface()
    except Exception as exc:
        if session is not None:
            session.close(force=True)
        return [f"Program import failed: {type(exc).__name__}: {exc}"], smoke_errors

    missing = sorted(symbol for symbol in REQUIRED_SYMBOLS if not hasattr(program, symbol))
    if missing:
        compile_errors.append(f"Missing required symbols: {', '.join(missing)}")
        return compile_errors, smoke_errors

    if not all(
        isinstance(getattr(program, name), str)
        for name in (
            "INSTRUCTION_KNOWLEDGE_ITEM",
            "INSTRUCTION_QUERY",
            "INSTRUCTION_RESPONSE",
            "ALWAYS_ON_KNOWLEDGE",
        )
    ):
        compile_errors.append("All instruction and always-on symbols must be strings")

    for schema_name in ("KnowledgeItem", "Query"):
        schema_type = getattr(program, schema_name)
        if not isinstance(schema_type, type) or not is_dataclass(schema_type):
            compile_errors.append(f"{schema_name} must be a dataclass type")
            continue
        schema_fields = list(fields(schema_type))
        if not schema_fields:
            compile_errors.append(f"{schema_name} must define at least one field")
        if len(schema_fields) > 20:
            compile_errors.append(f"{schema_name} exceeds the 20-field limit")
        for schema_field in schema_fields:
            annotation = getattr(schema_field.type, "__name__", str(schema_field.type))
            if not any(
                name in annotation
                for name in ("str", "int", "float", "bool", "List", "list")
            ):
                compile_errors.append(
                    f"{schema_name}.{schema_field.name} uses unsupported type {annotation}"
                )

    if compile_errors:
        return compile_errors, smoke_errors

    try:
        item = _instantiate(program.KnowledgeItem)
        query = _instantiate(program.Query)
        active_toolkit.reset_vector_collections()
        with active_toolkit.operation("quality_smoke_write"):
            session.write(item, "A smoke-test episode with one useful fact.")
        with active_toolkit.operation("quality_smoke_read"):
            result = session.read(query)
        if not isinstance(result, str):
            smoke_errors.append(f"read() returned {type(result).__name__}, expected str")
        elif len(result) > 3000:
            smoke_errors.append(f"read() returned {len(result)} characters, limit is 3000")
    except Exception as exc:
        smoke_errors.append(f"Smoke test failed: {type(exc).__name__}: {exc}")
    finally:
        if session is not None:
            session.close(force=bool(smoke_errors))

    return compile_errors, smoke_errors


def check_program_path(
    program_path: str,
    toolkit: Optional[MemoryToolkit] = None,
    import_whitelist: Optional[set] = None,
    import_timeout: float = 10.0,
    write_timeout: float = 60.0,
    read_timeout: float = 60.0,
    sandbox_memory_mb: int = 1024,
) -> QualityResult:
    path = Path(program_path)
    try:
        code = path.read_text(encoding="utf-8")
    except Exception as exc:
        return QualityResult(False, compile_errors=[f"Cannot read program: {exc}"])
    static_errors = static_check(code, import_whitelist)
    if static_errors:
        return QualityResult(False, static_errors=static_errors)
    compile_errors, smoke_errors = compile_and_smoke_check(
        str(path),
        toolkit=toolkit,
        import_timeout=import_timeout,
        write_timeout=write_timeout,
        read_timeout=read_timeout,
        sandbox_memory_mb=sandbox_memory_mb,
    )
    return QualityResult(
        passed=not (static_errors or compile_errors or smoke_errors),
        static_errors=static_errors,
        compile_errors=compile_errors,
        smoke_errors=smoke_errors,
    )


def check_program_code(
    code: str,
    toolkit: Optional[MemoryToolkit] = None,
    import_whitelist: Optional[set] = None,
    import_timeout: float = 10.0,
    write_timeout: float = 60.0,
    read_timeout: float = 60.0,
    sandbox_memory_mb: int = 1024,
) -> QualityResult:
    static_errors = static_check(code, import_whitelist)
    if static_errors:
        return QualityResult(False, static_errors=static_errors)
    with tempfile.TemporaryDirectory(prefix="mstar_quality_") as directory:
        path = Path(directory) / "candidate.py"
        path.write_text(code, encoding="utf-8")
        compile_errors, smoke_errors = compile_and_smoke_check(
            str(path),
            toolkit=toolkit,
            import_timeout=import_timeout,
            write_timeout=write_timeout,
            read_timeout=read_timeout,
            sandbox_memory_mb=sandbox_memory_mb,
        )
    return QualityResult(
        passed=not (static_errors or compile_errors or smoke_errors),
        static_errors=static_errors,
        compile_errors=compile_errors,
        smoke_errors=smoke_errors,
    )
