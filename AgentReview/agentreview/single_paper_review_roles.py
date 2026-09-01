"""Expose frozen Specialist and independent Meta prompt configurations."""
from agentreview.reviewer_identity import (
    EXPECTED_REVIEW_REQUEST_SHA256,
    META_REVIEWER_NAME,
    REVIEWER_IDENTITY_VERSION,
    REVIEWER_SPECS,
    load_meta_synthesizer,
    load_review_request_sections,
    load_reviewer_suite,
)

SPECIALIST_BACKEND = {
    "backend_type": "openai-chat",
    "temperature": 0.0,
    "max_tokens": 8192,
    "chat_template_kwargs": {"enable_thinking": False},
}
META_BACKEND = {
    "backend_type": "openai-chat",
    "temperature": 0.0,
    "max_tokens": 4096,
    "chat_template_kwargs": {"enable_thinking": False},
}


def get_specialist_reviewer_description(dimension: str) -> str:
    """Return the final rendered system prompt for one specialist task."""
    return load_reviewer_suite().task(dimension).system_prompt


def get_meta_reviewer_description() -> str:
    return load_meta_synthesizer().system_prompt


def _metadata(task_id: str, focus: str = "") -> dict[str, str]:
    return {
        "review_dimension": task_id,
        "focus": focus,
        **load_reviewer_suite().identity(task_id),
    }


def build_specialist_reviewer_configs() -> list[dict]:
    suite = load_reviewer_suite()
    return [
        {
            "name": str(spec["name"]),
            "role_desc": suite.task(str(spec["dimension"])).system_prompt,
            "backend": dict(SPECIALIST_BACKEND),
            "metadata": _metadata(str(spec["dimension"]), str(spec["focus"])),
        }
        for spec in REVIEWER_SPECS
    ]


def build_meta_reviewer_config() -> dict:
    synthesizer = load_meta_synthesizer()
    return {
        "name": META_REVIEWER_NAME,
        "role_desc": synthesizer.system_prompt,
        "backend": dict(META_BACKEND),
        "metadata": {"component": "meta_synthesis", **synthesizer.identity()},
    }


__all__ = (
    "EXPECTED_REVIEW_REQUEST_SHA256",
    "META_REVIEWER_NAME",
    "REVIEWER_IDENTITY_VERSION",
    "REVIEWER_SPECS",
    "build_meta_reviewer_config",
    "build_specialist_reviewer_configs",
    "get_meta_reviewer_description",
    "get_specialist_reviewer_description",
    "load_review_request_sections",
)
