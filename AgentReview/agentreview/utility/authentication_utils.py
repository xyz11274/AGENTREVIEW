#AgentReview/agentreview/utility/authentication_utils.py

import ipaddress
import logging
import os
from typing import Mapping, Optional
from urllib.parse import urlparse

import httpx
import openai

logging.getLogger("httpx").setLevel(logging.WARNING)


def _is_loopback_url(value: str) -> bool:
    hostname = (urlparse(str(value)).hostname or "").strip().lower()
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def resolve_openai_base_url(
    explicit_value: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve an explicit OpenAI-compatible endpoint without a cloud default."""
    values = os.environ if environ is None else environ
    base_url = str(
        explicit_value
        or values.get("OPENAI_BASE_URL")
        or values.get("OPENAI_API_BASE")
        or ""
    ).strip().rstrip("/")
    if not base_url:
        raise ValueError(
            "OpenAI-compatible endpoint is required: pass --openai-base-url or "
            "set OPENAI_BASE_URL/OPENAI_API_BASE"
        )
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(
            f"OpenAI-compatible base URL must be an HTTP(S) URL, got {base_url!r}"
        )
    return base_url


def get_openai_client(
    client_type: str,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[httpx.Timeout] = None,
):
    """

    Refer to [this page](https://platform.openai.com/docs/models) for authentication using OpenAI.
    Refer to [this page](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints) for
    authentication using Azure OpenAI.
    """

    assert client_type in ["azure_openai", "openai"]

    if not os.environ.get('OPENAI_API_VERSION'):
        os.environ['OPENAI_API_VERSION'] = "2023-05-15"

    if client_type == "openai":
        resolved_base_url = resolve_openai_base_url(base_url)
        resolved_api_key = str(api_key or os.environ.get("OPENAI_API_KEY") or "")
        if not resolved_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required; local vLLM endpoints may use 'dummy'"
            )
        client_options = {}
        if _is_loopback_url(resolved_base_url):
            transport_options = {"trust_env": False}
            if timeout is not None:
                transport_options["timeout"] = timeout
            client_options["http_client"] = httpx.Client(**transport_options)
        client = openai.OpenAI(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            timeout=timeout,
            max_retries=0,
            **client_options,
        )

    elif client_type == "azure_openai":
        endpoint: str = os.environ['AZURE_ENDPOINT']

        if not endpoint.startswith("https://"):
            endpoint = f"https://{endpoint}.openai.azure.com"

        os.environ['AZURE_ENDPOINT'] = endpoint
        
        client = openai.AzureOpenAI(
            api_key=os.environ['AZURE_OPENAI_KEY'],
            azure_endpoint=os.environ['AZURE_ENDPOINT'],  # f"https://YOUR_END_POINT.openai.azure.com"
            azure_deployment=os.environ['AZURE_DEPLOYMENT'],
            max_retries=0,
        )

    else:
        raise NotImplementedError

    return client
