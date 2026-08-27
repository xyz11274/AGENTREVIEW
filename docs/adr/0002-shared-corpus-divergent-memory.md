# 共享原始论文，经验与 Memory 自 Program 运行起分叉

> **状态：superseded by ADR-0008（2026-08-27）。** 本 ADR 仅用于解释旧 run；新主线改为共享冻结的 no-memory AI Full Review Corpus，再进化确定性 Memory 策略。

> 2026-08-17 按 ADR-0005 口径重写（原题为「共享历史经验语料，允许 Memory 内容分叉」，正文曾把共享对象写成固定 Historical Review Corpus——该表述作废）。

Effectiveness 实验中，所有 Seed / Evolved 共享**同一批原始历史论文**（Shared Raw Material：论文全文 + candidate-visible metadata + source anchors）。每个 Program 从运行开始即自主产生自己的审稿经验（Self-Generated Experience：自己的 review / trajectory / evidence），并自行决定哪些值得记、怎么组织、怎么检索。分叉链条固定为：`Shared Raw Material → Program 运行 → Self-Generated Experience 分叉 → Memory 分叉`——不存在「共享固定 Review、KnowledgeItem 阶段才开始分叉」的形态。要验证的命题是：同样的历史论文任务，经进化后的审稿与记忆策略，能否提高 held-out 论文上的 CoCoReviewBench 表现。

## Considered Options

- **共享固定经验库、程序二次加工**（Frozen Library Route：固定 no-memory Reviewer 预生成 AI review，所有 Seed 从中提取）：被否决。它把「审稿经验如何产生」也提前固定了，Seed 的差异退化为对既有 review 的再加工。
- **共享原始论文、经验自产**：被采纳。归因单位是整个 Program——包括它的经验生成策略、记忆架构与利用方式，这也正是部署单元。

## Consequences

- 两臂必须使用同一批历史论文与同一 candidate-visible metadata；输入差异会污染归因。
- 真人 review / gold opinions 不得进入共享材料（Human Reference Boundary，见 ADR-0005）。
- 实验结论的表述边界：收益归属于 Program 整体策略，不能单独分离 schema、write、read 各自的贡献（除非另做消融）。
- 历史论文集与 Effectiveness Holdout 必须按 paper ID 严格隔离（已验证，见 CONTEXT.md）。
