"""Measured preformal capacity decisions for the 8×24GB RTX 4090 host."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Sequence, Tuple


CAPACITY_CANDIDATES = (1, 2, 4)
MIN_TP_GPU_FREE_MIB = 768


@dataclass(frozen=True)
class CapacitySample:
    max_num_seqs: int
    repetition: int
    canonical_output_sha256: Tuple[str, ...]
    finish_reasons: Tuple[str, ...]
    protocol_dispositions: Tuple[str, ...]
    elapsed_seconds: float
    completed_requests: int
    p95_latency_seconds: float
    tp_gpu_free_mib: Tuple[int, ...]
    oom_count: int = 0
    schema_failure_count: int = 0
    protocol_failure_count: int = 0

    @property
    def throughput(self) -> float:
        if self.elapsed_seconds <= 0:
            raise ValueError("Capacity sample elapsed time must be positive")
        return self.completed_requests / self.elapsed_seconds


@dataclass(frozen=True)
class CapacityDecision:
    selected_max_num_seqs: int
    median_throughput: float
    median_p95_latency_seconds: float
    min_observed_free_mib: int
    rejected: Mapping[int, Tuple[str, ...]]


def _capacity_failures(samples: Sequence[CapacitySample]) -> Tuple[str, ...]:
    failures = []
    if len(samples) < 3:
        failures.append("FEWER_THAN_THREE_REPETITIONS")
        return tuple(failures)
    reference = (
        samples[0].canonical_output_sha256,
        samples[0].finish_reasons,
        samples[0].protocol_dispositions,
    )
    if any(
        (sample.canonical_output_sha256, sample.finish_reasons, sample.protocol_dispositions)
        != reference
        for sample in samples[1:]
    ):
        failures.append("WITHIN_CAPACITY_OUTPUT_INSTABILITY")
    if any(sample.oom_count for sample in samples):
        failures.append("OOM")
    if any(sample.schema_failure_count for sample in samples):
        failures.append("SCHEMA_FAILURE")
    if any(sample.protocol_failure_count for sample in samples):
        failures.append("PROTOCOL_FAILURE")
    free_values = [value for sample in samples for value in sample.tp_gpu_free_mib]
    if not free_values or min(free_values) < MIN_TP_GPU_FREE_MIB:
        failures.append("TP_GPU_FREE_MEMORY_MARGIN")
    return tuple(failures)


def select_qwen_capacity(samples: Iterable[CapacitySample]) -> CapacityDecision:
    grouped: Dict[int, list[CapacitySample]] = {value: [] for value in CAPACITY_CANDIDATES}
    for sample in samples:
        if sample.max_num_seqs not in grouped:
            raise ValueError("Capacity sample must use max-num-seqs 1, 2, or 4")
        grouped[sample.max_num_seqs].append(sample)
    rejected = {}
    for capacity in (4, 2, 1):
        values = sorted(grouped[capacity], key=lambda item: item.repetition)
        failures = _capacity_failures(values)
        if failures:
            rejected[capacity] = failures
            continue
        return CapacityDecision(
            selected_max_num_seqs=capacity,
            median_throughput=statistics.median(item.throughput for item in values),
            median_p95_latency_seconds=statistics.median(
                item.p95_latency_seconds for item in values
            ),
            min_observed_free_mib=min(
                value for item in values for value in item.tp_gpu_free_mib
            ),
            rejected=dict(rejected),
        )
    raise RuntimeError("No Qwen TP4 capacity candidate passed the preformal gate")


def materialized_max_model_len(
    required_request_lengths: Iterable[int], *, model_context_limit: int
) -> int:
    values = [int(value) for value in required_request_lengths]
    if not values or any(value <= 0 for value in values):
        raise ValueError("Required request lengths must be positive")
    selected = int(math.ceil(max(values) / 8192.0) * 8192)
    if selected > int(model_context_limit):
        raise RuntimeError("Formal request exceeds frozen model context limit")
    return selected


def select_active_paper_window(
    *, capacity: int, throughput_c: Sequence[float], throughput_2c: Sequence[float], two_c_passed: bool
) -> int:
    if len(throughput_c) < 3 or len(throughput_2c) < 3:
        raise ValueError("Active-paper window gate requires three repetitions")
    baseline = statistics.median(throughput_c)
    expanded = statistics.median(throughput_2c)
    if two_c_passed and baseline > 0 and expanded >= baseline * 1.10:
        return 2 * int(capacity)
    return int(capacity)


def frozen_request_timeout(max_observed_gate_latency_seconds: float) -> int:
    raw = max(600, math.ceil((2 * float(max_observed_gate_latency_seconds)) / 60) * 60)
    if raw > 3600:
        raise RuntimeError("Request class exceeds the 3600-second timeout gate")
    return int(raw)
