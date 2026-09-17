import concurrent.futures
import os
import threading
import time
import subprocess
import sys
import json
from pathlib import Path
from unittest.mock import patch

from agentreview.research_v2 import development_coco as coco


def test_unchanged_model_tree_hashes_once_across_concurrent_validations(tmp_path):
    (tmp_path / "weights").write_bytes(b"original")
    with patch.object(coco, "_binding", wraps=coco._binding) as digest:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(coco._model_identity, [tmp_path] * 8))
        assert all(item == results[0] for item in results)
        assert digest.call_count == 1


def test_hash_cache_detects_restored_mtime_equal_length_write_and_added_file(tmp_path):
    weights = tmp_path / "weights"
    weights.write_bytes(b"original")
    original_stat = weights.stat()
    first = coco._model_identity(tmp_path)
    weights.write_bytes(b"modified")
    os.utime(weights, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = coco._model_identity(tmp_path)
    assert first != second
    (tmp_path / "tokenizer").write_bytes(b"extra")
    third = coco._model_identity(tmp_path)
    assert second != third


def test_model_cache_result_cannot_be_poisoned_by_caller(tmp_path):
    (tmp_path / "weights").write_bytes(b"original")
    first = coco._model_identity(tmp_path)
    first["files"].clear()
    assert coco._model_identity(tmp_path)["files"]


def test_shared_admission_limits_all_clients_and_releases_failure(tmp_path):
    from agentreview.research_v2.request_admission import Slot
    active = maximum = 0
    lock = threading.Lock()
    def work(number):
        nonlocal active, maximum
        with Slot(tmp_path, 18001, 2):
            with lock:
                active += 1
                maximum = max(active, maximum)
            try:
                time.sleep(.02)
                if number == 0:
                    raise ValueError("transport failure")
            finally:
                with lock:
                    active -= 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(work, i) for i in range(8)]
        for f in futures:
            try:
                f.result()
            except ValueError:
                pass
    assert maximum == 2
    assert active == 0


def test_cross_process_admission_and_process_exit_release(tmp_path):
    from agentreview.research_v2.request_admission import Slot
    script = """
import json,os,sys,time
from pathlib import Path
from agentreview.research_v2.request_admission import Slot
slot = Slot(Path(sys.argv[1]), 18001, 2)
slot.__enter__()
start = time.monotonic_ns()
time.sleep(.08)
end = time.monotonic_ns()
Path(sys.argv[1], sys.argv[2]+'.json').write_text(json.dumps([start,end]))
os._exit(0)
"""
    children = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), str(i)]) for i in range(8)]
    for child in children:
        assert child.wait(timeout=10) == 0
    events = []
    for i in range(8):
        start, end = json.loads((tmp_path / f"{i}.json").read_text())
        events.extend([(start, 1), (end, -1)])
    count = maximum = 0
    for _, change in sorted(events):
        count += change
        maximum = max(maximum, count)
    assert maximum == 2
    first, second = Slot(tmp_path, 18001, 2), Slot(tmp_path, 18001, 2)
    try:
        assert first.try_acquire() and second.try_acquire()
    finally:
        first.close()
        second.close()


def test_force_rehash_reads_bytes_even_without_mutation(tmp_path):
    (tmp_path / "weights").write_bytes(b"original")
    with patch.object(coco, "_binding", wraps=coco._binding) as digest:
        expected = coco._model_identity(tmp_path)
        assert coco._model_identity(tmp_path, force_rehash=True) == expected
        assert digest.call_count == 2
