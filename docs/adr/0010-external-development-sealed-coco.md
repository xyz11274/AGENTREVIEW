---
status: superseded by ADR-0011
---

# ADR-0010：外部开发语料与密封 CoCo 测试

> 0915 的数据边界见 [ADR-0011](0011-development-coco-and-sealed-test.md)。以下记录 0831 的历史来源、划分与去重规则。

Research Core v2 使用 `OpenReview/train_clean` 生成历史 Review、Memory 和 Evolution feedback，并在系统冻结后评价独立 CoCo benchmark。由于任何非空 development 年份 cutoff 都无法保留13个 venue-year 各100篇，本版本不强制 temporal split；跨边界只排除 exact ID、canonical title、paperhash 和内容 hash 的直接命中，不建立复杂 family graph。最终评价冻结的 `coco_1300_protocol_compatible_resample_v1`，Review/Evolution 与 CoCo 继续使用独立进程和只读路径 allowlist。
