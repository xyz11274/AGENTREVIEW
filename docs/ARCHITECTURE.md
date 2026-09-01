# Research Core v2 架构

Research Core v2 由彼此隔离的五个 stage context 组成：Data Preparation、Review、Internal Evaluation、Evolution 和 External CoCo Evaluation。唯一共享对象是已保存且可哈希的不可变 artifact。

## Review runtime

```text
Frozen Paper ─┬─ deterministic role-local Query → Frozen Memory Snapshot ─┐
              └─ exact paper_id lookup → selected Frozen S2 Pool ────────┤
                                                                          ▼
                                  QUAL / CLAR / SIGN / ORIG / POL (独立调用)
                                                                          │
                                           five complete Specialist Reviews
                                                                          │
                              ┌───────────────────────────────────────────┴────┐
                              ▼                                                ▼
                    stateless Meta synthesis                       fixed-order concat
                              └─────────────────── Final Review ────────────────┘
```

每份原始模型输出先写 `*.raw.json`，再写完整 Specialist Review。Review 只读 Memory snapshot；生成后没有 evaluator hook 或 writeback。Retrieval 不调用生成模型。`llm_summarizer` seed 是唯一例外：deterministic top-k 和 hit IDs 已确定并记录后，只允许一次 summary generation，失败即失败。

Meta 读取 Frozen Paper 与五份固定顺序完整 Review。它不能读取 Memory、Program、S2、Internal Evaluation 或 CoCo。Concat 是独立 ablation。Meta 失败不会回退到 concat。

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

## Data and S2 isolation

Development 只使用 OpenReview `train_clean` 的三组互斥数据：Canonical Review Corpus 125、Evolution static 25、Evolution rotating pool 125。只做 exact ID、canonical title、paperhash 和 full-text hash 的最小直接去重。

CoCo 最终集合是 `coco_1300_protocol_compatible_resample_v1`：13 个 venue-year 各100篇，按实际 overall-score bucket 比例 largest remainder 分配名额，seed 0 稳定选择。它明确不是作者未公开的 official/original IDs。

Frozen System 绑定该 manifest 的路径、文件 SHA、内部 manifest SHA 和1,300计数；`coco-eval` 在启动外部进程前要求 Final Review IDs 与冻结集合完全相等。

两类 paper 分别绑定 `frozen_s2_external_development_v1` 与 `frozen_s2_coco1300_v1`。S2 网络客户端只存在于 data-preparation stage；Review 按 Frozen Paper 的 dataset identity 选择正确本地 pool。

## Minimal code ownership

- `agentreview/research_v2/`：所有 v2 stage、artifact schema、M★ Evolution 和 CLI。
- `agentreview/reviewer_identity.py`：五 Specialist 与独立 Meta identity loader。
- `agentreview/reviewer_runtime/`：Program sandbox、worker、Toolkit、离线 KnowledgeItem TaskAgent。
- `memory_evolution/embedding_adapter.py`：保留的 Qwen3 embedding backend。
- `REVIEW_Request.md` / `META_SYNTHESIS_Request_v1.md`：版本化 prompt source。

旧 screening、online Memory writeback、CoCo fitness、formal gates、bundle promotion、六任务 Evolution、viewer 和 legacy orchestration 已删除；需要追溯时使用 Git 历史。
