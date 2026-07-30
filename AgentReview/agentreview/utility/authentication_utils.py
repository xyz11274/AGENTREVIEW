#AgentReview/agentreview/utility/authentication_utils.py

import logging
import os

import openai

logging.getLogger("httpx").setLevel(logging.WARNING)


def get_openai_client(client_type: str):
    """

    Refer to [this page](https://platform.openai.com/docs/models) for authentication using OpenAI.
    Refer to [this page](https://learn.microsoft.com/en-us/azure/ai-services/openai/how-to/switching-endpoints) for
    authentication using Azure OpenAI.
    """

    assert client_type in ["azure_openai", "openai"]

    if not os.environ.get('OPENAI_API_VERSION'):
        os.environ['OPENAI_API_VERSION'] = "2023-05-15"

    if client_type == "openai":
        base_url = os.environ['OPENAI_API_BASE'].rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"OPENAI_API_BASE must be an HTTP(S) URL, got {base_url!r}"
            )
        client = openai.OpenAI(
            api_key=os.environ['OPENAI_API_KEY'],
            base_url=base_url,
            max_retries=0,
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
