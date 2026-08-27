# 五条 Specialist Evolution 独立，以冻结局部 Fitness 进化，再做系统级 Suite Selection

状态：accepted（2026-08-27）。

`QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL` 保留五条独立 Specialist Evolution：每条拥有自己的 Corpus、controller、lineage、checkpoint 和 **Specialist-Local Fitness**，一次 Candidate evaluation 只生成该角色的 review。比较不同 Memory method 前，该角色的 evaluator identity、指标、权重与聚合规则必须预先冻结；不得为不同 method 更换 evaluator 或调整权重。

单角色 fitness 不被表述为最终系统 CoCo 表现。各 Specialist Evolution 产生冻结候选后，再将五份 Specialist Review 组成 **Specialist Review Bundle**，以全局 CoCo 进行 **Suite Selection**；选定的五角色 **Review Suite** 完全冻结后才进入 C Holdout。Suite Selection 不回头 mutation 或重开已完成的 Specialist lineage。META 默认关闭，系统级输入是按冻结角色顺序确定性导出的 Bundle。

实验独立不意味工程上串行。五条 Evolution 可同时向一个全局、有界、公平的 Qwen 队列提交不同角色/论文的请求；不为了评一篇论文强制凑齐五个角色。每条 Evolution 初始最多占用一个 Qwen slot，调度必须防止某一角色饥饿或垄断服务。Qwen 服务的目标 `max-num-seqs` 为 4，实际并发从 2 开始容量验证，通过后再升至 4；CoCo 使用独立有界队列和 backpressure。

## Considered Options

- **一个 Candidate 同时生成五角色 Review，直接以全局 CoCo 进化**：拒绝；破坏五条 Evolution 的独立归因并放大单次评估成本。
- **五条 Evolution 完全串行**：拒绝；实验独立不要求资源闲置。
- **独立 Specialist-Local Evolution + 全局 Suite Selection + 公平有界调度**：采纳。

## Consequences

- Specialist 分数只支撑角色内 Memory method 比较；最终系统声明必须来自冻结 Review Suite 的全局评估。
- 运行产物必须分别记录 Specialist evaluator identity、Suite evaluator identity 与调度/容量参数。
- 仅调整 vLLM `max-num-seqs` 不会产生 Evolution 并行；controller/evaluator 必须通过共享队列实际提交并发工作。
