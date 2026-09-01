---
status: accepted
---

# ADR-0008：分离 Review、Evolution 与外部 CoCo

Research Core v2 将 Review Run、Evolution Run 和 CoCo Evaluation 设为通过不可变 artifact 交接的独立 stage，因为在线 writeback、单次 Review 后立即评价和 benchmark feedback 会破坏实验归因与测试隔离。Internal Evaluation 是 Evolution Run 内对已保存完整 Review 执行的批量模型评审；Meta 只作为可选无状态汇总器，完整 Review 始终先于任何 Chunk 或 embedding 保存。`REVIEW_Request.md` 只冻结五个 Specialist Reviewer 定义，Meta 由独立版本化的 `META_SYNTHESIS_Request_v1.md` 定义，不属于 Reviewer Role 或 Specialist Evolution。Meta 只输出包含五角色 synthesis、evidence anchors、recommendation 和 actionable suggestions 的 Final Review，不产生自定义分数或独立 confidence 指标。本决策取代 ADR-0004 至 ADR-0007 中关于内联 CoCo fitness、六任务 Evolution、fresh-state 内联审稿和 META Program 的冲突部分。
