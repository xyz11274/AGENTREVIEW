import unittest
from unittest.mock import Mock, patch

from scripts.build_frozen_s2_pool import (
    asset_record_from_work,
    canonical_sha256,
    request_search,
)


class FrozenS2PoolTest(unittest.TestCase):
    @staticmethod
    def _response(payload):
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.text = repr(payload)
        response.json.return_value = payload
        return response

    def _request(self, response, *, max_attempts=2):
        with patch(
            "scripts.build_frozen_s2_pool.requests.get",
            return_value=response,
        ) as request, patch(
            "scripts.build_frozen_s2_pool.time.sleep"
        ) as sleep:
            result = request_search(
                title="Target",
                top_k=10,
                api_key="fixture",
                max_attempts=max_attempts,
                min_interval_seconds=1.0,
            )
        return result, request, sleep

    def test_zero_total_without_data_is_normalized_to_empty_results(self):
        result, request, sleep = self._request(
            self._response({"total": 0, "offset": 0})
        )
        self.assertEqual(result["data"], [])
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_zero_total_with_empty_data_is_valid(self):
        result, request, sleep = self._request(
            self._response({"total": 0, "offset": 0, "data": []})
        )
        self.assertEqual(result["data"], [])
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_positive_total_without_data_is_malformed_and_retried(self):
        response = self._response({"total": 5, "offset": 0})
        with patch(
            "scripts.build_frozen_s2_pool.requests.get",
            return_value=response,
        ) as request, patch(
            "scripts.build_frozen_s2_pool.time.sleep"
        ) as sleep, self.assertRaisesRegex(RuntimeError, "malformed search payload"):
            request_search(
                title="Target",
                top_k=10,
                api_key="fixture",
                max_attempts=2,
                min_interval_seconds=1.0,
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_non_list_data_is_malformed_and_retried(self):
        response = self._response({"total": 1, "offset": 0, "data": {}})
        with patch(
            "scripts.build_frozen_s2_pool.requests.get",
            return_value=response,
        ) as request, patch(
            "scripts.build_frozen_s2_pool.time.sleep"
        ) as sleep, self.assertRaisesRegex(RuntimeError, "malformed search payload"):
            request_search(
                title="Target",
                top_k=10,
                api_key="fixture",
                max_attempts=2,
                min_interval_seconds=1.0,
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_unparseable_json_is_malformed_and_retried(self):
        response = self._response(None)
        response.json.side_effect = ValueError("invalid JSON")
        with patch(
            "scripts.build_frozen_s2_pool.requests.get",
            return_value=response,
        ) as request, patch(
            "scripts.build_frozen_s2_pool.time.sleep"
        ) as sleep, self.assertRaisesRegex(RuntimeError, "malformed search payload"):
            request_search(
                title="Target",
                top_k=10,
                api_key="fixture",
                max_attempts=2,
                min_interval_seconds=1.0,
            )
        self.assertEqual(request.call_count, 2)
        self.assertEqual(sleep.call_count, 1)

    def test_missing_or_invalid_total_is_malformed_and_retried(self):
        for payload in (
            {"offset": 0, "data": []},
            {"total": "0", "offset": 0, "data": []},
            {"total": True, "offset": 0, "data": []},
        ):
            with self.subTest(payload=payload), patch(
                "scripts.build_frozen_s2_pool.requests.get",
                return_value=self._response(payload),
            ) as request, patch(
                "scripts.build_frozen_s2_pool.time.sleep"
            ) as sleep, self.assertRaisesRegex(
                RuntimeError, "malformed search payload"
            ):
                request_search(
                    title="Target",
                    top_k=10,
                    api_key="fixture",
                    max_attempts=2,
                    min_interval_seconds=1.0,
                )
            self.assertEqual(request.call_count, 2)
            self.assertEqual(sleep.call_count, 1)

    def test_asset_result_count_does_not_modify_legacy_work_record(self):
        results = []
        work = {
            "paper_id": "target",
            "retrieved_papers": results,
            "result_sha256": canonical_sha256(results),
        }
        asset = asset_record_from_work(work)
        self.assertNotIn("result_count", work)
        self.assertEqual(asset["result_count"], 0)
        self.assertEqual(asset["retrieved_papers"], [])


if __name__ == "__main__":
    unittest.main()
