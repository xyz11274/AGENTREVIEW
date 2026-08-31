# Protocol 状态索引

最近核验：2026-08-31

本目录保存 Research Validity Core v1 的冻结语义、G2 物化合同和 Audit Hardening v2 接口。协议是目标与验收依据；代码接线和工件完成度以 [ARCHITECTURE.md](../ARCHITECTURE.md) 与 [STATUS.md](../STATUS.md) 为准。

## 状态标记

| 标记 | 含义 |
|---|---|
| CORE-CODE | 该协议的核心正常路径已有对应实现；同一行的其他标记表达剩余范围 |
| G2-PENDING | 正式 registry、manifest、identity、数据或 preflight 待物化 |
| WIRING-PENDING | helper/interface 已实现，生产组合根待接入 |
| V2-DEFERRED | Audit Hardening v2 延期目标 |
| DRAFT | 语义仍以 draft 身份保留 |

一份协议可以同时包含多个标记。

## 协议清单

| 协议 | 标记 | 当前解释 |
|---|---|---|
| [B-local role eligibility](b_local_role_eligibility_and_denominator_protocol_20260828.md) | CORE-CODE, G2-PENDING | role view、membership、threshold 已实现；真实 manifest/audit 待生成 |
| [Canonical evaluator records](canonical_evaluator_records_and_parser_protocol_20260828.md) | CORE-CODE, G2-PENDING | applicability/scoring record 已实现；paper evaluation record、registry 和 durable records 待物化 |
| [Canonical fitness/program/candidate identity](canonical_fitness_program_and_candidate_identity_protocol_20260829.md) | CORE-CODE, G2-PENDING, V2-DEFERRED | fitness、binary64 softmax、Program/Candidate identity 已实现；完整 protocol root/gate/recovery 分层推进 |
| [CLAR anchors](clar_subdimension_scoring_anchors_20260828.md) | G2-PENDING | Frozen scoring spec；通用 0–4 shell 已有代码，anchor binding、event derivation、registry/prompt/model identity 待实现 |
| [Eight RTX 4090 topology](eight_rtx4090_resource_topology_protocol_20260829.md) | G2-PENDING, V2-DEFERRED | 正式资源与 capacity 物化规范；复杂 lifecycle/watchdog 延期 |
| [Evaluator identity/capacity gate](evaluator_identity_and_capacity_gate_protocol_20260829.md) | CORE-CODE, G2-PENDING | selector helper 已实现；真实 semantic/runtime identity 与 results/selection 待生成 |
| [Final Suite sealing/Official authorization](final_suite_sealing_and_official_coco_authorization_protocol_20260829.md) | CORE-CODE, G2-PENDING, V2-DEFERRED | 五 winner、META-off、进程内授权已实现；durable seal/auth/reporting 待推进 |
| [Formal controller journal/recovery](formal_controller_journal_and_recovery_protocol_20260829.md) | G2-PENDING, V2-DEFERRED | Core 最小 decision lineage/journal 待实现；复杂 recovery/mirror 延期 |
| [Formal memory seed/read mode](formal_memory_seed_and_read_mode_protocol_20260829.md) | CORE-CODE, G2-PENDING, V2-DEFERRED | 三 seed、closed mode、fresh session、atomic budget 已实现；完整 transaction/recovery 分层推进 |
| [Frozen external evidence pool](frozen_external_evidence_pool_materialization_protocol_20260829.md) | CORE-CODE, G2-PENDING, WIRING-PENDING | pool/loader/render 已实现；真实 records 与 Candidate API/budget/audit 待完成 |
| [Full Review Corpus/projection](full_review_corpus_and_retrieval_projection_protocol_20260829.md) | CORE-CODE, G2-PENDING, V2-DEFERRED | schema/materialization/index 已实现；625 Corpus 与五索引待物化；后半段复杂 transaction/recovery 延期 |
| [Global top-3](global_top3_finalist_selection_protocol_20260829.md) | CORE-CODE, G2-PENDING | unique Program top-3 与 full-pool winner 已实现；正式 artifacts 待生成 |
| [Machine registry/gate](machine_registry_and_gate_protocol_20260828.md) | G2-PENDING | registry、namespace 与 gate artifacts 待物化 |
| [ORIG anchors](orig_subdimension_scoring_anchors_20260828.md) | G2-PENDING | Frozen scoring spec；anchor binding、event derivation、registry/prompt/model identity 待实现 |
| [POL anchors](pol_subdimension_scoring_anchors_20260828.md) | G2-PENDING | Frozen scoring spec；anchor binding、event derivation、registry/prompt/model identity 待实现 |
| [QUAL anchors](qual_subdimension_scoring_anchors_20260828.md) | G2-PENDING | Frozen scoring spec；anchor binding、event derivation、registry/prompt/model identity 待实现 |
| [Qwen request transaction/recovery](qwen_request_transaction_and_recovery_protocol_20260829.md) | G2-PENDING, V2-DEFERRED | Core raw response/attempt lineage 待持久化；restart/late arbitration 等复杂恢复延期 |
| [Core v1 / Hardening v2 profile](research_validity_core_v1_and_audit_hardening_v2_profile_20260830.md) | G2-PENDING, V2-DEFERRED | 实施分层权威；Core blocking 条目仍有代码/物化缺口，V2 条目延期 |
| [Role-specific reference-free evaluator](role_specific_reference_free_evaluator_protocol_20260828.md) | DRAFT, CORE-CODE, G2-PENDING | 两阶段 evaluator 与 fitness shell 已实现；完整 manifests/gates 待物化 |
| [Shared-pool attempt/failure](shared_pool_mstar_attempt_and_failure_protocol_20260829.md) | CORE-CODE, G2-PENDING | shared seeds/pool、attempt charging、dedup 已实现；durable journal 待完成 |
| [Shared Qwen scheduler/paper pipeline](shared_qwen_scheduler_and_parallel_pipeline_protocol_20260829.md) | CORE-CODE, WIRING-PENDING, G2-PENDING, V2-DEFERRED | service/proxy 与 DAG helper 已实现；跨角色单一 scheduler、production DAG 与 capacity artifacts 待接；复杂 service epoch/recovery 延期 |
| [SIGN anchors](sign_subdimension_scoring_anchors_20260828.md) | G2-PENDING | Frozen scoring spec；anchor binding、event derivation、registry/prompt/model identity 待实现 |

## Core v1 首次正式实验关注点

Core v1 优先完成：

- 真实 625-review Corpus、projection、fragment 与五索引；
- B-local、Evidence、rubric、Evaluator identity 与 smoke；
- Core-compatible embedding/Official Adapter 与 runtime config；
- shared-pool search、五 winner 和 Final Suite；
- 最小 request/decision lineage、provenance 与 durable Official authorization；
- capacity selection、runtime profile、final preflight；
- G1、G2、G3 同时通过。

## Audit Hardening v2

延期层集中覆盖：

- verified journal mirrors 与 activation；
- root watchdog、archive 和 external delivery；
- infrastructure generation replacement；
- complex recovery epochs 与 dependency re-seal；
- Evidence terminalization；
- reporting ingress、Hold、mirror 与 storage replacement；
- publication candidate、promotion 和 replicas。

该分层由 [ADR-0011](../adr/0011-research-validity-core-v1-and-audit-hardening-v2.md) 与 Core/Hardening profile 共同约束。
