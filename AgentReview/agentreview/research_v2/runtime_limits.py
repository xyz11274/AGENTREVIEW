"""Explicit run budgets and opt-in request wall deadlines, shared by workers."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import math
import os
from pathlib import Path
import time

from .io import atomic_write_json, read_json

_DEADLINE = ContextVar('request_wall_deadline', default=None)


class RequestWallTimeout(TimeoutError):
    """A request ran out of its queue/connect/read wall-clock allowance."""


def current_deadline():
    return _DEADLINE.get()


def validate_run_budget(spec):
    if not isinstance(spec, dict):
        raise ValueError('An explicit runtime budget is required before launch')
    limits = spec.get('http_attempts')
    if (not isinstance(limits, dict) or set(limits) != {'cloud', 'local'}
            or any(type(n) is not int or n <= 0 for n in limits.values())):
        raise ValueError('Runtime budget requires positive cloud/local HTTP attempt limits')
    for name in ('wall_seconds', 'request_wall_seconds'):
        n = spec.get(name)
        if type(n) not in (int, float) or not math.isfinite(n) or n <= 0:
            raise ValueError(f'Runtime budget requires positive finite {name}')
    return spec


def initialize_run_budget(spec, root):
    """Only actual launch starts the clock; resumes retain usage and deadline."""
    validate_run_budget(spec)
    path = Path(root).resolve() / 'call-budget.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            old = read_json(path)
            if (old.get('limits') != spec['http_attempts']
                    or old.get('wall_seconds') != spec['wall_seconds']
                    or old.get('request_wall_seconds') != spec['request_wall_seconds']):
                raise ValueError('Existing budget must retain its frozen limits')
            if (set(old.get('used', {})) != {'cloud', 'local'}
                    or any(type(n) is not int or n < 0 for n in old['used'].values())
                    or type(old.get('deadline_at')) not in (int, float)
                    or not math.isfinite(old['deadline_at'])):
                raise ValueError('Existing budget ledger is invalid')
        else:
            now = time.time()
            atomic_write_json(path, {'protocol': 'runtime-call-budget-v2',
                'limits': spec['http_attempts'], 'used': {'cloud': 0, 'local': 0},
                'started_at': now, 'deadline_at': now + spec['wall_seconds'],
                'wall_seconds': spec['wall_seconds'],
                'request_wall_seconds': spec['request_wall_seconds']})
    return path


@contextmanager
def request_deadline():
    """One deadline covers admission and transport, including SDK retries."""
    inherited = _DEADLINE.get()
    if inherited is not None:
        yield inherited
        return
    value = os.environ.get('AGENTREVIEW_REQUEST_WALL_SECONDS')
    seconds = None
    if value is not None:
        try:
            seconds = float(value)
        except ValueError as exc:
            raise ValueError('Request wall-clock budget must be positive and finite') from exc
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError('Request wall-clock budget must be positive and finite')
    path = os.environ.get('AGENTREVIEW_CALL_BUDGET')
    if path:
        remaining = read_json(path)['deadline_at'] - time.time()
        seconds = remaining if seconds is None else min(seconds, remaining)
    deadline = None if seconds is None else time.monotonic() + seconds
    token = _DEADLINE.set(deadline)
    try:
        remaining_seconds(deadline)
        yield deadline
    finally:
        _DEADLINE.reset(token)


def remaining_seconds(deadline):
    if deadline is None:
        return None
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise RequestWallTimeout('Request wall-clock timeout exhausted in admission/connect/stream')
    return seconds
