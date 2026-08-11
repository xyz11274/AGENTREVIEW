import ast
import json
import random
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .quality import DEFAULT_IMPORT_WHITELIST, check_program_code


@dataclass
class ChangeLog:
    summary: str
    details: List[str] = field(default_factory=list)
    score: Optional[float] = None


@dataclass
class EvalFeedback:
    static_score: float
    rotating_score: float
    failed_cases: List[Dict[str, Any]]
    success_cases: List[Dict[str, Any]]
    traces: List[Dict[str, Any]]
    task_description: str
    write_examples: List[Dict[str, Any]] = field(default_factory=list)
    debug_logs: List[Dict[str, Any]] = field(default_factory=list)
    feedback_seed: int = 0


@dataclass
class EvolutionConstraints:
    task_description: str
    import_whitelist: List[str] = field(
        default_factory=lambda: sorted(DEFAULT_IMPORT_WHITELIST)
    )
    max_read_chars: int = 3000
    max_llm_calls_per_operation: int = 1
    write_timeout: float = 60.0
    read_timeout: float = 60.0
    mutation_scope: str = "full"


@dataclass
class MutationResult:
    analysis: str
    strategy: str
    program_code: str
    change_log: ChangeLog
    raw_response: str = ""


@dataclass
class CompileFixResult:
    program_code: str
    attempts: int
    passed: bool
    errors: List[str] = field(default_factory=list)


def _complete(client, messages) -> str:
    if client is None:
        raise RuntimeError("Reflector requires an LLM client")
    if hasattr(client, "llm_completion"):
        return str(client.llm_completion(messages))
    if hasattr(client, "_get_response"):
        return str(client._get_response(messages))
    if hasattr(client, "complete"):
        return str(client.complete(messages=messages))
    if callable(client):
        return str(client(messages))
    raise TypeError(f"Unsupported reflector client: {type(client).__name__}")


def _apply_v4a_patch(source: str, response: str) -> tuple:
    """Apply the paper's single-file V4A update format."""
    begin = response.find("*** Begin Patch")
    end = response.find("*** End Patch", begin + 1)
    if begin < 0 or end < 0:
        raise ValueError("Reflector response is missing V4A patch markers")
    patch_lines = response[begin:end].splitlines()
    update_lines = [
        index
        for index, line in enumerate(patch_lines)
        if line.startswith("*** Update File:")
    ]
    if len(update_lines) != 1:
        raise ValueError("V4A patch must contain exactly one Update File block")
    filename = patch_lines[update_lines[0]].split(":", 1)[1].strip()
    if filename != "program.py":
        raise ValueError("V4A patch may update only program.py")

    hunks = []
    current = None
    for line in patch_lines[update_lines[0] + 1 :]:
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if line.startswith("*** "):
            raise ValueError(f"Unsupported V4A operation: {line}")
        if current is None:
            if line.strip():
                raise ValueError("V4A change appeared before the first @@ hunk")
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
    result = "\n".join(source_lines) + ("\n" if trailing_newline else "")
    commit_block = response[:begin].strip()
    title_match = re.search(r"(?m)^Title:\s*(.+)$", commit_block)
    details = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^-\s+(.+)$", commit_block)
    ]
    title = title_match.group(1).strip() if title_match else "V4A memory mutation"
    return result, ChangeLog(summary=title, details=details), commit_block


def _scope_parts(code: str) -> Dict[str, Any]:
    tree = ast.parse(code)
    instructions = {}
    classes = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and (
                    target.id.startswith("INSTRUCTION_")
                    or target.id == "ALWAYS_ON_KNOWLEDGE"
                ):
                    instructions[target.id] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.ClassDef) and node.name in {
            "KnowledgeItem",
            "Query",
            "KnowledgeBase",
        }:
            classes[node.name] = ast.dump(node, include_attributes=False)
    return {"instructions": instructions, "classes": classes}


def validate_mutation_scope(parent: str, child: str, scope: str) -> None:
    if scope == "full":
        return
    parent_parts = _scope_parts(parent)
    child_parts = _scope_parts(child)
    if scope == "schema_logic":
        if parent_parts["instructions"] != child_parts["instructions"]:
            raise ValueError("schema_logic mutation changed instruction constants")
    elif scope == "instructions_only":
        if parent_parts["classes"] != child_parts["classes"]:
            raise ValueError("instructions_only mutation changed schema or logic")
    else:
        raise ValueError(f"Unknown mutation scope: {scope}")


def mutate_program(
    parent_program_code: str,
    eval_feedback: EvalFeedback,
    lineage_log: List[ChangeLog],
    llm_client,
    constraints: EvolutionConstraints,
    reference_programs: Optional[List[str]] = None,
) -> MutationResult:
    rng = random.Random(eval_feedback.feedback_seed)
    failed_cases = list(eval_feedback.failed_cases)
    if len(failed_cases) > 2:
        # Efraimidis–Spirakis weighted sampling without replacement.
        keyed = []
        for case in failed_cases:
            weight = max(1e-6, 1.0 - float(case.get("score", 0.0)))
            key = rng.random() ** (1.0 / weight)
            keyed.append((key, case))
        failed_cases = [
            case for _, case in sorted(keyed, key=lambda value: value[0], reverse=True)[:2]
        ]
    selected_feedback = asdict(eval_feedback)
    selected_feedback["failed_cases"] = failed_cases
    selected_feedback["success_cases"] = list(eval_feedback.success_cases)[:2]
    payload = {
        "feedback": selected_feedback,
        "lineage": [asdict(item) for item in lineage_log],
        "constraints": asdict(constraints),
        "reference_programs": list(reference_programs or []),
    }
    prompt = f"""
You are an expert Python programmer specializing in knowledge base system design.
Given a task-specific MemoryProgram, its score, and rotating-validation failures,
diagnose root causes and improve only the MemoryProgram. The model weights,
Task Agent, topology, and evaluator are fixed.

The program consists of three coupled parts:
1. Schema: KnowledgeItem and Query dataclasses.
2. Logic: KnowledgeBase.write() and KnowledgeBase.read().
3. Instruction: all three INSTRUCTION_* constants plus ALWAYS_ON_KNOWLEDGE.

KnowledgeBase.__init__(toolkit) receives the paper-compatible toolkit:
- toolkit.db: an in-memory sqlite3 connection.
- toolkit.chroma: an ephemeral Chroma-compatible client.
- toolkit.llm_completion(messages, **kwargs): at most one call per read/write.
- toolkit.logger.debug(message): captured memory debug logging.

KnowledgeItem and Query fields must use only str, int, float, bool, list[str],
or Optional[str], with dataclass metadata descriptions where useful.

Make a targeted improvement based on the traces. Diagnose missing storage fields,
retrieval recall, ranking, excessive context, entity/time/state matching, or other
specific failures. Preserve structures that previously improved the score. If the
parent has a compile/runtime issue, prefer the smallest repair.

Hard constraints:
- Keep KnowledgeBase.write(item, raw_text) and KnowledgeBase.read(query) compatible.
- Imports must be from this whitelist only:
  {", ".join(constraints.import_whitelist)}
- Do not access files, networks, subprocesses, eval, or exec.
- read() must always return str with at most {constraints.max_read_chars} characters.
- A read or write operation may call the LLM at most
  {constraints.max_llm_calls_per_operation} time(s).
- Do not modify or work around the evaluator or quality gates.
- Mutation scope is {constraints.mutation_scope}. For "schema_logic", keep all
  instruction constants unchanged. For "instructions_only", keep both dataclasses
  and KnowledgeBase logic unchanged. For "full", all MemoryProgram parts may change.

Task and evaluation feedback:
{json.dumps(payload, ensure_ascii=False)}

Parent program:
```python
{parent_program_code}
```

Before the patch, output exactly this commit-message structure:
*** Commit Message
Title: <one-line summary>
- <root cause / diagnosis>
- <specific change>

Then output one V4A patch. The markers must be on their own lines:
*** Begin Patch
*** Update File: program.py
@@ <optional context hint>
 unchanged context line
-removed line
+added line
 unchanged context line
*** End Patch

Use one or more contextual update hunks. Do not output a full replacement file,
JSON, Markdown fences, Add File, Delete File, or any path other than program.py.
""".strip()
    messages = [{"role": "user", "content": prompt}]
    raw_responses = []
    last_error = "Reflector did not return an applicable V4A patch"
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        raw = _complete(llm_client, messages)
        raw_responses.append(raw)
        try:
            code, change_log, analysis = _apply_v4a_patch(
                parent_program_code, raw
            )
            validate_mutation_scope(
                parent_program_code,
                code,
                constraints.mutation_scope,
            )
            return MutationResult(
                analysis=analysis,
                strategy=change_log.summary,
                program_code=code,
                change_log=change_log,
                raw_response="\n\n--- reflector attempt separator ---\n\n".join(
                    raw_responses
                ),
            )
        except ValueError as exc:
            last_error = str(exc)
        if attempt >= max_attempts:
            break
        repair_prompt = f"""
The previous reflector response could not be applied: {last_error}

Repair it into the required commit message followed by exactly one contextual
V4A Update File patch for program.py. Do not return JSON, Markdown fences, or a
complete replacement file.

Previous response:
{raw}
""".strip()
        messages = [{"role": "user", "content": repair_prompt}]
    raise ValueError(f"{last_error} after {max_attempts} reflector attempts")


def compile_fix_loop(
    mutated_code: str,
    error_messages: List[str],
    max_attempts: int = 3,
    llm_client=None,
    constraints: Optional[EvolutionConstraints] = None,
    toolkit=None,
) -> CompileFixResult:
    constraints = constraints or EvolutionConstraints(task_description="")
    code = mutated_code
    errors = list(error_messages)
    if llm_client is None:
        return CompileFixResult(code, 0, False, errors)

    for attempt in range(1, max(0, int(max_attempts)) + 1):
        prompt = f"""
Repair the following memory program with the smallest possible change.
Return a commit message followed by one V4A contextual update patch for
program.py using exact *** Begin Patch / *** End Patch markers.

Errors:
{json.dumps(errors, ensure_ascii=False)}

Constraints:
{json.dumps(asdict(constraints), ensure_ascii=False)}

Program:
```python
{code}
```
""".strip()
        raw = _complete(llm_client, [{"role": "user", "content": prompt}])
        try:
            code, _, _ = _apply_v4a_patch(code, raw)
        except ValueError as exc:
            errors = [str(exc)]
            continue
        quality = check_program_code(
            code,
            toolkit=toolkit,
            import_whitelist=set(constraints.import_whitelist),
            write_timeout=constraints.write_timeout,
            read_timeout=constraints.read_timeout,
        )
        errors = quality.errors
        if quality.passed:
            return CompileFixResult(code, attempt, True, [])
    return CompileFixResult(code, max(0, int(max_attempts)), False, errors)
