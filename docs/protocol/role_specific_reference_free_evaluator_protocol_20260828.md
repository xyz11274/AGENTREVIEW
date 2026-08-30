# Role-Specific Reference-Free Evaluator Protocol

状态：draft（更新于 2026-08-29）。通用信息边界、聚合公式、Candidate Independence、两阶段 applicability、`0–4 + N/A` 外壳、smoke gate、23 个 subdimension anchors、B-local eligibility/denominator、canonical record/parser/judgment/evidence-support、machine-registry/gate、Evaluator Identity/capacity-gate 与 Frozen External Evidence Pool materialization/API/budget/state-isolation contract 已接受；Evaluator 的具体 manifests/gate results、role-specific threshold 具体整数、生成后的 machine registry/schema/parser artifacts，以及 Evidence Pool 的实际 records/manifests/budget整数尚未冻结，禁止据此启动 formal run。

Shared-pool M★ attempt/failure semantics 见 `docs/protocol/shared_pool_mstar_attempt_and_failure_protocol_20260829.md`。三个memory方法是mandatory initial seeds，共享global candidate pool、softmax parent selection与每role 20个attempt slots；不再要求per-lineage equal evolution budget或lineage champion。两层fitness、canonical Decimal/binary64边界及Program/Candidate identity见 `docs/protocol/canonical_fitness_program_and_candidate_identity_protocol_20260829.md`。

## Authority

唯一 rubric 语义来源是 `AgentReview/REVIEW_Request.md`，当前 SHA256 为 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`。本协议不得从 Human/OpenReview review、CoCo reference、CoCo taxonomy、CoCo Split/Classify 或 CoCo score 反推评分锚点。

## Frozen Common Contract

- 当前允许 Reviewer 与 Evaluator 同为冻结的 `qwen3.8-27b`，formal gate 不得实现 same-model rejection；必须保留可替换 evaluator provider/model 接口，未来更换模型时产生新的 Evaluator Identity，分数不得混合。
- 当前 formal input modality 是 text-only Markdown，不提供 PDF page render 或 figure image pixels；底层模型具备多模态能力不等于本实验获得视觉观测。未来新增 PDF/image input 时必须创建新的 protocol/input identity。
- 每个 `Candidate × paper × role` 使用 fresh session，并执行 Candidate-blind Applicability phase，再执行 Candidate-visible Scoring phase。
- Applicability 不跨 Candidate 缓存；相同 paper×role 的 applicability 不一致时触发 `APPLICABILITY_INCONSISTENCY`，formal run fail-fast。
- 单篇 fitness 是 applicable subdimensions 的等权 macro-average；Candidate fitness 是 evaluable B-local papers 的等权平均。禁止把全部subdimension scores跨paper flatten后平均。
- 全部 subdimensions 为 `N/A` 时 `paper_fitness=N/A`，对所有 Candidate 一致地从分母排除。
- 数值外壳为 `0–4 + N/A`；相同数值不跨 role 解释。
- 评分 polarity-neutral，不以 criticism 数量作为高分条件。
- 保存全部 raw outputs；最多一次相同 generation identity 的 fresh-session retry，第一个合法输出成为 canonical。
- Reflector 只能接收整个 evaluation batch 聚合后的 scores、overall fitness、预注册 failure codes 与 compile/runtime/protocol error。

B-local role eligibility、role-specific membership、最低可评价数量与 denominator audit 的冻结规则见 `docs/protocol/b_local_role_eligibility_and_denominator_protocol_20260828.md`。五个 role 共享同一个 frozen B-local full pool，但分别通过预注册的 deterministic selection rule 冻结 static-25 membership 与 rotating schedule；同一 role 的全部 lineage/method/seed/Candidate 必须共享完全相同的集合和顺序。

## Allowed Evidence Classes

- `PAPER`：论文正文、附录、论文内引用、表格、图、公式、checklist 与 artifact statement。
- `FROZEN_EXTERNAL`：独立 Frozen External Evidence Pool 中的记录；该池不是 Candidate Memory，formal manifest 必须冻结 partition/paper/source/cutoff/provenance/hash/evidence ID。v1 分区为 `PRIOR_WORK`、`POLICY`、`ARTIFACT_LICENSE`，当前无 `PLAGIARISM_OVERLAP`。
- `STRUCTURAL`：无 LLM 确定性提取的 section/offset、anchor map、table/figure/equation 位置以及 bibliography/appendix/artifact/checklist presence。

禁止的 evidence 包括 Human/OpenReview opinion、gold label、CoCo asset/output、Evaluator-only 外部资料、live retrieval 与 formal 期间临时扩充的记录。协议不声称模型内部不会使用 parametric knowledge；约束是 parametric knowledge 不属于 admissible external evidence。Candidate 自报 subcategory、分数和 anchor validity 也不构成证据。

任何会影响正式 score 的 external factual judgment 必须在 Scoring output 中绑定具体 `evidence_id`。没有 frozen evidence ID 时，Evaluator 不得仅凭参数知识对 Candidate 奖励或处罚；相应外部判断必须为 `N/A` 或从该 subdimension 判断中排除。`POLICY` evidence 只能建立规则内容；判定 violation 还必须绑定 paper evidence。当前无 `PLAGIARISM_OVERLAP`，所以缺少确定 overlap/duplicate evidence 时 `POL-PLG=N/A`。

Vector Search、LLM Summarizer、Experience Learner 获得完全相同的只读 evidence API、调用预算与返回规则。每个 Candidate 可针对当前 paper 自主决定是否、何时以及在冻结预算内调用多少次，这一调用策略允许被 Evolution 优化；所有 query、调用次数与返回 evidence IDs 必须记录。v1 返回 canonical records 的原文、原顺序与 provenance，禁止 lineage-specific 改写、摘要、重切片、重嵌入、重排序或派生 evidence store。External evidence 只用于当前 paper Review，不得写入持久 Memory，paper 结束后不保留派生状态。Evaluator 读取目标 paper 的全部 frozen records。只有证据超过预注册 context/token budget 时，才能启用统一、与 lineage 无关且对 Evaluator相同的 deterministic truncation/retrieval policy。

Evidence ID 是规范化 canonical JSON identity 的 SHA256。Identity 至少包含 `pool_version`、`partition`、`target_paper_id`、`canonical_source_identity`、`cutoff` 与 `content_sha256`，不得直接拼接未规范化字符串。Manifest 另存 partition、target paper/family、source URL/ID、venue/year、cutoff、retrieval/snapshot time、MIME/type、content SHA、provenance 和 availability status。Parser 必须验证 ID 属于当前 target paper 的 pool。

`POLICY` 或 `ARTIFACT_LICENSE` 页面在 cutoff 前无法获得时，availability 必须为 `unavailable` 或 `not_archived_before_cutoff`。禁止当前页面替代、live fallback 或以 parametric knowledge 补全；依赖该证据的正式判断为 `N/A`，缺失进入 audit 但不处罚 Candidate。

## Two-Phase Output Contract

Applicability phase 只能输出 role、paper identity、每个合法 subdimension 的 `applicable` 或 `N/A`，以及预注册 event codes。Candidate Review 不得出现在该阶段上下文中；不得输出 weakness、expected criticism、importance 或 ideal answer。

Scoring phase 只能对 Applicability phase 标记为 applicable 的 subdimensions 输出整数 `0–4`、用于审计的合法 anchor IDs 和预注册 event codes。任何自由文本 reasoning 只允许保存在 evaluator-only raw output，不得进入 Reflector feedback。

对于依赖论文外事实的评分，合法 anchor IDs 必须包含对应 Frozen External Evidence Pool 的 `evidence_id`；parser 必须拒绝用无绑定外部事实影响 score 的输出。

Canonical record、strict closed-schema parser 与 registry authority 的冻结 contract 见 `docs/protocol/canonical_evaluator_records_and_parser_protocol_20260828.md`。Formal parser 必须拒绝未知 subdimension、未知 event code、越界分数、applicable 集合不一致和 Candidate 自报 score 的复制。`CanonicalScoringRecord` 必须显式引用对应 `CanonicalApplicabilityRecord` 的 canonical SHA。

## Numeric Shell

以下只是数值外壳，不替代逐 subdimension anchors：

- `0`：对该 subdimension 的 material 内容没有有效评价，或存在足以使该 subdimension 失效的事实/证据错误。
- `1`：评价主要泛化、片面或缺少 paper-grounded 支撑，未覆盖 material 内容。
- `2`：部分正确且有依据，但对 material 内容的覆盖、具体性或判断仍明显不足。
- `3`：对主要 material 内容评价正确、具体并有充分论文依据，仅有次要遗漏。
- `4`：对 material/important 内容的评价准确、具体、充分 grounded 且判断校准良好；不要求更多 criticism，也不奖励穷举。
- `N/A`：该 subdimension 对当前 paper 不可评价或不适用；不进入分母。

每个 subdimension 必须在冻结版中把上述词语具体化，说明何为 material、哪些结论可以由哪些 evidence class 支撑，以及哪些错误触发 `0`。

## 23-Subdimension Inventory

逐项文件统一使用 **subdimension scoring anchors** 或 **role-specific rubric anchors**，不得简称为 Source Anchors。

### QUAL

QUAL 第 1/5 轮已 frozen，见 `docs/protocol/qual_subdimension_scoring_anchors_20260828.md`。

| ID | Frozen rubric focus | External-evidence dependency to resolve |
|---|---|---|
| `QUAL-MET` | 方法、算法、模型或系统设计的技术正确性 | 通常可由 `PAPER`/`FROZEN_EXTERNAL` 判断 |
| `QUAL-EXP` | 数据集、baselines、metrics、ablations 与实验设计是否足以支持 claims | 比较基线可能依赖 `FROZEN_EXTERNAL` |
| `QUAL-REP` | 实现、超参数、数据划分、代码与复现信息是否充分 | 主要由 `PAPER`/`STRUCTURAL` 判断 |
| `QUAL-CMP` | 与相关工作和 state of the art 的比较是否充分 | 只评价 frozen evidence 中可核验的比较；池未覆盖的工作不得用于惩罚 Candidate |
| `QUAL-STA` | 统计检验、方差、不确定性与结论强度是否匹配 | 主要由 `PAPER` 判断 |

### CLAR

CLAR 第 2/5 轮已 frozen，见 `docs/protocol/clar_subdimension_scoring_anchors_20260828.md`。

| ID | Frozen rubric focus | External-evidence dependency to resolve |
|---|---|---|
| `CLAR-WRT` | 结构、行文、术语与算法/流程表达 | `PAPER`/`STRUCTURAL` 足够 |
| `CLAR-NOT` | 符号定义、一致性和数学推导说明 | `PAPER`/`STRUCTURAL` 足够 |
| `CLAR-FIG` | 图表、可视化的清晰度、标注与解释作用 | `PAPER`/`STRUCTURAL` 足够 |

### SIGN

SIGN 第 3/5 轮已 frozen，见 `docs/protocol/sign_subdimension_scoring_anchors_20260828.md`。

| ID | Frozen rubric focus | External-evidence dependency to resolve |
|---|---|---|
| `SIGN-BRD` | 对多个研究方向的潜在广泛影响 | 可由论文 claims 判断，但不得把模型常识当作可验证外部事实 |
| `SIGN-DOM` | 对特定应用领域的重要性和实际效用 | 可能需要 `FROZEN_EXTERNAL`；必须限制推断强度 |
| `SIGN-SOT` | 相对强基线或 state of the art 的改进幅度与稳健性 | PAPER 内 comparative claim 可评分；global SOTA identity 必须有 frozen evidence |
| `SIGN-IMP` | 现实与社会影响、受益/受损群体、风险、公平、环境与可及性 | 主要依据 paper claims，外推必须保守 |

### ORIG

ORIG 第 4/5 轮 Q61–Q65 已冻结，完整 anchors 见 `docs/protocol/orig_subdimension_scoring_anchors_20260828.md`。

| ID | Frozen rubric focus | External-evidence dependency to resolve |
|---|---|---|
| `ORIG-PROB` | 新问题、任务或数据集 formulation | 只能判断相对 Frozen External Evidence Pool 的 originality，不宣称绝对首次 |
| `ORIG-MTH` | 新算法、方法或架构的概念推进 | 只能判断相对 Frozen External Evidence Pool 的 originality，不宣称绝对首次 |
| `ORIG-ANL` | 新理论分析、洞见或现象解释 | 只能判断相对 Frozen External Evidence Pool 的 originality，不宣称绝对首次 |
| `ORIG-EXP` | 新数据、benchmark、实验设置或评价协议 | 只能判断相对 Frozen External Evidence Pool 的 originality，不宣称绝对首次 |
| `ORIG-COM` | 已有技术组合是否产生有理由的新能力 | 只能判断相对 Frozen External Evidence Pool 的 originality，不宣称绝对首次 |
| `ORIG-NEG` | 负面结果或批判性分析是否严谨并产生新认识 | 严谨性由 `PAPER` 判断，originality 只相对 frozen evidence 判断 |

### POL

POL 第 5/5 轮 Q66–Q70 已冻结，完整 anchors 见 `docs/protocol/pol_subdimension_scoring_anchors_20260828.md`。

| ID | Frozen rubric focus | External-evidence dependency to resolve |
|---|---|---|
| `POL-ETH` | 伦理、fairness、bias、harm、dual use 与 mitigation | 主要依据论文披露；不得虚构法律或政策义务 |
| `POL-DAT` | consent、PII、privacy、data licence 与允许用途 | 需要论文声明和 frozen policy/licence evidence |
| `POL-ANO` | submission 中的作者、机构、致谢或 URL 泄露 | `PAPER`/`STRUCTURAL` 足够 |
| `POL-PLG` | plagiarism、self-plagiarism 或 dual submission | 无确定性 frozen overlap/duplicate evidence 时为 `N/A`；parametric knowledge 不是 admissible evidence |
| `POL-IMP` | broader-impact/checklist、IRB/ethics approval、software/data licence 与合规证据 | 论文声明与 frozen policy/licence evidence |

## Blocking Decisions Before Freeze

1. 按 `docs/protocol/evaluator_identity_and_capacity_gate_protocol_20260829.md` 生成当前 `qwen3.8-27b` Evaluator 的具体 semantic/runtime manifests、artifact hashes、request JSON、capacity choice 与全部 gate 结果；未来外部 API 只是新的 Evaluator Identity，不是当前 formal 前置条件。
2. 按 `docs/protocol/frozen_external_evidence_pool_materialization_protocol_20260829.md` 生成三个分区的实际 raw/projected records、item/set manifests、cutoff audits、API/budget identity、token-count preflight 与 canonical SHAs。
3. 五个 role 各自的 `min_static_evaluable`、`min_full_pool_evaluable` 具体整数、制定依据、selection seed/hash 与 role-specific static/rotating manifests。
4. 五个 role 的完整 object/requirement/entity registries、POL machine ownership map、完整 JSON Schema 文件、parser/serializer/validator version，以及 evaluator/N/A/evidence-limitation/orchestrator registries 的机器可读 artifacts。
5. 按 `docs/protocol/canonical_fitness_program_and_candidate_identity_protocol_20260829.md` 与 `docs/protocol/formal_controller_journal_and_recovery_protocol_20260829.md` 物化已冻结的Decimal、softmax ordering/binary64、Program/Candidate identity、dependency closure、append-only journal与birth/selection transaction schemas，补齐具体Python/math/PRNG/dependency manifests、durability gates及全部expected replay vectors。
6. 按 `docs/protocol/final_suite_sealing_and_official_coco_authorization_protocol_20260829.md` 物化suite controller/journal、五winner integrity checks、C/Human/CoCo access-control gate、permanent seal与expected denial/recovery vectors。
7. 按 `docs/protocol/shared_qwen_scheduler_and_parallel_pipeline_protocol_20260829.md` 预注册mixed-workload manifest、within-capacity stability标准、logical request/scheduler identity、paper DAG、active-paper window与CoCo/Embedding resource gates。
8. 按 `docs/protocol/eight_rtx4090_resource_topology_protocol_20260829.md` 物化已冻结的8×24GB RTX4090 TP4、GPU/NUMA assignment、context/KV capacity、Embedding/CoCo phase topology、exclusive-resource gate与resource-transition journal，生成concrete `max-model-len`、process allowlist、cpuset与gate outputs。
9. 按 `docs/protocol/qwen_request_transaction_and_recovery_protocol_20260829.md` 物化request/request-attempt schemas、raw-response-first storage、timeout table、backend restart、queue backpressure、endpoint isolation与resource contamination recovery gates。
10. 按 `docs/protocol/full_review_corpus_and_retrieval_projection_protocol_20260829.md` 物化A区125×5 no-memory Corpus build journal、canonical Full Review schema、strict capability denial、subdimension Retrieval Projection、exact-flat embedding index、Review Memory budgets、ledger/manifest与commit barriers。
11. 按 `docs/protocol/formal_memory_seed_and_read_mode_protocol_20260829.md` 发布三个v2 seed Programs，物化closed `memory_read_mode`、least-privilege sandbox、paper isolation、atomic budget与single-Summary gates；旧seed artifacts/fitness不得混入。
