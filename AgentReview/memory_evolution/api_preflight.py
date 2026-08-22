import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

import httpx

from agentreview.utility.authentication_utils import (
    get_openai_client,
    resolve_openai_base_url,
)


def run_preflight(
    base_url: str,
    api_key: str,
    timeout: float = 15.0,
    *,
    model_name: str = "",
    max_tokens: int = 128,
    reasoning_effort: str = "",
    reasoning_effort_supported: bool = False,
):
    normalized = resolve_openai_base_url(base_url)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid OpenAI-compatible base URL: {base_url!r}")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname, port, type=socket.SOCK_STREAM
            )
        }
    )
    models_url = normalized + "/models"
    request = urllib.request.Request(
        models_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "AgentReview-API-Preflight/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=max(1.0, float(timeout)),
            context=ssl.create_default_context(),
        ) as response:
            body = response.read(4096).decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        status = int(exc.code)
        raise RuntimeError(
            f"API preflight reached {models_url} but returned HTTP {status}: "
            f"{body[:500]}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"API preflight could not reach {models_url}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not 200 <= status < 300:
        raise RuntimeError(f"API preflight returned unexpected HTTP {status}")
    try:
        payload = json.loads(body)
        model_values = payload.get("data", []) if isinstance(payload, dict) else []
        model_ids = sorted(
            str(value.get("id") or "")
            for value in model_values
            if isinstance(value, dict) and value.get("id")
        )
        model_count = len(model_values)
    except json.JSONDecodeError:
        model_ids = []
        model_count = None
    chat_checked = False
    chat_response_chars = None
    if model_name:
        if model_name not in model_ids:
            raise RuntimeError(
                f"Served model {model_name!r} is absent from {models_url}; "
                f"available={model_ids}"
            )
        request_timeout = max(1.0, float(timeout))
        client = get_openai_client(
            "openai",
            base_url=normalized,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout),
        )
        kwargs = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply with exactly OK and no explanation.",
                }
            ],
            "temperature": 0.0,
            "max_tokens": max(1, int(max_tokens)),
            "timeout": request_timeout,
        }
        if reasoning_effort and reasoning_effort_supported:
            kwargs["reasoning_effort"] = reasoning_effort
        try:
            completion = client.chat.completions.create(**kwargs)
            content = (completion.choices[0].message.content or "").strip()
        except Exception as exc:
            raise RuntimeError(
                "Chat completion preflight failed with the configured request "
                f"shape: {type(exc).__name__}: {exc}"
            ) from exc
        if not content:
            raise RuntimeError(
                "Chat completion preflight returned no message.content; "
                "reasoning fields are not accepted as business output"
            )
        chat_checked = True
        chat_response_chars = len(content)
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "base_url": normalized,
        "models_url": models_url,
        "resolved_addresses": addresses,
        "http_status": status,
        "model_count_in_response": model_count,
        "model_ids": model_ids,
        "served_model_name": str(model_name or ""),
        "chat_checked": chat_checked,
        "chat_response_chars": chat_response_chars,
        "reasoning_effort_supported": bool(reasoning_effort_supported),
        "reasoning_effort": str(reasoning_effort or ""),
        "max_tokens": int(max_tokens),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Verify DNS, TLS, authentication, and routing to an OpenAI-compatible API."
    )
    parser.add_argument(
        "--base-url",
        "--openai-base-url",
        dest="base_url",
        default=None,
    )
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high"],
        default="none",
    )
    parser.add_argument(
        "--reasoning-effort-supported",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    try:
        base_url = resolve_openai_base_url(args.base_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    result = run_preflight(
        base_url,
        api_key,
        args.timeout,
        model_name=args.model_name,
        max_tokens=args.max_tokens,
        reasoning_effort=(
            "" if args.reasoning_effort == "none" else args.reasoning_effort
        ),
        reasoning_effort_supported=args.reasoning_effort_supported,
    )
    model_count = result["model_count_in_response"]
    model_text = str(model_count) if model_count is not None else "unknown"
    print(
        f"[API PREFLIGHT] endpoint={result['base_url']}, "
        f"models_url={result['models_url']}",
        flush=True,
    )
    print(
        f"[API PREFLIGHT] HTTP={result['http_status']}, models={model_text}, "
        f"resolved_addresses={len(result['resolved_addresses'])}, "
        f"python={result['python_version']}",
        flush=True,
    )
    print(
        f"[API PREFLIGHT] model={result['served_model_name']}, "
        f"chat_response_chars={result['chat_response_chars']}, "
        "reasoning_effort_supported="
        f"{str(result['reasoning_effort_supported']).lower()}, "
        f"reasoning_effort={result['reasoning_effort'] or 'none'}, "
        f"max_tokens={result['max_tokens']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
