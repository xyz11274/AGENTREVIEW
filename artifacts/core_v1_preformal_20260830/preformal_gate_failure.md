# Research Validity Core v1 preformal gate result

- Gate time: `2026-08-30`
- Disposition: `PRECONDITION_FAILED`
- Capacity benchmark: `NOT_STARTED`
- Formal experiment: `NOT_STARTED`
- Existing `runs/` artifacts: `UNCHANGED`

## Completed operations

1. Captured and hashed the old-service snapshot before shutdown.
2. Verified that no live AgentReview/formal controller depended on GPU4/5 CoCo services.
3. Stopped the exact tmux services `coco_split`, `coco_classify`, and `qwen38`.
4. Verified that ports `8000`, `13098`, and `13099` were closed and all eight GPUs returned to `15 MiB` baseline use.
5. Verified the GPU0–3 NUMA0/PIX topology and GPU4–7 NUMA1/PIX topology.

Snapshot artifact:

- path: `artifacts/core_v1_preformal_20260830/service_snapshot_before_stop.md`
- SHA256: `3e77d923c01ecf8c943a843b5391eaeac814d2f1d0f7091ef356490e1f36b01d`

## Blocking gate

The required `max-model-len` candidate cannot be materialized from the current Core v1 repository state. The repository contains `memory_evolution.capacity_gate.materialized_max_model_len()`, which rounds an already-computed set of request lengths, but no formal request materialization artifact or executable materializer supplies those lengths.

The missing formal inputs are:

- complete frozen Reviewer, Evaluator Applicability, Evaluator Scoring, Reflector, compile-fix, and post-retrieval Summarizer request vectors;
- concrete frozen B-local full-pool manifest, role-specific static-25/rotating schedules, and role eligibility thresholds;
- Frozen External Evidence Pool records/manifests and delivered evidence token statistics;
- concrete Memory API, Evidence API, Summarizer input/output, Reviewer Memory context, and query token budget integers;
- frozen evaluator identities and the required five-role deterministic smoke-gate results;
- a production Core v1 formal entrypoint. The current repository exposes only the library composition root `memory_evolution/core_formal.py::CoreRoleEvolutionRunner`.

Machine existence checks found:

- B-local artifacts: protocol Markdown only;
- Frozen External Evidence implementation/artifacts: none (only a string injection field in the Core runner and Review Memory's unrelated `max_api_calls`);
- budget/config result JSON artifacts: none outside existing legacy `runs/`;
- evaluator identity/smoke-gate JSON artifacts: none;
- request materialization implementation: only the rounding helper, no producer of `required_request_lengths`;
- Core formal executable: none under `scripts/` or a CLI entrypoint.

The implementation matrix already classifies these as real preformal checks, including real 125-paper selection/625-review build, real embedding stability, frozen B-local manifests and eligibility thresholds, evaluator 5×3 smoke, full request token materialization, and concrete budgets/caps/timeouts.

## Why 1/2/4 was not launched

Launching TP4 with `max-model-len=262144` would silently inherit the old runtime value, explicitly forbidden by the authorization. Choosing any smaller value without the missing formal request/budget materialization would be subjective and could change the feasible Reviewer/Memory/Evidence input envelope. Therefore no fresh capacity process was started and no synthetic capacity result was presented as a formal gate result.

## Exclusive-resource state

The shutdown-side exclusive compute check passed at the time of verification:

- GPU0–7 observed memory use: `15 MiB` each;
- no Qwen/CoCo compute process remained;
- ports `8000`, `13098`, and `13099` were closed.

Service readiness did not run because the length/config precondition failed. Per the authorized rule, formal startup stopped here.
