#AgentReview/agentreview/backends/openai.py

import os
import random
import re
import threading
import time
from typing import List

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from agentreview.arguments import parse_args
from agentreview.utility.authentication_utils import get_openai_client
from .base import IntelligenceBackend
from ..message import SYSTEM_NAME, Message

# Default config follows the OpenAI playground
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_TOKENS = 4096

# Check https://platform.openai.com/docs/models for more models

DEFAULT_MODEL = "gpt-5-mini"

END_OF_MESSAGE = "<EOS>"  # End of message token specified by us not OpenAI
STOP = ("<|endoftext|>", END_OF_MESSAGE)  # End of sentence token
BASE_PROMPT = f"The messages always end with the token {END_OF_MESSAGE}."

_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


class EmptyModelResponseError(RuntimeError):
    """The provider returned HTTP success without assistant text."""

    def __init__(
        self,
        message,
        *,
        finish_reason=None,
        completion_tokens=None,
        reasoning_tokens=None,
        requested_tokens=None,
    ):
        super().__init__(message)
        self.finish_reason = finish_reason
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.requested_tokens = requested_tokens
        self.max_completion_tokens_exhausted = finish_reason == "length"
        self.reasoning_tokens_exhausted = bool(
            finish_reason == "length"
            and reasoning_tokens
            and completion_tokens
            and reasoning_tokens >= completion_tokens * 0.8
        )


def _exception_chain_summary(error: Exception) -> str:
    values = []
    current = error
    seen = set()
    while current is not None and id(current) not in seen and len(values) < 4:
        seen.add(id(current))
        values.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " <- ".join(values)


def _log_retry(retry_state):
    error = retry_state.outcome.exception()
    agent_name = retry_state.args[2] if len(retry_state.args) > 2 else "request"
    wait_seconds = retry_state.next_action.sleep if retry_state.next_action else 0
    category = (
        "TIMEOUT"
        if isinstance(error, (APITimeoutError, httpx.TimeoutException))
        else "HTTP/API ERROR"
    )
    print(
        f"[API RETRY][{category}] {agent_name}: "
        f"attempt {retry_state.attempt_number + 1}/3 "
        f"in {wait_seconds:.1f}s after {_exception_chain_summary(error)}",
        flush=True,
    )


def _rate_limit_chat_request(min_interval_seconds: float):
    global _LAST_REQUEST_AT
    min_interval_seconds = max(0.0, float(min_interval_seconds or 0.0))
    with _REQUEST_LOCK:
        now = time.monotonic()
        wait_seconds = min_interval_seconds - (now - _LAST_REQUEST_AT)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _LAST_REQUEST_AT = time.monotonic()


class OpenAIChat(IntelligenceBackend):
    """Interface to the ChatGPT style model with system, user, assistant roles separation."""

    stateful = False
    type_name = "openai-chat"

    def __init__(
            self,
            temperature: float = DEFAULT_TEMPERATURE,
            max_tokens: int = DEFAULT_MAX_TOKENS,
            model: str = DEFAULT_MODEL,
            merge_other_agents_as_one_user: bool = True,
            request_timeout_seconds: float = 600.0,
            connect_timeout_seconds: float = 30.0,
            reasoning_effort: str = None,
            retry_max_tokens: int = None,
            **kwargs,
    ):
        """
        Instantiate the OpenAIChat backend.

        args:
            temperature: the temperature of the sampling
            max_tokens: the maximum number of tokens to sample
            model: the model to use
            merge_other_agents_as_one_user: whether to merge messages from other agents as one user message
        """
        super().__init__(
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
            merge_other_agents_as_one_user=merge_other_agents_as_one_user,
            request_timeout_seconds=request_timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            reasoning_effort=reasoning_effort,
            retry_max_tokens=retry_max_tokens,
            **kwargs,
        )
        self.client_type = kwargs.get("openai_client_type", None)
        self.client = get_openai_client(self.client_type)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model
        self.merge_other_agent_as_user = merge_other_agents_as_one_user
        self.min_interval_seconds = float(os.environ.get("AGENTREVIEW_API_MIN_INTERVAL_SECONDS", "2.0"))
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self.connect_timeout_seconds = max(1.0, float(connect_timeout_seconds))
        self.request_timeout = httpx.Timeout(
            timeout=self.request_timeout_seconds,
            connect=self.connect_timeout_seconds,
            write=self.connect_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )
        self.base_url = str(getattr(self.client, "base_url", "unknown")).rstrip("/")
        self.last_response_info = {}
        self.reasoning_effort = reasoning_effort
        self.reasoning_effort_supported = None
        self.retry_max_tokens = max(
            self.max_tokens,
            int(retry_max_tokens or self.max_tokens * 2),
        )
        self.empty_response_attempts = max(
            1,
            int(os.environ.get("AGENTREVIEW_EMPTY_RESPONSE_ATTEMPTS", "3")),
        )
        self.empty_response_retry_seconds = max(
            0.0,
            float(os.environ.get("AGENTREVIEW_EMPTY_RESPONSE_RETRY_SECONDS", "3")),
        )

    def _is_reasoning_model(self):
        return self.model.lower().startswith(("gpt-5", "o1", "o3", "o4"))

    def _completion_kwargs(
        self,
        messages,
        *,
        max_tokens=None,
        reasoning_effort=None,
    ):
        output_tokens = max(1, int(max_tokens or self.max_tokens))
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "timeout": getattr(
                self, "request_timeout", self.request_timeout_seconds
            ),
        }
        if self._is_reasoning_model():
            kwargs["max_completion_tokens"] = output_tokens
            active_effort = (
                reasoning_effort
                if reasoning_effort is not None
                else getattr(self, "reasoning_effort", None)
            )
            if (
                active_effort
                and getattr(self, "reasoning_effort_supported", None) is not False
            ):
                kwargs["reasoning_effort"] = active_effort
        else:
            kwargs["max_tokens"] = output_tokens
            kwargs["stop"] = STOP
        return kwargs

    @staticmethod
    def _reasoning_effort_is_unsupported(error):
        message = str(error).lower()
        body = getattr(error, "body", None)
        body_param = (
            str(body.get("param", "")).lower()
            if isinstance(body, dict)
            else ""
        )
        mentions_parameter = (
            "reasoning_effort" in message
            or "reasoning effort" in message
            or body_param == "reasoning_effort"
        )
        return mentions_parameter and any(
            marker in message
            for marker in (
                "unsupported",
                "not supported",
                "unknown",
                "unrecognized",
                "not permitted",
                "invalid parameter",
                "invalid value",
                "extra inputs",
                "unexpected keyword",
            )
        )

    def _create_completion(self, kwargs, agent_name):
        try:
            completion = self.client.chat.completions.create(**kwargs)
            if "reasoning_effort" in kwargs:
                self.reasoning_effort_supported = True
            return completion
        except (BadRequestError, TypeError) as exc:
            if (
                "reasoning_effort" in kwargs
                and self._reasoning_effort_is_unsupported(exc)
            ):
                unsupported_effort = kwargs.pop("reasoning_effort")
                self.reasoning_effort_supported = False
                print(
                    f"[API CONFIG] {agent_name or 'request'}: provider rejected "
                    f"reasoning_effort={unsupported_effort}; retrying without it",
                    flush=True,
                )
                return self.client.chat.completions.create(**kwargs)
            raise

    @retry(
        retry=retry_if_exception_type(
            (
                RateLimitError,
                APIConnectionError,
                APITimeoutError,
                InternalServerError,
                httpx.TimeoutException,
            )
        ),
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(min=2, max=20),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _get_response(
        self,
        messages,
        agent_name=None,
        max_tokens=None,
        reasoning_effort=None,
    ):
        # Refer to https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints for how to
        # make API calls
        _rate_limit_chat_request(self.min_interval_seconds)

        if self.client_type not in {"openai", "azure_openai"}:
            raise NotImplementedError
        requested_tokens = max(1, int(max_tokens or self.max_tokens))
        completion = self._create_completion(
            self._completion_kwargs(
                messages,
                max_tokens=requested_tokens,
                reasoning_effort=reasoning_effort,
            ),
            agent_name,
        )

        choice = completion.choices[0]
        message = choice.message
        response = (message.content or "").strip()
        response = re.sub(
            r"^\s*<think>.*?</think>\s*",
            "",
            response,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        refusal = getattr(message, "refusal", None)
        reasoning_content = getattr(message, "reasoning_content", None)
        self.last_response_info = {
            "response_chars": len(response),
            "finish_reason": getattr(choice, "finish_reason", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "reasoning_tokens": getattr(details, "reasoning_tokens", None),
            "request_id": getattr(completion, "_request_id", None),
            "response_id": getattr(completion, "id", None),
            "refusal": refusal,
            "reasoning_content_chars": (
                len(reasoning_content)
                if isinstance(reasoning_content, str)
                else 0
            ),
            "requested_tokens": requested_tokens,
            "reasoning_effort": (
                reasoning_effort or getattr(self, "reasoning_effort", None)
            )
            if getattr(self, "reasoning_effort_supported", None) is not False
            else None,
        }
        if not response:
            completion_tokens = getattr(usage, "completion_tokens", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
            error = EmptyModelResponseError(
                "Provider returned an empty assistant message "
                f"(finish_reason={getattr(choice, 'finish_reason', None)}, "
                f"completion_tokens={completion_tokens}, "
                f"reasoning_tokens={reasoning_tokens}, "
                f"requested_tokens={requested_tokens}, "
                f"refusal={refusal!r}, "
                f"reasoning_content_chars="
                f"{self.last_response_info['reasoning_content_chars']}). "
                "The provider returned an invalid success response; retrying "
                "may recover from gateway routing or token exhaustion.",
                finish_reason=getattr(choice, "finish_reason", None),
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                requested_tokens=requested_tokens,
            )
            raise error
        return response

    @staticmethod
    def _lower_reasoning_effort(effort):
        if effort in {"high", "medium"}:
            return "low"
        if effort == "low":
            return "minimal"
        return effort

    @staticmethod
    def _failure_category(exc):
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
            return "TIMEOUT"
        if isinstance(exc, (APIStatusError, APIConnectionError)):
            return "HTTP/API ERROR"
        return "CLIENT ERROR"

    def _get_response_for_agent(self, agent_name: str, messages):
        started = time.monotonic()
        prompt_chars = sum(
            len(str(message.get("content", "")))
            for message in messages
            if isinstance(message, dict)
        )
        print(
            f"[API START] {agent_name}: model={self.model}, "
            f"endpoint={self.base_url}, messages={len(messages)}, "
            f"prompt_chars={prompt_chars}, output_limit={self.max_tokens}, "
            f"token_parameter={'max_completion_tokens' if self._is_reasoning_model() else 'max_tokens'}, "
            f"reasoning_effort={self.reasoning_effort or 'provider_default'}, "
            f"timeouts=connect:{self.connect_timeout_seconds:g}s/"
            f"read:{self.request_timeout_seconds:g}s, "
            f"empty_response_attempts={self.empty_response_attempts}",
            flush=True,
        )
        response = None
        current_max_tokens = self.max_tokens
        current_reasoning_effort = self.reasoning_effort
        for attempt in range(1, self.empty_response_attempts + 1):
            try:
                response = self._get_response(
                    messages,
                    agent_name,
                    max_tokens=current_max_tokens,
                    reasoning_effort=current_reasoning_effort,
                )
                break
            except EmptyModelResponseError as exc:
                labels = ["EMPTY RESPONSE"]
                if exc.reasoning_tokens_exhausted:
                    labels.append("REASONING TOKEN EXHAUSTED")
                if exc.max_completion_tokens_exhausted:
                    labels.append("MAX_COMPLETION_TOKENS EXHAUSTED")
                label_text = "][".join(labels)
                if attempt >= self.empty_response_attempts:
                    print(
                        f"[API FAILED][{label_text}] {agent_name}: "
                        f"empty response persisted "
                        f"after {attempt} attempts and "
                        f"{time.monotonic() - started:.1f}s - {exc}",
                        flush=True,
                    )
                    raise
                previous_tokens = current_max_tokens
                previous_effort = current_reasoning_effort
                if exc.max_completion_tokens_exhausted:
                    current_max_tokens = min(
                        self.retry_max_tokens,
                        max(current_max_tokens + 1024, current_max_tokens * 2),
                    )
                    current_reasoning_effort = self._lower_reasoning_effort(
                        current_reasoning_effort
                    )
                wait_seconds = self.empty_response_retry_seconds + random.uniform(
                    0.0, self.empty_response_retry_seconds
                )
                print(
                    f"[API RETRY][{label_text}] {agent_name}: "
                    f"HTTP success contained no usable "
                    f"text; retry {attempt + 1}/{self.empty_response_attempts} "
                    f"in {wait_seconds:.1f}s, "
                    f"max_completion_tokens={previous_tokens}"
                    f"->{current_max_tokens}, "
                    f"reasoning_effort={previous_effort or 'provider_default'}"
                    f"->{current_reasoning_effort or 'provider_default'}",
                    flush=True,
                )
                time.sleep(wait_seconds)
            except Exception as exc:
                category = self._failure_category(exc)
                print(
                    f"[API FAILED][{category}] {agent_name}: failed after "
                    f"{time.monotonic() - started:.1f}s - "
                    f"{_exception_chain_summary(exc)}",
                    flush=True,
                )
                raise
        info = self.last_response_info
        print(
            f"[API DONE] {agent_name}: elapsed={time.monotonic() - started:.1f}s, "
            f"response_chars={info.get('response_chars')}, "
            f"finish_reason={info.get('finish_reason')}, "
            f"completion_tokens={info.get('completion_tokens')}, "
            f"reasoning_tokens={info.get('reasoning_tokens')}, "
            f"requested_tokens={info.get('requested_tokens')}, "
            f"reasoning_effort={info.get('reasoning_effort') or 'provider_default'}, "
            f"request_id={info.get('request_id') or 'n/a'}, "
            f"response_id={info.get('response_id') or 'n/a'}",
            flush=True,
        )
        return response

    def query(
            self,
            agent_name: str,
            role_desc: str,
            history_messages: List[Message],
            global_prompt: str = None,
            request_msg: Message = None,
            *args,
            **kwargs,
    ) -> str:
        """
        Format the input and call the ChatGPT/GPT-4 API.

        args:
            agent_name: the name of the agent
            role_desc: the description of the role of the agent
            env_desc: the description of the environment
            history_messages: the history of the conversation, or the observation for the agent
            request_msg: the request from the system to guide the agent's next response
        """

        # Merge the role description and the global prompt as the system prompt for the agent
        if global_prompt:  # Prepend the global prompt if it exists
            system_prompt = f"You are a helpful assistant.\n{global_prompt.strip()}\n{BASE_PROMPT}\n\nYour name is {agent_name}.\n\nYour role:{role_desc}"
        else:
            system_prompt = f"You are a helpful assistant. Your name is {agent_name}.\n\nYour role:{role_desc}\n\n{BASE_PROMPT}"

        all_messages = [(SYSTEM_NAME, system_prompt)]
        for msg in history_messages:
            if msg.agent_name == SYSTEM_NAME:
                all_messages.append((SYSTEM_NAME, msg.content))
            else:  # non-system messages are suffixed with the end of message token
                all_messages.append((msg.agent_name, f"{msg.content}{END_OF_MESSAGE}"))

        if request_msg:
            all_messages.append((SYSTEM_NAME, request_msg.content))
        else:  # The default request message that reminds the agent its role and instruct it to speak
            all_messages.append(
                (SYSTEM_NAME, f"Now you speak, {agent_name}.{END_OF_MESSAGE}")
            )

        messages = []
        for i, msg in enumerate(all_messages):
            if i == 0:
                assert (
                        msg[0] == SYSTEM_NAME
                )  # The first message should be from the system
                messages.append({"role": "system", "content": msg[1]})
            else:
                if msg[0] == agent_name:
                    messages.append({"role": "assistant", "content": msg[1]})
                else:
                    if messages[-1]["role"] == "user":  # last message is from user
                        if self.merge_other_agent_as_user:
                            messages[-1][
                                "content"
                            ] = f"{messages[-1]['content']}\n\n[{msg[0]}]: {msg[1]}"
                        else:
                            messages.append(
                                {"role": "user", "content": f"[{msg[0]}]: {msg[1]}"}
                            )
                    elif (
                            messages[-1]["role"] == "assistant"
                    ):  # consecutive assistant messages
                        # Merge the assistant messages
                        messages[-1]["content"] = f"{messages[-1]['content']}\n{msg[1]}"
                    elif messages[-1]["role"] == "system":
                        messages.append(
                            {"role": "user", "content": f"[{msg[0]}]: {msg[1]}"}
                        )
                    else:
                        raise ValueError(f"Invalid role: {messages[-1]['role']}")

        response = self._get_response_for_agent(agent_name, messages)

        # Remove the agent name if the response starts with it
        response = re.sub(rf"^\s*\[.*]:", "", response).strip()  # noqa: F541
        response = re.sub(
            rf"^\s*{re.escape(agent_name)}\s*:", "", response
        ).strip()  # noqa: F451

        # Remove the tailing end of message token
        response = re.sub(rf"{END_OF_MESSAGE}$", "", response).strip()

        return response
