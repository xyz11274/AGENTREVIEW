"""Supervise an explicitly launched pilot/study within a durable wall budget."""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'AgentReview'))
from agentreview.research_v2.io import atomic_write_json, read_json
from agentreview.research_v2.runtime_limits import initialize_run_budget
from runtime_0930_progress import process_start_ticks


def supervise(command, *, root, budget_path, grace_seconds=10):
    """Graceful stop starts before the deadline; hard stop uses the same deadline."""
    root, budget_path = Path(root).resolve(), Path(budget_path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    deadline = read_json(budget_path)['deadline_at']
    if time.time() >= deadline:
        raise ValueError('Existing run budget is exhausted; retain ledger and request a new explicit authorization')
    requested, previous = [], {}
    def request_stop(signum, frame):
        requested.append(signum)
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
    state = {'status': 'running', 'pid': os.getpid(), 'process_start_ticks': process_start_ticks(os.getpid()),
             'started_at': time.time(), 'deadline_at': deadline, 'hard_stop': False,
             'call_budget': str(budget_path)}
    state_path = root / 'supervisor-state.json'
    child, stop_at, reason = None, None, None
    def save():
        state['updated_at'] = time.time()
        atomic_write_json(state_path, state, immutable=False)
    def terminate(sig):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass
    try:
        with (root / 'supervisor.lock').open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.Popen(command, start_new_session=True)
            state.update(child_pid=child.pid, child_start_ticks=process_start_ticks(child.pid))
            save()
            while child.poll() is None:
                now = time.time()
                if stop_at is None and (requested or deadline - now <= grace_seconds):
                    reason = 'operator_stop' if requested else 'budget_exhausted'
                    stop_at = min(deadline, now + grace_seconds)
                    state.update(status='stopping', reason=reason, stop_requested_at=now)
                    terminate(signal.SIGINT)
                    save()
                if stop_at is not None and now >= stop_at:
                    state['hard_stop'] = True
                    terminate(signal.SIGKILL)
                    child.wait(timeout=2)
                    break
                time.sleep(min(.05, max(.001, (stop_at or deadline) - now)))
            code = child.wait()
            state.update(status=reason or ('complete' if code == 0 else 'paused'), child_returncode=code,
                         finished_at=time.time())
            save()
            return state
    finally:
        if child is not None and child.poll() is None:
            terminate(signal.SIGKILL)
            child.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pilot', 'run'))
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--study', type=Path)
    args = parser.parse_args()
    from agentreview.research_v2.shared_experiment import bound
    if args.action == 'pilot':
        if args.profile is None:
            parser.error('pilot requires --profile')
        from pilot_0930 import load_run
        run = load_run(args.profile)
        run.validate()
        document = read_json(run.protocol_path)
        bound(document['implementation']['runtime_launcher'])
        root, spec = run.root, document.get('runtime_budget')
        bound(document['implementation']['completion_launcher'])
        command = [sys.executable, '-u', '-B', str(REPO / 'deployment/run_smallflow_0930.py'),
                   '--profile', str(args.profile.resolve())]
    else:
        if args.study is None:
            parser.error('run requires --study')
        from three_search_study import validate
        document, protocols = validate(args.study.resolve())
        root, spec = Path(document['output_root']), document.get('runtime_budget')
        command = [sys.executable, '-u', '-B', str(REPO / 'deployment/three_search_study.py'),
                   'run', '--study', str(args.study.resolve())]
    budget_path = initialize_run_budget(spec, root)
    os.environ['AGENTREVIEW_CALL_BUDGET'] = str(budget_path)
    os.environ['AGENTREVIEW_REQUEST_WALL_SECONDS'] = str(spec['request_wall_seconds'])
    state = supervise(command, root=root, budget_path=budget_path)
    print({'status': state['status'], 'root': str(root), 'hard_stop': state['hard_stop']}, flush=True)
    return 0 if state['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
