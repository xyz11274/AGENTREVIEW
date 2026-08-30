import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from memory_evolution.core_scheduler import (
    GlobalQwenScheduler,
    GlobalQwenService,
    LogicalQwenRequest,
    PaperDagInput,
    run_bounded_paper_dags,
    ScheduledToolkitProxy,
)


class CoreSchedulerTest(unittest.TestCase):
    def test_role_round_robin_fifo_and_global_capacity(self):
        requests = [
            LogicalQwenRequest("q2", "QUAL", 2, "reviewer", None),
            LogicalQwenRequest("p0", "POL", 0, "evaluator", None),
            LogicalQwenRequest("c0", "CLAR", 0, "reflector", None),
            LogicalQwenRequest("q1", "QUAL", 1, "compile_fix", None),
        ]
        active = 0
        maximum = 0
        lock = threading.Lock()

        def execute(request):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return request.logical_request_id

        records = GlobalQwenScheduler(2).run(requests, execute)
        self.assertLessEqual(maximum, 2)
        self.assertEqual([item.logical_request_id for item in records[:2]], ["q1", "c0"])
        qual = [item for item in records if item.role == "QUAL"]
        self.assertEqual([item.logical_request_sequence for item in qual], [1, 2])

    def test_paper_dag_scoring_waits_for_reviewer_and_applicability(self):
        events = []
        lock = threading.Lock()

        def event(value):
            with lock:
                events.append(value)
            return value

        papers = [
            PaperDagInput("paper-2", "QUAL", 1, None),
            PaperDagInput("paper-1", "QUAL", 0, None),
        ]
        results = run_bounded_paper_dags(
            papers,
            active_paper_window=2,
            build_context=lambda item: event((item.paper_id, "context")),
            build_evidence_availability=lambda item: event((item.paper_id, "evidence")),
            run_reviewer=lambda item, context, evidence: event((item.paper_id, "reviewer")),
            run_applicability=lambda item, context, evidence: event((item.paper_id, "applicability")),
            run_scoring=lambda item, context, evidence, review, applicability: event((item.paper_id, "scoring")),
        )
        self.assertEqual([item.paper_id for item in results], ["paper-1", "paper-2"])
        for paper_id in ("paper-1", "paper-2"):
            scoring = events.index((paper_id, "scoring"))
            self.assertLess(events.index((paper_id, "reviewer")), scoring)
            self.assertLess(events.index((paper_id, "applicability")), scoring)

    def test_long_lived_toolkit_proxies_share_one_global_capacity(self):
        active = 0
        maximum = 0
        lock = threading.Lock()

        class Toolkit:
            def llm_completion(self, messages, **kwargs):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return messages[0]["content"]

        with GlobalQwenService(2) as service:
            proxies = {
                role: ScheduledToolkitProxy(Toolkit(), service, role)
                for role in ("QUAL", "CLAR", "SIGN", "ORIG", "POL")
            }
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(
                        proxies[role].llm_completion,
                        [{"role": "user", "content": role}],
                        agent_name="Reviewer",
                    )
                    for role in proxies
                ]
                self.assertEqual({future.result() for future in futures}, set(proxies))
            records = service.execution_records
        self.assertLessEqual(maximum, 2)
        self.assertEqual(len(records), 5)
        self.assertEqual(len({item.logical_request_id for item in records}), 5)


if __name__ == "__main__":
    unittest.main()
