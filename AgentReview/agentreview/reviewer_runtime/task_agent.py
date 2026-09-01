import json
import re
from dataclasses import MISSING, fields
from typing import Any

def _annotation_name(annotation: Any) -> str:
    return getattr(annotation, "__name__", str(annotation).replace("typing.", ""))


def dataclass_schema(dataclass_type: type) -> dict[str, Any]:
    properties = {}
    required = []
    for item in fields(dataclass_type):
        properties[item.name] = {"type": _annotation_name(item.type)}
        if item.default is MISSING and item.default_factory is MISSING:
            required.append(item.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _parse_json_object(response: str) -> dict[str, Any]:
    text = (response or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        value = None
        for match in re.finditer(r"\{", text):
            try:
                candidate, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
        if value is None:
            raise
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _matches_annotation(value: Any, annotation: Any) -> bool:
    name = getattr(annotation, "__name__", str(annotation)).lower()
    if "list" in name:
        return isinstance(value, list)
    if "dict" in name:
        return isinstance(value, dict)
    if "bool" in name:
        return isinstance(value, bool)
    if "int" in name:
        return isinstance(value, int) and not isinstance(value, bool)
    if "float" in name:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


class TaskAgent:
    """Fixed prompting wrapper whose behavior is influenced only by program instructions."""

    def __init__(
        self,
        toolkit,
        system_prompt: str = "You are a precise task agent.",
        task_id: str = "",
    ):
        self.toolkit = toolkit
        self.system_prompt = system_prompt
        self.task_id = task_id
        self.last_parser_debug: dict[str, Any] = {}

    def _complete(self, prompt: str, agent_name: str = "Memory Harness") -> str:
        return self.toolkit.llm_completion(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            agent_name=agent_name,
        )

    def _structured_completion(self, prompt: str, dataclass_type: type):
        response = self._complete(
            prompt, agent_name=f"Memory {dataclass_type.__name__}"
        )
        self.last_parser_debug = {
            "schema": dataclass_type.__name__,
            "raw_response_prefix": response[:500],
            "parser_success": False,
        }
        try:
            value = _parse_json_object(response)
        except Exception as exc:
            self.last_parser_debug["parser_error"] = f"{type(exc).__name__}: {exc}"
            raise
        items = list(fields(dataclass_type))
        expected = {item.name for item in items}
        required = {
            item.name
            for item in items
            if item.default is MISSING and item.default_factory is MISSING
        }
        actual = set(value)
        missing = required - actual
        extra = actual - expected
        if missing or extra:
            self.last_parser_debug["parser_result"] = value
            self.last_parser_debug["parser_error"] = (
                f"field mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
            )
            raise ValueError(
                f"JSON fields do not match {dataclass_type.__name__}: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        invalid = [
            item.name
            for item in items
            if item.name in value
            and not _matches_annotation(value[item.name], item.type)
        ]
        if invalid:
            self.last_parser_debug["parser_result"] = value
            self.last_parser_debug["parser_error"] = (
                f"type mismatch: fields={sorted(invalid)}"
            )
            raise TypeError(
                f"JSON field types do not match {dataclass_type.__name__}: "
                f"{sorted(invalid)}"
            )
        self.last_parser_debug.update({"parser_success": True, "parser_result": value})
        return dataclass_type(**value)

    def extract_knowledge_item(self, program, raw_text: str):
        schema = json.dumps(dataclass_schema(program.KnowledgeItem), ensure_ascii=False)
        prompt = f"""
{program.INSTRUCTION_KNOWLEDGE_ITEM}

Text:
{raw_text}

The KnowledgeItem must conform to this schema:
{schema}

Output ONLY a valid JSON object matching the schema fields. No explanation.
""".strip()
        return self._structured_completion(prompt, program.KnowledgeItem)

    def generate_query(self, program, question: str):
        schema = json.dumps(dataclass_schema(program.Query), ensure_ascii=False)
        prompt = f"""
{program.INSTRUCTION_QUERY}

Question:
{question}

The Query must conform to this schema:
{schema}

Output ONLY a valid JSON object matching the schema fields. No explanation.
""".strip()
        return self._structured_completion(prompt, program.Query)

    def generate_response(self, program, question: str, memory_context: str) -> str:
        prompt = f"""
{program.INSTRUCTION_RESPONSE}

Always-on knowledge:
{program.ALWAYS_ON_KNOWLEDGE}

Original question:
{question}

Memory context:
{memory_context}

Provide the final answer only.
""".strip()
        return self._complete(prompt, agent_name="Memory Response").strip()
