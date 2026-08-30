import unittest

from memory_evolution.capacity_gate import (
    CapacitySample,
    frozen_request_timeout,
    materialized_max_model_len,
    select_active_paper_window,
    select_qwen_capacity,
)


def sample(capacity, repetition, *, output="stable", free=1024, oom=0):
    return CapacitySample(
        max_num_seqs=capacity,
        repetition=repetition,
        canonical_output_sha256=(output,),
        finish_reasons=("stop",),
        protocol_dispositions=("SUCCESS",),
        elapsed_seconds=10.0 / capacity,
        completed_requests=10,
        p95_latency_seconds=5.0,
        tp_gpu_free_mib=(free, free, free, free),
        oom_count=oom,
    )


class CapacityGateTest(unittest.TestCase):
    def test_selects_highest_stable_passing_capacity(self):
        values = []
        for capacity in (1, 2, 4):
            for repeat in range(3):
                values.append(sample(capacity, repeat))
        decision = select_qwen_capacity(values)
        self.assertEqual(decision.selected_max_num_seqs, 4)

    def test_falls_back_from_unstable_or_low_memory_capacity_four(self):
        values = [sample(1, index) for index in range(3)]
        values += [sample(2, index) for index in range(3)]
        values += [sample(4, 0, output="a"), sample(4, 1, output="b"), sample(4, 2, output="a")]
        decision = select_qwen_capacity(values)
        self.assertEqual(decision.selected_max_num_seqs, 2)
        self.assertIn("WITHIN_CAPACITY_OUTPUT_INSTABILITY", decision.rejected[4])

    def test_context_window_active_window_and_timeout_are_measured_derivations(self):
        self.assertEqual(
            materialized_max_model_len([8000, 8200, 16001], model_context_limit=32768),
            16384,
        )
        self.assertEqual(
            select_active_paper_window(
                capacity=4,
                throughput_c=[10, 10, 10],
                throughput_2c=[11, 11, 11],
                two_c_passed=True,
            ),
            8,
        )
        self.assertEqual(frozen_request_timeout(301), 660)


if __name__ == "__main__":
    unittest.main()
