# Canonical Fitness, Program, and Candidate Identity Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen semantic contract（2026-08-30），Q126–Q140与Q230–Q527已接受。两层fitness、Canonical Decimal、M★ binary64 softmax、Program/Candidate identity与research-observable Query Embedding bindings属于CORE_V1；完整audit/recovery identities继续保留为HARDENING_V2目标。具体Profile见 `research_validity_core_v1_and_audit_hardening_v2_profile_20260830.md`。CORE_V1 canonical manifests/schema/gate未物化前不得启动formal run；HARDENING_V2未完成不阻塞首次Core。

## 1. Two-Level Fitness Aggregation

每个 `Candidate × paper × role` 的 fitness 严格按两层等权平均计算：

1. 在单篇 paper 内，对该 role 的所有 `APPLICABLE` subdimension integer scores 求等权平均，得到 `paper_fitness`；
2. 在 Candidate 层，对全部 evaluable papers 的 `paper_fitness` 求等权平均，得到 `candidate_fitness`。

禁止将不同 paper 的全部 applicable subdimension scores flatten 后重新平均。这样，每篇 evaluable paper 在 Candidate fitness 中权重相同，不因其 applicable subdimension 数量更多而获得更大权重。

全部 subdimensions 为 `N_A` 的 paper，其 `paper_fitness=N_A`，并从 Candidate denominator 排除。由 validity/aggregator 规则产生的 paper-level zero 是合法数值 `0`，必须进入 Candidate denominator。

Canonical records 必须保留足以无歧义重算两层平均的来源，包括：

- 固定顺序的 applicable score vector；
- 每篇 paper 的 integer numerator、applicable-subdimension denominator 与 `paper_fitness`；
- Candidate 的 evaluable paper IDs、paper-fitness vector、paper denominator 与 `candidate_fitness`；
- `N_A` exclusion set、paper-level-zero events 与对应 provenance。

## 2. Canonical Decimal for Ranking

Formal static-25 finalist membership 与 full-pool winner selection直接使用由以下冻结规则生成的 canonical fixed-precision Decimal：

- Decimal arithmetic context precision固定为50位有效数字；
- canonical scale固定为小数点后12位；
- rounding固定为 `ROUND_HALF_EVEN`；
- Canonical JSON fitness必须是固定长度十进制字符串，例如 `"2.733333333333"`；
- 禁止exponent form、negative zero与任何binary-float-to-Decimal conversion；
- 合法范围固定为 `[0.000000000000, 4.000000000000]`，越界属于protocol/data failure。

- `paper_fitness_decimal` 必须直接从该paper的exact integer numerator/denominator独立quantize；
- Candidate fitness必须基于所有未舍入paper rational values完成两层等权聚合，再且仅在最终Candidate fitness上执行一次canonical Decimal quantization；
- 禁止平均已经舍入的paper Decimal，避免double rounding；
- canonical Decimal 必须由上一节的两层聚合来源确定性生成；
- 不得从 platform float 或其字符串表示反向生成 canonical Decimal；
- tie 由 canonical Decimal 的严格相等判定；两个不同exact fitness若量化为相同Decimal，正式ranking中仍视为同分，不回退比较exact rational；
- finalist/winner ordering 不要求 exact-rational cross multiplication；
- Candidate ID 是冻结的次级 tie-break；
- canonical numerator/denominator 继续作为审计和重算来源，但不自动取代已冻结的 Decimal ranking key。

Formal code 不得使用 Python 默认 `float` 比较、平台默认 repr 或未冻结的 Decimal context 决定 finalist/winner。

## 3. Binary64 Boundary for M★ Parent Softmax

Shared-pool parent selection保留当前 M★ IEEE-754 binary64 执行语义，不改为 Decimal softmax。Eligible vector顺序固定为：

1. initial seeds按 `Vector Search → LLM Summarizer → Experience Learner`；
2. 成功admission的children按global attempt admission顺序追加；
3. duplicate与failed attempts不进入vector。

不得为了canonicalization改为Candidate-ID排序。其余冻结边界包含：

- `tau=0.15`；
- canonical Decimal固定字符串通过冻结Python `float()` 转为IEEE-754 binary64，禁止使用其他中间float来源；
- eligible candidate vector及上述插入顺序；
- CPython/Python、`math`、PRNG algorithm与相关runtime identity；
- formal run seed以及每轮抽样前后的完整 RNG state；
- 每轮candidate vector、canonical Decimal、转换后binary64的 `float.hex()`、logits、raw weights、normalized probabilities与chosen parent；
- parent-selection commit record及其 canonical SHA。

执行路径固定为 `fitness / 0.15`、减去最大 logit、`math.exp` 生成raw weights，再由 `random.Random.choices` 抽样。Normalized probabilities用于完整审计，但不得替代实际提交给冻结抽样实现的raw weights。Formal manifest 必须精确冻结Python、`math`、`random.Random`及最终实现，不能只写“softmax”。

一轮 parent selection 一旦提交，resume/checkpoint replay 必须读取 committed selection，不得重新计算或再次消耗 RNG。尚未提交完成的 selection 只能从冻结的 pre-selection RNG state 以完全相同 identity 重放。

## 4. Program Identity

每个 Program 使用冻结 canonical JSON serializer构建完整Program manifest，并计算不截断的64位小写 `program_identity_sha256`。Identity object显式包含domain-separated `identity_type`与schema version。每个代码或behavior asset记录logical asset key、media type、byte length与SHA256；语义有序数组保持冻结顺序，无序集合确定性排序去重。Identity 至少绑定：

- exact Program/code bytes及其逐文件 identity；
- MemoryProgram schema与behavior assets；
- formal memory seed/read-mode protocol identity与closed `memory_read_mode`；
- 内嵌 prompt、instruction和behavior-relevant config；
- allowed toolkit、Frozen Evidence API capability与其他可调用能力；
- Review Memory API capability，以及是否具有受限post-retrieval LLM Summarizer capability；具有时绑定其prompt/config/request-budget identity；
- paper-isolation、RetrievedMemoryLedger/RetrievalResultManifest schema、atomic budget与Summary input/output budget policy identities；
- CanonicalReviewerMemoryContext authoritative payload/envelope schema、domain-separated non-self-hash serializer、Reviewer-visible projection identity、mode-specific formatter/framing与prompt injection identity；
- StructuredViewItem `viewitem_` identity schema/serializer、五项source-content-kind/mode legality/anchor mapping、coverage validator及统一`max_reviewer_memory_context_tokens` derivation/token-accounting/safety-reserve manifest identity；
- CanonicalSummarizerInputRecord `suminput_` authoritative schema/serializer、SummarizerVisibleInputProjection SHA、`memqry_`/`memqocc_`/`qemb_` identities、byte-preserving query validation、Embedding observed-query SHA handshake、one-memqocc/one-request与cache-hit resolution、batch/item raw-first projection、VALIDATED barrier、仅由tensor position/qemb/canonical QueryEmbeddingRecord items构成且排除全部execution bindings的`qembbatch_` pure-content identity、same-content independent self-contained Journal commits/exact-event projection/non-authoritative dedup observation、RFC4648 padded Base64 vector bytes/no-compression/tensor ordering/padding exclusion/strict decode identity、max-event/durability gate、`matatt_` bounded state machine/event-before-success recovery/cutoff→recovered→retry order/noncausal paused-owner recovery/actual-dispatch accounting、temporary/orphan non-promotion、role-terminal idempotent projection与cross-role projection independence、content-qemb/execution-chain separation、role-local sequence/FIFO/recovery reassembly、`EMBEDDING_SERVICE_EPOCH_COMMITTED` durable-only no-gap lifecycle、request actual-dispatch no-fault-exemption/one-recovery allowance、owning-role-only third-epoch failure/next-role cursor/sealed-old-run `ROLE_RUN_SCHEDULER_ADMITTED`/suite-level one-shot compatibility lifecycle barrier、Scheduler Journal cutoff、padding exclusion、paper-terminal cache、independent scheduler/batch-wait、real-vector byte stability、dual-tokenizer accounting、length-prefixed framing、chat layout及query/Summary token gates；
- registry与dependency lock identity；
- Reviewer中会改变Program行为的model/config identity；
- canonical manifest/schema version与serializer identity。

以下字段不得进入 Program identity：lineage、parent、attempt、Candidate ID、fitness、Evaluator Identity/output/result。绝对路径、mtime、PID、port、hostname、GPU ordinal以及CUDA/driver/GPU/max-num-seqs等纯执行环境只作runtime/execution provenance，不进入Program identity；只有正式定义为Program行为输入的资产才允许进入。

Dedup 只比较完整 `program_identity_sha256`。不做 AST normalization、embedding/semantic similarity或近似代码去重；即使只存在空白、注释或格式差异，只要 exact bytes 不同，也视为不同 Program。Formal Program只能加载manifest中声明的behavior dependency closure；undeclared import/file/environment/network behavior按Candidate-originated protocol failure处理，不得事后补manifest。

Initial seed birth ordering继续使用canonical keys `vector_search → llm_summarizer → experience_learner`，但新formal必须使用三个v2 code/assets与全新Program identities。每个Program必须声明唯一`memory_read_mode ∈ {DIRECT_RETRIEVAL, POST_RETRIEVAL_LLM_SUMMARY, STRUCTURED_EXPERIENCE_RECALL}`。Mode变化必然产生新Program identity并重新执行capability/compile/runtime gates；旧seed identity、fitness、checkpoint与Memory snapshot不得混入新formal。

## 5. Immutable Candidate Birth Identity

`canonical_candidate_id = "cand_" + full_lowercase_sha256(canonical_birth_identity)`，绑定immutable birth event。Canonical birth identity显式包含domain-separated `identity_type`与schema version，并由以下受控字段确定性生成：

- role；
- formal run seed；
- birth kind；
- birth slot；
- parent candidate ID；
- `program_identity_sha256`；
- controller protocol identity。

Initial seeds使用冻结的 seed key/birth slot，并显式写入JSON `parent_candidate_id: null`；children使用global evolution attempt index作为birth slot。Candidate ID使用完整hash且是deterministic ordering的正式键。Candidate ID在birth record首次提交时即固定，resume/replay必须保持完全一致；不得依据后续score、成功/失败状态、pool admission或ranking重新编号。

每个child attempt必须遵守以下transaction顺序：

1. 提交immutable `attempt_id/global_attempt_index`；
2. 原子提交parent selection、chosen parent与selection前后RNG state；
3. 执行rotating evaluation、Reflector mutation与compile/fix；
4. compile/fix成功后生成最终Program manifest；
5. 计算并提交immutable Candidate birth record与Candidate ID；
6. 执行admission-time Program dedup；
7. 仅非重复Candidate执行static-25 canonical evaluation并决定pool admission。

Compile前失败只有attempt provenance，不产生Candidate birth。Compile成功但Program duplicate仍产生Candidate birth record与Candidate ID，但不得进入pool或获得softmax probability mass。已经提交的parent selection在resume/replay时不得重新选择。

Lineage provenance可以由birth chain派生或另存，但不得改变Candidate identity。

## 6. Admission-Time Strict Dedup

Child在compile/runtime gate通过后、static-25 evaluation开始前执行strict Program dedup：

- 若shared pool尚无相同 `program_identity_sha256`，按正常流程进入static-25 evaluation；
- 若已有完全相同Program，记录 `DUPLICATE_PROGRAM_MUTATION`；
- duplicate attempt正常消耗一个global evolution attempt slot；
- 不重复执行static-25 evaluation，不新增eligible pool entry，也不增加该Program的softmax probability mass；
- parent、attempt、seed-lineage、duplicate target与Program manifest provenance全部保留。

三个mandatory initial seeds在正式初始化时若发生严格Program identity重复，initialization gate直接fail-fast；不得以共享一个pool entry、复制seed或临时更换Program规避。

Finalist阶段继续执行strict dedup，作为admission gate之外的防御性完整性检查；若其结果与admission audit不一致，视为controller/protocol failure，而不是静默修复pool。

## 7. Materialization, Catalog, and Compatibility Identities

Q308–Q513冻结额外domain-separated完整SHA256 identities。顶层以`protocolroot_ / protocolrootval_ PASS`授权`formalroot_`；Root/finfra/audit identities沿用Q500。Protected Official使用`opiprun_ / opipatt_ / opipevidence_ / officialinputdelivery_`。Reporting增加`officialreporteventreg_ / officialreportmirror_ / officialreportmirrorverify_`、`reportctrlreq_ / reportingholdreq_ / reportingholdpending_`、`reportreplacement_`。Publication使用`officialpubrun_ / officialpubcand_`、纯content `officialpubcontent_`与authority `officialpubartifact_`。Post-terminal archive/delivery使用external `rootarchiveatt_ / externaldelivery_`，不进入Formal identity。Temporary path、PID、timestamp等provenance不得进入pure semantic identities。

Q514–Q527增加`protocolvalrun_ / protocolvalatt_ / protocolvalevidence_ / protocolbootstrap_`并冻结Protocol Root canonical ordering、benchmark-independent validation与双Profile标记。CORE_V1只要求影响research semantics、protected access、minimal lineage与fail-stop的identities；Reporting ingress/seal、formal publication attempts/replicas与archive identities保留为HARDENING_V2。Profile标记不改变identity domain定义，也不得让同一formalroot混用不同Protocol/Profile manifests。

Compatibility Plan绑定唯一target、direct comparisons及PASS catvals。Quiescence commit slot与Catalog ranges避免identity cycle；Readiness/Access epochs由独立infrastructure journals授权。Export candidate、Access PASS和promotion三层预算分离。Replay可信Audit FAIL仍可生成真实Export，但未形成可信terminal不得越过gate；post-decision NOT_READY只取消Registry计算出的下游eligibility，不改写formal decision。

## 8. Required Formal Gates and Audit

Formal前必须冻结并验证：

- 两层聚合与flattened-average反例test vectors；
- `N_A` exclusion与paper-level zero test vectors；
- Decimal precision/rounding/serialization、tie与边界test vectors；
- paper独立quantization、Candidate single-quantization与double-rounding反例；
- Decimal-to-binary64 conversion和完整softmax/replay vectors；
- insertion-order eligible vector、duplicate/failed exclusion与 `random.Random.choices` vectors；
- Program manifest canonicalization、exact-byte distinction与excluded-field invariance；
- domain separation、显式null、full-hash ID与Program dependency-closure gates；
- attempt/parent-selection/Candidate-birth transaction crash points与resume/replay stability；
- admission-time duplicate、mandatory-seed duplicate及finalist defensive-dedup paths；
- 全部schema/version/serializer/runtime SHA与expected canonical outputs。

任一gate未通过或identity缺失，禁止启动formal Evolution。
