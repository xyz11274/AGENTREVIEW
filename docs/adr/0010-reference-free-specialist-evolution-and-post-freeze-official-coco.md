# Specialist Evolution 完全 reference-free，Official CoCo 仅用于冻结后的独立评价

状态：accepted（2026-08-28）。本 ADR 取代 ADR-0006 与 ADR-0009 中允许 Human Reference 进入 Evolution evaluator、使用 Global CoCo 进行 B-suite Suite Selection，以及把 CoCo Split/Classify 纳入 Specialist Evaluator Identity 的条款。

23 个 role/subdimension 的可执行 evaluator contract 由 `docs/protocol/role_specific_reference_free_evaluator_protocol_20260828.md` 单独维护；该文件标记为 frozen 前不得启动 formal run。

最终 Official CoCo 本身包含与 Human Review/reference opinions 的比较。若这些 reference 在 Evolution 阶段进入 fitness、selection、mutation feedback、early stopping、调权或 winner selection，Evolution 就获得了 Human-reference supervision，最终 CoCo 也不再是独立外部评价。因此，Human/OpenReview reference 与全部 CoCo 能力必须和 Specialist Evolution 彻底隔离。

五条 Specialist Evolution 分别使用完全 **reference-free、paper-grounded** 的 role-specific evaluator。Evaluator 输入白名单仅包括：

- 当前 paper；
- 合法且按数据协议冻结的 source anchors；
- 当前 role 的冻结 rubric；
- Candidate Review；
- 与 benchmark 无关的冻结 evaluator configuration。

论文外事实只能由独立的 **Frozen External Evidence Pool** 支撑。该池不是任何 Candidate 当前的 Memory；它按 paper/source/cutoff/provenance/hash 冻结，v1 包含三个分区：`PRIOR_WORK` 使用现有 frozen S2 temporal v2；`POLICY` 冻结对应 venue/year/cutoff 的官方 submission、anonymity、ethics、checklist 与 dual-submission 规则；`ARTIFACT_LICENSE` 冻结论文明确引用的数据、代码、license、IRB/consent 等外部页面。当前不建立 `PLAGIARISM_OVERLAP`，因此没有确定 overlap/duplicate evidence 时 `POL-PLG=N/A`。`POLICY` 只能证明规则是什么，不能脱离 paper evidence 单独证明论文已经违规。

每个 target paper 建立唯一 set-level manifest，每个 `PRIOR_WORK/POLICY/ARTIFACT_LICENSE` source item 建立独立 evidence record/ID；具体外部事实不得只引用 query/result-list identity。Evidence item 同时冻结 immutable raw blob 与无 LLM deterministic canonical text projection，分别保存 hash 和 projection identity。v1 API 只允许 availability、metadata listing、specific evidence read 与 partition sequential read，不提供 semantic search/rerank；三条 mandatory lineage 共享完全相同的 API、call/record/token budget、返回顺序与错误规则。Cutoff 后 retrieval 只有在可验证 cutoff-before archive/version 时可采纳，live current page 不能替代历史版本。Formal 前用冻结 Evaluator tokenizer 验证全部完整 delivered sets 均在预算内；任何超限必须发布新的 pool/API version 与统一 deterministic policy，runtime 禁止 adaptive truncation。

每个 Candidate×paper 使用fresh evidence session、独立且相同的call/record/token预算。Budget耗尽只停止新delivery，不直接处罚；Candidate-originated invalid request默认使当前Candidate evaluation按预注册runtime/protocol failure规则失败，只有暴露orchestrator/manifest identity错误时才升级role-level fail-fast。Paper结束后只持久保存canonical Review与API audit，禁止evidence-derived Memory/cache。Evaluator看不到Candidate调用日志，只读取完整合法delivered set并评价最终Review，API调用本身不奖励或处罚。

Vector Search、LLM Summarizer、Experience Learner 三条 mandatory lineage 获得完全相同的 Frozen External Evidence Pool 只读 API、调用预算与返回规则。每个 Candidate 可针对当前 paper 自主决定是否调用、何时调用及在冻结预算内调用多少次；是否读取 external evidence 属于允许 Evolution 优化的策略。所有 query、调用次数与返回 evidence IDs 必须记录。当前 v1 读取结果保持 canonical record、原文、原顺序与 provenance，不得做 lineage-specific 改写、摘要、重切片、重嵌入、重排序或写入持久 Memory/派生 evidence database；证据只用于当前 paper Review，paper 结束后不得保留 external-evidence 派生状态。Evaluator 读取目标 paper 的全部 frozen external evidence，用于判断调用或不调用是否造成 material evidence omission。只有未来证据规模超过预注册 context/token budget 时，才允许使用统一、与 lineage 无关且对 Evaluator 相同的 deterministic truncation/retrieval policy。禁止 live search、临时扩充池或 Evaluator-only 外部资料。

Evidence ID 对规范化后的 canonical JSON identity 计算 SHA256，不采用未规范化字符串拼接。Identity 至少包含 `pool_version`、`partition`、`target_paper_id`、`canonical_source_identity`、`cutoff` 与 `content_sha256`。Manifest 同时保存 partition、target paper/family、source URL/ID、venue/year、cutoff、retrieval/snapshot time、MIME/type、content SHA、provenance 与 availability status。Parser 必须验证 Evaluator 引用的 evidence ID 属于当前 target paper 的 Frozen External Evidence Pool。

协议不声称 LLM Evaluator 不会调用参数知识，因为模型内部认知过程不可验证。可执行边界是：**parametric knowledge is not admissible external evidence**。模型参数知识可以不可避免地影响内部推理，但不得作为正式 external factual judgment 的独立证据。任何影响 `QUAL-CMP`、`SIGN-SOT`、`ORIG-*`、`POL-PLG` 等正式 score 的论文外事实必须绑定到 Frozen External Evidence Pool 中的具体 evidence ID；没有可引用 frozen evidence 时，不得仅凭模型记忆奖励或处罚 Candidate，相应外部判断应为 `N/A` 或不计分。

Evaluator 输入黑名单包括：Human/OpenReview review、Human/reference opinion、gold opinion、CoCo reference、CoCo Split/Classify、CoCo taxonomy/category coverage、CoCo score、由上述信息派生的标签、反馈、权重或统计量。黑名单同时适用于 Candidate、Reviewer、MemoryProgram、Evolution Reflector、fitness evaluator、controller、selection、mutation feedback、early stopping、调权和 winner selection；`evaluator-only` 不是例外。

`QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL` 分别按照自身冻结的 role-specific rubric 评价，不共享一套通用 CoCo-inspired 指标。旧 `alignment/completeness/grounding_specificity/verifiability/clarity + Human category coverage + harmonic mean` 公式整体退出 Specialist Evolution。每个 subdimension 的 `relevant/N/A`、correctness、importance、grounding、coverage 等判断必须根据论文本身完成。Candidate 自报的 subcategory、标签或分数只视为不可信输出，不构成评价依据。

对于 role `r` 和 paper `p`，设独立 evaluator 判定的 applicable subdimension 集合为 `A(r,p)`，每个 subdimension 的冻结量表得分为 `s(r,p,d)`，则单篇 fitness 为无人工权重的 macro-average：

`paper_fitness(r,p) = mean(s(r,p,d) for d in A(r,p))`

`N/A` 从分母排除且必须由独立 evaluator 基于 paper 判定。Candidate score 再对本次被评 B-local papers 做等权平均。公共 hallucination、invalid anchor、duplicate padding、严重 role leakage 等只作为 validity/penalty rules，不得重新形成跨角色共享的一套 quality dimensions。不同 role 的 fitness 只用于角色内 Candidate 比较，不进行跨角色数值比较。

若某 paper 的全部 subdimensions 均为 `N/A`，则 `paper_fitness=N/A`，并且必须对所有 Candidate 一致地从 paper-level 平均分母排除，不得转换为 `0` 或满分。最低 evaluable-paper 数量不使用跨 role 的统一比例或常数；每个 role 必须在查看 Candidate 结果前，依据 frozen B-local manifest 与 Candidate-blind、Human/CoCo-free eligibility audit，分别预注册 `min_static_evaluable` 和 `min_full_pool_evaluable` 的明确整数。Audit 只判断 role-level evaluability，不产生 expected weakness、ideal criticism、评分答案或 Candidate-specific information，也不得缓存为正式 applicability map。低于相应 role/stage 阈值时 formal run fail-fast。

Frozen External Evidence Pool 中 `result_count=0` 是合法状态，不是基础设施错误，也不得处罚 Candidate。能仅依据 paper 判断的 subdimensions 继续评分；必须依赖论文外事实而 pool 为空或证据不足的判断为 `N/A`。`POLICY` 或 `ARTIFACT_LICENSE` 页面在 cutoff 前无法获得时，availability 记为 `unavailable` 或 `not_archived_before_cutoff`，不得用当前最新版替代、live fallback 或以 parametric knowledge 补充。Formal audit 必须按 role 记录 empty-pool paper 数量、缺失 evidence 状态，以及由此产生的 paper-level 与 subdimension-level `N/A` 数量。

Subdimension 使用统一的 `0–4 + N/A` 数值外壳，但每个 role/subdimension 分别冻结自身评分锚点。`4` 表示 Candidate Review 对该 subdimension 中 material/important 内容的评价准确、具体且充分 grounded，不表示 criticism 数量更多，也不鼓励穷举。不同 role 的相同数值不做横向解释。

Validity/penalty 采用规则化处理，不构造统一加权 penalty dimension。单个局部 fabricated material claim 或 invalid anchor 使受影响 subdimension 记 `0`；`material` 指足以影响 review judgment 的事实或证据错误。满足以下任一预注册条件即触发 systematic hallucination，并使整篇 paper fitness 记 `0`：存在至少两个彼此独立的 material fabricated claims；fabrication 跨至少两个 subdimensions；或一个虚构的核心实验、数值、表格、引文或结论被用于支撑多个评价判断。局部 role leakage 内容忽略，主体严重越界时整篇 paper fitness 记 `0`；duplicate padding 先确定性去重再评分。Evaluator 必须输出固定 event code，全部阈值与 failure code 必须在 formal run 前冻结。

同一 role 内 no-memory、三种 Memory method、所有 Seed 和所有 Evolved Candidate 必须共享完全相同的 Evaluator Identity。该 Identity 至少冻结 role rubric 与 subdimension 定义、judge model/revision、prompt SHA、generation/解析规则、`relevant/N/A` 规则、聚合与处罚规则、source-anchor 规则以及 B-local manifest；不得包含任何 Human/CoCo 组件。不同 role 的 local fitness 不做数值横向比较。

当前不强制 Reviewer 与 Evaluator 使用不同模型，也不把独立 API Evaluator 设为 formal 前置条件；允许二者同为当前 `qwen3.8-27b`，实现不得加入 same-model rejection。系统必须保留可替换的 evaluator provider/model 接口。Provider、model/version、temperature、prompt、generation configuration、schema/parser、role-specific aggregation 及任何 determinism 参数全部冻结并进入 Evaluator Identity，保存每次调用的全部 raw outputs 与 attempt provenance。未来更换为独立 Evaluator 时必须形成新的 Evaluator Identity，新旧分数不得混合。Qwen3.8-27B FP8 只是同模型量化版本，不得在未来独立模型实验中宣称为不同 Evaluator 身份。

Formal Evolution 中每个 `Candidate × paper × role` 只执行一次冻结的确定性评价：`temperature=0`、greedy decoding、固定 model revision、prompt SHA、parser、seed，并使用 fresh evaluator session。正式运行前只在 A/B/C 之外的 benchmark-independent smoke cases 上执行重复一致性 gate；若同一输入不能稳定产生合法 schema 和一致 score，则该 Evaluator Identity 不得进入 formal run。Smoke gate 不得读取 Human/CoCo，也不得在 formal run 开始后根据实验结果重新调节 evaluator。

Evaluator Determinism Gate 固定为每个 role 5 个 benchmark-independent smoke cases、每 case 3 次重复，共 75 次 evaluation。Raw 文本或非计分措辞可以不同，但 canonical parsed JSON、applicability map、`0–4` score vector 与 event-code set 必须完全一致，不设置数值容差。任一正式决策不一致时，该 Evaluator Identity 不得进入 formal run。Smoke cases 必须位于 A/B/C 之外，也不得读取 Human/CoCo。

每个 `Candidate × paper × role` 最终只能产生一个 Canonical Evaluator Score。基础设施错误、输出截断、非法 JSON 或 schema mismatch 可在完全相同的输入、model/revision、prompt、seed、temperature、generation budget 和 parser identity 下，以 fresh session 最多 retry 一次。保存全部 raw attempts，第一个合法输出成为 canonical；两次均非法则 formal run fail-fast。非法输出不得记为 `0`，retry 也不得用于 judge voting 或 best-of selection。

每个 Candidate 必须满足 **Candidate Independence**：从完全相同的 frozen paper input、source anchors、role rubric、paper order、A-state baseline 和 Evaluator Identity 开始，在独立 evaluator session 与独立 runtime state 中评价。Candidate A 的 Review、score、evaluator reasoning、Memory mutation、cache 或任何中间状态不得影响 Candidate B。若为效率预生成 Candidate 间共享的 Paper Evaluation Context，只允许通过无 LLM 的确定性流程提取 candidate-independent 的客观 paper-derived metadata，例如 section/offset、source-anchor map、table/figure/equation 位置与 bibliography/appendix/artifact/checklist presence。只有可由明确结构事实确定的 applicable/N/A 才可共享；涉及语义判断的 applicability、weakness、importance、expected criticism 等必须由每个 Candidate 的独立 evaluator session 自行判断。禁止任何共享的 evaluator judgment 或带答案的 Paper Rubric Audit。

每个 Candidate 的独立 evaluator session 内使用两阶段 protocol。Applicability phase 只读取 paper、anchors 与 role rubric，Candidate Review 必须尚未进入上下文；该阶段只输出各 subdimension 的 `applicable/N/A`，不得输出 weakness、expected criticism 或理想答案。Scoring phase 随后才读取 Candidate Review，并且只能为第一阶段 applicable 的 subdimensions 输出 `0–4`。Applicability 不跨 Candidate 缓存。相同 paper×role 在不同 Candidate 独立 session 中若产生不同 applicability，触发固定 event code `APPLICABILITY_INCONSISTENCY` 并作为 protocol failure；不得允许不同分母继续比较。

Evolution Reflector 只允许接收跨整个 B-local evaluation batch 聚合后的 role-specific subdimension scores、overall fitness、预注册 failure codes，以及 compile/runtime/protocol error。它不得接收逐 paper 分数、逐 paper rationale、自由文本 evaluator feedback、Paper Evaluation Context 原文、expected issue、详细 evidence answer，或任何足以反推出某篇论文“标准答案”的信号。反馈协议必须压缩、聚合且预注册。

保留原 M★ 的 static/rotating 语义：`static 25` 的 aggregate fitness 是唯一 search fitness，用于 parent selection 与 promotion；每个 iteration 的 `rotating 5` 只生成当前 batch 的聚合 Reflector feedback，不进入 Candidate 排名。不同 rotating subset 的结果不得直接用于跨 Candidate 比较。

五个 role 共享同一个 frozen B-local total paper/family pool，但分别通过预注册 deterministic selection rule、frozen seed/hash 与 role eligibility audit 产生 role-specific static-25 membership 和 rotating schedule，禁止人工 cherry-picking。同一 role 内所有 lineage、method、seed 和 Candidate 必须使用完全相同的 membership、schedule 和 paper order。Rotating batch 不设置 fitness-validity threshold；若无 evaluable paper，记录 `NO_EVALUABLE_ROTATING_PAPERS`，不得生成虚假 fitness、临时换 paper 或处罚 Candidate。Full-pool finalist reevaluation 仍使用统一冻结的 B-local full pool。

Final Review Suite、五个 Specialist MemoryProgram、Reviewer/model revision、prompt、temperature、generation budget、META mode 与全部配置完全冻结后，才允许在 untouched C 上运行 **Official CoCo Evaluation**。只有该阶段可以读取 Human Review/reference opinions。Official CoCo 的结果只用于独立最终报告；查看 C、CoCo 或 Human-reference 结果后，不得对当前系统继续 fitness、selection、mutation、重选、调权或 Evolution。

取消 B-suite 阶段。五个 Specialist 分别在各自冻结的 B-local evaluator 下独立 Evolution，并各自选出唯一 winner；五个 winner 按 `QUAL → CLAR → SIGN → ORIG → POL` 固定顺序直接组成 Final Review Suite，不设置跨角色二次选择、coordinate search 或 Global CoCo selection。原 B-suite papers/families 保持 reserved，不并入 B-local 或 C。

每个 role 使用 shared-pool 两阶段 Role-Local Winner Selection。Vector Search、LLM Summarizer、Experience Learner 是三个 mandatory initial search seeds，而不是三条独立优化分支：三者全部通过初始化 gates 且strict Program identity互不重复后共同进入 candidate pool，随后由 global softmax parent selection 共享每role总计20个evolution attempt slots。Parent softmax保留冻结的M★ IEEE-754 binary64语义与 `tau=0.15`。允许不同seed/descendant lineage获得不同后续attempt数量，不设置per-lineage quota或lineage champion。Child在compile/runtime gate后、static evaluation前执行strict Program dedup；duplicate mutation消耗attempt但不新增pool entry或softmax mass。20 attempts结束后，从三个合法initial seeds和全部成功完成static-25 canonical evaluation的children构成global eligible pool；Candidate无需优于parent才可进入。Fitness严格先对每篇paper的applicable subdimensions等权平均，再对evaluable papers等权平均；排序使用冻结精度与rounding生成的canonical Decimal。先按严格Program identity防御性去重，再按canonical Decimal static-25 fitness降序、canonical candidate ID升序选择固定global top-3，不设lineage席位或tie扩容。三名finalists在相同frozen B-local full pool、paper order、Evaluator Identity与独立session下统一重评；按canonical Decimal full-pool fitness降序、candidate ID升序产生唯一winner，static分数不参与最终tie-break。Finalist failure、Evaluator exhaustion或unique pool不足3均role fail-fast，不得递补。

Formal search state以每role append-only、hash-chained Controller Journal为唯一权威。Checkpoint仅是journal projection；attempt、parent selection、Candidate birth、dedup、static evaluation与admission必须通过immutable event durable commit。Resume先验证journal并重建pool/RNG，已提交parent不得重选。Program/Candidate/attempt IDs使用domain-separated canonical JSON与不截断SHA256；Program只能访问manifest声明的behavior dependency closure。

五个role允许工程并行，但各自使用single-writer controller与独立journal/pool/RNG；paper workers只产生identity-bound immutable artifacts，aggregate由role controller按冻结paper顺序一次性提交，worker完成顺序不得改变结果。五个 `ROLE_WINNER_COMMITTED` 完成后，独立suite controller按固定role顺序提交 `FINAL_SUITE_COMMITTED`。只有其integrity通过后才能 `OFFICIAL_COCO_AUTHORIZED`；`OFFICIAL_COCO_STARTED` 后Suite永久sealed且Official CoCo仅作report-only评价。

Reviewer、Evaluator、Reflector、compile-fix与注册的post-retrieval LLM Summarizer共享单个Qwen global sequence budget。Preformal以mixed workload测试 `max-num-seqs=1/2/4`并冻结最高通过capacity；五role逻辑并行但由 `QUAL → CLAR → SIGN → ORIG → POL` round-robin、role内FIFO scheduler统一限流。单篇deterministic Memory retrieval先提交paper-local ledger/manifest，可选Summary完成后再生成Reviewer output；Candidate-blind Applicability可与该路径并行，Scoring必须等待两者。Official CoCo仅在授权后部署；embedding优先preformal冻结document index，runtime仅计算必要query embedding。External Evidence与Review Memory的Candidate-private派生状态均不得跨paper持久化；只有冻结immutable assets可共享。

8×24GB RTX4090 formal拓扑固定为：Evolution在GPU0–3单岛运行唯一Qwen TP4、GPU6运行Embedding、GPU4/5禁用CoCo、GPU7 reserve；Official授权后GPU4/5才部署Split/Classify。Capacity gate与formal整机exclusive，未注册compute process使preflight fail-fast且不得自动kill。`max-model-len`由全部formal request classes按冻结tokenizer完整materialize并向上取整到8192倍数，不沿用未经验证的262144，也不允许runtime truncation。资源阶段以独立journal events durable commit。

Final Suite之前的raw-audit readiness与Compatibility Export access validation分别使用独立single-writer Infrastructure Journal。Readiness epoch、run、attempt、evidence与terminal只由Infrastructure Journal授权，scope Journal仅保存幂等引用；Quiescence、Compatibility N/A、Audit Gate Root和NOT_READY dependency closure都具有稳定canonical identity。Infrastructure Journal损坏以首个非法event为cutoff：受影响gate authority在新Journal identity下对原immutable Catalog重新验证，但不得借此重跑、修改或重新解释Corpus、winner与Compatibility certificate。Export Candidate提交后的confirmed storage identity损坏是独立terminal integrity outcome，不允许回到semantic source重建。

Infrastructure Journal generations由formal前冻结的独立Registry root-of-trust授权；Registry自身损坏会终止当前formal root，不能递归创建replacement后继续混用。Scope只通过tagged Readiness projection引用权威terminal，Suite-level Audit Gate Controller独占跨scope dependency-closure决定。已提交Suite或Official authorization不会被原地撤销：Official开始前如gate authority失效，旧chain被执行阻断并以新Suite/authorization chain重新取得资格；Official开始后只允许记录审计失败，禁止重封、重选、重跑或把CoCo/Human结果反馈回系统。

Readiness projection统一进入Suite Journal的Audit Gate namespace，sealed research Journals永不重开。每Catalog的infrastructure recovery形成唯一线性successor chain，Composition必须在Compatibility N/A与唯一Plan lifecycle之间exactly-one。Official开始后的故障由预注册、结果不可见的Impact Registry决定仅保留audit-invalid report还是立即停止；多NOT_READY通过显式active/resolved trigger与closure-set快照机械合并。相同winner composition的re-seal只更新Suite/gate authority，不得创建第二Compatibility Plan；若既有Compatibility authority不可复用，则必须形成新的role-run/winner provenance和新composition。

Infrastructure root进一步拆为Registry rules、不可自我替换的Registry Journal和managed generations；generation损坏恢复优先复用合法prefix中的完整Evidence，而不是重新执行semantic validation。Compatibility branch以显式adoption event固定，Audit recovery以显式Resolution和empty active-set snapshot证明完成。Official STARTED前禁止evaluation dispatch；完成状态显式区分formal-audit valid与audit-invalid report-only。Suite Run与sealed Final Suite instance分别建模，crash或re-seal不能通过新ordinal制造无前驱平行Suite。

Root storage不可用与canonical integrity损坏是互斥failure classes：verified mirror只能逐bytes复制authority，不能修复或投票；每个managed Infrastructure Journal kind在一个top-level root内最多replacement一次。Preserved Evidence只能通过专用terminalization recovery转成terminal。Compatibility branch commit是全部downstream authority barrier；Audit Gate Controller拥有Registry管理的epoch和bounded Resolution事务，Closure Set采用逐delta连续successor chain。Official impact以累积快照保守聚合，completed report status可单调降为audit-invalid或execution-integrity-invalid。每个Suite Run有唯一birth与terminal；同一composition最多一次re-seal，再次pre-STARTED失效会关闭当前top-level formal root，不能靠新composition重置。

Top-level Formal Root使用独立最小Control Journal取得birth与terminal authority，并与finfrajournal及其verified read-only mirror隔离；mirror只证明历史prefix。Root storage incident使用冻结三路径预算。Managed generation replacement必须先提交完整Replacement Plan，preserved Evidence通过独立terminalization decision落地。每active trigger最多跨initial与一个recovery Audit Gate Controller epoch，且Controller recovery由Root/Registry锚定的独立Validator验证。v1禁止跨Root保留winner/search state。Official reporting必须消费latest impact state，并区分completed与stopped authorities；Suite abandonment由closed reason Registry决定。

Root Control Journal可写时，root failure先经Shutdown Barrier收束全部workflow再terminal；不可写时只有external witness证明fail-stop，不能授权恢复。finfrajournal authority-changing event只有经formalrootctl记录的verified-mirror activation才向下游生效，避免mirror自引用。Replacement Plan与Controller preflight都采用独立、bounded、Evidence-first验证。Official report artifact必须在commit时仍绑定current impact/report status；未授权materialization应ABANDON，只有曾current的artifact才SUPERSEDED。新Formal Root只复用root-independent input assets，A区625条Review及全部Review Memory派生资产必须重建。

Root Control与finfrajournal存储必须处于不同failure domain。Registry authority通过exactly-once activation采用，mirror publication、Root incident、replacement与AGC preflight均有bounded事务和closed failure后果。Official report进一步拆为pure content、authority artifact与current projection；Reporting状态降级立即使旧artifact失去current资格。Cross-Root input reuse由birth-time closed allowlist控制，旧raw Reviewer response也不可复用。Formal Root最终严格区分formal-valid completion、report-only-invalid completion与Official execution-integrity stop。

Infrastructure activation按closed transition registry同时约束grant、replacement、seal与revoke；PENDING mirror hold期间新旧authority都不能产生越权work。Root Control mirror采用非递归prefix witness，外部Watchdog负责bounded recovery。Official Reporting使用独立single-writer Journal、deterministic no-model materializer及一次storage-integrity replacement上限。Report-only Root completion只容纳post-start authority invalidation或post-freeze audit failure，绝不绕过pre-start gates；Root terminal之后全部formal authority永久冻结。

C/Human/CoCo protected inputs在Root birth只冻结sealed commitment，只有Official授权后才能逐bytes preflight，失败则Root terminal且禁止换snapshot或返回Evolution。Reporting Journal使用Hold串行化Impact/Status与artifact/export，publication只允许current且标签匹配的报告。Shutdown incomplete由closed Registry解释；post-terminal archive是external non-formal状态，不能重开Root或更改任何实验authority。

Protected Input preflight采用Evidence-first least-privilege访问，STARTED只能引用唯一Delivery Manifest且所有实际读取可审计。Reporting Hold、artifact/current/publication intents进入single-writer durable ingress全序；publication先生成private candidate再原子promotion，并将content与authority分离。HARDENING_V2 Root成功完成必须拥有匹配最终Reporting状态的publication artifact；CORE_V1按ADR-0011改为至少提交immutable Official result manifest与deterministic report/result artifact。全部Protocol registries由纯semantic protocolroot统一绑定，并以独立protocolrootval PASS证明跨Registry一致后才能Birth。

Q526后实施阻塞范围由ADR-0011与双Profile Amendment修正：reference-free evaluator、Corpus/Memory、M★ search、Final Suite、protected Official access、Official report-only isolation、Protocol Root validation和最小decision lineage仍为CORE_V1硬前置；复杂mirror/replacement/re-seal recovery及Reporting/Publication/Archive HA保留为HARDENING_V2目标，不阻塞首次research-valid run。

本协议的总信息流不变量是：

> **Candidate 之间独立；Evaluator 条件固定；Reflector 只接收聚合反馈；Human/CoCo 信息在 Evolution 全程不可见。**

## Consequences

- 当前 `CoCoReviewFitness` 不能作为正式 Specialist Evolution fitness；其中统一 judge schema、CoCo Split/Classify、taxonomy coverage 与 Human-reference comparison 都必须从 formal Evolution 路径移除或替换。
- Formal gate 必须验证 Evolution 进程无法读取 Human/CoCo 资产，而不只是验证 Candidate prompt 没有泄漏。
- Evolution artifact 不得保存或接收 Human/CoCo 派生的 rationale、类别、分数、缓存命中结果或反馈信号。
- Official CoCo 是 measurement，不是 objective；不能用于挑选五角色组合或证明某次 Evolution selection 的正确性。
- Formal gate 必须验证 Candidate session/runtime state 隔离、共享 artifact 白名单和 Reflector batch-level feedback schema；仅靠 prompt 约定不视为通过。
- Final Review Suite 的形成是五个独立 winner 的确定性组合，不产生新的跨角色优化预算。
- 三个 mandatory initial seed identities、shared-pool global 20-attempt controller、global finalist产生规则/数量、B-local full-pool manifest、paper order和canonical candidate ID排序规则都必须在Evolution前预注册；不得事后按lineage平衡或保留代表。
