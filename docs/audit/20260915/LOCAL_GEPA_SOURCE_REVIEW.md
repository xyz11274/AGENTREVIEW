# 本地 GEPA 搜索策略源码核验

日期：2026-09-15。范围：只读 `/data0/xyj/shengao/gepa` 源码，并以纯函数验证关键边界。

状态：方案研究已结束。用户随后确认采用 [M★ 原文机制](../../adr/0014-mstar-paper-guided-memory-evolution.md)，本文件保留源码事实和此前讨论背景。

本地 HEAD：`15ee314f9c7d34ec153b809d401f42f55c4dcd76`，提交日期 2026-09-11；核验开始时工作树干净。本记录描述该源码版本，尚未核验历史实验实际导入的 GEPA 安装包版本。未运行模型或完整优化实验。

## 1. 结论与默认配置

默认算法是：**从有样本或指标专长的候选中选父代，利用执行反馈反思变异，通过训练小批次总分改善检验后完整验证，更新各项最佳记录，最后选验证集平均主分数最高的候选。**

| 配置 | `gepa.optimize` | `optimize_anything` 的 GEPA 引擎 |
|---|---|---|
| 父代选择 | `pareto` | `pareto` |
| 前沿类型 | `instance`：每个验证样本 | `hybrid`：验证样本与评价目标 |
| 反思子代准入 | `strict_improvement` | `strict_improvement` |
| 验证 | 默认完整验证集 | `full_eval` |
| 分支合并 | `use_merge=False` | `merge=None` |

依据：[核心入口](../../../../gepa/src/gepa/api.py#L46)、[GEPA 引擎配置](../../../../gepa/src/gepa/gepa_launcher.py#L493)、[可选合并配置](../../../../gepa/src/gepa/gepa_launcher.py#L920)。配置项与自定义策略可以覆盖这些默认值。

核心接口区分 `scores` 与 `objective_scores`：前者是每篇论文一个主分数，默认准入求和、最终选择求平均；后者是每篇论文的质量指标字典，供目标前沿使用。即使选择 `hybrid`，也仍需明确定义主分数；框架不会自动把 CoCo 分项变成某个合理的主指标。[EvaluationBatch](../../../../gepa/src/gepa/core/adapter.py#L15)

## 2. 一次默认反思变异的路径

1. 父代选择器从当前前沿抽取候选，抽取训练小批次。
2. 在该批次运行父候选，取得结果、分数和轨迹；adapter 构造反思材料，由反思模型产生新组件文本。
3. 在同一批次运行子候选。
4. 默认以 `sum(child_scores) > sum(parent_scores)` 接纳反思子代；总分相等也不通过。`AllImprovements` 保留通过该条件的提案。
5. 对选中的提案运行验证，追加候选源码、父代 ID、逐篇分数和指标，并更新前沿。默认没有再要求验证集平均分必须超过父代或历史第一才进入候选池。

依据：[父代运行](../../../../gepa/src/gepa/proposer/reflective_mutation/reflective_mutation.py#L297)、[反思材料](../../../../gepa/src/gepa/proposer/reflective_mutation/reflective_mutation.py#L473)、[严格改善规则](../../../../gepa/src/gepa/strategies/acceptance.py#L44)、[提案选择](../../../../gepa/src/gepa/strategies/proposal_selection.py#L34)、[准入后验证与入池](../../../../gepa/src/gepa/core/engine.py#L571)。自定义 `SelectionStrategy` 可以改变提案选择行为，本节限定默认策略。

因此，“比父代某一方面进步”属于准入问题，“在全部已评价候选中某一方面最佳”属于前沿问题，两者使用不同规则。

## 3. 前沿怎样形成，怎样选父代

四类可配置的比较位置：

| `frontier_type` | 一个比较位置表示什么 |
|---|---|
| `instance` | 一篇验证论文的主分数 |
| `objective` | 一个质量指标在验证样本上的平均分 |
| `hybrid` | 上述两组位置的并集 |
| `cartesian` | 一篇验证论文的某一个质量指标 |

对每个位置，更高分替换旧的最佳集合；精确相等加入并列集合；更低分不进入该位置的集合。[逐项更新](../../../../gepa/src/gepa/core/state.py#L910)、[前沿类型映射](../../../../gepa/src/gepa/core/state.py#L1028)

父代抽样之前还会执行 `remove_dominated_programs`：按聚合分从低到高检查候选；某候选获得最佳的所有位置都能由其他剩余候选覆盖时，裁剪它的父代采样资格。不同位置可以由不同候选覆盖。这是基于最佳位置覆盖的裁剪，与完整分数向量的数学非支配集合不同。

裁剪后，一个候选在多少个位置取得最佳，就在采样列表中出现多少次，再随机抽取。例如分别占据 5 个和 1 个位置的两个候选，父代选择概率之比为 5:1。`hybrid` 的样本位置与指标位置直接合并计数，默认不平衡两类的数量差异。[实际父选择实现](../../../../gepa/src/gepa/gepa_utils.py#L40)、[选择器调用](../../../../gepa/src/gepa/strategies/candidate_selector.py#L18)

## 4. 候选存档、繁衍资格、最终获选

完成准入和验证的候选追加到 `program_candidates`，父代记录追加到 `parent_program_for_candidate`。失去全部前沿位置时，这些历史记录仍然存在。[候选与谱系追加](../../../../gepa/src/gepa/core/state.py#L978)

最终 `best_candidate` 使用整个已接纳候选池的 `val_aggregate_scores` 最大值；正常完整验证时，就是逐篇主分数的平均。它可以选中已经没有父代采样资格的候选。[最终结果](../../../../gepa/src/gepa/core/result.py#L99)、[默认验证策略](../../../../gepa/src/gepa/strategies/eval_policy.py#L34)

示意数据，假定 A、B、C 都已通过准入并完成验证：

| 候选 | 论文 1 | 论文 2 | 平均分 | 默认 `instance` 父代资格 | 最终选择 |
|---|---:|---:|---:|---|---|
| A | 1.0 | 0.0 | 0.5 | 有 | — |
| B | 0.0 | 1.0 | 0.5 | 有 | — |
| C | 0.6 | 0.6 | 0.6 | 无 | C |

所以之前提出的“总体最优也继续繁衍”是为总体最优增加父代采样机会；GEPA 默认已经保存它、也允许它最终胜出。

## 5. 进化树与合并

默认关闭 merge，反思变异记录单一父代。启用 merge 后可以记录两个父代，谱系成为允许分支汇合的有向无环图。

内置 merge 按候选字典里的完整文本组件重组：只有一条分支修改的组件取修改版；两条分支都修改的组件取聚合分更高父代的版本，并列随机择一。它不会在组件字符串内部自动综合两段代码的优势。[合并实现](../../../../gepa/src/gepa/proposer/merge.py#L155)

合并候选还需满足至少一个组件只被一条分支相对共同祖先改变的条件。共享 MemoryProgram 若作为一个完整字符串组件，两条分支都改变了它，则该三元组不满足合并条件。[合并资格](../../../../gepa/src/gepa/proposer/merge.py#L27)

合并提案有独立准入条件：比较批次上的合并总分大于或等于两个父代总分的最大值，然后验证并入池；它不使用反思子代的严格改善条件。[合并准入](../../../../gepa/src/gepa/core/engine.py#L1007)

## 6. 与当前项目相关的适配边界

- 本地 mstar 的 `run_gepa_baseline.py` 显式设置 `pareto + instance`，只优化 `always_on` 文本，并冻结其余 Memory 程序骨架。[调用配置](../../../../mstar/scripts/run_gepa_baseline.py#L356)
- GEPA 核心入口默认 `skip_perfect_score=True`、`perfect_score=1.0`。反思器在小批次所有分数均大于或等于该阈值时跳过反思。直接接入 1–5 的 CoCo 数值会触发错误的“已满分”判断，需要明确配置满分阈值或关闭跳过。这是未来接入风险，不是对 0831 历史实验根因的归因。[跳过条件](../../../../gepa/src/gepa/proposer/reflective_mutation/reflective_mutation.py#L449)
- 核心接口省略 `valset` 时使用 `trainset`；本项目需要显式构建已确定用途的数据分区，且 GEPA 的训练/验证都属于开发侧，封存测试不传入搜索器。[数据加载](../../../../gepa/src/gepa/api.py#L231)
- `objective` 的平均只统计实际出现该指标的样本。适用范围、合法 `X` 与失败分母需要由 CoCo 适配明确处理。[指标聚合](../../../../gepa/src/gepa/core/state.py#L858)
- `optimize_anything` 会从 `side_info["scores"]` 和 `side_info["<组件名>_specific_info"]["scores"]` 提取优化目标。角色诊断应放入独立的诊断字段，保持已接受的“角色评分仅用于诊断”。[目标提取](../../../../gepa/src/gepa/adapters/optimize_anything_adapter/optimize_anything_adapter.py#L689)
- 核心一次调用接收一个 `seed_candidate` 字典。三种 Memory Seed 进入同一共享候选池的初始化方式需要明确实现，不能把字典中的多个组件误当成多个初始候选。[入口参数](../../../../gepa/src/gepa/api.py#L46)

## 7. 无模型验证

通过 `uv run --no-project --offline --python /usr/bin/python3`，直接加载本地 `gepa_utils.py`，并从本地源码提取严格准入类和合并资格函数，仅使用标准库运行确定性样例：

| 检查 | 结果 |
|---|---|
| 父分 `[4,4]`，子分 `[5,2]` | 拒绝，虽然第一篇改善 |
| 父分 `[4,4]`，子分 `[5,3]` | 拒绝，总分相等 |
| 父分 `[4,4]`，子分 `[5,4]` | 通过严格改善条件 |
| A/B/C 均衡示例的父代候选集合 | 只有 A、B |
| A=(1,0)、B=(1,1) 的并列优势裁剪 | 只有 B |
| 一个组件、两条分支均修改 | 合并资格为假 |
| 两个组件、两条分支各修改不同组件 | 合并资格为真 |

这是控制逻辑验证，未评估模型反思能力或 CoCo 实验收益。已撤回的融合方案见 [系统重审记录](SYSTEM_REASSESSMENT.md)第 7.1 节；当前采用 M★ 原文机制。
