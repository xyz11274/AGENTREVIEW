# Bounded paper concurrency with complete CoCo receipts

Status: implementation tested on development papers; service tuning and scale-up
are gated by artifacts under `artifacts/shared-throughput-20260917`.

The completed pilot selected `seed-vector_search` (static Paper. 3.4).
`candidate-0002` copied its parent's source byte for byte. The existing shared
runner and upstream MSTAR pool admit by candidate ID, without a source-hash
rejection rule. Preserve that historical admission, consumed attempt and parent
sampling weight. Six pool entries represent five distinct sources. Historical
results are not rewritten; their original runtime bytes have been archived.

An opt-in frozen execution profile now permits two paper tasks in one assessment.
Each task creates its own completion client and request directory and restores
each of the five role memories from its frozen build snapshot. The shared
embedding object/cache is an encoder cache, never a writable paper knowledge base.
Each paper runs five roles and Meta, then original CoCo Split, Classify and Judge.
Generation may overlap another paper's scoring. MSTAR parent selection,
reflection, child assessment and admission remain sequential dependent operations.

The 18001 service uses one cross-process request admission limit for memory,
reviewers, Meta and Judge. Local file locks release slots even after process exit.
Split and Classify have their own explicit limits. Old executions retain the old
exclusive Reviewer/shared Judge lease; the new profile uses a shared mode lease
and bounded request admission. Decoding parameters and complete output budgets
are unchanged. Service startup parameters are measured separately from application
admission and paper concurrency.

CoCo still receives only complete final reviews. Each paper has an original,
independently validated assessment receipt. The cohort receipt binds those child
receipts, their ordered paper/role artifacts, identical candidate/memory/runtime,
reference closure, all stage evidence and original per-paper scores. It recomputes
the same aggregate and rejects missing, duplicated, changed or nested children.
`validate_assessment_receipt()` validates both atomic and composed receipts.
Any task failure stops new submissions, joins in-flight tasks, preserves evidence
and prevents admission of the incomplete cohort. Failed work is never zero-filled.

Model digest caching is process-local and concurrency-safe. The first observation
hashes all bytes. Subsequent checks require both unchanged namespace/stat identity
and no Linux inotify mutations, including writes within the same timestamp tick,
attribute changes and queue overflow. A stat-only prototype failed the restored-
mtime regression and was replaced. Champion freezing forces another byte-level
verification. Nothing is trusted across process restarts solely from timestamps.

Qwen3-Embedding already loads lazily once per backend and deduplicates identical
texts through a revision/semantics-bound SQLite cache and a shared lock. Both
two-paper measurements were warm-cache runs: no Qwen embedding reload or encoding
occurred. This is reported rather than credited as a new optimization. CoCo CPU
text metrics, model hashing, admission waits, generation, each scoring stage,
running/waiting requests, KV usage and preemptions are timed separately.

The serial two-paper run completed in 740.66 seconds (9.72 valid reviews/hour).
The two-paper pipeline completed in 607.02 seconds (11.86/hour), a 22.0% throughput
increase on this small development workload. Both had zero failed reviews and
zero observed preemptions. End-to-end Paper. changed from [3.2, 3.4] to [3.0, 3.4]
with different generated reviews; this alone does not establish scorer stability.
Fixed-input original-Judge controls are kept separate and never counted as new
full-flow reviews. One diagnostic inherited IDE proxies and failed with 502s; its
failed artifacts are retained and the diagnostic environment was corrected to
match the isolated CoCo worker before retrying.

Fixed-input controls revealed variation even with the original serving parameters
and identical request payloads: Paper. changed by [-0.2, +0.2], with unchanged
cohort mean. The user selected Paper. as the primary stability gate. Preserve the
original stricter failure record, and bind `stability-policy.v2.json`: absolute
per-paper delta at most 0.2 and absolute cohort-mean delta at most 0.1. Required
classification and category scores must all be complete; their numerical changes
are reported rather than used as a second promotion gate. This is a small-sample
development screening policy, not evidence of deterministic scoring.

The 16-sequence trial is rejected despite faster execution: both Paper. values
fell by 0.2, exceeding the cohort-mean tolerance. With eight sequences and a 4096
batch-token ceiling, the fixed-input trial passes at [3.2, 3.6]. Subsequent trials
and final configuration selection are recorded in `judge-comparison.json` and
the frozen throughput gate, not inferred from these intermediate observations.

The user then requested stopping boundary tuning and launching the workable
configuration promptly. The selected configuration is the already validated
two-paper application pipeline with the original bounded serving parameters
(eight sequences, default 2048 batch tokens, chunked prefill enabled, Judge prefix
caching disabled). The 4096 diagnostic remains a passing unpromoted observation;
the prefix diagnostic was cancelled before any scoring request. A 65536 batch
ceiling could not initialize with the unchanged 65536 context at 0.85 memory
allocation: only 0.17 GiB KV space remained versus 1.04 GiB required. Do not label
the unchunked comparison complete or reduce context to make it pass.

`throughput-gate.frozen.json` validates complete receipts, fixed-input Paper.
stability, live/measured runtime byte equivalence, original service arguments and
unchanged pilot artifacts. `expanded/protocol.frozen.json` binds that gate. The
expanded development run has launched and entered real build-review generation;
its completion and effective throughput must still be measured from new receipts.

Expanded development assets contain build 8, static 6 and rotating 8 papers, with
six planned MSTAR proposals and a fresh frozen-Champion development evaluation.
The formal gate is closed: historical exposure completeness and exhaustive
publication-family linkage are not established. The sealed partition remains
excluded from generation, retrieval and scoring. Expansion requires a completed,
bound throughput/integrity/stability gate and a newly frozen configuration.
