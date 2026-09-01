# ADR-0006：Family-Deduplicated Temporal v2 四区数据协议

- 状态：Superseded for Research Core v2 by ADR-0010
- 适用范围：历史经验、Evolution、Effectiveness Screening 与正式 Benchmark

## Context

CoCoReviewBench source pool 包含同一研究工作的多个 submission records。正式实验单位需要对应独立研究工作，并通过时间和用途隔离控制信息流。

当前 v2 对 OpenReview API v2 title 与 paperhash 做规范化，以传递闭包形成 Paper Family，再为每个 family 选择一个 representative target。

## Frozen assignment

当前来源计数：

| Item | Count |
|---|---:|
| CoCo source records | 3900 |
| Paper families / representative targets | 3889 |
| duplicate submission records merged | 11 |

四区分配：

| Zone | Meaning | Family targets |
|---|---|---:|
| A | Historical Experience | 2391 |
| B | Evolution Validation | 299 |
| C | Effectiveness Holdout | 300 |
| D | Held-out Benchmark Test | 899 |

## Decision

1. 一个 Paper Family 选择一个 representative target。
2. 每个 family 固定属于一个 Zone。
3. A 区按冻结顺序用于 Program-specific historical replay。
4. B 区用于 static selection、rotating selection、fitness、mutation 与 promotion。
5. C 区用于冻结 Program 的独立 proxy screening。
6. D 区用于六任务 Program 与配置冻结后的 `sealed_d_coco_style` 内联测量；Reviewer Core 或部署执行面持久化的 D 区候选产物可进入独立 Official CoCo Adapter。
7. Candidate-visible data 与 evaluator-only reference 分开物化、分开读取。
8. paper ID、normalized title 与 family 三种跨区交集均为零。
9. A/B/C 的最大 paper review cutoff 早于或等于 D 的最小 cutoff。

## Family and representative rules

family identity 由 normalized title 与 paperhash 的传递关系建立。Representative 依次按以下稳定字段排序：

1. accepted/published 状态；
2. venue year；
3. PDF 显式版本；
4. stable paper ID；
5. source hash。

同一规则适用于完整 source pool，并由 manifest 记录 family accounting audit。

## Temporal cutoff

当前源数据提供 year 粒度。每篇论文的 paper_review_cutoff 固定为 venue year 前一年的 12 月 31 日。未来精确 submission/review date 形成新的 versioned manifest 和资产身份。

## Frozen S2 boundary

每个 target 的 Frozen S2 entry 记录：

- target identity 与 canonical query title；
- pool 与 retrieval method version；
- paper_review_cutoff；
- top_k 和 query hash；
- result records 与内容 SHA256。

正式 capability 按当前 target scope 读取冻结 pool。publication time 满足 target cutoff。Pool v2 完整覆盖 3889 个 target。

## Materialized experiment counts

- 每个 Specialist bundle 包含 2391 个 A 区 Episode 与 150 个 B 区 Validation target。
- B 区其余 149 个 family 构成 META Validation reserve；Specialist 与 META 两组 B family 互斥。
- Qwen3 selection cache 覆盖五个 Specialist 的 `5 × (2391 + 150) = 12,705` 条文本，向量维度为 4096，截断数量为 0。
- Effectiveness Screening 从 A 区选择 20 个历史目标、从 C 区选择 20 个评测目标；7 arms × 2 replicates 形成 280 个目标审稿包，六个 Program arms × 2 replicates 形成 12 条历史 replay 命令。

## Human Reference Boundary

Candidate 读取论文、candidate-visible metadata、source anchors 与显式 capability 结果。Human review、gold、taxonomy、judge rationale 和 benchmark score由 evaluator-only Adapter 管理。

## Formal startup order

1. 校验 Asset Manifest 的版本和 SHA。
2. 校验 split v2 的 family accounting、四区 membership、cross-zone identity 与 temporal audit。
3. 校验 A/B task bundle membership 与 canonical preprocessing。
4. 校验 Frozen S2 v2 coverage、query identity、record hash 与 cutoff。
5. 启动 Evolution 或 final evaluation。

## Consequences

- configs/coco_temporal_split_v2/manifest.json 是当前冻结 split 身份。
- configs/frozen_s2_temporal_v2.jsonl 与 sidecar 是当前冻结检索资产。
- 3900 表达 source records；3889 表达正式 family targets；D 区规模为 899。
- C 区测量结果只影响后续独立实验设计；当前冻结 Program 身份保持稳定。
- D 区密封套件通过 `final-evaluate-suite` 生成 Review 并内联计分；CoCo 官方三阶段评测通过 `agentreview.coco_eval` 读取已持久化 Candidate Review 独立运行。
