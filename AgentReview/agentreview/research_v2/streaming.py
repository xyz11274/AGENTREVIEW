"""Assemble Ark SSE deltas into the ordinary completion consumed by the host."""
import httpx
from openai import APIConnectionError, APITimeoutError
from openai.types.chat import ChatCompletion

from .execution_telemetry import current_request


class InterruptedCompletionStream(ConnectionError):
    """The transport ended before a terminal completion choice arrived."""


class CompletionStream:
    def __init__(self):
        self.header = {}
        self.choices = {}
        self.usage = None

    def add(self, chunk):
        value = chunk.model_dump(mode='json')
        trace = current_request()
        if trace is not None:
            trace.chunk(value)
        if not self.header:
            self.header = {key: value[key] for key in ('id', 'created', 'model')}
        if value['model'] != self.header['model']:
            raise ValueError('Completion stream model changed between chunks')
        for part in value['choices']:
            choice = self.choices.setdefault(part['index'], {'index': part['index'],
                'finish_reason': None, 'fragments': {'content': [], 'reasoning_content': [], 'refusal': []}})
            for field, fragments in choice['fragments'].items():
                fragment = part['delta'].get(field)
                if fragment is not None:
                    fragments.append(fragment)
            if part['finish_reason'] is not None:
                choice['finish_reason'] = part['finish_reason']
        if value.get('usage') is not None:
            self.usage = value['usage']

    def finish(self):
        if not self.choices or any(choice['finish_reason'] is None for choice in self.choices.values()):
            raise InterruptedCompletionStream('Stream ended before finish_reason; partial text is saved in the attempt stream')
        choices = []
        for choice in self.choices.values():
            message = {'role': 'assistant', **{key: ''.join(parts) for key, parts in choice['fragments'].items() if parts}}
            choices.append({'index': choice['index'], 'finish_reason': choice['finish_reason'], 'message': message})
        return ChatCompletion.model_validate({**self.header, 'object': 'chat.completion',
                                              'choices': choices, 'usage': self.usage})


def stream_completion(client, payload):
    result = CompletionStream()
    trace = current_request()
    if trace is not None:
        trace.update(status='waiting_first_chunk')
    try:
        with client.chat.completions.create(**payload) as stream:
            for chunk in stream:
                result.add(chunk)
    except httpx.TimeoutException as exc:
        raise APITimeoutError(request=exc.request) from exc
    except httpx.TransportError as exc:
        raise APIConnectionError(request=exc.request) from exc
    return result.finish()


async def async_stream_completion(client, payload):
    result = CompletionStream()
    trace = current_request()
    if trace is not None:
        trace.update(status='waiting_first_chunk')
    try:
        stream = await client.chat.completions.create(**payload)
        async with stream:
            async for chunk in stream:
                result.add(chunk)
    except httpx.TimeoutException as exc:
        raise APITimeoutError(request=exc.request) from exc
    except httpx.TransportError as exc:
        raise APIConnectionError(request=exc.request) from exc
    return result.finish()
