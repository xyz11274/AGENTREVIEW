# 实验数据协议：paper-level temporal split + 四区隔离

状态：2026-08-27 由 ADR-0009 进一步细化 B zone：新主线将 B 拆为 paper/family 零重叠的 `B-local` 与 `B-suite`。四区隔离、temporal 约束和 Human Reference Boundary 仍完全有效。

状态：2026-08-18 修订。此前“CoCo 覆盖的 venue-year 全量 `benchmark_reserved`”规则已被本 ADR 明确取代，旧 manifest 只允许分析历史 run，禁止进入新的 formal pipeline。

CoCoReviewBench 的 3900 条 submission records 是只读 benchmark source pool，不等于 3900 个独立研究工作。正式单位是 `1 paper family = 1 representative target`，再通过 frozen manifest 一次性划为四个 family-level 区域：

- A. Historical / Experience：Candidate 按冻结顺序从 empty Memory 开始，自产 Review / trajectory / evidence，审完 Paper_i 后才写 Memory。
- B. Evolution Validation：只用于 fitness、selection、mutation、promotion；human reference 仅 evaluator 可见；每篇 B 评估前恢复同一 A 状态，B 不写 Historical Memory。
- C. Effectiveness Holdout：Program 冻结后的首次独立 development measurement，不反馈当前 Program。
- D. Held-out CoCo Benchmark Test：从未进入 A/B/C，Program 与实验配置完全冻结后才通过 Benchmark Adapter 运行。

当前 v2 规则同时规范化 OpenReview API v2 的 title 与 paperhash。每族 representative 统一按 accepted/published 状态、venue year、PDF 显式版本号、稳定 paper ID/source hash 排序；禁止读取 benchmark score、review 或 metareview，也禁止单篇特判。只读审计得到 3900 records → 3889 families/targets，A/B/C/D=2391/299/300/899，共排除 11 条 duplicate submissions。同一 family 只出现一次；paper ID、normalized title、paper family 的 cross-zone overlap 必须为 0，并要求 `max(A/B/C paper_review_cutoff) <= min(D paper_review_cutoff)`。

源数据没有精确 submission/review dates，因此当前 cutoff 明确采用保守 year-level fallback：每篇论文的 `paper_review_cutoff = venue year 前一年的 12-31`。未来补齐精确日期时必须创建新的 versioned manifest，不得原地改写 frozen v2。

Semantic Scholar 同样受 cutoff 约束。每个 target 的 frozen entry 必须记录 target identity、pool version、retrieval method/version、paper_review_cutoff、top_k/config、result SHA256；publication date/year 缺失或晚于 cutoff 的结果必须拒绝。formal mode 缺 provenance、缺 target、SHA 不符或发现未来论文时 fail fast。S2 不默认注入，Candidate 只能选择是否读取当前 paper 对应的同一 frozen pool，禁止 live query 和跨 paper 读取。

Human Reference Boundary 对四区均不变：Candidate 只看论文和 candidate-visible metadata；human review、gold、taxonomy、judge rationale/full trace 永远留在 evaluator-only 边界。Reflector 只接收 scalar fitness、安全数值指标、预定义非 gold error category 与资源统计。

新主线的 B zone 内部协议为：

- `B-local` 冻结一个大于单次 evaluation 用量的 paper/family pool。`static=25` 是每次 local evaluation 的固定 static set 规模，`rotating_size=5` 是从其余 frozen rotating pool 中按预注册规则选取的单次规模；二者都不代表 B-local 总容量。
- `B-suite` 仅用于五条 Specialist Evolution 完成后的 Global CoCo Suite Selection。
- `B-local` 与 `B-suite` 在 paper ID 和 paper family 层面均零重叠；两者的 manifest 和抽样规则在运行前冻结。
- C Holdout 不参与 Specialist Evolution、Suite Selection 或任何配置选择，保持完全 untouched。

Formal 启动顺序固定为：Asset Manifest SHA/version 校验 → split v2 的 A/B/C/D paper/title/family + temporal + v1/v2 comparison audit → A/B 数据成员与 canonical preprocessing 校验 → Frozen S2 v2 future-leak + query-integrity audit → PASS 后才允许 Evolution。split v1 / S2 v1 因 `query_title_serialization_contamination` 明确拒绝。D 的正式路径固定为 `Frozen Reviewer → Review → Benchmark Adapter → CoCo evaluator`。

## Consequences

- 旧 `benchmark_reserved_venue_year_v1.json` 与 `asset_manifest_v1.json` 标记为 superseded，formal gate 不兼容。
- CoCo 论文进入 A/B/C 后永久排除 D；看过 C/D 结果后不得回改当前 Program 并仍称其 untouched。
- 3900 仅是 submission source pool；family dedup 后当前 D 为 899 targets。
- 当前 formal blocker 由 Formal Readiness Audit 按实际 Asset Manifest 报告；不得用未完成/未审计的 S2 work records 解除 blocker。
