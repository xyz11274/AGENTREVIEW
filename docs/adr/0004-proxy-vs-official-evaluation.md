# ADR-0004：Fitness、Proxy、Sealed D 与 Official CoCo 分层

- 状态：Accepted
- 适用范围：Evolution 选择、C 区效果筛选、D 区密封测量与外部 CoCo 评测

## Context

Evolution fitness 直接参与候选选择。效果筛选需要独立测量 Program 相对 Seed 与 baseline 的收益。D 区需要 formal gate 保护的一次密封套件测量。CoCoReviewBench 自带程序产生另一类外部评测结果。

当前 temporal v2 提供：

- B 区 299 个 family targets；
- C 区 300 个 family targets；
- D 区 899 个 family targets。

## Decision

系统使用四类评测身份：

1. **Evolution Fitness**：在 B 区执行，服务候选选择、Reflector 反馈与 Program promotion。
2. **CoCo-style Proxy Screening**：在 C 区执行，比较 no-memory、三个 Seed 和三个 evolved lineage arms，报告 proxy 身份。
3. **Sealed D Suite Evaluation**：`final-evaluate-suite` 在 formal gate 后执行六任务 D 区测量，在每份 Review 生成后使用固定 ReviewSplit/ReviewClassify revision、`CoCoMetricBenchmarkAdapter` 与 `CoCoReviewFitness` 内联计分，最终持久化紧凑套件报告并标记 `sealed_d_coco_style` 身份。
4. **Official CoCo Evaluation**：`agentreview.coco_eval` 消费已持久化的 `candidate_reviews.jsonl`，调用 CoCoReviewBench 自带 split、classify 和 evaluation 程序，持久化各阶段产物并报告 official 身份。

Benchmark Adapter 位于 Reviewer 外层。Sealed D 以进程内不可变 `ReviewSubmission` 作为逐条计分边界；Official CoCo 以已持久化的 Candidate Review artifact 作为运行边界。

## Metric identity

每份结果记录以下来源：

- data zone 与 frozen manifest SHA；
- metric identity；
- evaluator code、model revision 与 prompt SHA；
- Program、selection、suite/meta manifest identity；
- generation model 与 runtime provenance。

CoCoReviewFitness 使用 CoCo 的指标设计作为参考。Official 分数由固定 CoCoReviewBench protocol files 和自带三阶段程序产生。两类结果各自保存名称与 provenance。Official Adapter 持久化 `candidate_official_input.jsonl`、split、classify、reviewer selection、protocol identity 与 evaluation；Sealed D 持久化六任务聚合报告和身份哈希。

## Consequences

- B 区指标用于搜索，C 区指标用于独立效果筛选，D 区 sealed 指标用于密封套件测量。
- Effectiveness Screening 逐 lineage 报告 evolved − seed、seed − baseline 与 evolved − baseline。
- Program 与实验配置在进入 D 区前完成冻结。
- `final-evaluate-suite` 执行 formal protocol gate、D 区数据校验、Review 生成与 inline sealed CoCo-style metric。
- `agentreview.coco_eval` 从冻结 Candidate artifact 启动，独立校验官方 protocol files、benchmark files、prompt、selection 与三阶段参数。
- 新 benchmark 通过新的 Benchmark Adapter 接入，并携带独立 metric identity。
