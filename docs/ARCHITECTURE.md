# Research Core v2 架构

> 0915 当前运行结构见 [共享 M★ 实现](SHARED_MSTAR_IMPLEMENTATION.md)：共享候选池与五库独立、标准 CoCo `Paper.` 选优、严格用途分区及独立结果报告。本文后续保留 0831 运行结构；Internal Evaluation、全部 CoCo 外置与五套独立进化描述用于解释旧代码。

> 0915 算法方向已采用 [ADR-0014](adr/0014-mstar-paper-guided-memory-evolution.md) 的 M★ 原文机制。旧实现中的确定性查询限制、具体搜索参数和非记忆工作流变异范围需要按新实验协议逐项核定；原文与本地代码差异以 ADR 中的核验为准。

> 0915 主指标已由 [ADR-0015](adr/0015-paper-fitness-and-separate-results-report.md) 确定为标准 CoCo Table-1 `Paper.`。统一结果报告在原始产物落盘后通过独立后处理生成，与初次评审生成和评分流程分别执行。

> 0915 有限失败恢复与完整评价准入按 [ADR-0016](adr/0016-bounded-failure-recovery.md) 落实到共享入口。协议与数据校验已接通，真实部署模型的小规模验收和正式参数冻结仍待完成。

## 0915 共享运行模块

| 模块 | 职责 |
|---|---|
| `experiment_protocol.py` | 显式冻结配置、用途分区、来源隔离与封存访问 |
| `shared_evolution.py` | 单池 softmax、当前轮反馈、候选预算、Champion 与恢复 |
| `shared_experiment.py` | 五库构建、评审生成、CoCo 交接、系统冻结与最终测试 |
| `development_coco.py` / `development_coco_worker.py` | 原标准 CoCo 执行与完整评分凭据 |
| `experiment_requests.py` | 请求预算、原始返回与恢复 |
| `coco_native.py` | CoCo 原始提示与提取流程基线 |
| `experiment_report.py` | 另行生成逐篇明细、各 seed 汇总与角色诊断 |

## 0831 历史运行结构

Research Core v2 由彼此隔离的五个 stage context 组成：Data Preparation、Review、Internal Evaluation、Evolution 和 External CoCo Evaluation。唯一共享对象是已保存且可哈希的不可变 artifact。

具体部署参数、S2 单路 1 request/s 约束、CoCo/Review 并行条件和常见环境问题见 [EXPERIMENT_CONFIGURATION.md](EXPERIMENT_CONFIGURATION.md)。

## Review runtime

```text
Frozen Paper ─┬─ deterministic role-local Query → Frozen Memory Snapshot ───┐
              └─ current-paper Frozen S2 Capability ─────────────────────┐  │
                                                                         ▼  ▼
                    evolved Program plans call/skip, selection and usage
                                      │
                                      ▼
                      QUAL / CLAR / SIGN / ORIG / POL (独立调用)
                                      │
                       five complete Specialist Reviews
                                      │
                      ┌───────────────┴────────────────┐
                      ▼                                ▼
            stateless Meta synthesis         fixed-order concat
                      └────────── Final Review ─────────┘
```

每份原始模型输出先写 `*.raw.json`，再写完整 Specialist Review。Review 只读 Memory snapshot；生成后没有 evaluator hook 或 writeback。Program 对每篇论文自主决定是否调用 current-paper S2 capability，调用后可以不用或只选择返回集合的子集；可信宿主按选中 ID 格式化 evidence。Retrieval 不调用生成模型。`llm_summarizer` seed 是唯一例外：deterministic top-k 和 hit IDs 已确定并记录后，只允许一次 summary generation，失败即失败。

Primary 的宿主配置不提供 S2 on/off 决策。`forced_consult/forced_off` 只属于带独立 identity 的 diagnostic intervention；`forced_consult` 仅要求查询，并不强制选择或注入。winner reload 后仍通过同一个 Program planning seam 自主决定。Review provenance 分别保存 Memory 的 `logs/context` 和 S2 的 capability、intervention、call/selection/injection trace、使用指令、context 顺序以及 pool/context hash。

Meta 读取 Frozen Paper 与五份固定顺序完整 Review。0915 使用 `META_SYNTHESIS_Request_v2.md` 生成结构化观点：每项独立保存结论、来源角色、证据 anchor 和建议，宿主校验字段及实际引用，再逐项以空行分隔的 Markdown 段落渲染 Final Review。原始模型输出先落盘，结构化内容和渲染结果共同保存；恢复时重新校验其对应关系。结构合法性有确定性检查，语义独立性通过实际生成与评价检验。

Meta 的 request、output、rendering 与冻结 identity 均具有 v2 身份，v1 文件保留为历史证据。标准 CoCo 原有 Split/Classify 和评分逻辑保持原样。Meta 输入限于论文和 Five-Review Bundle；Concat 保持独立 ablation，Meta 输出契约失败保留其原始响应与结构化错误记录。

Canonical bootstrap 通过一个 bounded runner 最多保持8个 Specialist invocation 在途。每个 run ID 有跨进程独占锁；raw response、完整 Review、请求 identity 与 generation/retry identity 完整绑定。模型 endpoint 或共享 runtime 故障会停止补充队列并保留 pending checkpoint；paper-local 空输出失败不会伪装成基础设施故障。同 run ID 恢复时只跳过通过完整 artifact 校验的角色。

## Offline Memory lifecycle

```text
complete canonical Reviews + optional Internal Evaluations
  → Program.KnowledgeItem derivation
  → KnowledgeBase.write
  → immutable role-local snapshot
  → Review Run read-only
```

`vector_search` 与 `llm_summarizer` 以确定性方式从完整 Review 派生 experience；`experience_learner` 和 evolved Program 仅在离线 build-memory 阶段允许生成 KnowledgeItem。五个角色共享 canonical no-memory Review 来源，但分别构建、冻结和读取自己的 Memory。

## 历史 M★-aligned Evolution

每个角色拥有独立的 `Program / pool / Reflector / patcher / lineage`：

```text
three seeds with static evaluations
  → softmax parent selection (temperature 0.15)
  → rotating complete Reviews + qualitative Internal Evaluation feedback
  → one Reflector V4A patch (+ at most one repair)
  → AST capability check + sandbox protocol smoke
  → separate static Review/Evaluation batch
  → append candidate to role-local pool
```

Internal Evaluation 可复用同一个基础模型，也保留独立 evaluator identity 接口。主要反馈是 `review_quality / major_missing_points / factual_evidence_issues / improvement_suggestions`，不构建复杂固定 reward。CoCo、human references、其他角色输出和 Meta 均不进入 feedback packet。

Program mutation 同时覆盖 Memory lifecycle、Review Strategy 与 Autonomous S2 Policy。S2 policy 的有效 mutation 可以改变 call/skip、查询后不用、evidence 子集、context 顺序、decision label 和 usage instruction；它不能改变 Frozen S2 Pool、跨论文查询或增加 Reviewer 模型调用。

## 0915 失败控制与评价凭据

`failure_policy.py` 为旧进化运行器记录候选修复、整轮修复和候选失败预算，操作前持久化预算预留，恢复复用原记录。当前工程默认分别为 1、10、5，运行配置可调整；科学实验的搜索规模、样本量和重复次数继续独立确定。

可复核的候选契约违规进入候选失败处理。共享数据、评价、服务与归因未定的故障暂停整轮；恢复需要包含原因、修复和验证的凭据，并建立新的评价版本。受影响候选完成统一重评后，完整 Evaluation Receipt 才能支持再次排序和发布。预算耗尽时保存终止原因及原 Champion，当前轮次保留未完成身份。

`evolution.py` 的评价凭据绑定论文清单、Review/Evaluation 文件及哈希、程序和 snapshot 身份、评分 identity、逐篇分数与均值；候选池和冻结发布读取这些凭据。此处当前绑定的是历史角色内评 `review_quality`。共享 Program Version 的五角色运行、最终 Meta Review 和标准 CoCo `Paper.` 的端到端候选评价仍属于后续迁移，按其实际实现与验证单独验收。

## Data and S2 isolation

Development 只使用 OpenReview `train_clean` 的三组互斥数据：Canonical Review Corpus 125、Evolution static 25、Evolution rotating pool 125。只做 exact ID、canonical title、paperhash 和 full-text hash 的最小直接去重。

CoCo 最终集合是 `coco_1300_protocol_compatible_resample_v1`：13 个 venue-year 各100篇，按实际 overall-score bucket 比例 largest remainder 分配名额，seed 0 稳定选择。它明确不是作者未公开的 official/original IDs。

Frozen System 绑定该 manifest 的路径、文件 SHA、内部 manifest SHA 和1,300计数；`coco-eval` 在启动外部进程前要求 Final Review IDs 与冻结集合完全相等。

两类 paper 分别绑定 `frozen_s2_external_development_v1` 与 `frozen_s2_coco1300_v1`。S2 网络客户端只存在于 data-preparation stage；全机只允许一个串行构建进程，任意两次请求启动至少间隔1秒，development 与 CoCo 不并发。Review 按 Frozen Paper 的 dataset identity 暴露正确本地 pool 作为 capability，而不是固定注入。当前 pool 每篇最多保留 top-5，因此第一版 Evolution 能学习 call/skip、查看后不用、子集选择和使用方式，不能从更宽候选集学习 reranking。

Canonical bootstrap 的固定 `memory=false, s2=true` Review 可以继续作为共享来源语料；它不属于 winner，也不能证明 S2 policy 已进化。正式 seed/static/Evolution 必须在 autonomous seam 迁移和 Tiny E2E 通过后开始。CoCo S2 pool 仍是最终论文可调用的冻结能力资产，即使 evolved Program 对部分论文选择不用。

## S2 usage 与归因

`summarize_s2_usage` 从不可变 Review provenance 派生 overall 及按 role、program version、decision label 分组的报告。策略率的统一分母仅包含 capability 可用且 intervention 为 `program_autonomous` 的 Review；报告固定包含 eligible、lookup、injection、consulted-but-unused、empty-result rate 和平均 selected 数，强制诊断样本单列。

usage rate 描述行为，不单独证明质量提升来源。归因诊断在冻结 development set 上运行 baseline/evolved Memory × baseline/evolved S2 policy 四格组合，并计算 Memory/S2 policy 主效应和 interaction。四格都保留 Program 自主决策，不用 `forced_consult/off` 代替某一格，也不参与 winner 选择或读取 CoCo 结果。

## External evaluation scheduling

CoCo 的 Split/Classify 与 27B Review 是独立服务和独立进程。某实验臂 A 满足1,300篇 exact-set barrier 后，其整臂 CoCo scoring 可以和实验臂 B 的 Review 同时排队运行，前提是服务使用不同物理 GPU。Official candidate runner 只消费已保存 Final Review 并连接常驻 endpoint，不拥有 vLLM 的启动、停止、清理或 GPU pin，因此不会在 CoCo 运行期间让 27B 空闲，也不会全局 `pkill` 其他服务。CoCo 结果仍严格禁止进入 Review、Internal Evaluation 或 Evolution。

## Minimal code ownership

- `agentreview/research_v2/`：所有 v2 stage、artifact schema、M★ Evolution 和 CLI。
- `agentreview/reviewer_identity.py`：五 Specialist 与独立 Meta identity loader。
- `agentreview/reviewer_runtime/`：Program sandbox、worker、Toolkit、离线 KnowledgeItem TaskAgent。
- `memory_evolution/embedding_adapter.py`：保留的 Qwen3 embedding backend。
- `REVIEW_Request.md` / `META_SYNTHESIS_Request_v2.md`：当前版本化 prompt source；Meta v1 保留历史身份。
- `configs/frozen_meta_synthesis_v2.json`：结构化 Meta 生成与渲染的冻结 identity。
- `agentreview/research_v2/failure_policy.py`：持久化失败预算、恢复版本和发布条件。

旧 screening、online Memory writeback、CoCo fitness、formal gates、bundle promotion、六任务 Evolution、viewer 和 legacy orchestration 已删除；需要追溯时使用 Git 历史。
