"""Observe HTTP dispatches, including SDK retries; optionally enforce run budgets."""
import fcntl
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from .execution_telemetry import current_request, emit
from .io import atomic_write_json, read_json
from .runtime_limits import current_deadline, remaining_seconds


class RunCallBudgetExhausted(RuntimeError):
    failure_category = "shared_runtime"


def dispatch(request):
    endpoint = urlparse(str(request.url))
    if not endpoint.path.endswith("/chat/completions"):
        return
    remaining_seconds(current_deadline())
    group = "local" if endpoint.hostname in {"localhost", "127.0.0.1", "::1"} else "cloud"
    path = os.environ.get("AGENTREVIEW_CALL_BUDGET")
    if path:
        path = Path(path)
        with path.with_suffix(".lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            value = read_json(path)
            if time.time() >= value["deadline_at"] or value["used"][group] >= value["limits"][group]:
                raise RunCallBudgetExhausted(f"Run {group} HTTP attempt or wall-clock budget exhausted")
            value["used"][group] += 1
            atomic_write_json(path, value, immutable=False)
    trace = current_request()
    if trace is not None and trace.value.get('status') not in {'complete', 'failed', 'interrupted'}:
        trace.update(http_dispatches=trace.value.get("http_dispatches", 0) + 1,
                     endpoint_host=endpoint.hostname, last_http_dispatch_at=time.time())
    emit(event="http_dispatch", stage="api_wire", group=group, host=endpoint.hostname,
         request_id=trace.tags.get("request_id") if trace else None)


def response_headers(response):
    trace = current_request()
    if trace is not None and trace.value.get('status') not in {'complete', 'failed', 'interrupted'}:
        trace.update(http_status=response.status_code, response_headers_at=time.time(),
                     server_request_id=response.headers.get("x-request-id"))


def observe_client(client, *, asynchronous=False):
    """Keep the SDK transport and retries; append observation hooks only."""
    if asynchronous:
        async def request_hook(request):
            dispatch(request)
        async def response_hook(response):
            response_headers(response)
    else:
        request_hook, response_hook = dispatch, response_headers
    client._client.event_hooks.setdefault("request", []).append(request_hook)
    client._client.event_hooks.setdefault("response", []).append(response_hook)


def process_identity():
    import sys
    import agentreview
    # Record presence and proxy destination; never persist credentials/userinfo.
    proxies = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if os.environ.get(name):
            parsed = urlparse(os.environ[name])
            proxies[name] = {"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port}
    return {"pid": os.getpid(), "executable": sys.executable, "cwd": os.getcwd(),
            "observer_file": __file__, "agentreview_file": agentreview.__file__,
            "sys_path": sys.path, "proxies": proxies,
            "NO_PROXY": os.environ.get("NO_PROXY"), "no_proxy": os.environ.get("no_proxy"),
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "credential_present": bool(os.environ.get("ARK_API_KEY"))}
