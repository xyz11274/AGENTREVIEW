"""Assemble Ark SSE deltas into the ordinary completion consumed by the host."""
import httpx
import asyncio
from contextvars import copy_context
from queue import Queue, Empty
from threading import Event, Thread
from openai import APIConnectionError, APITimeoutError
from openai.types.chat import ChatCompletion

from .execution_telemetry import current_request
from .runtime_limits import request_deadline, remaining_seconds, RequestWallTimeout


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


def _stream_chunks(client, payload, deadline):
    if deadline is None:
        with client.chat.completions.create(**payload) as stream:
            yield from stream
        return
    # The SDK's read timeout resets on every received block. A separate deadline
    # also bounds a stalled first block, connect, and an endlessly active stream.
    # Only the consumer assembles/traces deltas; late worker output is discarded.
    queue, cancelled, active = Queue(), Event(), []
    def receive():
        try:
            with client.chat.completions.create(**payload) as stream:
                active.append(stream)
                if cancelled.is_set():
                    return
                for chunk in stream:
                    if cancelled.is_set():
                        return
                    queue.put(('chunk', chunk))
            queue.put(('done', None))
        except BaseException as exc:
            queue.put(('error', exc))
    context = copy_context()
    thread = Thread(target=lambda: context.run(receive), daemon=True,
                    name='bounded-completion-stream')
    thread.start()
    try:
        while True:
            try:
                kind, value = queue.get(timeout=remaining_seconds(deadline))
            except Empty as exc:
                raise RequestWallTimeout('Request wall-clock timeout exhausted in connect/stream') from exc
            remaining_seconds(deadline)
            if kind == 'done':
                return
            if kind == 'error':
                raise value
            yield value
    finally:
        cancelled.set()
        if thread.is_alive() and active:
            # Closing a response can itself wait on the transport read lock.
            # Cleanup must not extend the caller's absolute deadline.
            def close():
                try:
                    active[0].close()
                except Exception:
                    pass
            Thread(target=close, daemon=True, name='completion-stream-cleanup').start()


def stream_completion(client, payload):
    result = CompletionStream()
    trace = current_request()
    if trace is not None:
        trace.update(status='waiting_first_chunk')
    try:
        with request_deadline() as deadline:
            if trace is not None:
                trace.update(request_wall_remaining_seconds=remaining_seconds(deadline))
            for chunk in _stream_chunks(client, payload, deadline):
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
        with request_deadline() as deadline:
            if trace is not None:
                trace.update(request_wall_remaining_seconds=remaining_seconds(deadline))
            try:
                async with asyncio.timeout(remaining_seconds(deadline)):
                    stream = await client.chat.completions.create(**payload)
                    async with stream:
                        async for chunk in stream:
                            result.add(chunk)
            except TimeoutError as exc:
                if isinstance(exc, RequestWallTimeout):
                    raise
                raise RequestWallTimeout('Request wall-clock timeout exhausted in connect/stream') from exc
    except httpx.TimeoutException as exc:
        raise APITimeoutError(request=exc.request) from exc
    except httpx.TransportError as exc:
        raise APIConnectionError(request=exc.request) from exc
    return result.finish()
