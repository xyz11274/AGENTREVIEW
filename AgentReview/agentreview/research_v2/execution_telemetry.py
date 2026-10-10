"""Optional stage timing shared by orchestrators and isolated CoCo workers."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import fcntl
import json
import os
from pathlib import Path
import threading
import time

from .io import atomic_write_json

_REQUEST = ContextVar('api_request_trace', default=None)


def current_request():
    return _REQUEST.get()


def emit(**value):
    root = os.environ.get("AGENTREVIEW_TELEMETRY_ROOT")
    if not root:
        return
    path = Path(root) / "worker-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps({"time": time.time(), "pid": os.getpid(),
                     "thread": threading.get_ident(), **value}, ensure_ascii=False) + "\n")


@contextmanager
def span(stage, **tags):
    start = time.monotonic()
    identity = f"{os.getpid()}-{threading.get_ident()}-{time.time_ns()}"
    emit(event="start", stage=stage, span=identity, **tags)
    try:
        yield
    except BaseException as exc:
        emit(event="end", stage=stage, span=identity, seconds=time.monotonic()-start,
             status="interrupted" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "failed",
             error=str(exc), **tags)
        raise
    else:
        emit(event="end", stage=stage, span=identity, seconds=time.monotonic()-start,
             status="complete", **tags)


def timed(stage, *tag_names):
    """Record the whole operation, including cache lookup and exceptional exits."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            tags = {key: str(kwargs[key]) for key in tag_names if key in kwargs}
            with span(stage, **tags):
                return function(*args, **kwargs)
        return run
    return decorate


class RequestTrace:
    """Persist partial stream evidence and metadata independently of completion."""

    def __init__(self, path, **tags):
        self.path = Path(path)
        self.tags = tags
        self.identity = f'{os.getpid()}-{threading.get_ident()}-{time.time_ns()}'
        self.started = time.monotonic()
        self.last_saved = self.started
        self.value = {'status': 'queued', 'started_at': time.time(), 'chunks': 0,
                      'content_chars': 0, 'reasoning_chars': 0, 'first_chunk_seconds': None,
                      'first_content_seconds': None, 'response_id': None, 'finish_reasons': {},
                      'last_chunk_at': None, **tags}

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.with_name(self.path.name.replace('.progress.json', '.stream.jsonl')).open('a')
        self.token = _REQUEST.set(self)
        emit(event='start', stage='api_request', span=self.identity, progress_path=str(self.path), **self.tags)
        self.update()
        return self

    def update(self, **values):
        self.value.update(values, updated_at=time.time(), elapsed_seconds=time.monotonic()-self.started)
        atomic_write_json(self.path, self.value, immutable=False)
        emit(event='progress', stage='api_request', span=self.identity, **self.value)
        self.last_saved = time.monotonic()
        self.stream.flush()

    def chunk(self, value):
        now = time.time()
        self.value['chunks'] += 1
        self.value['response_id'] = value.get('id') or self.value['response_id']
        self.value['last_chunk_at'] = now
        if self.value['first_chunk_seconds'] is None:
            self.value['first_chunk_seconds'] = time.monotonic() - self.started
        for choice in value.get('choices', []):
            delta = choice.get('delta', {})
            if delta.get('content') and self.value['first_content_seconds'] is None:
                self.value['first_content_seconds'] = time.monotonic() - self.started
            self.value['content_chars'] += len(delta.get('content') or '')
            self.value['reasoning_chars'] += len(delta.get('reasoning_content') or '')
            if choice.get('finish_reason') is not None:
                self.value['finish_reasons'][str(choice['index'])] = choice['finish_reason']
        self.stream.write(json.dumps({'received_at': now, 'chunk': value}, ensure_ascii=False) + '\n')
        if value.get('usage'):
            self.value['usage'] = value['usage']
        if self.value['chunks'] == 1 or time.monotonic() - self.last_saved >= 1:
            self.update(status='streaming')

    def __exit__(self, kind, error, traceback):
        try:
            status = 'complete' if kind is None else ('interrupted' if issubclass(kind, (KeyboardInterrupt, SystemExit)) else 'failed')
            details = {} if kind is None else {'error': {'type': kind.__name__, 'message': str(error)}}
            if error is not None:
                cause = error
                chain = []
                while cause is not None and len(chain) < 8:
                    chain.append({'type': type(cause).__name__, 'message': str(cause)})
                    cause = cause.__cause__
                details['error']['causes'] = chain
            self.update(status=status, finished_at=time.time(), **details)
            emit(event='end', stage='api_request', span=self.identity,
                 seconds=self.value['elapsed_seconds'], status=status, **details, **self.tags)
        finally:
            self.stream.close()
            _REQUEST.reset(self.token)


def summarize(root, *, now=None):
    """Read timing events, including live spans and retry events between checkpoints."""
    now = time.time() if now is None else now
    path = Path(root) / 'worker-events.jsonl'
    stages, active, retries = {}, {}, []
    if not path.exists():
        return {'updated_at': now, 'stages': {}, 'active': [], 'retries': []}
    with path.open() as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        lines = handle.readlines()
    for line in lines:
        row = json.loads(line)
        event, key = row['event'], row.get('span')
        if event == 'start':
            active[key] = dict(row, started_at=row['time'])
        elif event == 'progress' and key in active:
            active[key].update(row)
        elif event == 'end':
            active.pop(key, None)
            stats = stages.setdefault(row['stage'], {'complete': 0, 'failed': 0, 'interrupted': 0,
                                                     'seconds': 0.0, 'max_seconds': 0.0})
            status = row.get('status', 'complete')
            stats[status if status in ('complete', 'interrupted') else 'failed'] += 1
            stats['seconds'] += row['seconds']
            stats['max_seconds'] = max(stats['max_seconds'], row['seconds'])
        elif event == 'retry':
            retries.append(row)
    for row in active.values():
        row['elapsed_seconds'] = now - row['started_at']
        row['idle_seconds'] = now - (row.get('last_chunk_at') or row['started_at'])
    return {'updated_at': now, 'stages': stages, 'active': list(active.values()), 'retries': retries}
