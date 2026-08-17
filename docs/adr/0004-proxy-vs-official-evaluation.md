# Proxy 测量与官方评测分离，内部 fitness 与外部 benchmark 分开报告

当前最小闭环可在 C 区预注册子集上运行，报告与进化 fitness 同构的指标（judge 五维、类别覆盖率、调和平均），用于闭环、调试和观察 evolved program 是否沿进化目标产生真实效果。正式结论必须在 frozen D manifest（当前 900 篇；CoCo source pool 共 3900 篇）上、按官方 evaluation pipeline 和官方指标重新跑出。C 结果不得表述为官方 CoCoReviewBench 成绩；进化时优化的 fitness 不得等同于最终 benchmark 指标。

## Considered Options

- **demo 直接用官方 benchmark 子集**：被否决。开发调试期反复使用正式 benchmark 会造成适应性过拟合（adaptive overfitting），且官方评测有工程前提未就绪（reviewer selection 文件缺失、ReviewClassify checkpoint 待验证）。
- **只报 fitness 同构指标作为最终证据**：被否决。fitness 是进化优化的目标函数，直接拿它当最终证据会让"进化有效"的结论循环论证；官方 pipeline（split → classify → evaluate）是独立的外部验证通道。
- **proxy 闭环 + 官方终验、两套指标分开报告**：被采纳。

## Consequences

- 所有报告/论文图表必须标注分数来源：proxy-development 还是 official-benchmark。
- fitness 同构指标在 demo 中仍有价值（直接反映进化目标的达成度），但只能与官方指标并列呈现，不能替代。
- 官方评测前的工程前提（`benchmark_reviewer_selection.json` 重新生成、ReviewClassify checkpoint 完整性验证）登记在 docs/TODO.md，是发布任何正式结论的阻塞项。
- CoCoReviewBench 是**当前第一个正式外部 benchmark，不是系统的唯一目标**。系统保持 benchmark-agnostic（见 ADR-0005 的 Benchmark Adapter）：未来接入其他 review benchmark 只新增 adapter，不改 Reviewer 核心；本 ADR 的「proxy/official 分离、fitness/benchmark 分开报告」原则对任何后续 benchmark 同样适用。
