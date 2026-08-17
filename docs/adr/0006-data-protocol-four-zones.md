# 实验数据协议：paper-level temporal split + 四区隔离

状态：2026-08-18 修订。此前“CoCo 覆盖的 venue-year 全量 `benchmark_reserved`”规则已被本 ADR 明确取代，旧 manifest 只允许分析历史 run，禁止进入新的 formal pipeline。

CoCoReviewBench 的 3900 篇论文是 benchmark source pool，不等于每次正式实验必须评测的 D。source pool 在正式 Evolution 前通过 frozen manifest 一次性划为四个 paper-level、work-family-atomic 的区域：

- A. Historical / Experience：Candidate 按冻结顺序从 empty Memory 开始，自产 Review / trajectory / evidence，审完 Paper_i 后才写 Memory。
- B. Evolution Validation：只用于 fitness、selection、mutation、promotion；human reference 仅 evaluator 可见；每篇 B 评估前恢复同一 A 状态，B 不写 Historical Memory。
- C. Effectiveness Holdout：Program 冻结后的首次独立 development measurement，不反馈当前 Program。
- D. Held-out CoCo Benchmark Test：从未进入 A/B/C，Program 与实验配置完全冻结后才通过 Benchmark Adapter 运行。

当前 frozen split 位于 `AgentReview/configs/coco_temporal_split_v1/manifest.json`：A=2400（venue-year ≤2022），B=300（2023，按 venue 对 family 做确定性 50/50 B/C 分配），C=300（2023），D=900（2024–2025）。同一 work family 整体分配；不采用 paper random split。审计强制要求 paper ID、normalized title、paper family overlap 均为 0，且 `max(A/B/C paper_review_cutoff) <= min(D paper_review_cutoff)`。

源数据没有精确 submission/review dates，因此当前 cutoff 明确采用保守 year-level fallback：每篇论文的 `paper_review_cutoff = venue year 前一年的 12-31`。未来补齐精确日期时必须创建新的 versioned manifest，不得原地改写 v1。

Semantic Scholar 同样受 cutoff 约束。每个 target 的 frozen entry 必须记录 target identity、retrieval method/version、paper_review_cutoff、top_k/config、result SHA256；publication date 缺失或晚于 cutoff 的结果必须拒绝。formal mode 缺 provenance、缺 target、SHA 不符或发现未来论文时 fail fast。S2 不默认注入，Candidate 只能选择是否读取当前 paper 对应的同一 frozen pool，禁止 live query 和跨 paper 读取。

Human Reference Boundary 对四区均不变：Candidate 只看论文和 candidate-visible metadata；human review、gold、taxonomy、judge rationale/full trace 永远留在 evaluator-only 边界。Reflector 只接收 scalar fitness、安全数值指标、预定义非 gold error category 与资源统计。

Formal 启动顺序固定为：Asset Manifest SHA 校验 → A/B/C/D paper/title/family + temporal audit → A/B 数据成员与 canonical preprocessing 校验 → S2 future-leak audit → PASS 后才允许 Evolution。D 的正式路径固定为 `Frozen Reviewer → Review → Benchmark Adapter → CoCo evaluator`。

## Consequences

- 旧 `benchmark_reserved_venue_year_v1.json` 与 `asset_manifest_v1.json` 标记为 superseded，formal gate 不兼容。
- CoCo 论文进入 A/B/C 后永久排除 D；看过 C/D 结果后不得回改当前 Program 并仍称其 untouched。
- 3900 仅是 source pool；当前 D manifest 为 900 篇。
- 当前 formal blocker 不再是 development pool 为 0，而是 time-safe frozen S2 pool 尚未生成、A/B canonical bundles 尚未重建、post-protocol Programs 尚未产生。
