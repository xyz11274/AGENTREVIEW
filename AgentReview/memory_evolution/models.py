from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Episode:
    id: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "Episode":
        if isinstance(value, cls):
            return value
        return cls(
            id=str(value.get("id", "")),
            text=str(value.get("text", "")),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class EvalItem:
    id: str
    question: str
    answer: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: Any) -> "EvalItem":
        if isinstance(value, cls):
            return value
        return cls(
            id=str(value.get("id", "")),
            question=str(value.get("question", "")),
            answer=str(value.get("answer", "")),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass
class EvalResult:
    mean_score: float
    success_cases: List[Dict[str, Any]] = field(default_factory=list)
    failed_cases: List[Dict[str, Any]] = field(default_factory=list)
    traces: List[Dict[str, Any]] = field(default_factory=list)
    runtime_stats: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def to_serializable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_serializable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): to_serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_serializable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
