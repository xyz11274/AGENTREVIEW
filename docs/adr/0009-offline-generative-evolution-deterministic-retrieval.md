---
status: accepted
---

# ADR-0009：离线生成式 Evolution、确定性召回与显式 Summarizer 例外

Research Core v2 允许 Internal Evaluator、Reflector 和 candidate builder 在 Evolution Run 中使用生成模型，并允许它们产生可追溯的 candidate-local Derived Memory；共享 query、召回、筛选和排序始终确定性完成。保留 M★ 的 LLM Summarizer Seed 作为唯一例外：它只能在命中 ID 冻结后显式摘要固定集合，不能改变召回或 retry。Reviewer Memory 只存 Review Experience，不复制历史论文正文；S2 使用独立冻结 lane，三个 initial seeds 默认启用但 candidate 可显式关闭。
