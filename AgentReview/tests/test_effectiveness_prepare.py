import unittest
from unittest.mock import patch

from effectiveness_screening.prepare import (
    DESIGN_VERSION,
    decision_stratum,
    has_author_reply,
    reviewer_overall_scores,
    semantic_scholar_query_candidates,
    semantic_scholar_search,
    text_value,
)


class _Response:
    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Requests:
    RequestException = RuntimeError

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        return self.responses.pop(0)


class EffectivenessPrepareTest(unittest.TestCase):
    def test_design_version_is_frozen(self):
        self.assertEqual(DESIGN_VERSION, "effectiveness-screening-v2-temporal")

    def test_decision_stratum(self):
        self.assertEqual(
            decision_stratum(
                {"conference": "ICLR 2025", "decision": "Accept (Poster)"}
            ),
            "ICLR-accept",
        )
        self.assertEqual(
            decision_stratum({"conference": "NeurIPS 2024", "decision": "Reject"}),
            "NeurIPS-reject",
        )
        self.assertIsNone(
            decision_stratum({"conference": "ICLR 2025", "decision": "Withdrawal"})
        )

    def test_author_and_score_parsing(self):
        record = {
            "reviews": [
                [
                    ["Reviewer 1", {"scores": {"Overall": 2, "Confidence": 4}}],
                    ["Author", {"value": {"comment": [1]}}],
                ],
                [["Reviewer 2", {"scores": {"Overall": 7}}]],
            ]
        }
        self.assertTrue(has_author_reply(record))
        self.assertEqual(reviewer_overall_scores(record), [2.0, 7.0])
        self.assertEqual(text_value({"value": "A title"}), "A title")

    def test_semantic_scholar_query_fallback_is_deterministic(self):
        self.assertEqual(
            semantic_scholar_query_candidates("Method: A descriptive title"),
            ["Method: A descriptive title", "A descriptive title"],
        )
        self.assertEqual(
            semantic_scholar_query_candidates("A descriptive title"),
            ["A descriptive title"],
        )

    @patch("effectiveness_screening.prepare.time.sleep")
    @patch(
        "effectiveness_screening.prepare.time.monotonic",
        side_effect=[0.0, 2.0, 3.0],
    )
    def test_semantic_scholar_search_retries_429(
        self, _monotonic, sleep
    ):
        requests = _Requests(
            [
                _Response(429, headers={"Retry-After": "2"}),
                _Response(200, payload={"data": [{"paperId": "p1"}]}),
            ]
        )
        papers, last_request_at = semantic_scholar_search(
            requests,
            query="query",
            limit=20,
            api_key="key",
            min_interval_seconds=1.0,
            max_attempts=2,
            last_request_at=None,
        )
        self.assertEqual(papers, [{"paperId": "p1"}])
        self.assertEqual(last_request_at, 3.0)
        self.assertEqual(requests.calls, 2)
        sleep.assert_called_once_with(2.0)


if __name__ == "__main__":
    unittest.main()
