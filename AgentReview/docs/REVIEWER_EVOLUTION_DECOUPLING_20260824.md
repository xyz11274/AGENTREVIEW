# Reviewer / Evolution Decoupling (2026-08-24)

## Result

The runtime dependency direction is now:

```text
MSTAR Evolution
      |
      v
AgentReview Reviewer Core
      |
      v
Candidate Review (candidate_reviews.jsonl)
      |
      v
Official CoCo Evaluation
```

`agentreview.reviewer_runtime` does not import `memory_evolution`. It contains no
search controller, mutation, reflection, selection, lineage, evolution budget,
fitness, or evolution checkpoint code. MSTAR imports the Core and remains the
owner of all optimizer behavior.

## Reviewer Core

The stable public values are exported from `agentreview.reviewer_runtime`:

- `ReviewRequest`: one paper, one `task_id`, one explicit episode sequence.
- `ReviewResult`: review text plus query/context and stable identities.
- `ReviewerEngine.review(request, memory_program)`: one-shot public entrypoint.
- `ReviewerSession`: stateful history replay and snapshot/restore used by MSTAR
  and batch review orchestration.
- `MemoryProgramLoader`: accepts a seed file, any existing
  `runs/.../best_program.py`, a future optimizer's standalone Python output, or
  a promoted task suite root.
- `KnowledgeItem`, `Query`, `KnowledgeBase`, `write`, `read`, and all
  `INSTRUCTION_*`/`ALWAYS_ON_KNOWLEDGE` fields retain their executable contract.

The process sandbox, sandbox worker, fixed TaskAgent prompting, capability
toolkit, request construction, episode formatting, and MemoryProgram loader now
live under `agentreview/reviewer_runtime/`. Candidate code remains isolated from
the host and receives only the existing capability RPC surface.

The Core is deliberately unaware of A/B/C/D. It understands only ordinary
requests and the existing `self_generated_after_review` episode protocol. Zone
selection belongs to CLI/evolution adapters.

## MSTAR integration and unchanged semantics

`memory_evolution.evaluator.evaluate_program` now asks `ReviewerSession` to
generate each review. MSTAR still performs, in the same order:

1. chronological historical replay and writeback;
2. static/rotating validation selection;
3. Reviewer Core generation;
4. MSTAR fitness computation;
5. reflection, mutation, selection, lineage accounting, and promotion.

The refactor does not change softmax selection, temperature, lineage fairness,
attempt/completed budgets, static/rotating allocation, reflector prompts,
contextual V4A, embeddings, compile repair, fitness formulas, or checkpoint
identity. `candidate_reviews.jsonl` is an observation artifact only. It is not
part of controller state, checkpoint/resume, reflection input, selection,
fitness, or champion determination.

Existing imports remain valid through compatibility modules:

```text
memory_evolution.program_protocol
memory_evolution.sandbox
memory_evolution.task_agent
memory_evolution.toolkit
memory_evolution.integration
memory_evolution.checkpoint
```

Existing `python -m memory_evolution.cli evolve-task ...`, seeds, promoted
programs, run artifacts, and legacy Reviewer JSON files remain readable.

## Reviewer CLI

Single task:

```bash
python -m agentreview.cli review \
  --task-id QUAL \
  --memory-program memory_evolution/seeds/vector_search.py \
  --paper-id paper-1 \
  --question-file request.txt \
  --run-identity reviewer-run-1 \
  --output candidate_reviews.jsonl
```

Temporal C/D batch:

```bash
python -m agentreview.cli review \
  --task-id QUAL \
  --memory-program runs/.../best_program.py \
  --temporal-manifest configs/coco_temporal_split_v2/manifest.json \
  --zone effectiveness_holdout \
  --coco-root /data/xyj/shengao/CoCoReviewBench \
  --episodes-jsonl frozen_A_episodes.jsonl \
  --run-identity effectiveness-run-1 \
  --output candidate_reviews.jsonl
```

Only `effectiveness_holdout` (C) and `held_out_benchmark_test` (D) are accepted
by the temporal Reviewer adapter. A temporal run requires an explicit frozen
history file. Fresh memory is available only through the explicit
`--allow-fresh-memory` debugging override. The manifest is never used to guess
an A history.

META remains a single task, but its question must contain the already-frozen
specialist reviews. For temporal META batch use `--questions-jsonl`; the Core
does not silently run or concatenate the five specialists.

The legacy six-role CLI remains the upper-level orchestration layer and now
emits a per-paper `candidate_reviews.jsonl` alongside `reviews.json`,
`memory.json`, `review_history.json`, and `timing.json`.

## Candidate review contract

Each JSONL row is uniquely one `(paper_id, task_id)` review within a selected
run:

```json
{
  "schema_version": "agentreview-candidate-review-v1",
  "paper_id": "paper-1",
  "task_id": "META",
  "review_text": "...",
  "reviewer_identity": {
    "name": "Final Meta-Reviewer",
    "task_id": "META",
    "rubric_sha256": "..."
  },
  "memory_program_identity": {
    "logical_identity": "META/best_program",
    "source_kind": "promoted_best_program"
  },
  "memory_program_sha256": "...",
  "model": "qwen3.8-27b-fp8",
  "revision": "...",
  "run_identity": "final-run-1",
  "provenance": {}
}
```

`provenance` is optional and is the only location for optimizer-specific fields
such as candidate, parent, iteration, lineage, replicate, or score. Neither the
Reviewer Core nor CoCo requires it.

## Independent official CoCo evaluation

Entry point:

```bash
python -m agentreview.coco_eval \
  --input candidate_reviews.jsonl \
  --task-id META \
  --coco-root /data/xyj/shengao/CoCoReviewBench \
  --output-dir coco_evaluation
```

`--task-id` is mandatory. `--run-identity` can select one run from an
observation artifact. After filtering, duplicate paper IDs are rejected; no
specialist reviews are implicitly concatenated.

The adapter writes only official `{id, text}` candidate input, then runs the
official programs in order:

```text
src/evaluation/_2_split.py
  -> src/evaluation/_3_classify.py
  -> src/evaluation/_4_evaluation.py
```

It does not use MSTAR run directories, lineages, selections, champions, or
checkpoints. It does not call `CoCoReviewFitness`, whose harmonic formula
remains MSTAR evolution fitness rather than the official benchmark protocol.

No evaluation flags are supplied by default, so `_4_evaluation.py` retains its
official default set (category level, old full-text metrics, and paper-level
judge). The official flags are forwarded without reinterpretation. The judge
default is one `qwen3.8-27b-fp8` backend for the complete evaluation; there is
no fallback and no mixed-judge report.

The adapter pins the SHA-256 content of the official split, classify,
evaluation, taxonomy/prompt registry, judge prompt, parser/utilities, shipped
left-out selection, complete human classify reference, and all supported
benchmark JSONL files. It records the actual CoCo Git HEAD but does not reject
non-protocol changes such as onboarding or service launch scripts.

The checkout does not ship `benchmark_reviewer_selection.json`. The adapter
uses CoCo's own `_0_human_review.load_selection_from_left_out_file` to create an
equivalent selection sidecar in the output directory, then points the official
evaluator at that sidecar. Human-reference construction and aggregation remain
inside official `_4_evaluation.py`.

## Four-zone boundary

- A (`historical_experience`) is explicit Reviewer history.
- B (`evolution_validation`) remains owned by MSTAR fitness, selection, and
  reflection.
- C (`effectiveness_holdout`) is available only through the independent
  Reviewer adapter.
- D (`held_out_benchmark_test`) is available only through the independent
  Reviewer/final-evaluation adapters and never enters evolution.

The Core contains no zone names or temporal manifest parser.

## Deterministic verification

Tests cover:

- Reviewer-only execution of a seed;
- Reviewer-only execution of an existing `best_program.py`;
- Core import without any `memory_evolution` module load;
- mocked semantic parity between the legacy evolution generation entry and the
  new Core;
- standalone candidate JSONL to CoCo mapping without provenance;
- CoCo duplicate-paper rejection and no lineage/checkpoint dependency;
- future optimizer output loaded as a plain MemoryProgram;
- explicit history required for temporal C/D unless debug override is chosen;
- mocked official split/classify/evaluation command orchestration.

No new evolution, real LLM smoke, or long CoCo benchmark is part of this
verification.
