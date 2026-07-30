import json
import re
from collections import Counter
from typing import Any, Callable, Dict, Optional

from .tasks import META_TASK, SPECIALIST_TASKS


def _tokens(text: str):
    return re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)


def exact_match(prediction: str, gold: str, item: Optional[Dict[str, Any]] = None) -> float:
    def normalize(value):
        return " ".join(_tokens(value))

    return float(normalize(prediction) == normalize(gold))


def token_f1(prediction: str, gold: str, item: Optional[Dict[str, Any]] = None) -> float:
    predicted = _tokens(prediction)
    expected = _tokens(gold)
    if not predicted and not expected:
        return 1.0
    if not predicted or not expected:
        return 0.0
    common = Counter(predicted) & Counter(expected)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted)
    recall = overlap / len(expected)
    return 2.0 * precision * recall / (precision + recall)


def rubric_score(prediction: str, gold: str, item: Optional[Dict[str, Any]] = None) -> float:
    metadata = (item or {}).get("metadata") or {}
    rubric = metadata.get("rubric")
    if not rubric:
        return token_f1(prediction, gold, item)
    required = rubric.get("required_terms", []) if isinstance(rubric, dict) else []
    if not required:
        return token_f1(prediction, gold, item)
    normalized = prediction.lower()
    return sum(term.lower() in normalized for term in required) / len(required)


def build_llm_judge_metric(toolkit) -> Callable:
    def llm_judge(prediction: str, gold: str, item: Optional[Dict[str, Any]] = None) -> float:
        question = (item or {}).get("question", "")
        prompt = (
            "Score the candidate answer against the reference from 0 to 1. "
            "Return only JSON like {\"score\": 0.5}.\n\n"
            f"Question: {question}\nReference: {gold}\nCandidate: {prediction}"
        )
        response = toolkit.llm_completion([{"role": "user", "content": prompt}])
        try:
            match = re.search(r"\{.*\}", response, flags=re.DOTALL)
            data = json.loads(match.group(0) if match else response)
            score = float(data["score"])
        except Exception:
            match = re.search(r"(?<!\d)(?:0(?:\.\d+)?|1(?:\.0+)?)(?!\d)", response)
            if match:
                score = float(match.group(0))
            else:
                score = token_f1(prediction, gold, item)
        return max(0.0, min(1.0, score))

    return llm_judge


def _json_object(text: str) -> Dict[str, Any]:
    start = str(text).find("{")
    if start < 0:
        raise ValueError("judge response contains no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(text[start : index + 1])
                if not isinstance(value, dict):
                    raise ValueError("judge JSON must be an object")
                return value
    raise ValueError("judge response contains incomplete JSON")


def _harmonic(left: float, right: float) -> float:
    left = max(0.0, float(left))
    right = max(0.0, float(right))
    if not left or not right:
        return 0.0
    return 2.0 * left * right / (left + right)


def _normalized_five_point(value: Any) -> Optional[float]:
    if value == "X":
        return None
    if not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value) / 5.0))


class CoCoReviewFitness:
    """Game-resistant task fitness inspired by CoCoReviewBench.

    CoCoReviewBench is a metric-design reference here, not the searched
    architecture. Candidate-provided category fields are never trusted.
    """

    SPECIALIST_METRICS = (
        "alignment",
        "completeness",
        "grounding_specificity",
        "verifiability",
        "clarity",
    )
    META_METRICS = (
        "adjudication_clarity",
        "groundedness",
        "verifiability",
        "conflict_coverage",
        "reference_alignment",
        "reference_completeness",
        "overall_calibration",
        "decision_consistency",
    )

    def __init__(
        self,
        classifier_toolkit,
        judge_toolkit,
        task_id: str,
        formal: bool = True,
    ):
        if task_id not in SPECIALIST_TASKS + (META_TASK,):
            raise ValueError(f"Unknown review task: {task_id}")
        self.classifier_toolkit = classifier_toolkit
        self.judge_toolkit = judge_toolkit
        self.task_id = task_id
        self.formal = bool(formal)
        self.last_details: Dict[str, Any] = {}

    def _candidate_categories(self, prediction: str) -> set:
        try:
            return set(
                self.classifier_toolkit.classify_review(prediction, self.task_id)
            )
        except RuntimeError:
            if self.formal:
                raise
            prefix = rf"\b{re.escape(self.task_id)}-[A-Z]+\b"
            return set(re.findall(prefix, prediction))

    def _specialist_prompt(
        self, prediction: str, gold: str, question: str
    ) -> str:
        return (
            "You are a fixed peer-review evaluator. Compare the candidate "
            "specialist review with correctness-oriented human reference opinions. "
            "Score alignment, completeness, grounding_specificity, verifiability, "
            "and clarity from 1 to 5. Use X only for verifiability when no "
            "verifiable claim exists. Do not reward category-label repetition. "
            "Return one JSON object with exactly these five keys.\n\n"
            f"Task: {self.task_id}\n\nPAPER AND SOURCE ANCHORS:\n"
            f"{question[:60000]}\n\nREFERENCE:\n{gold}\n\nCANDIDATE:\n{prediction}"
        )

    def _meta_prompt(self, prediction: str, gold: str, question: str) -> str:
        return (
            "Evaluate a final meta-review against the human meta-review. Score "
            "adjudication_clarity, groundedness, verifiability, conflict_coverage, "
            "reference_alignment, reference_completeness, overall_calibration, and "
            "decision_consistency from 1 to 5. Return one JSON object with exactly "
            "these eight keys.\n\n"
            f"SPECIALIST INPUTS:\n{question[:60000]}\n\n"
            f"HUMAN META-REVIEW:\n{gold}\n\nCANDIDATE META-REVIEW:\n{prediction}"
        )

    def __call__(
        self,
        prediction: str,
        gold: str,
        item: Optional[Dict[str, Any]] = None,
    ) -> float:
        metadata = (item or {}).get("metadata") or {}
        question = str((item or {}).get("question") or "")
        prompt = (
            self._meta_prompt(prediction, gold, question)
            if self.task_id == META_TASK
            else self._specialist_prompt(prediction, gold, question)
        )
        response = self.judge_toolkit.llm_completion(
            [{"role": "user", "content": prompt}]
        )
        parsed = _json_object(response)
        expected = (
            set(self.META_METRICS)
            if self.task_id == META_TASK
            else set(self.SPECIALIST_METRICS)
        )
        if set(parsed) != expected:
            raise ValueError(
                f"Judge fields mismatch: expected={sorted(expected)}, "
                f"got={sorted(parsed)}"
            )
        for name, value in parsed.items():
            if value == "X" and name == "verifiability":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 1 <= float(value) <= 5
            ):
                raise ValueError(f"Invalid 1-5 judge score for {name}: {value!r}")
        if self.task_id == META_TASK:
            normalized = {
                name: _normalized_five_point(parsed.get(name))
                for name in self.META_METRICS
            }
            adjudication_values = [
                normalized[name]
                for name in self.META_METRICS[:4]
                if normalized[name] is not None
            ]
            reference_values = [
                normalized[name]
                for name in ("reference_alignment", "reference_completeness")
                if normalized[name] is not None
            ]
            decision_values = [
                normalized[name]
                for name in ("overall_calibration", "decision_consistency")
                if normalized[name] is not None
            ]
            groups = [
                sum(values) / len(values)
                for values in (adjudication_values, reference_values, decision_values)
                if values
            ]
            if len(groups) != 3 or any(value <= 0 for value in groups):
                score = 0.0
            else:
                score = len(groups) / sum(1.0 / value for value in groups)
            self.last_details = {
                "task_id": self.task_id,
                "judge": parsed,
                "groups": groups,
                "fitness": score,
            }
            return score

        normalized = {
            name: _normalized_five_point(parsed.get(name))
            for name in self.SPECIALIST_METRICS
        }
        values = [value for value in normalized.values() if value is not None]
        quality = sum(values) / len(values) if values else 0.0
        human_categories = set(metadata.get("categories") or [])
        candidate_categories = self._candidate_categories(prediction)
        coverage = (
            len(candidate_categories & human_categories) / len(human_categories)
            if human_categories
            else 0.0
        )
        coverage = max(0.0, min(1.0, coverage))
        score = _harmonic(quality, coverage)
        self.last_details = {
            "task_id": self.task_id,
            "judge": parsed,
            "normalized_quality": normalized,
            "quality_mean": quality,
            "human_categories": sorted(human_categories),
            "candidate_categories": sorted(candidate_categories),
            "intersection_coverage": coverage,
            "fitness": score,
        }
        return score


def get_metric(
    name: str,
    toolkit=None,
    *,
    task_id: Optional[str] = None,
    judge_toolkit=None,
    formal: bool = True,
) -> Callable:
    metrics = {
        "exact_match": exact_match,
        "token_f1": token_f1,
        "rubric_score": rubric_score,
    }
    if name == "llm_judge":
        if toolkit is None:
            raise ValueError("llm_judge requires a configured toolkit")
        return build_llm_judge_metric(toolkit)
    if name == "coco_review":
        if toolkit is None or judge_toolkit is None or task_id is None:
            raise ValueError(
                "coco_review requires classifier toolkit, distinct judge toolkit, "
                "and task_id"
            )
        return CoCoReviewFitness(
            toolkit,
            judge_toolkit,
            task_id=task_id,
            formal=formal,
        )
    try:
        return metrics[name]
    except KeyError as exc:
        raise ValueError(f"Unknown metric: {name}") from exc
