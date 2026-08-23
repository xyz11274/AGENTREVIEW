#AgentReview/agentreview/backends/openai.py

import os
import random
import re
import threading
import time
from typing import List

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
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


def _log_retry(error, agent_name, attempt, max_attempts, wait_seconds):
    category = (
        "TIMEOUT"
        if isinstance(error, (APITimeoutError, httpx.TimeoutException))
        else "HTTP/API ERROR"
    )
    print(
        f"[API RETRY][{category}] {agent_name}: "
        f"attempt {attempt + 1}/{max_attempts} "
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
            read_timeout_seconds: float = 600.0,
            write_timeout_seconds: float = 60.0,
            pool_timeout_seconds: float = 30.0,
            api_max_retries: int = 2,
            reasoning_effort: str = None,
            reasoning_effort_supported: bool = False,
            retry_max_tokens: int = None,
            openai_base_url: str = None,
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
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            api_max_retries=api_max_retries,
            reasoning_effort=reasoning_effort,
            reasoning_effort_supported=reasoning_effort_supported,
            retry_max_tokens=retry_max_tokens,
            openai_base_url=openai_base_url,
            **kwargs,
        )
        self.client_type = kwargs.get("openai_client_type", None)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.model = model
        self.merge_other_agent_as_user = merge_other_agents_as_one_user
        self.min_interval_seconds = float(os.environ.get("AGENTREVIEW_API_MIN_INTERVAL_SECONDS", "2.0"))
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self.connect_timeout_seconds = max(1.0, float(connect_timeout_seconds))
        self.read_timeout_seconds = max(1.0, float(read_timeout_seconds))
        self.write_timeout_seconds = max(1.0, float(write_timeout_seconds))
        self.pool_timeout_seconds = max(1.0, float(pool_timeout_seconds))
        self.api_max_retries = max(0, int(api_max_retries))
        self.request_timeout = httpx.Timeout(
            timeout=self.request_timeout_seconds,
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.write_timeout_seconds,
            pool=self.pool_timeout_seconds,
        )
        self.client = get_openai_client(
            self.client_type,
            base_url=openai_base_url,
            timeout=self.request_timeout,
        )
        self.base_url = str(getattr(self.client, "base_url", "unknown")).rstrip("/")
        self.last_response_info = {}
        self.reasoning_effort = reasoning_effort
        self.reasoning_effort_supported = bool(reasoning_effort_supported)
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
        chat_template_kwargs=None,
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
        else:
            kwargs["max_tokens"] = output_tokens
            kwargs["stop"] = STOP
        active_effort = (
            reasoning_effort
            if reasoning_effort is not None
            else getattr(self, "reasoning_effort", None)
        )
        if active_effort and getattr(self, "reasoning_effort_supported", False):
            kwargs["reasoning_effort"] = active_effort
        if chat_template_kwargs:
            kwargs["extra_body"] = {
                "chat_template_kwargs": dict(chat_template_kwargs)
            }
        return kwargs

    def _create_completion(self, kwargs, agent_name):
        return self.client.chat.completions.create(**kwargs)

    def _get_response(
        self,
        messages,
        agent_name=None,
        max_tokens=None,
        reasoning_effort=None,
        chat_template_kwargs=None,
    ):
        # Refer to https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints for how to
        # make API calls
        _rate_limit_chat_request(self.min_interval_seconds)

        if self.client_type not in {"openai", "azure_openai"}:
            raise NotImplementedError
        requested_tokens = max(1, int(max_tokens or self.max_tokens))
        request_kwargs = self._completion_kwargs(
            messages,
            max_tokens=requested_tokens,
            reasoning_effort=reasoning_effort,
            chat_template_kwargs=chat_template_kwargs,
        )
        retryable = (
            RateLimitError,
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            httpx.TimeoutException,
        )
        max_attempts = getattr(self, "api_max_retries", 2) + 1
        for attempt in range(1, max_attempts + 1):
            try:
                completion = self._create_completion(request_kwargs, agent_name)
                break
            except retryable as exc:
                if attempt >= max_attempts:
                    raise
                wait_seconds = min(20.0, max(2.0, 2.0 ** attempt)) * random.uniform(
                    0.75, 1.25
                )
                _log_retry(
                    exc,
                    agent_name or "request",
                    attempt,
                    max_attempts,
                    wait_seconds,
                )
                time.sleep(wait_seconds)

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
        reasoning = getattr(message, "reasoning", None)
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
            "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else 0,
            "requested_tokens": requested_tokens,
            "chat_template_kwargs": dict(chat_template_kwargs or {}),
            "reasoning_effort": (
                reasoning_effort or getattr(self, "reasoning_effort", None)
            )
            if getattr(self, "reasoning_effort_supported", False)
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
                f"{self.last_response_info['reasoning_content_chars']}, "
                f"reasoning_chars={self.last_response_info['reasoning_chars']}, "
                f"raw_content_chars={len(message.content or '')}). "
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
    def _failure_category(exc):
        if isinstance(exc, (APITimeoutError, httpx.TimeoutException)):
            return "TIMEOUT"
        if isinstance(exc, (APIStatusError, APIConnectionError)):
            return "HTTP/API ERROR"
        return "CLIENT ERROR"

    def _get_response_for_agent(
        self,
        agent_name: str,
        messages,
        *,
        chat_template_kwargs=None,
    ):
        started = time.monotonic()
        prompt_chars = sum(
            len(str(message.get("content", "")))
            for message in messages
            if isinstance(message, dict)
        )
        thinking_mode = dict(chat_template_kwargs or {}).get(
            "enable_thinking", "provider_default"
        )
        print(
            f"[API START] {agent_name}: model={self.model}, "
            f"endpoint={self.base_url}, messages={len(messages)}, "
            f"prompt_chars={prompt_chars}, output_limit={self.max_tokens}, "
            f"token_parameter={'max_completion_tokens' if self._is_reasoning_model() else 'max_tokens'}, "
            "reasoning_effort_supported="
            f"{str(getattr(self, 'reasoning_effort_supported', False)).lower()}, "
            f"reasoning_effort={self.reasoning_effort or 'provider_default'}, "
            f"enable_thinking={str(thinking_mode).lower()}, "
            f"timeouts=connect:{self.connect_timeout_seconds:g}s/"
            f"read:{getattr(self, 'read_timeout_seconds', self.request_timeout_seconds):g}s/"
            f"write:{getattr(self, 'write_timeout_seconds', self.connect_timeout_seconds):g}s/"
            f"pool:{getattr(self, 'pool_timeout_seconds', self.connect_timeout_seconds):g}s, "
            f"max_retries={getattr(self, 'api_max_retries', 2)}, "
            f"empty_response_attempts={self.empty_response_attempts}",
            flush=True,
        )
        response = None
        current_max_tokens = self.max_tokens
        current_reasoning_effort = self.reasoning_effort
        for attempt in range(1, self.empty_response_attempts + 1):
            try:
                response_options = {}
                if chat_template_kwargs:
                    response_options["chat_template_kwargs"] = (
                        chat_template_kwargs
                    )
                response = self._get_response(
                    messages,
                    agent_name,
                    max_tokens=current_max_tokens,
                    reasoning_effort=current_reasoning_effort,
                    **response_options,
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
                if exc.max_completion_tokens_exhausted:
                    current_max_tokens = min(
                        self.retry_max_tokens,
                        max(current_max_tokens + 1024, current_max_tokens * 2),
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
                    f"reasoning_effort={current_reasoning_effort or 'provider_default'}",
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
