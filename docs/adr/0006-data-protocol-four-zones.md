# 实验数据协议：一个 Source Pool + 四个隔离数据区

## Official Benchmark Venue-Year Isolation

CoCoReviewBench 与 OpenReview Source Pool 存在来源重叠，因此 A/B/C 与 D 的隔离不只依赖 paper-level 去重。

当前协议采用 Venue × Year 作为主要 benchmark reservation 单元：

1. 从 CoCoReviewBench 的 metadata / manifest 中取得其覆盖的全部 venue-year cells；
2. 将这些 venue-year cells 标记为 `benchmark_reserved`；
3. OpenReview 中属于任何 `benchmark_reserved` venue-year 的论文均不得进入 A. Historical / Experience、B. Evolution Validation、C. Effectiveness Holdout；
4. 即使某篇论文本身未被 CoCo 最终 reviewer-selection 选中，只要属于 reserved venue-year，也仍然排除。

因此：

`Development Candidate Pool
= OpenReview Source Pool

- benchmark_reserved venue-year cells`

A / B / C 均从 Development Candidate Pool 中建立。

Venue-Year Isolation 是主要划分规则；建立各 split 后仍必须执行第二层审计：

- paper ID overlap = 0
- normalized title overlap = 0
- paper family overlap = 0

这些 overlap checks 用于验证隔离正确性，而不是替代 Venue-Year Isolation。

reserved venue-year 列表必须由 Benchmark metadata / manifest 生成并版本化，不依赖人工目录判断。

所有实验数据从 Source Pool（当前：`/data0/xyj/shengao/OpenReview`、`/data0/xyj/shengao/CoCoReviewBench`）经 manifest 划分为四个互相隔离的数据区，不再笼统使用 train/test：

- **A. Historical / Experience Set** — 各 Program 做过历史任务、自产经验。Candidate 可见论文全文 + candidate-visible metadata + source anchors + 可选 S2；不可见 human review / gold / benchmark labels / judge score。所有 Program 面对完全相同的 paper IDs、顺序、builder、context budget。冻结 paper IDs / order / source / SHA256 / preprocessing version。当前候选资产：`memory_corpus.jsonl` 的 20 篇。
- **B. Evolution Validation Set** — Evolution 的 selection / mutation / promotion 依据。Human reference 只在 evaluator 侧。三条 Seed lineage 共用同一套，一次正式 run 内冻结，不得换题。**当前不存在正式集合**（pilot_full validation 因 ADR-0005 边界违反作废；不允许拿 proxy 20 顶替）——建立它是实验前置（见 docs/TODO.md）。
- **C. Effectiveness Holdout (proxy)** — Evolution 完成、Program 冻结后的第一次独立 measurement。严格顺序 `Evolution 完成 → Program frozen → proxy`；禁止「跑 proxy → 看结果 → 改 Program → 再跑 proxy」，否则 proxy 退化为 validation。当前资产：`effectiveness_validation.jsonl` 20 篇。
- **D. Official Benchmark** — 系统最外层独立裁判。当前 CoCoReviewBench benchmark pool 共 3900 篇；其覆盖的 venue-year cells 整体进入 `benchmark_reserved`。最终实际 evaluated N 由官方 reviewer-selection manifest / evaluation pipeline 决定。

## Memory 写回边界

Memory 构建不是 Evolution 之前的独立预处理步骤，而是每个 Candidate fitness evaluation 的组成部分。

对于每个 Candidate：

`fresh empty Memory State → 在 A. Historical / Experience Set 上运行自己的审稿策略 → 自产 review / trajectory / evidence → 持续构建自己的 Program-specific Memory → 进入 B. Evolution Validation → 生成 validation reviews → external evaluator 计算 fitness`

A 区生成的 review / trajectory / evidence 属于 Self-Generated Experience，可由当前 Program 决定如何写入自己的 Memory。

B 区用于评价已经通过 A 区形成经验的 Candidate。B 区论文产生的 review 用于 fitness / evolution feedback，但不作为该 Candidate 的 Historical Memory 写回来源，以避免 Validation paper 之间因顺序产生额外学习效应。

C. Effectiveness Holdout 与 D. Official Benchmark 同样不承担历史经验积累职责；其 review 仅用于独立 measurement / benchmark evaluation。

**隔离硬约束**：四区两两 paper ID 零重叠，并同时验证 normalized title / paper family 零重叠；每区独立 manifest / SHA256；不得靠目录名判断 split 身份。

**Human Reference 流向**：

| 数据区                     | Candidate 看论文 | Candidate 看 Human Review    | 用于 Evolution selection |
| -------------------------- | ---------------- | ---------------------------- | ------------------------ |
| A. Historical / Experience | 是               | 否                           | 否（间接形成经验）       |
| B. Evolution Validation    | 是               | 否，evaluator only           | 是                       |
| C. Effectiveness Holdout   | 是               | 否，evaluator only           | 否                       |
| D. Official Benchmark      | 是               | 否，benchmark evaluator only | 否                       |

## Considered Options

- **proxy 20 篇兼作 Evolution Validation**：被否决。一区两用会使 proxy 退化为训练信号，development measurement 失去独立性（适应性过拟合）。
- **Evolution train/validation 从同一论文池随机切分**：被否决（iter5 的教训）。同池切分有 paper family 泄漏风险，且 pilot_full 的 validation 曾与 candidate-visible 的真人参照混用。
- **一源四区 + manifest 划分 + 硬隔离**：被采纳。

## Consequences

- 建立正式 Evolution Validation Set 是 QUAL 重新进化的阻塞项（QUAL 最小实现约 20–50 篇，数量为工程配置；采样规则见 TODO）。
- 每区的建立都要产出独立 manifest（paper IDs、SHA256、来源、采样规则、与另三区的隔离校验记录）。
- 任何「先看结果再调」的行为只允许发生在 B 区（经 fitness），发生在 C/D 区即污染。



**Official Benchmark Venue-Year Isolation**

当前 CoCoReviewBench 与 OpenReview Source Pool 存在论文来源重叠，因此 A/B/C 与 D 的隔离不只采用 paper-level 去重，而优先采用  **Venue × Year 标签级隔离** 。

系统从 Official Benchmark metadata 中确定其覆盖的 venue-year cells，并将这些 cells 标记为 `benchmark_reserved`。任何属于 `benchmark_reserved` venue-year 的 OpenReview 论文均不得进入 Historical / Experience Set、Evolution Validation Set 或 Effectiveness Holdout，即使该论文自身并未被 CoCoReviewBench 最终 selection 使用。

因此 development pool 定义为：

`OpenReview Source Pool - benchmark_reserved venue-year cells`

再从该 Development Pool 中划分 A / B / C。

Venue-Year Isolation 是主要的数据划分规则；paper ID、normalized title、paper family overlap 检查作为第二层审计，要求最终仍全部为零。

Benchmark 的 venue-year 标签应从 benchmark metadata / manifest 生成，不依赖人工目录判断；未来新增或更换 Benchmark 时，由对应 Benchmark Adapter 提供其 reserved metadata 范围。
