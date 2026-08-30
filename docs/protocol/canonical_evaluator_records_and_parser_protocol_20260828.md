# Canonical Evaluator Records and Parser Protocol

状态：contract frozen（2026-08-28），Q76–Q100 的 record/parser/judgment/evidence-support contract 与 machine-registry/gate 构建规则已接受。各 role 的完整 machine-readable registry artifacts、JSON Schema、serializer/parser artifacts 仍未生成，禁止据此启动 formal run。

## 1. Three Canonical Records

每个正式 `Candidate × paper × role` evaluation 分别保存三个相互绑定、不可替代的 records。

### CanonicalApplicabilityRecord

保持 Candidate-blind，不得包含 Candidate ID、review SHA、Candidate review text、Candidate memory identity 或任何 Candidate-specific field。至少绑定：

- schema、serializer、parser、protocol 与 Evaluator Identity SHA；
- role、paper ID、family ID、stage 与 execution identity；
- paper input、Source Anchor map、STRUCTURAL context 与 external-evidence manifest SHA；
- raw attempt IDs、canonical attempt ID 与 retry provenance；
- role-fixed canonical applicability vector 与 N/A reason codes。

### CanonicalScoringRecord

只评分对应 ApplicabilityRecord 中 applicable 的 subdimensions，并且必须显式保存 `canonical_applicability_sha256`。至少绑定：

- Candidate canonical ID 与 Candidate Review SHA；
- 与 ApplicabilityRecord 相同的 role/paper/family/input/protocol/Evaluator Identity identity；
- raw attempt IDs、canonical attempt ID 与 retry provenance；
- role-fixed canonical score vector、atomic evaluator events、paper anchors 与 external evidence IDs。
- 与 applicability 分离的 scoring-level `evidence_limitations`。

Parser 必须验证 `canonical_applicability_sha256` 存在、内容可解析、identity 完全一致且 applicability vector 与 scoring vector 相容；错配直接 schema/protocol failure。

### CanonicalPaperEvaluationRecord

由 deterministic parser/aggregator 产生，不由 LLM 直接输出。至少绑定：

- `canonical_applicability_sha256` 与 `canonical_scoring_sha256`；
- role、paper/family、Candidate、stage 与 Evaluator Identity；
- 完整 applicability/score vector；
- 原子 events、派生 decisions 与 validity result；
- `paper_fitness`（number 或 `N_A`）、paper exclusion status 与 denominator contribution；
- deterministic aggregation/version identity。

只保存最终平均 fitness 不满足审计要求。

## 2. Role-Fixed Vector and Strict Types

每个 role 的所有合法 subdimensions 必须完整出现，并由冻结 serializer 按 role-specific frozen order canonicalize。Parser 不依赖 raw model output 的 object-key order。

- applicability enum 只能为 `APPLICABLE` 或 `N_A`；
- `APPLICABLE` 对应 score 必须是 JSON integer `0`、`1`、`2`、`3` 或 `4`；
- `N_A` 对应 score 必须是 JSON `null`；
- 禁止缺项、额外项、未知 subdimension、重复 ID、float、string-number、boolean-number 或任何隐式 coercion；
- ApplicabilityRecord 不含 score；ScoringRecord 必须形成与 ApplicabilityRecord 完全一致的 role-fixed vector。

Canonical serializer 冻结 key order、array order、string/Unicode normalization、number encoding 与 duplicate handling；canonical bytes 计算 SHA256。Raw 字段顺序不影响 canonical identity。

### Applicability Decision Projection

完整 ApplicabilityRecord 包含 execution provenance，因此不同 fresh sessions 的 record SHA 不应相同。另行计算 `applicability_decision_sha256`，其 projection 只包含：

- role、paper 与 protocol identity；
- role-fixed applicability vector；
- 每个 `N_A` 的唯一 primary reason code 与排序去重的 availability detail codes；
- 允许的 applicability anchor/entity IDs。

Projection 明确排除 session、attempt、timestamp、Candidate、review、runtime metadata 和 scoring-level `evidence_limitations`。同一 `paper × role` 的全部 Candidate 必须具有完全相同的 `applicability_decision_sha256`；否则触发 `APPLICABILITY_INCONSISTENCY`，整个 role formal run fail-fast。

## 3. Strict Closed-Schema Parser

Raw evaluator output 必须自身形成唯一一个合法 schema object。Parser：

- 拒绝未知字段、未知 enum 和缺失 required field；
- 拒绝 prose/code fence 包裹、多个 JSON objects 与尾随文本；
- 不执行 regex repair、code-fence stripping、字段补全、numeric coercion、默认值注入或 best-effort parsing；
- 不因输出看似“语义合理”而绕过 schema；
- 保存每个 raw attempt 的 bytes/hash、provider request identity、finish reason、usage 与 error provenance。

第一次非法时，只允许按 Q29 在完全相同 frozen generation identity 下启动一次 fresh-session retry。第一个完全合法 raw object 成为 canonical；两次均非法则整个 role formal run fail-fast，非法输出不记 `0`。

Provider 若返回独立 reasoning channel，可单独原样归档，但 reasoning 不进入 canonical decisions、fitness、Reflector 或 retry selection。

## 4. Frozen Two-Level Identity

Evaluator atomic event 必须引用 canonical judgment object。采用两层 identity：

- `judgment_id = SHA256(canonical judgment object)`，表示 Candidate-independent underlying judgment object；
- `event_id = SHA256(canonical event identity)`，表示该 object 在一个具体 Candidate evaluation 中发生的原子事件。

Candidate ID、Candidate Review SHA 和 evaluation identity 不进入 `judgment_id`，但必须进入 `event_id` 与 ScoringRecord。不同 Candidate 对同一 underlying problem 可共享 `judgment_id`，其 evaluation events 仍彼此独立。

仅对 event fields 或自由文本描述直接计算 SHA 不足以保证语义去重：同一 primary omission 可能在不同 subdimension、以不同措辞或不同 supporting evidence 被重复表达。`judgment_id` 不由模型自由命名，而由 parser 计算。

## 5. Canonical Judgment Object

Canonical judgment object 只包含以下受控 identity fields：

```json
{
  "role": "POL",
  "object_class": "COMPLIANCE_OMISSION",
  "requirement_key": "DATA_CONSENT",
  "target_entity_ids": ["..."],
  "governing_obligation_ids": ["..."]
}
```

- `role` 是冻结 role enum；
- `object_class` 来自 role-specific closed registry；
- `requirement_key` 来自 role-specific closed registry；
- `target_entity_ids` 来自 Candidate-independent deterministic Paper Evaluation Context entity registry；
- `governing_obligation_ids` 是真正定义当前义务身份的 frozen policy/licence rule IDs。

自由文本、subdimension、event code、score、Candidate ID、review SHA、evaluation identity 和 evaluator reasoning 禁止进入 judgment-object identity。所有 array 在 canonicalization 前排序并去重。

### Target Entity Registry

Entity registry 只描述可观察对象身份，不包含 expected weakness、合规结论或 evaluator judgment。允许的实体来源包括：

- `paper:<paper_id>`；
- 明确 dataset/data-source entity；
- artifact/code repository entity；
- claim、section、table、figure、equation 或其他 Source Anchor entity；
- policy、checklist、approval 或 licence manifest entity。

Entity ID 使用冻结规则，根据 paper ID、entity type、规范化显式名称、source span/anchor 或 frozen manifest identity 确定性生成。不存在正文 anchor 的 missing-document judgment 使用 `paper_id + requirement_key + affected entity IDs` 建立对象身份，不得要求预生成 expected omission。

### Governing Identity vs Supporting Evidence

真正定义义务身份的 frozen policy/licence rule IDs 进入 `governing_obligation_ids`，并参与 `judgment_id`。普通 `paper_anchor_ids` 与 supporting `external_evidence_ids` 不进入 `judgment_id`，避免同一问题因引用不同证据被拆成多个 objects。

Supporting evidence 仍必须：

- 验证属于当前 target paper/allowed pool；
- 分别排序和去重；
- 进入 canonical event/record bytes；
- 进入 `event_id` identity；
- 完整保留用于审计。

## 6. POL Ownership Resolver

`requirement_key` 必须由 closed machine registry 确定性映射到唯一 primary POL subdimension；Evaluator 不拥有自由选择 ownership 的权限。已冻结的 ownership families 为：

| Requirement family | Primary subdimension |
|---|---|
| `ETHICAL_RISK / FAIRNESS / BIAS / HARM / DUAL_USE / MITIGATION` | `POL-ETH` |
| `DATA_CONSENT / DATA_PRIVACY / DATA_LICENSE / PERMITTED_USE` | `POL-DAT` |
| `ANONYMITY_DISCLOSURE` | `POL-ANO` |
| `PLAGIARISM / MATERIAL_REUSE / DUAL_SUBMISSION` | `POL-PLG` |
| `IMPACT_STATEMENT / CHECKLIST / APPROVAL_IDENTIFIER / LICENSE_IDENTIFIER_DOCUMENTATION` | `POL-IMP` |

正式 parser/validator 执行：

- event 挂到非 primary subdimension：protocol failure；
- 同一 `judgment_id` 被分配到多个 subdimensions：protocol failure，不静默去重；
- 同一 object 产生多个 event codes：全部保留审计，但作为同一个 underlying issue 影响质量，不形成重复累加 penalty；
- `SYSTEMATIC_HALLUCINATION` 的独立 fabricated-claim 数量按不同 `judgment_id` 计算；
- 一个 fabricated core evidence 支撑多个 judgments 时，按共享 canonical evidence-object identity 识别。

## 7. Three Separate Registries

### Evaluator Atomic Judgment Events

LLM Evaluator 只能输出原子、当前 evidence-level 的 judgment events，例如：

- `MATERIAL_OMISSION`
- `MATERIAL_MISJUDGMENT`
- `FABRICATED_MATERIAL_CLAIM`
- `INVALID_PAPER_EVIDENCE`：anchor 存在，但语义上不支持 Candidate judgment
- `UNBOUND_EXTERNAL_FACT`
- `ROLE_LEAKAGE_LOCAL`
- `ROLE_LEAKAGE_SEVERE`

最终机器可读 registry 必须冻结每个 code 的允许字段、允许 subdimensions、materiality 和 score consequence。Evaluator 不得直接输出最终 validity、paper-level zero、referential-integrity 结论或 retry eligibility。

### Deterministic Referential-Integrity Events

下列由 validator 对 canonical IDs 与 frozen manifests 机械验证后产生，不由 LLM 输出：

- `INVALID_PAPER_ANCHOR_ID`：anchor 不存在或不属于当前 target paper/input identity；
- `INVALID_EXTERNAL_EVIDENCE_ID`：evidence ID 不存在，或 paper/partition/cutoff/pool scope 不合法。

Raw JSON 可以结构合法但含 referentially invalid ID。这不是 schema retry 条件。Validator 先记录 event，并将非法 ID 从 admissible support set 排除；随后由冻结 rubric/aggregator 根据 judgment materiality、剩余合法证据与 support rule 决定 score consequence。非法 ID 不无条件机械地令整个 subdimension 为 `0`；若一个核心 material subdimension judgment 完全依赖被排除的证据、因而没有任何 admissible support，则可确定性归 `0`。

### Applicability / N/A Reasons

N/A reasons 使用独立 closed registry，不与评分错误混合。每个 `N_A` 必须有唯一 primary reason code，可附加排序去重的 availability detail codes。已接受的 primary registry 为：

- `ROLE_NOT_APPLICABLE`
- `INPUT_MODALITY_UNOBSERVABLE`
- `REQUIRED_EXTERNAL_EVIDENCE_UNAVAILABLE`
- `POLICY_UNAVAILABLE`
- `NOT_ARCHIVED_BEFORE_CUTOFF`
- `NO_AUDITED_OVERLAP_EVIDENCE`

`ROLE_NOT_APPLICABLE` 表示触发对象本身不存在；其余 codes 表示当前不可观察或所需 frozen evidence 不可获得。`APPLICABLE` 不得携带 N/A reason。输入损坏、论文不完整、manifest 错误或数据 identity 不一致属于 data/protocol failure，不得伪装成 `N_A`。

### Scoring-Level Evidence Limitations

若 external evidence 只限制 subdimension 的部分论文外判断，而 `PAPER` 本身仍足以正式评分，则该 subdimension 保持 `APPLICABLE`。被排除的 external judgment 记录在 ScoringRecord 的 `evidence_limitations` 中，至少包含受控 `requirement_key`、partition、availability/detail code 与 `formal_judgment="EXCLUDED"`。

Evidence limitation：

- 不属于 N/A reason；
- 不属于 Candidate event/error；
- 不形成 penalty；
- 不进入 `applicability_decision_sha256`；
- 必须排序去重并进入 ScoringRecord canonicalization；
- 用于审计 global SOTA、external comparison completeness、global novelty status 等被排除的外部判断。

证据状态使用两个独立 identities：

- Candidate-independent `evidence_availability_sha256`：只表示当前 target paper、cutoff、frozen pool、role 与 requirement registry 下客观可用的 evidence/requirement 状态；同一 paper×role 的所有 Candidate 必须一致。
- Candidate-specific `evidence_limitation_sha256`：只表示当前 Candidate 实际提出的 external judgments 中，哪些因证据不可用而从正式评分判断中排除；允许 Candidate 间不同。

二者均不得进入 `applicability_decision_sha256`。Availability projection 不得包含 Candidate/review identity 或 expected judgment；limitation projection 必须引用对应 Candidate judgment IDs 与 availability identity。

### Deterministic Parser/Aggregator/Orchestrator Decisions

下列不是 LLM 自由判断：

- `DUPLICATE_PADDING`：由冻结 deterministic duplicate normalizer 派生；
- `SYSTEMATIC_HALLUCINATION`：由 atomic fabrication events 根据 Q30 阈值派生；
- `APPLICABILITY_INCONSISTENCY`：由跨 Candidate canonical map 比较派生；
- `NO_EVALUABLE_ROTATING_PAPERS`：由 batch denominator 状态派生；
- `INVALID_JSON`、`SCHEMA_FAILURE`、`TRUNCATED_OUTPUT` 与 retry eligibility：由 parser/orchestrator 派生；
- subdimension zero、paper-level zero、`paper_fitness=N_A` 与 final validity：由冻结 aggregator rules 派生。

其中 `SYSTEMATIC_HALLUCINATION` 的已冻结触发条件是：至少两个彼此独立的 material fabricated claims；fabrication 跨至少两个 subdimensions；或一个 fabricated core evidence object 被用于支撑多个评价 judgments。Evaluator 只输出原子 `FABRICATED_MATERIAL_CLAIM`；parser/aggregator 根据 canonical judgment/evidence identities 计算上述条件。

Structural invalidity 与 substantive evidence failure 严格分离。只有非法 JSON/schema/type/enum、截断等 Q29 infrastructure conditions 允许 retry。结构合法但 evidence/anchor ID referentially invalid 的输出不得 retry，也不得通过重生成修复。`MATERIAL_OMISSION` 与 `MATERIAL_MISJUDGMENT` 按 role-specific `0–4` anchors 评价，不自动归 `0`。

## 8. Admissible-Support Policy

每个 closed-registry requirement 必须在 machine registry 中预先绑定 deterministic admissible-support policy。Mapping identity 必须显式包含 `role + requirement_key + registry_version`，避免不同 role 中同名 requirement 产生歧义。允许的 policy enums 为：

- `PAPER_REQUIRED`
- `EXTERNAL_REQUIRED`
- `PAPER_AND_EXTERNAL_REQUIRED`
- `PAPER_OR_EXTERNAL`

Evaluator 不自由选择 support policy。Validator 只机械检查剩余合法 IDs 是否满足 required evidence classes；合法 evidence 内容是否真正支持 judgment，仍由 Evaluator 做语义判断。

结构合法的 record 引用非法 anchor/evidence ID 时：

1. 不 retry，不把非法 ID 当 admissible evidence；
2. Validator 产生 referential-integrity event；
3. 从该 judgment 的 admissible support set 中移除非法 ID；
4. 若 support policy 仍满足，只保留 invalid-ID event，不机械改变 score；
5. 若 support policy 不满足且 `judgment_impact=CORE`，派生 `MATERIAL_JUDGMENT_UNSUPPORTED`，affected subdimension=`0`；
6. 若 support policy 不满足且 `judgment_impact=SUPPORTING`，由原 role-specific 0–4 anchor 表达局部影响，不新增 penalty、不自动归零。

非法 ID 只证明证据不可采纳，不自动证明事实 fabricated。已经由 Evaluator 语义确认的 `FABRICATED_MATERIAL_CLAIM` 继续按 Q30 令 affected subdimension=`0`。

## 9. Judgment Impact

每个 material judgment 使用冻结 enum：

- `CORE`：仅当移除该 judgment 后，会使该 subdimension 的主要结论失去成立所必需的依据，或使主要结论失去全部 admissible grounding；
- `SUPPORTING`：一般增强、例证、局部支持，或只影响主要结论置信程度的 judgment。

`judgment_impact` 是语义判断，由 Evaluator 输出并纳入冻结 prompt、schema 与 smoke consistency gate。Aggregator 不重新自由判断 impact，只执行上节的确定性 consequence。不得把“material”泛化为 `CORE`；普通 material issue 可以是 `SUPPORTING`。

## 10. Canonical Evidence Object and Reuse

为检测同一 fabricated core evidence 支撑多个 judgments，建立 Candidate-wording-independent `evidence_object_id`：

`evidence_object_id = SHA256(canonical(role, evidence_object_class, controlled_fact_key, target_entity_ids))`

Identity 只包含 closed-registry controlled fact identity、deterministic target entities 与 evidence-object class；禁止 Candidate 自由文本、event wording、subdimension、score、Candidate/review/evaluation identity。每个 fabrication event 保存 `evidence_object_id` 与排序去重的 `supported_judgment_ids`。

若同一合法 canonical `evidence_object_id` 支撑两个或以上不同 `judgment_id`，Aggregator 按 Q30 确定性派生 `SYSTEMATIC_HALLUCINATION`，令 `paper_fitness=0`。

保守边界：若 fabricated claim 无法通过 closed registry 和 deterministic entity identity 唯一规范化，不得猜测它与另一 claim 属于同一 evidence object，也不得为满足规则而强行生成共享 ID。该 claim 仍保留独立 `FABRICATED_MATERIAL_CLAIM` event，并可通过 Q30 的其他条件——至少两个独立 material fabrications，或 fabrication 跨至少两个 subdimensions——触发 systematic hallucination。

## 11. Remaining Formal Artifacts

Machine registry 的 namespace、构建信息边界、无 fallback、保守 entity merge、versioning 与 formal gate contract 已冻结，详见 `docs/protocol/machine_registry_and_gate_protocol_20260828.md`。仍需生成：

1. 五个 role 的完整 `object_class` 与 `requirement_key` closed registry artifacts。
2. Paper Evaluation Context entity-registry schema、normalization/version 与 deterministic ID test vectors。
3. POL 每个具体 `requirement_key` 到 primary subdimension 的完整 machine-readable mapping。
4. 每个 role+requirement 的 admissible-support policy、允许的 judgment impact 与 evidence-object class/controlled-fact registries。
5. Atomic/derived/runtime registry 的最终字段约束与所有 consequence test vectors。
6. 完整 JSON Schema、canonical serializer/parser/validator versions、test vectors 与 SHA identities。
