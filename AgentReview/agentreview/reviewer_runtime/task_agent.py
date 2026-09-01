import json
import re
import types
from dataclasses import fields
from typing import Any, Union, get_args, get_origin


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if isinstance(annotation, str):
        normalized = annotation.replace("typing.", "").replace(" ", "")
        string_schemas = {
            "str": {"type": "string"},
            "int": {"type": "integer"},
            "float": {"type": "number"},
            "bool": {"type": "boolean"},
            "list": {"type": "array", "items": {"type": "string"}},
            "list[str]": {"type": "array", "items": {"type": "string"}},
            "List[str]": {"type": "array", "items": {"type": "string"}},
            "Optional[str]": {
                "anyOf": [{"type": "string"}, {"type": "null"}]
            },
            "str|None": {
                "anyOf": [{"type": "string"}, {"type": "null"}]
            },
            "None|str": {
                "anyOf": [{"type": "string"}, {"type": "null"}]
            },
        }
        if normalized not in string_schemas:
            raise TypeError(f"Unsupported dataclass field annotation: {annotation!r}")
        return string_schemas[normalized]

    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is list:
        # The candidate sandbox permits only list[str] and preserves it as list.
        return {"type": "array", "items": {"type": "string"}}

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is list:
        item_annotation = arguments[0] if arguments else str
        return {"type": "array", "items": _annotation_schema(item_annotation)}
    if origin in {Union, types.UnionType}:
        return {
            "anyOf": [
                {"type": "null"}
                if argument is type(None)
                else _annotation_schema(argument)
                for argument in arguments
            ]
        }
    raise TypeError(f"Unsupported dataclass field annotation: {annotation!r}")


def dataclass_schema(dataclass_type: type) -> dict[str, Any]:
    properties = {}
    for item in fields(dataclass_type):
        properties[item.name] = _annotation_schema(item.type)
    return {
        "type": "object",
        "properties": properties,
        # OpenAI strict structured output requires every property to be required;
        # nullable annotations express nullability in the property's own schema.
        "required": list(properties),
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
    if isinstance(annotation, str):
        normalized = annotation.replace("typing.", "").replace(" ", "")
        if normalized in {"Optional[str]", "str|None", "None|str"}:
            return value is None or isinstance(value, str)
        if normalized in {"list", "list[str]", "List[str]"}:
            return isinstance(value, list) and all(
                isinstance(item, str) for item in value
            )
        annotation = {"str": str, "int": int, "float": float, "bool": bool}.get(
            normalized, annotation
        )

    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is list:
        item_annotation = arguments[0] if arguments else str
        return isinstance(value, list) and all(
            _matches_annotation(item, item_annotation) for item in value
        )
    if origin in {Union, types.UnionType}:
        return any(
            value is None
            if argument is type(None)
            else _matches_annotation(value, argument)
            for argument in arguments
        )
    if annotation is list:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if annotation is bool:
        return isinstance(value, bool)
    if annotation is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if annotation is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if annotation is str:
        return isinstance(value, str)
    return False


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

    def _complete(
        self,
        prompt: str,
        agent_name: str = "Memory Harness",
        response_format: dict[str, Any] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {"agent_name": agent_name}
        if response_format is not None:
            kwargs["response_format"] = response_format
        return self.toolkit.llm_completion(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )

    def _structured_completion(self, prompt: str, dataclass_type: type):
        schema = dataclass_schema(dataclass_type)
        response = self._complete(
            prompt,
            agent_name=f"Memory {dataclass_type.__name__}",
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": dataclass_type.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
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
        required = set(schema["required"])
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
