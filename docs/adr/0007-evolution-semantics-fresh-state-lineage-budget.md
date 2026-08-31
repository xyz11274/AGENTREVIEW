# ADR-0007：Fresh-State Replay、全局候选池与 20-Attempt 搜索

- 状态：Accepted
- 适用范围：MemoryProgram evaluation、parent selection、lineage、预算与恢复

## Context

MemoryProgram 的效果同时取决于源码和它从历史论文构建的运行时 Memory。可解释比较需要让每个候选从同一初始条件构建自己的 Memory。搜索还需要明确的 attempt 单位、统一候选池与可恢复控制器状态。

## Decision

### 1. Fresh-state historical replay

每个 Seed、Candidate 与 Program evaluation 从空 Memory State 开始，按冻结顺序执行同一 run-scoped Historical Episode Selection：

> Paper_i loaded → Program reads prior Memory → TaskAgent generates Review_i → TaskAgent constructs KnowledgeItem_i → Program calls `write(item, raw_text)` → checkpoint commits completed prefix

Formal Evolution 默认从 25 个 Static Validation target 选择 `5 × 25 = 125` 个 A 区 Episode。每个 B 区 target 从相同的历史 baseline snapshot 开始。B 区生成结果用于评价和反馈，baseline snapshot 保持稳定。

### 2. Code lineage

候选继承父代 Program 源码和 provenance。每次评价建立新的运行时 Memory。Lineage 记录：

- parent candidate 与 source SHA；
- origin Seed；
- attempt ID；
- mutation、quality、compile 与 evaluation outcome；
- fitness 和 promotion provenance。

### 3. Global quality-passing pool

搜索先评估三个 Seed，再将通过质量门禁且完成 Static Evaluation 的 Seed 加入全局候选池。每次 Evolution Attempt 从全局池选择父代。完成 Static Evaluation 的合格子代加入同一全局池。默认策略以分数 softmax 采样，temperature 为 0.15；best policy 作为显式 ablation。

Lineage 用于来源追踪和 lineage champion 统计。Parent selection 面向完整全局池。

### 4. Attempt budget

默认搜索包含 20 个 Evolution Attempts。一次 attempt 覆盖：

1. parent selection；
2. rotating feedback；
3. Reflector 生成完整子代源码；
4. protocol、source、compile 与 quality gates；
5. static evaluation；
6. outcome commit。

每个 outcome 消费一个 attempt，包括 Reflector/变异生成失败、门禁拒绝、编译失败、质量失败与评估结果。固定 attempt 预算直接表达实际搜索机会与成本。

### 5. Selection

- Static Selection 默认 25，用于跨候选比较。
- Rotating Selection 默认 5，用于构造新的变异反馈。
- Selection Repeats 默认 3。
- Selection Top K 默认 3。
- Overall selection 先取 Static Score 前 3 名，再各执行 3 次重复评估；最高均值获胜，方差作为同均值 tie-break。
- 每条 provenance lineage 的 Static Score 最高候选形成 lineage champion。

### 6. Feedback boundary

Reflector 接收 scalar fitness、安全聚合指标、预定义错误类别、运行失败类型与资源统计。Evaluator-only human/gold 内容由外部评价边界管理。

### 7. Checkpoint and resume

History checkpoint 绑定：

- checkpoint protocol version；
- Candidate、lineage、replicate 与 Program SHA；
- Historical manifest 与配置 identity；
- 完成 Episode 的严格冻结顺序 prefix 与 `next_paper_index`；
- Memory snapshot 内容身份与已提交 sidecar 集合。

Search controller 原子记录：

- 已提交 Seeds 与 Candidates；
- attempt cursor；
- RNG state；
- selection repeats；
- pool、scores、lineage 与 trace 日志边界。

相同 run identity 恢复已提交前缀，并从下一个完整工作单元继续。

### 8. Resource provenance

正式运行记录：

- LLM calls 与 token usage；
- context 和 memory context size；
- embedding 与 Frozen S2 usage；
- latency 和可得的 cost；
- model、revision、generation parameters；
- sandbox memory、timeout 与 capability counters。

`runtime_resources.json` 记录 runtime policy version 与 sandbox memory；`llm_runtime.json` 记录 logical model、endpoint preflight、base URL history 与 runtime embedding namespace；`selection.json`、`embedding_closure_smoke.json`、`evolution_budget.json` 和两类 checkpoint 分别记录选择、闭环验证、预算与恢复身份。

资源统计用于 performance–cost 分析。同一正式比较共享相同系统级资源边界。

## Consequences

- Fitness 收益归因于当前 Program 源码与其自生成经验策略。
- 三个 Seed 保持独立起点；完成 Static Evaluation 的合格 Seed 与子代共同参与全局父代选择。
- 20 次 attempt 的每种 outcome 都进入预算与 provenance。
- overall champion 表达全局最优；lineage champion 表达来源维度的最佳结果。
- evolution_budget.json、scores.jsonl、lineage.jsonl 和 controller checkpoint 共同解释完整搜索过程。
- Effectiveness Screening 以 lineage-qualified Seed/Evolved pairs 测量提升效果。
