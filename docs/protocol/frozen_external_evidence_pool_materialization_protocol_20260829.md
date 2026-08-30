# Frozen External Evidence Pool Materialization Protocol

状态：frozen contract（2026-08-29），Q106–Q115、Q150与Q155已接受。本文冻结v1 evidence granularity、raw/projection identity、read-only API、cutoff validation、token-budget preflight、Candidate budget/error semantics、delivery framing与state isolation；实际records、manifests、预算整数与API artifacts尚待生成。

## 1. Set-Level and Item-Level Identity

每个 target paper 建立唯一 `EvidenceSetManifest`。每个 `PRIOR_WORK`、`POLICY`、`ARTIFACT_LICENSE` source item 建立独立 `EvidenceRecord` 和可引用 `evidence_id`。

`PRIOR_WORK` 原始 S2 query/result-list 另保留 set/query identity、retrieval provenance、result order 与 audit counters；但 Candidate/Evaluator 对具体论文外事实作正式判断时必须引用对应 prior-work item 的 evidence ID，不能只引用整个 query/result blob。

`result_count=0` 是合法 empty set：保存 set manifest、query identity、availability、cutoff/provenance 与 zero-result audit；不得伪造 placeholder evidence record，也不得处罚 Candidate。

EvidenceSetManifest 至少包含：

- pool/API version；
- target paper/family/cutoff identity；
- partition availability；
- 每个 partition 的有序 evidence IDs；
- query/set identities；
- raw/projection/manifest aggregate SHAs；
- token-count与 delivery-policy identity；
- cutoff-validation aggregate result。

## 2. Raw Authority and Canonical Text Projection

每个 item 同时冻结：

- immutable raw bytes/blob 与 `raw_content_sha256`；
- MIME/type、source URL/ID、retrieval/snapshot/archive provenance；
- 无 LLM deterministic projection pipeline 的 tool/version/config；
- model-delivered canonical text 与 `canonical_text_sha256`；
- projection success/failure与完整 audit。

Evidence identity 同时绑定 raw content、canonical projection 与 projection-pipeline identity。Raw blob 是最终审计权威；canonical text 是 Reviewer/Evaluator 实际可读取的冻结投影。

Projection 只允许确定性 decoding、HTML/PDF text extraction、Unicode normalization 与明确冻结的 whitespace normalization。禁止摘要、改写、semantic dedup、semantic chunking、rerank、embedding、LLM清洗或其他语义加工。任何 projection tool/config change 都产生新 evidence/pool identity。

## 3. v1 Deterministic Read-Only API

v1 只提供：

- `get_availability(target_paper_id)`；
- `list_evidence_metadata(target_paper_id, partition)`；
- `read_evidence(target_paper_id, evidence_ids)`；
- `read_partition(target_paper_id, partition, cursor, limit)`。

返回顺序由 frozen EvidenceSetManifest 决定。API 不提供 semantic search、embedding、rerank、query rewrite 或 lineage-specific index。三条 mandatory lineage 获得完全相同的 API、call/record/token budget、pagination、错误规则与返回 bytes。

Candidate 可针对当前 paper 自主决定是否调用、何时调用和读取哪些 records；是否调用属于允许 Evolution 优化的策略。所有调用参数、次数、返回 evidence IDs、返回顺序、bytes/tokens、错误与 latency 必须进入 audit。

### Candidate × Paper Budget

每个 Candidate × paper 同时冻结：

- `max_api_calls`；
- `max_delivered_records`；
- `max_delivered_tokens`。

三条 mandatory lineage 使用完全相同的 capability、预算与计数规则。所有实际 delivery—including metadata—均按冻结 tokenizer/framing config 计入预算；重复读取同一 evidence 再次计费。Availability 可使用独立固定小预算，但对所有 lineage 相同。具体整数必须在 pool materialization/token statistics 后预注册，不得凭经验或根据 formal result 调整。

### API Status and Failure Ownership

- Pool/blob/hash/manifest/service 内部故障属于 infrastructure failure，不处罚 Candidate，并中止当前 evaluation；若影响 identity/integrity则 role-level fail-fast。
- Candidate 发出越权 paper ID、非法 cursor、未知 partition 或其他 malformed/out-of-scope request 时记录 `INVALID_EVIDENCE_API_REQUEST`，默认使当前 Candidate evaluation 按冻结 Candidate runtime/protocol failure 规则失败，不转换为 fitness `0`、不增加预算、不替换 evidence，也不自动使整个 role fail-fast。
- 只有 Candidate invalid request 暴露 orchestrator routing、manifest scope 或 protocol identity 错误时，才升级为 role-level failure。
- 达到任一预算返回确定性的 `EVIDENCE_BUDGET_EXHAUSTED`，不再 delivery 新 evidence；Candidate 可继续使用已读取信息完成 Review。Budget exhaustion 本身不奖励、不处罚，也不产生额外预算。

未来增加 search/ranking 时必须产生新的 pool/API identity，并对所有 lineage 使用完全相同的 frozen index、query normalization、ranking/tie-break 与 budget rules；新旧结果不得混合。

## 4. Strict Cutoff Validation

- `PRIOR_WORK`：必须证明 publication/version date 不晚于 target cutoff；日期未知不可采纳。
- `POLICY`：必须证明该官方规则版本在对应 venue/year/cutoff 时实际生效。
- `ARTIFACT_LICENSE`：必须证明论文引用的资源/page/version 在 cutoff 前已经存在。

Snapshot/retrieval 可发生在 cutoff 后，但只能使用可验证的 cutoff-before archive/version。当前 live page 不得替代历史版本。无法证明时 availability 记为 `unavailable` 或 `not_archived_before_cutoff`；相关 external judgment 按已冻结 N/A/evidence-limitation 规则处理，不处罚 Candidate。

`POLICY` 只证明规则内容；正式 violation 还必须绑定 `PAPER` evidence。Parametric knowledge 不是 admissible external evidence。

## 5. Token-Budget Materialization Gate

Formal materialization preflight 使用冻结 Evaluator tokenizer、chat template、evidence framing与delivery order，计算每个 target paper 的完整 delivered evidence token count。

- 所有 target papers 均不超过预注册 context/token budget，才能启动 formal；
- 任一 paper 超限，当前 pool/API identity 不得进入 formal；
- 必须在 formal 前发布新的 pool/API version，并冻结统一、lineage-independent、Evaluator一致的 deterministic truncation/retrieval policy；
- 新 version 重新执行 token、cutoff、API、registry、Evaluator context 与 determinism gates；
- runtime 禁止按 Candidate、lineage、当前 prompt length、剩余窗口或模型反馈 adaptive truncation。

所有被 deterministic policy 排除的 evidence IDs、原因、原始/投递 token counts 与 policy SHA 必须进入 audit。

Candidate 自主调用预算可以小于完整 delivered set，但三条 lineage 必须完全相同。Evaluator 读取当前 pool/API identity 定义的完整 delivered set，不得访问 pool 外资料。

## 6. Frozen Delivery Framing

Evaluator 的完整 evidence context 固定按以下 partition 顺序装配：

1. `PRIOR_WORK`
2. `POLICY`
3. `ARTIFACT_LICENSE`

Partition 内严格使用 EvidenceSetManifest order。Item delimiter、evidence ID、partition/source/cutoff、availability/provenance、canonical text 与 untrusted-evidence boundary 均使用冻结格式，并对最终 bytes 计算 `evidence_delivery_context_sha256`。

External content 始终作为 untrusted quoted data，不得覆盖 system、role rubric、schema 或 evaluator instructions。Runtime 不得为处理疑似 prompt injection 临时删除、改写、摘要或重排语义内容；安全 framing 必须在 formal 前统一冻结并通过 gate。

Candidate API 返回 item 使用同一 canonical metadata/text bytes，不得为不同 lineage 生成不同 framing。

## 7. Paper-Level State Isolation

每个 Candidate × paper 使用 fresh evidence session、独立 budget counters 与独立 audit log。Paper 结束后只允许持久保存：

- canonical Candidate Review及其引用的 evidence IDs；
- 完整只读 API audit log。

禁止持久保存External Evidence-derived Candidate-private summary、runtime embedding、rerank、re-chunk、query cache、Memory mutation或跨-paper call history；每个Candidate×paper的派生状态在paper结束后丢弃。Pool层可按immutable raw/projection SHA使用共享只读内容缓存，该缓存不得包含Candidate行为、选择、摘要或派生语义状态。

Frozen immutable document embeddings可以作为长期共享asset；Review Memory协议自身合法的persistent Memory/embedding state不受上述External Evidence隔离规则误伤。Embedding identity绑定model/revision、tokenizer、pooling、normalization、dtype、projection implementation、source manifest SHA、vector-byte SHA与index SHA。

Full Review Corpus index可供formal Vector Search使用；External Evidence embedding index在v1必须保持dormant，不暴露给Candidate/Evaluator、不参与semantic search/rerank或Evidence API返回顺序。V1 runtime query embedding只服务合法Review Memory/Vector Search，不得查询External Evidence index。未来启用须形成新的pool/API protocol version，并向全部initial seeds提供相同index、query normalization、ranking policy、capability与budget。

## 8. Evaluator Call-Log Blindness

Evaluator 不读取 Candidate evidence-API调用日志、调用次数、token usage或 budget status。Evaluator 输入只包含 paper、anchors/context、role rubric、Candidate Review和 target paper的完整合法 delivered evidence set。

- 调用 external evidence 不产生奖励；
- 不调用不直接处罚；
- 调用次数少不等于遗漏；
- 只有最终 Review相对 admissible evidence 的 material omission、misjudgment、unsupported external claim等质量问题影响 fitness。

API logs仅用于protocol、budget与lineage-capability audit，不得进入 evaluator prompt、fitness、Reflector feedback或winner selection。

## 9. Pending Materialization

Formal-ready 仍需生成并冻结：

- 每个 target paper 的 EvidenceSetManifest；
- 三个 partition 的 item-level EvidenceRecords/raw blobs/projections；
- evidence/query/set IDs 与 aggregate manifests；
- cutoff validation records；
- v1 API implementation/response schema/error registry；
- Candidate call/record/token budget具体整数与 availability budget；
- Evaluator full-delivery context budget；
- delivery framing/context SHA schema；
- paper-level session isolation与 audit-log schema；
- tokenizer-based token-count report；
- empty/unavailable/excluded evidence audit；
- all artifact canonical SHAs and gate results。
