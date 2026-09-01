# Research Core v2 架构

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

Meta 读取 Frozen Paper 与五份固定顺序完整 Review。它不能读取 Memory、Program、S2、Internal Evaluation 或 CoCo。Concat 是独立 ablation。Meta 失败不会回退到 concat。

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

## M★-aligned Evolution

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
- `REVIEW_Request.md` / `META_SYNTHESIS_Request_v1.md`：版本化 prompt source。

旧 screening、online Memory writeback、CoCo fitness、formal gates、bundle promotion、六任务 Evolution、viewer 和 legacy orchestration 已删除；需要追溯时使用 Git 历史。
