import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


def run_preflight(base_url: str, api_key: str, timeout: float = 15.0):
    normalized = base_url.rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid OPENAI_API_BASE: {base_url!r}")

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
        model_count = len(payload.get("data", [])) if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        model_count = None
    return {
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "base_url": normalized,
        "models_url": models_url,
        "resolved_addresses": addresses,
        "http_status": status,
        "model_count_in_response": model_count,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Verify DNS, TLS, authentication, and routing to an OpenAI-compatible API."
    )
    parser.add_argument(
        "--base-url", default=os.environ.get("OPENAI_API_BASE")
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not args.base_url:
        raise SystemExit("OPENAI_API_BASE is not set")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set")
    result = run_preflight(args.base_url, api_key, args.timeout)
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


if __name__ == "__main__":
    main()
