# AgentReview Research Core v2 设计

0915 已实现的共享标准 CoCo 运行协议、代码入口与验证边界统一见 [SHARED_MSTAR_IMPLEMENTATION.md](SHARED_MSTAR_IMPLEMENTATION.md)。下文保留被 ADR-0011–0016 取代的历史研究设计，以便解释旧实验。

> 状态：0831 历史设计与实现基线。0915 已确认的数据和评测边界由 [ADR-0011](adr/0011-development-coco-and-sealed-test.md) 定义：开发集使用标准 CoCo，最终测试严格隔离。本文原有整体 1–5 内评、全部 CoCo 外置、固定数据来源与样本量等条款保留为历史背景；0915 的其余实验条件仍在讨论。实际旧实现完成项见 `RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md`。

> 共享策略更新：0915 已采用 [ADR-0013](adr/0013-shared-strategy-independent-role-memory.md) 的一套共享记忆策略和五个角色独立记忆库。下文关于五个独立 Program/pool/lineage 的条款属于被取代的历史设计。

> 算法更新：0915 已采用 [ADR-0014](adr/0014-mstar-paper-guided-memory-evolution.md) 的 M★ 原文机制。下文是旧实现基线，其中的确定性查询限制、搜索参数和非记忆工作流变异范围仍需按新协议核定；当前 GEPA 方案研究已经结束。

> 评分与报告更新：0915 已采用 [ADR-0015](adr/0015-paper-fitness-and-separate-results-report.md) 的标准 CoCo Table-1 `Paper.` 主指标；每个 seed 的逐篇分数和 CoCo 汇总由独立后处理另外生成，原始生成流程保存自身产物。

## 1. 研究目标

Research Core v2 要回答的核心问题是：角色独立的 Reviewer Program 能否通过共同进化 Memory 与 S2 使用策略，生成更准确、深入、有用的论文 Review。S2 不是预先固定开启或关闭的系统因子；Evolution 要学习是否查询、何时查询、选择什么 evidence，以及如何使用它。

系统首先服务 Review 能力和可解释实验。manifest、gate、audit、eligibility、runtime protocol 或额外 selection 层只有在缺少它就无法保证实验正确性时才进入主链。

## 2. 已确认的不变量

1. 正式审稿体系只有 `QUAL / CLAR / SIGN / ORIG / POL` 五个 Specialist Reviewer。
2. 五个 Reviewer 独立审稿、独立 Memory、独立 Program lineage、独立 Evolution。
3. Meta Reviewer 是可选、无状态的最终汇总器；它没有 Memory，不参与 Evolution，也不产生候选分支。
4. Review Run 不做 Reflection 或 Evolution；Evolution Run 不发起 Review 请求，只消费已经保存的 Review 和 Internal Evaluation。
5. Reviewer 及其 Memory、Retrieval、Reflection、Evolution 对 CoCo 完全不可见。CoCo 只消费已经保存的 Final Review。
6. Semantic Scholar 只在数据准备阶段联网；正式实验只读冻结本地资产。
7. Retrieval 本身不调用生成模型：query、筛选、排序和命中集合均确定性完成。唯一例外是 LLM Summarizer Seed 在命中 ID 冻结后增加显式摘要调用；摘要不属于召回，不能改变集合或 retry。
8. 完整 Specialist Review 和 Final Review 是一级数据；Chunk、embedding 和 vector index 是可重建、可追溯的二级派生数据。
9. 原始完整 Review 永久保留，不能被 Chunk 或 embedding 替代。
10. Internal Evaluation 只在 Evolution Run 内对历史完整 Review 批量执行，不在正常 Review 后在线触发。
11. Internal Evaluator 默认可以与 Reviewer 使用同一个基础模型以研究 self-critique，同时保留独立 evaluator model 接口。
12. `REVIEW_Request.md` 只保留 `QUAL / CLAR / SIGN / ORIG / POL` 五个 Specialist 定义；其中的五角色 identity、职责与输出要求永久冻结。Meta 使用独立版本化的 `META_SYNTHESIS_Request_v1.md`，不属于 Reviewer Role。
13. `review_quality` 是单一 `1–5` 整体分；它只提供候选排序信号，不扩展成加权 reward 或固定 checklist。
14. Internal Evaluator 盲看论文、冻结角色请求、完整 Review 和 Reviewer 实际看到的检索证据，不看 Program 身份、其他 Reviewer、human reference 或 CoCo。
15. Evolution 可以离线生成 candidate-local Derived Memory；Derived Memory 在下一轮 Review 前冻结。除 LLM Summarizer Seed 的显式 post-retrieval 摘要外，Review-time Memory 流程不调用生成模型。
16. 第一版每个角色对每篇论文只调用一次 Reviewer 模型；Workflow Evolution 不得用额外 planning、critique 或 rewrite 调用换取更多推理预算。
17. 五 Reviewer 与 CoCo 顶层五轴同构，但职责文本独立冻结，CoCo 子类与评测资产严格隔离。
18. Formal development 使用 `OpenReview/train_clean`，final 使用独立 CoCo benchmark；不强制年份 cutoff，跨边界只做 exact ID、canonical title、paperhash 和内容 hash 最小直接去重。
19. 三个 M★ seeds 与 candidates 共享同一批角色基础完整 Review，但各自独立构建和冻结 Derived Memory。
20. 每个角色独立选择 top-1；第一版不做 bundle evaluator、bundle score 或组合搜索。
21. `meta_enabled=true` 是预注册 Primary，`meta_enabled=false` 是预注册 Concat Ablation；两者在打开 CoCo 前同时冻结。
22. 最终 CoCo 使用13个 venue-year 各100篇的1,300篇 protocol-compatible resample；它不是训练集，也不声称复现作者未发布的原始 IDs。
23. Reviewer Memory 构建并冻结后永不吸收新评审论文；新 Review/Evaluation 只作为 Evolution Feedback 修正后续方法与方向。
24. 正式 Evolution 采用 M★ 的三 seeds、20 attempts、static 25、rotating 5、softmax temperature 0.15；失败 candidate 留档但不入 pool。
25. Formal development 只包含互斥的 Canonical Review Corpus 125、Evolution static 25 和 Evolution rotating pool 125，共275篇；不设置 Bundle Diagnostic 分区。
26. 论文输入是带独立规范标题的 JSON record；S2 只在数据准备阶段按标题查询并冻结，Review Run 只按 `paper_id` 本地读取。
27. External Development Corpus 与 CoCo-1300 各自拥有独立 Frozen S2 Pool，不能混用 evidence universe。
28. 每个 Program Version 必须针对每篇论文自主决定是否查询 Frozen S2、查询后是否使用、选择哪些 evidence 以及如何使用；三个 initial seeds 以 `always-top5` 作为共同起点，而不是宿主固定 `s2_enabled=true`。
29. Reviewer Memory 检索 Review Experience，不把历史论文正文切块后复制给 Reviewer。
30. Frozen S2 Pool 必须在冻结前确定性排除目标论文、同 family/version 副本和重复结果；exact-title self match 不能作为外部证据。
31. Memory 只冻结最小 provenance envelope，payload schema 由 Program Version 进化；初始 Experience Learner 使用 trigger/heuristic/evidence/failure/importance 五类语义。
32. Reflector 只接收父代及其五个 rotating failure packets，不接收 human review、CoCo、其他角色或 evaluator hidden reasoning。
33. 三个 development 分区按 venue-year 尽量均衡、使用稳定 hash seed `0` 依次抽样，不使用 decision、human score 或 review。
34. 已生成的 Canonical no-memory Review 固定 `memory_enabled=false, s2_enabled=true`，可保留为共享来源语料；它不属于 evolved winner，也不能作为 Autonomous S2 Policy 的行为或效果证据。625份完整 Review 冻结后统一执行一次 Internal Evaluation。
35. 保留 M★ 的 KnowledgeItem、Query、KnowledgeBase.write/read、Program、pool、Reflector、patcher 和 lineage；生命周期改为 offline build → frozen snapshot → read-only Review Run。
36. 每个 Evolution Attempt 生成一次 V4A patch，失败时最多一次显式 repair；任何 outcome 都消费一次预算，失败 candidate 不进入 pool。
37. Candidate Sandbox 以 M★ Program/Toolkit 语义为权威，只在外层增加独立 worker/bubblewrap 安全隔离。
38. 唯一入口是 `python -m agentreview.research_v2 <stage>`；stage 通过已保存 artifact 交接，`run-evolution` 只是薄 coordinator。
39. Paper-local failure 进行一次 identical retry，仍失败则保存失败并在 static 中按 `review_quality=1.0` 计入；Candidate-local failure 不入 pool；Infrastructure failure 停止并恢复。
40. 完整 Review/Evaluation/Final Review 每条使用独立 JSON 原子提交；JSONL、Chunk、embedding 和 index 都是可重建派生数据。
41. Meta 失败不允许自动 concat fallback；Concat Ablation 是独立预注册系统身份。
42. `freeze-system` 只冻结最终 Review runtime及最小来源链接；Evaluator/Reflector 不进入 runtime，也不建设 readiness gate。
43. 第一版必须通过边界能力测试和 Tiny E2E；Tiny 不产生正式 winner、不启动275篇正式实验或 CoCo-1300。
44. Meta Final Review 不输出自定义最终分数或独立 confidence 字段；recommendation 是审稿结论，不作为 Internal Evaluation 或 Evolution 指标。
45. 首个正式实验的所有生成阶段共享同一冻结 BF16 Qwen3.8-27B 基础模型，并显式关闭 thinking；Internal Evaluator 仍保留可替换模型接口。
46. Primary 的 S2 intervention 固定为 `program_autonomous`；`forced_consult/forced_off` 只用于有独立 identity 的预注册 diagnostic，不参与 winner 选择；前者只强制查询，不强制选择或注入。
47. winner evaluation 与 freeze/reload 后的 Review 使用同一 Program planning interface，不由 CLI 或宿主替 winner 决定 S2。
48. S2 usage trace 是 Review 一级 provenance；usage rate 描述行为，Memory 与 S2 policy 的效果归因使用冻结 development 数据上的2×2 diagnostic，不使用 CoCo 反馈。

## 3. 唯一主链

```text
Data preparation
  Paper assets + Semantic Scholar
  → freeze local paper/S2 assets

Review Run
  Frozen Paper + current-paper Frozen S2 Capability + role-local Memory
  → Program autonomously plans S2 call/skip, selection and usage
  → deterministic Retrieval + freeze hit IDs
  → optional explicit summarization (LLM Summarizer Seed only)
  → QUAL / CLAR / SIGN / ORIG / POL independently
  → save 5 immutable Specialist Reviews
  → meta_enabled ? stateless Meta : deterministic ordered concat
  → save immutable Final Review

External Evaluation
  Saved Final Review
  → isolated CoCo Split
  → isolated CoCo Classify
  → isolated CoCo Evaluation
  → save external report
```

Evolution 是另一条可独立启动、停止和复现的链：

```text
Saved historical Specialist Reviews
  → role-local batched Internal Evaluation
  → save Internal Evaluation artifacts
  → role-local Reflection
  → role-local Evolution candidate
  → save new Program Version
  → later Review Run uses that version
```

Review Run 结束于完整 Review 的保存，不跟随 evaluator 调用。允许一个薄的实验调度器按顺序启动这些独立 stage，但任何 stage 都只能通过已保存、不可变的 artifact 交接；Review 请求本身不能触发 writeback、Internal Evaluation、Reflection 或 candidate mutation。

## 4. 五 Reviewer 与 Meta

五份 Specialist Review 是系统的核心输出，不是 Meta 的临时上下文。每份 Review 先独立提交并永久保存；一位 Reviewer 失败不能抹去其他已经成功的 Review。

`meta_enabled=true` 时，Meta 只读取论文和五份完整 Review，生成一份新的 Final Review。`meta_enabled=false` 时，系统用固定标题和固定角色顺序进行字节级确定的拼接，不做摘要、去重或语义修复。两种模式都保存 Final Review，并保留其五个 source review IDs。

Meta 使用冻结模型、prompt、generation config 和一次模型调用，只读取 Paper 与五份完整 Specialist Review，不读取 Memory、Frozen S2、Internal Evaluation 或 CoCo。Meta transport/empty-response failure 允许一次相同请求重试；仍失败则保存 Meta failure，Primary Final Review 保持未完成。系统不得自动切换到 concat，因为 Concat Ablation 是独立预注册身份，不是容错路径。

`REVIEW_Request.md` 是五个 Specialist Reviewer 的唯一共同职责来源，文件中不保留 Meta 章节。`META_SYNTHESIS_Request_v1.md` 独立定义 Meta 的输入边界、汇总职责与输出格式，并拥有自己的版本和内容 hash。它不进入任何 role-local Program、Memory、pool 或 lineage；修改 Meta prompt 也不能产生新的 Specialist candidate。

`META_SYNTHESIS_Request_v1.md` 的输入只有 Frozen Paper，以及按 `QUAL / CLAR / SIGN / ORIG / POL` 固定顺序排列的五份完整 Specialist Review。它保留五角色 synthesis、evidence anchors、recommendation 和 actionable suggestions；不输出 Meta 自定义最终分数，也不输出独立 confidence 字段。Meta recommendation 只是 Final Review 的审稿结论，不能作为 Evolution feedback、candidate reward 或系统评价指标。

### 4.1 生成模型冻结

首个正式实验统一使用同一个本地基础模型：

```text
source_model = Qwen/Qwen3.8-27B
immutable_revision = 1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0
weight_precision = BF16
served_model_alias = qwen3.8-27b
temperature = 0.0
enable_thinking = false
```

Reviewer、Internal Evaluator、Reflector、单次 repair、LLM Summarizer 和可选 Meta 都绑定该模型快照。Reviewer 与 Reflector 的 `max_output_tokens=8192`；Internal Evaluator、LLM Summarizer 与 Meta 的 `max_output_tokens=4096`。运行身份同时冻结 vLLM、Transformers、tokenizer 和 chat template 的版本或内容 hash，服务 endpoint 只作为每次运行的连接信息记录，不替代模型身份。

Internal Evaluator 保留独立 model provider/model ID 接口，以便后续只替换 evaluator 做 ablation。首个正式实验刻意使用 same-base self-evaluation；最终报告必须把潜在的 self-preference/self-critique bias 作为限制说明。各组件不得依赖服务端默认 thinking policy，也不得增加未记录的 hidden retry。

## 5. Review 一级数据

最小 Specialist Review 记录需要表达：

```text
review_id
paper_id
role
review_text
reviewer_program_version
memory_strategy_version
paper_asset_version
frozen_s2_version
retrieval_provenance
model_identity
generation_config
run_id
created_at
```

`retrieval_provenance` v2 必须保留真实 Memory context 与完整 S2 决策 trace，而不是一个宿主 `enabled` 布尔值：

```text
memory
  logs
  context
s2
  capability_available
  intervention
    protocol = s2-intervention-v1
    mode = program_autonomous | forced_off | forced_consult
    label
  s2_disposition = not_consulted | consulted_no_use | use
  decision_label
  lookup_count
  requested_limits
  candidate_ids
  selected_ids
  injected_ids
  usage_instruction
  context_order
  evidence
  pool_version
  pool_sha256
  context_sha256
```

Primary 固定使用 `mode=program_autonomous, label=formal-primary-v1`。Capability availability、lookup、selection 与 injection 是四个不同事实：available 不能记作 lookup，candidate 不能记作 selected，selected 也必须以实际注入 ID 再确认。该 provenance 随 Review 一起冻结，使 winner evaluation 与 reload 后的行为可复核。

最小 Final Review 记录需要表达：

```text
final_review_id
paper_id
mode = meta | concat
review_text
source_review_ids[5]
meta_config_version?  # 仅 meta 模式
run_id
created_at
```

保存顺序是硬约束：模型 raw output → 完整 Review record → 可选派生 Chunk → embedding/index。Parser 可以识别传输或结构格式，但不能静默修改 Review 语义。

每个 Memory Chunk 至少绑定 `source_review_id`、`derivation_version`、`chunk_index`、原文 span 和内容 hash。Embedding/index 必须绑定 Chunk ID、embedding identity 和 index version；删除全部派生数据后，系统仍能从完整 Review 重建它们。

所有可进化 Memory item 只冻结以下 envelope：

```text
item_id
role
source_review_ids
source_evaluation_ids
derivation_version
payload
```

`payload` 由 Program Version 定义。初始 Experience Learner 使用 `trigger / review_heuristic / evidence_to_check / failure_mode / why_it_matters`；Vector/Direct 与 Summarizer 可以使用完整 Review 的可追溯 section/fragment。任何 payload 都不得嵌入历史论文正文。

Evolution 可以进一步从完整 Review 和对应 Internal Evaluation 归纳 candidate-local Derived Memory。每条生成式 memory item 必须记录 `source_review_ids`、`evaluation_ids`、生成模型与派生版本。Derived Memory 在 Program Version 进入 Review Run 前整体冻结，不能在线写回。

冻结边界之后生成的新论文 Review 和 Internal Evaluation 形成 append-only Evolution Feedback。它们可以改变后续 Reflection、Program mutation 和 Memory 构建方法，但新论文内容、Review 文本和评价结果本身都不能追加进已冻结的 Canonical Review Corpus 或 Reviewer Memory。

## 6. Retrieval 与 Frozen S2

Retrieval 输入只能由冻结论文字段通过版本化确定性规则构造，例如 title、abstract、section text 或它们的固定拼接。允许的召回操作包括 embedding、cosine similarity、BM25、metadata filter、非生成式 reranker、top-k 和 hydration。

Review Run 记录最终 query 表示、所有命中 ID、分数、排序、过滤原因与所用资产版本。Direct/Vector 与 Experience Learner 的 read path 不调用生成模型。LLM Summarizer 是唯一例外：它必须先提交并冻结 `retrieved_record_ids`，然后对该固定集合调用一次摘要模型，同时保存 `summary_input`、raw output 和 final output；摘要不能改变集合、重新筛选或触发 retrieval retry。

Semantic Scholar 获取器属于 data preparation。冻结资产完成后，Review、Internal Evaluation 和 Evolution 进程都不拥有 S2 网络能力。

论文数据使用 JSON record，规范标题是独立字段。S2 data preparation 以规范标题作为 exact canonical query，保存查询、结果、时间 cutoff 和内容 hash；Review Run 不再执行标题搜索，而是按稳定 `paper_id` 从所选 Frozen S2 Pool 本地读取。

0831 已有 `AgentReview/configs/frozen_s2_temporal_v2.jsonl`，共 3,889 条，覆盖 CoCo 四区的 family representative，内容 SHA256 为 `367cdff9...ef49`。它不覆盖 `OpenReview/train_clean.jsonl` 的 34,848 个 ID，因此不能冒充 external development S2 数据库。

该资产还有 self-match 缺陷：3,889 个目标中有 1,660 个（42.68%）至少包含一条与目标规范标题相同的结果，5,931 条结果中有 1,666 条（28.09%）属于这种目标论文或版本副本；当前 builder 只过滤时间，不排除 self/same-family。因此现有 `retrieved_papers` 只能称作 exact-title-search candidates，不能直接作为已验证的 related literature 注入 Reviewer。

新系统复用 record schema、canonical-title query、时间过滤、hash 和 exact-ID loader，但把硬编码 CoCo 四区的 builder 改成接受通用 paper manifest，并在冻结前执行：

1. 固定 overfetch（默认 raw 50）；
2. 按 S2 paperId、DOI、arXiv、CorpusId、DBLP 等强 ID 排除目标与 family aliases；
3. 当强 ID 缺失时，以规范化标题等于任一 family title alias 作为保守 self-match 排除；
4. 按 S2 paperId、强外部 ID、规范化标题依次确定性去重；
5. 保留 API 原始顺序，过滤后取前 K，不做 query rewrite 或 retry；
6. 保存 `self_results_rejected`、排除理由、`duplicate_results_rejected`、raw response hash 和最终结果 hash。

正式至少维护两个经过上述排除规则的新隔离资产：

```text
frozen_s2_external_development_v1
frozen_s2_coco1300_v1
```

Frozen S2 在 Review Run 中是受限 capability，不是宿主预先拼入 prompt 的固定区块。Program 的版本化 `plan_review` 对每篇论文和角色读取 Memory context，并获得只绑定当前 `paper_id` 的 evidence access。它可以零次或一次调用 `retrieve_current_s2(limit<=5)`；零次表示不需要，调用后可以选择不用，也可以选择返回集合的有序子集。Program 没有可指定任意 `paper_id` 的接口，不能跨论文读取或联网。

Program 返回结构化 Review Plan，至少冻结：

```text
protocol = evolvable-review-plan-v1
s2_disposition = not_consulted | consulted_no_use | use
selected_s2_ids
decision_label
usage_instruction
context_order = permutation(memory, s2, strategy)
review_strategy
```

可信宿主验证 plan 后只按 `selected_s2_ids` 从本次候选集合格式化 evidence。非法 ID、disposition 与 trace 不一致、重复查询或非法 context order 都使 candidate-local 执行失败，不静默回退到固定开启或关闭。三个 initial seeds 的共同 seed policy 是 `always-top5`，之后 mutation 可以改变逐 case 决策；Primary caller 始终使用 `program_autonomous` intervention。

初始双 lane 协议固定为：

```text
Memory lane
  query = canonical title + abstract
  role-specific index
  top_k = 5 Review Experience items

S2 lane
  Program may call runtime exact-paper capability once
  maximum returned candidates = 5 self-filtered frozen papers
  Program may use none or an ordered subset
```

两个 lane 各自排序，不归一化或混合分数；Reviewer context 使用独立的 `[REVIEW EXPERIENCE]` 与 `[FROZEN S2 EVIDENCE]` 区块。Program 可以进化 Memory query 字段、top-k、filter、排序、S2 call/skip、S2 子集、context 顺序与 evidence usage instruction，但不能改变冻结 S2 Pool。LLM Summarizer 只摘要已经冻结的 Memory hits。

当前两个 pool 每篇只冻结 top-5，所以第一版搜索空间能学习是否调用、查看后不用、子集、顺序和使用方式，不能学习从更宽 S2 candidate bank 中 rerank。若后续把该能力纳入研究问题，必须先为 development 与 CoCo 同步构建版本化的 wider pool（例如 top-20）并在打开 sealed test 前冻结；不能只修改 CoCo pool。

已完成的 canonical bootstrap 使用旧的固定 S2 注入，可保留为所有 Program 共享的 Review 来源语料，因为所有 candidate 看到相同 corpus；它不进入 S2 usage report，也不能用于宣称 S2 policy improvement。正式 seed/static/Evolution 在本 capability seam、provenance v2、winner reload 与 Tiny E2E 验证完成后开始。CoCo S2 pool 仍必须完成，因为它是 sealed paper 上供 winner 自主选择的能力资产，而不是 CoCo scorer 的输入。

Reviewer Memory 的检索单位必须是 Review Experience，例如审查启发、证据模式、失败模式、方法学检查点或完整 Review 的可追溯片段。历史论文正文只作为该 Review 的来源上下文，不作为 Memory chunk 直接复制给当前 Reviewer；论文外部证据由独立 Frozen S2 lane 提供。

## 7. Internal Evaluation 与 Evolution

Internal Evaluation 是 Evolution Run 的第一个离线阶段。它批量读取历史完整 Specialist Review，为 self-critique / self-improvement 生成反馈；它不是正常 Review pipeline 的尾处理，也不写回 Reviewer Memory。

Internal Evaluator 通过统一接口加载。默认配置允许它与 Reviewer 使用同一基础模型，但使用独立、版本化的 evaluator prompt 和独立调用上下文；实验可以只替换 evaluator model 而不改变 Review 或 Evolution artifact schema。

Evaluator 的固定可见输入是：

```text
Frozen Paper
Reviewer Role + frozen REVIEW_Request
Complete Specialist Review
Exact retrieved evidence shown to that Reviewer
```

输入使用匿名 candidate identity。Evaluator 看不到 Program 源码、lineage、历史分数、其他 Reviewer 输出、human review 或 CoCo。相同基础模型的 self-evaluation 是研究变量；模型 identity、prompt、temperature 与调用顺序必须记录，未来可只替换 evaluator model 做 ablation。

每条 Internal Evaluation 永久保存模型 raw output，并至少规范化为：

```text
evaluation_id
review_id
review_quality
major_missing_points
factual_evidence_issues
improvement_suggestions
evaluator_model_identity
evaluator_prompt_version
run_id
created_at
```

`review_quality` 是单一 `1–5` 整体分。固定 static batch 上的平均值是 M★ softmax parent selection 和候选排序使用的唯一标量；不计算加权子分或硬性规则 reward。Reflection 主要读取 `major_missing_points`、`factual_evidence_issues` 和 `improvement_suggestions`，而不是围绕总分做数值调参。

Evolution 产生的 Program Version 可以修改：

- Memory representation 与从完整 Review 派生 Memory 的策略；
- deterministic query construction、Retrieval、filter、ranking 与 hydration；
- 每篇 Review 的 S2 call/skip、consulted-but-unused、候选子集与 evidence usage instruction；
- Review workflow；
- Review strategy，包括如何使用已经检索出的证据。

第一版 Review Strategy 作为冻结 Request 外的独立 versioned block 注入。Workflow 可以改变确定性 context assembly、证据排序、token budget 和审稿检查顺序；每个角色对每篇论文仍只执行一次 Reviewer 模型调用，不能增加 planner、draft critic、rewrite 或 hidden retry。

Evolution 永久冻结：

- 只包含五个 Specialist 定义的 `REVIEW_Request.md`；
- `QUAL / CLAR / SIGN / ORIG / POL` 五个 Reviewer Role；
- CoCo Firewall；
- 当前实验冻结的论文、S2 与模型输入资产。

`META_SYNTHESIS_Request_v1.md` 随 Primary Meta System 单独冻结。它不是 Evolution 的 mutation target，也不是 Specialist Reviewer identity 的组成部分。

每次 mutation 的 Reflector Feedback Packet 固定包含：

```text
Parent Program source/version
Parent static mean review_quality
5 rotating failure cases:
  paper identity + compact paper context
  complete candidate Review
  retrieved Memory IDs
  S2 lookup/candidate/selected/injected IDs
  S2 disposition, decision label, usage instruction and evidence shown
  summarizer output (Summarizer only)
  review_quality
  major_missing_points
  factual_evidence_issues
  improvement_suggestions
```

Reflector 不读取 human review、CoCo、其他角色输出、其他 candidate 私有反馈或 evaluator hidden reasoning。输出是一份新的完整可执行 Program Version，并记录 M★ lineage。

### 7.1 S2 usage 与2×2归因诊断

每个 Review 的 provenance 可以派生 overall 以及按 role、Program Version、decision label 分组的 S2 usage summary。策略统计的 eligible 定义固定为：`capability_available=true` 且 intervention mode 为 `program_autonomous`。除 eligible rate 外，以下 rate 均以 eligible Reviews 为分母；无 eligible 样本时记为 `0.0` 并同时报告分母：

- lookup rate：`lookup_count > 0`；
- injection rate：`injected_ids` 非空；
- consulted-but-unused rate：disposition 为 `consulted_no_use`；
- empty-result rate：发生 lookup 且 `candidate_ids` 为空；
- mean selected count：`selected_ids` 数量的 eligible-sample 均值。

`forced_consult/forced_off` 样本按 intervention mode 单列，不能进入上述 policy rates。若还要判断 Reviewer 文本是否实际援引 evidence，应另用统一 S2 citation marker 统计 cited IDs；injection 本身不等于 textual use。

usage 行为与质量提升来源是两个问题。Memory/S2 归因只在冻结 development static set 上组装四个可比系统：

```text
baseline Memory + baseline autonomous S2 policy
evolved Memory  + baseline autonomous S2 policy
baseline Memory + evolved autonomous S2 policy
evolved Memory  + evolved autonomous S2 policy
```

四格共享论文、Reviewer identity、模型、generation config、评测器与非目标组件，各 S2 policy 仍自主运行，不用 forced intervention 替代。设四格分数依次为 `y00/y10/y01/y11`，则 Memory 主效应为 `((y10-y00)+(y11-y01))/2`，S2 policy 主效应为 `((y01-y00)+(y11-y10))/2`，interaction 为 `y11-y10-y01+y00`。该报告是 winner 冻结后的 diagnostic，不参与 candidate 选择；若要在 CoCo 上运行，必须在打开 Sealed CoCo Test 前预注册，且结果永不反馈 Evolution。

## 8. CoCo Firewall

CoCoReviewBench 通过文件交接读取 Final Review 的 `{id, text}` 视图。它自己的 ReviewSplit、ReviewClassify、23 个子类、human reviews、rebuttal、meta-review、decision、reference、judge prompt、judge trace 和 score 全部位于独立 evaluator 工作区。

这些数据不得用于：

- Reviewer prompt 或 role rubric；
- Memory 写入或检索；
- Internal Evaluation；
- Reflection 或 candidate mutation；
- parent/child/winner selection、early stopping 或超参数调优；
- 根据 CoCo 结果重跑 Evolution。

CoCo 的 Split/Classify/Judge 本身会调用生成模型。这不违反 Retrieval 禁令，因为它们只存在于外部 Evaluation stage。为保证可复现性，外部评测需要冻结 benchmark commit/文件 hash、测试 ID、reviewer selection、Split/Classify checkpoint、judge model/prompt，以及每个派生 stage 的输出。

### 五角色与 CoCo 的关系

CoCo 也使用 `QUAL / CLAR / SIGN / ORIG / POL` 作为五个顶层类别。因此 Research Core v2 不能声称其顶层架构与 CoCo 完全无关。当前建议的准确声明是：

> 系统采用与 CoCo 顶层五轴同构的固定 Reviewer 架构，但 Reviewer 的职责文本独立冻结；CoCo 的子类、提示、reference、处理结果和分数不进入审稿或演化。

### 数据隔离

Formal development 从 `OpenReview/train_clean` 选择，final 从独立 CoCo benchmark 选择。Program、Derived Memory、Reviewer/Meta 配置与实验选择全部冻结后，才允许打开 Sealed CoCo Test。

年份调查显示，没有任何非空 development cutoff 能同时保留原协议的13个 venue-year 组：例如 `dev <= 2022` 会把 final 压缩到5组，虽然剩余1,495篇直接去重后的代表论文仍可凑1,300，但已不能保持“每组100”。因此本版本不强制 temporal split，以保留 venue/year 多样性和13组各100的最终协议。

跨边界只做一次性最小直接去重：exact source ID、规范化 canonical title、规范化 paperhash，以及 exact PDF/full-text SHA256。每个 final record 独立对这些 forbidden key sets 判断并记录命中理由；不建立传递闭包 family graph、复杂 family audit 或 eligibility 层。

本地 CoCoReviewBench、其可达 Git 历史与原论文均没有发布用于系统开发的 official training/development split。`OpenReview/train_clean` 是本项目已有的独立开发来源，不把其中 human reviews、metareview 或 decision 提供给 Reviewer/Memory/Evolution。

CoCo process 与 Review/Evolution process 使用不同的路径能力。Review/Evolution 只读 paper、Frozen S2、Memory 和自身 review/evaluation artifacts；CoCo process 才能读取 benchmark reference、taxonomy、Split/Classify checkpoint 与 judge 资产。该隔离通过独立进程和显式只读 allowlist 实现，不增加全局 audit/gate。

### 最终系统与消融

打开 CoCo 前同时预注册并冻结：

- **Primary Meta System**：`meta_enabled=true`；
- **Concat Ablation**：`meta_enabled=false`，固定顺序拼接相同五份 Specialist Review。

CoCo 最终报告两套系统，但结果不能改变其 primary/ablation 身份，也不能反馈到 Reviewer、Memory、Evolution 或 Meta prompt。

五个角色各自按固定 static batch 的平均 `review_quality` 选择 top-1。第一版彻底不提供 Bundle Diagnostic、`bundle_quality`、bundle evaluator、top-k coordinate search 或相关接口。

### Formal development 三分区

最小直接去重完成后，从 `OpenReview/train_clean` 冻结三个互斥集合：

```text
Canonical Review Corpus   125 papers
Evolution static           25 papers
Evolution rotating pool   125 papers
Total                     275 papers
```

Canonical Review Corpus 为每篇论文生成五角色 no-memory 完整 Review，冻结后成为所有 seeds/candidates 共享的基础 Review 结果；每个 Program 独立构建自己的 Memory。Static 对所有 Candidate 固定。每轮从 rotating pool 确定性抽取5篇形成 Evolution Feedback。任何新 Review/Evaluation 都不进入 Canonical Corpus 或 Reviewer Memory。

抽样不读取 decision、human score、reviews 或 metareview。最小直接去重后，按13个 venue-year 尽量均衡分配每个 split 的名额；组内使用 `sha256("split-seed-0" + split_name + paper_id)` 排序。按 Canonical 125 → static 25 → rotating 125 的顺序从尚未使用的记录中冻结，保存 ID 列表、输入 JSON hash、去重结果和抽样代码 hash。

已提交 canonical bootstrap 的 legacy no-memory 精确定义为：

```text
memory_enabled = false
s2_enabled = true
```

这一定义只描述共享来源语料，不延续到 seed/static/winner Review 的 S2 控制面。125篇生成 `125 × 5 = 625` 份完整 Specialist Review，全部成功/失败 artifact 先永久保存。完成后统一运行一次 Internal Evaluation，并与 Review 分开保存。Vector/Direct 与 Summarizer 从完整 Review 派生原始 Review Experience，不按 evaluator 分数删除记录；Experience Learner 可以读取 Review 与对应 Internal Evaluation 做离线经验归纳。

### CoCo-1300 最终协议

发布资产包含 13 个 venue-year 文件，每个 300 篇，共 3,900 篇。论文主实验从每组随机取 100 篇并保持原 score distribution，共 1,300 篇；但论文没有提供 seed、第二阶段抽样算法或 IDs，仓库还显式忽略了作者可能使用的 `benchmark/*_100.jsonl` 与 `benchmark/benchmark.jsonl`。因此原始 1,300 无法从本地资产精确恢复。

Research Core v2 最终只评价一次冻结的 **CoCo-1300 Protocol-Compatible Resample**：

1. 对 13 个已发布 venue-year 组分别取 100 篇；
2. 使用 benchmark construction 相同的标准化 overall-score 区间 `[0,3) / [3,5) / [5,7) / [7,10]`；
3. 按每组实际 bucket 比例用 largest-remainder 分配 100 个名额；
4. 在稳定 paper-ID 排序后以固定 seed `0` 在各 bucket 抽样；
5. 在任何 Review、Internal Evaluation 或 CoCo 调用前写出并冻结 1,300-ID manifest、抽样代码 hash 与 source benchmark hashes。

该结果标记为 `coco_1300_protocol_compatible_resample_v1`，不能称作作者 official/original split；若未来取得作者原始1,300 IDs，应作为新的独立 protocol version，不覆盖已有结果。

## 9. M★ 的复用边界

从 M★ 复用或小型重写：

- role-scoped Program source/hash/version 与 lineage；
- 每个角色独立的 append-only candidate pool；
- softmax parent selection；
- static evaluation 与 rotating feedback 的职责分离；
- Reflector 生成补丁、编译检查和最小 smoke test；
- iteration、pool、score、program source/hash 与 RNG state 的最小 checkpoint。

Research Core v2 对齐 M★ 的“seed → evaluate → parent selection → failure reflection → candidate mutation → candidate evaluation → append pool”搜索语义，但用不可变 artifact 拆开执行：

```text
Program Version
  → separate Review Run
  → Saved Specialist Reviews
  → Internal Evaluation batch
  → Reflection + mutation
  → next Program Version
```

因此一个自动 coordinator 可以保持 M★ 的迭代体验和 checkpoint/lineage 语义，但原始 `EvolutionLoop` 不能在 evaluator 内直接调用 Review generation。五个角色各自拥有独立循环、pool、feedback 与 winner。

固定 static batch 上的平均 `review_quality` 为 pool 提供简单 fitness；rotating batch 的质性失败反馈交给 Reflector。父代选择仍可复用 M★ 的 global-within-role softmax 策略，但五个角色之间绝不共享 pool。

三个 initial seeds 保留 M★ 的原始研究身份：

1. **Vector / Direct Retrieval**：完整 Review 的派生单元进入 embedding index，确定性 top-k 后 hydration；不调用生成模型。
2. **LLM Summarizer**：确定性召回并冻结命中 ID，随后显式生成摘要，再交给 Reviewer；这是唯一 post-retrieval 生成例外。
3. **Experience Learner**：在 Evolution/Memory build 阶段从共享基础 Review 形成结构化 experience，冻结后在 Review Run 中确定性读取；read path 不调用生成模型。

所有 seeds 和 candidates 在同一 Reviewer Role 内共享同一份不可变基础完整 Review Corpus，但独立构建、保存和使用自己的 Derived Memory。原 M★ 由 TaskAgent 用 LLM 生成通用 Query/KnowledgeItem 的实现不直接复用；共享 query construction 仍保持确定性，Experience 的生成只发生在已经允许的离线 Evolution/Memory build 边界。

### M★-compatible MemoryProgram 生命周期

保留 `KnowledgeItem / Query / KnowledgeBase.write/read` 与 Program、pool、Reflector、patcher、lineage 的核心结构：

```text
Offline Memory Build
  Canonical Full Review + optional Internal Evaluation
  → KnowledgeItem
  → KnowledgeBase.write(...)
  → freeze Memory Snapshot

Review Run
  Frozen Paper JSON
  → deterministic Query
  → KnowledgeBase.read(...)
  → freeze Memory hit IDs
  → optional Summarizer transform
  → one Reviewer call
```

生成模型可以在离线 Evolution / Experience Learner 阶段生成或修改 KnowledgeItem。Review Run 禁止生成或修改 KnowledgeItem，`write()` 不可用；Query 必须由 Program 对冻结字段确定性构造，禁止生成式 rewrite、decomposition、repair、relevance judgment 和 retry。

每个 Program Version 从同一 Canonical Review Corpus 独立 build 并冻结 snapshot。子代继承父代 Program source、Review Strategy 与 lineage，不继承父代物化 Memory state。Runtime `read()` 只访问当前 role/program snapshot；LLM Summarizer 仍只能在 hit IDs 冻结后执行显式 transform。

### Reflector、patcher 与 attempt

每次 role-local Evolution Attempt 执行：

```text
select parent by softmax
→ Reflector emits one V4A patch
→ deterministic patch apply
→ compile + protocol smoke
→ on failure, at most one explicit repair call
→ compile + protocol smoke again
→ commit outcome
```

Repair 只能读取父代源码、原 patch 和 patch/compile/protocol error，不读取新论文或评价。原始 Reflector output、patch、repair output、最终源码和错误均永久保存；不 silent fallback 到父代。成功、patch 失败、compile 失败、smoke 失败或执行失败都消费一个 attempt；失败 candidate 不进入 pool。

### M★-first Candidate Sandbox

M★ 的 Program contract、dataclass/schema 校验、import allowlist、`KnowledgeBase(toolkit)` 形状和 `write/read` 调用语义保持为 candidate-visible authority。原 M★ 的同进程 `exec` 不作为安全边界，而是透明放入独立 worker/bubblewrap：

```text
no network
read-only Program source
candidate-local writable snapshot directory
no CoCo paths
M★ import allowlist
CPU / memory / timeout bounds
```

Toolkit 保持 M★ 风格接口，宿主按 stage 暴露最少 capability：Offline Memory Build 提供 embedding、允许时的 KnowledgeItem LLM 和日志；Review Run 提供 embedding、Frozen S2 read、仅 Summarizer Program 可用的 transform LLM 和日志。进程通信是内部实现，不成为新的研究 protocol、identity graph 或 audit system；classifier、CoCo、live S2、任意 filesystem、generic vector DB 和网络能力不暴露。

正式搜索参数与 M★ 对齐：

```text
initial_seeds = 3
mutation_attempts = 20
static_batch_size = 25
rotating_batch_size = 5
softmax_temperature = 0.15
winner = highest mean review_quality on static batch
```

通过源码解析、编译和最小 smoke test并完成 static evaluation 的子代进入 role-local pool，即使它低于父代也保留；执行或产物失败的 candidate 永久保存失败 artifact，但不进入 pool。Tiny profile 使用 `3–5 attempts / static 10 / rotating 3`，只验证闭环，不产生正式 winner。

不复用：

- 除离线 Experience Learner/Evolution 的 KnowledgeItem induction 与 LLM Summarizer 的显式 post-retrieval transform 外，在 Memory lifecycle 中调用 LLM；
- 在 Review Run 中用 LLM 生成 Query 或 KnowledgeItem；
- `EvolutionLoop` 内联生成 Review、打分和 final test；
- generic benchmark/evaluator/web/demo/baseline 层；
- Chroma、Azure、Weave、Redis 等非必要依赖；
- META evolution 或跨角色 global pool。

原 M★ 三个 seed 的研究身份保持不变，但运行边界按本文调整：Summarizer 只能处理冻结后的召回集合，Experience induction 移到离线 Evolution/Memory build，共享 query construction 不使用 LLM。

## 10. research_v2 替换 legacy 的迁移策略

Research Core v2 是新的唯一研究主链，不承担 0831 legacy 实验入口、artifact 或配置的长期兼容。历史实现已经由 Git 保存；完成对应 v2 能力后，可以直接删除不再服务研究目标的旧代码，而不是继续维护双主链、兼容适配器或 silent fallback。

优先保留并按新生命周期收敛的 M★ 核心机制是：

- Program 与 Toolkit；
- KnowledgeItem、Query 与 KnowledgeBase；
- Reflector 与 patcher；
- role-local pool 与 lineage；
- Candidate Sandbox。

保留意味着复用其核心接口和搜索语义；不意味着保留旧的 orchestration、在线状态变化或 CoCo 耦合。需要调整的生命周期统一为 `offline Memory Build → freeze snapshot → Review Run read-only`。

明确删除、不迁入 research_v2 主链的 legacy 组件是：

- CoCo fitness feedback；
- old formal gates、readiness/eligibility graph 与 four-zone protocol；
- effectiveness/screening pipeline；
- bundle promotion、suite/meta promotion 与组合 selection；
- online Memory writeback；
- META Program、META Evolution 与跨角色 global pool；
- legacy experiment orchestration，包括把 Review、Evaluation、Evolution 或 CoCo 串成共享可变状态的入口。

五角色定义、冻结 S2 的数据准备经验、原子 artifact 写入方式和独立 CoCo Adapter 可以按 v2 边界提取其必要逻辑，但 legacy module 本身没有兼容承诺。删除以 v2 对应能力和边界测试已经存在为条件，不以旧测试继续全绿为目标。

迁移按能力纵切，不发布双主链：

1. 建立五 Specialist identity loader；同批从 `REVIEW_Request.md` 删除 Meta 章节，新增并冻结 `META_SYNTHESIS_Request_v1.md`。
2. 建立 artifact、`prepare-data`、Retrieval、Memory 与 Review 主链。
3. 迁入并收敛 M★ 核心，建立独立 Internal Evaluation 与 Evolution stages。
4. 建立无状态 Meta/concat 和进程隔离的 CoCo Adapter。
5. 每个 v2 对应能力及边界测试通过后，立即删除相应 legacy module、CLI、config 和只验证旧协议的 test；不增加兼容适配器。

第一条最小可运行命令固定为：

```bash
python -m agentreview.research_v2 prepare-data
```

## 11. 最小必要复现信息

主链只要求能回答“相同输入是否能复核同一实验条件”的最小记录：代码 commit、运行配置、随机种子、paper/S2/memory 资产版本、五个 Program/Memory Strategy 版本、模型 identity、检索 provenance、raw/full outputs 和 stage 状态。

这些信息属于 artifact 本身或一个轻量 run record。它们不构成全局 readiness protocol，也不允许阻止已经成功的 Review 永久保存。

### 唯一 stage CLI

```bash
python -m agentreview.research_v2 <stage>
```

第一版 stages：

```text
prepare-data
bootstrap-corpus
build-memory
review
internal-eval
evolve-step
run-evolution
freeze-system
coco-eval
```

每个 stage 先完整保存输出 artifact，再由下一 stage 读取。`run-evolution` 只是按 `build-memory → review → internal-eval → evolve-step` 顺序调用独立 stage 的薄 coordinator，每一步都可单独启动、停止和复现。`coco-eval` 由该 CLI 启动独立 CoCo-only 进程；Reviewer/Evolution 进程仍无法读取 CoCo 资产。

每个 stage 只写一个轻量 `run.json`：配置、输入 artifact IDs、输出 artifact IDs、模型 identity、seed、开始/结束时间和状态。不建设全局 readiness gate、manifest graph 或 event registry。

### freeze-system

`freeze-system` 写出不可变 `system.json`，直接绑定：

```text
Review runtime
  5 role Program sources/hashes
  5 frozen Memory snapshot IDs
  REVIEW_Request.md SHA
  role definitions SHA
  Reviewer model + generation config
  embedding identity
  retrieval/query versions
  Frozen S2 Pool identity
  evolvable Review Plan protocol + capability contract
  5 role-local Autonomous S2 Policy hashes
  Primary S2 intervention identity
  Primary Meta model/prompt/config
  META_SYNTHESIS_Request_v1.md SHA
  Concat Ablation config

Reproducibility links
  code commit
  development split IDs/hash
  Canonical Review Corpus ID
  source Evolution run IDs
  evaluator/reflector/summarizer configs
```

Evaluator、Reflector 和 search controller 只作为 provenance链接，不进入 Review runtime。该 stage 只确认直接引用文件存在且内容 hash 匹配，然后原子提交 `system.json`；不执行 eligibility、全局 readiness 或外部评价。

### Failure semantics

```text
Paper-local failure
  one identical retry
  → still failed: save raw failure
  → static review_quality = 1.0
  → continue other papers

Candidate-local failure
  Program build / snapshot / general execution failure
  → attempt failed, candidate excluded from pool
  → continue search

Infrastructure failure
  model service / disk / committed checkpoint unavailable
  → stop current run
  → resume from last committed artifact
```

Canonical Corpus bootstrap 不要求单次 `625/625` 成功。成功 Review 立即永久保留，失败项定向重试；重试耗尽后按每个角色实际成功集合冻结，同一角色的所有 Candidate 使用完全相同的冻结集合。失败不能使先前成功记录作废。

### Artifact layout 与原子提交

```text
artifacts/
  data/
  s2/
  reviews/<run_id>/<paper_id>/<role>.json
  final_reviews/<run_id>/<paper_id>.json
  evaluations/<run_id>/<paper_id>/<role>.json
  programs/<role>/<version>/
  memories/<role>/<version>/
  evolution/<role>/<run_id>/
  coco/<run_id>/
  runs/<run_id>/run.json
```

完整 Review、Internal Evaluation 和 Final Review 每条写入独立临时 JSON 后用 `os.replace` 原子提交。Review JSON 同时保存完整 `review_text`、raw model output、Program/Memory/Retrieval provenance。JSONL index、Chunk、embedding、vector index 和聚合报告都是派生资产，可以删除重建；一级 JSON 一旦提交便不原地修改。

### 能力测试与 Tiny E2E

第一版能力测试必须证明：

1. 完整 Review 先于 Chunk/Embedding 保存，派生数据可重建；
2. 五角色的 Memory、snapshot、pool 与 lineage 相互隔离；
3. Review Run 不调用 Internal Evaluation、Reflection、`write` 或 Evolution；
4. Query/召回确定性，Summarizer 只能处理冻结 hit IDs；
5. Frozen S2 无网络、排除 self/same-family，Program 只能对当前 `paper_id` 零次或一次查询；
6. 新 Review 不写入冻结 Memory，只形成 Evolution Feedback；
7. Meta 无 Memory/Evolution，失败不 fallback concat；
8. CoCo 资产对 Review/Evolution 不可见；
9. Paper-local failure 不使其他成功 Review 作废；
10. Candidate failure 消耗 attempt 但不进入 pool；
11. concurrency 参数不改变 Program/System identity；
12. Program 能分别产生 call、skip、empty-result、consulted-but-unused 和 subset-use plan，非法 ID/trace 被拒绝；
13. winner freeze/reload 后通过同一 autonomous interface 决策，Primary caller 不提供固定 S2 on/off；
14. usage summary 排除 forced diagnostics，2×2 attribution 的主效应与 interaction 由四格纯计算复现。

Tiny E2E 顺序为：

```text
2 papers × 5 roles no-memory + autonomous-S2 smoke
→ 10 papers: no-memory vs 3 seeds
→ 1 role × 3 seeds × 3 attempts
→ 5 roles each complete 1 tiny evolution
→ save 5 complete Specialist Reviews
→ generate Primary Meta and Concat Ablation
→ stop before CoCo
```

完成标准是 Review、snapshot、feedback、patch、lineage 和两个 Final Review 身份全部可追溯，并且 Review/Evolution/CoCo 边界测试通过。旧测试全绿不能替代这些研究能力测试。

## 12. 设计树结论

设计树已经闭合。默认模型与 generation config、Specialist/Meta prompt 边界、research_v2 stage 和 Autonomous S2 Capability 均已确认。固定 S2 canonical corpus 可以保留；正式 Evolution 尚未开始，因此 migration 的生效边界是 seed/static/Evolution 之前完成 capability、provenance、mutation、winner reload、usage 与 Tiny E2E 验证，不改写已经提交的 canonical artifact。

## 13. 明确不进入第一版

- META Evolution；
- CoCo 驱动的调参或筛选；
- 除冻结命中集合后的 LLM Summarizer 外，任何 retrieval-time 生成式摘要、改写或 retry；
- 跨角色 Memory 或 candidate pool；
- 243 种五角色组合的暴力搜索；
- giant manifest/gate/audit/eligibility registry；
- 一条记录失败导致整个 corpus 作废；
- 为兼容 legacy 而提供 silent fallback。
