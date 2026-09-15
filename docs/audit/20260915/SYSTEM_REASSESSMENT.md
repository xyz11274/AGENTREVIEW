# AgentReview 0915 系统重审与实验决策记录

日期：2026-09-15。状态：历史审计完成，Meta 与完整正文根因修复、共享 M★ 和标准 CoCo 运行入口已实现；实际数据与实验参数继续确定，部署模型验收待执行。

修改基线为 `AgentReview_0915` 的 `0915@907f02d0c28845c10343d41b5bc691786c47bf4e`。历史结果读取自 `AgentReview_0831`；两工作树开始审计时指向同一提交且均干净。旧报告位于 [repo_admin](../../../../repo_admin/AGENTREVIEW_0831_ARCHITECTURE_AUDIT_20260915.md)。本记录补充独立核验与新发现，旧报告的建议不自动成为本轮决定。

## 1. 结论

**现有证据支持 AgentReview 的历史 Paper 分落后，但不支持“标准 CoCo 全指标已经全面落后”的说法。** Native 没有完成类别质量与完整性评分。恢复缓存后，对齐论文仍可见 Paper 分差；输入截断、thinking、生成预算等差异使其无法单独归因于 Memory 或多角色架构。

**修正输入与评价目标，按 M★ 原文落实记忆程序搜索。** 0831 进化使用单角色的整体 1–5 内评分，最终 CoCo、Meta 的表现不参与选择；92 个成功变异也没有改变 Memory 算法。0915 新共享入口已将 Schema、读写 Logic 与记忆交互 Instruction 接入搜索空间，最终完整评审的标准 CoCo `Paper.` 进入选优。

### 用户已确定

- 以 0915 文件为修改起点，0831 作为历史证据。
- 进化评审使用标准 CoCo。
- 2026-09-15 用户确认开发集与最终测试严格划分、不能混用。详细边界见 [ADR-0011](../../adr/0011-development-coco-and-sealed-test.md)。
- 本轮 seed 指三种初始记忆方案：Vector Search、LLM Summarizer、Experience Learner。三种方案和独立的无 memory 对照组都需要标准 CoCo 评分，保留初始表现供进化结果比较；随机重复次数另行讨论。
- 无 memory 组保留相同五角色、汇总流程和其他条件，仅关闭记忆；CoCo 原始 prompt 单列基础模型对照。详细定义见 [ADR-0012](../../adr/0012-no-memory-controlled-comparison.md)。
- 共同进化一套记忆组织、构建、检索和使用策略，五角色保持各自职责与独立 Memory 库，采用同一个候选池与谱系。详细定义见 [ADR-0013](../../adr/0013-shared-strategy-independent-role-memory.md)。
- 共享候选以最终完整评审的标准 CoCo Table-1 `Paper.` 作为主指标选优，质量分项和失败覆盖同时报告；五角色各自的评分用于诊断。见 [ADR-0015](../../adr/0015-paper-fitness-and-separate-results-report.md)。
- 每个 seed 的全部逐篇分数与 CoCo 汇总集中展示。用户明确报告应另外生成：使用独立后处理读取已保存结果，初次评审和评分各自保存原始产物，统一报告不嵌入首次生成流程。
- 持续失败采用有限预算、可复核归因、共享故障暂停及修复后的统一重评；整轮预算耗尽时保留已有 Champion 和未完成状态。标准 CoCo 保持原样，本轮已授权修复 AgentReview 生成契约与运行控制。见 [ADR-0016](../../adr/0016-bounded-failure-recovery.md)。
- 用户确认本轮遵循 M★ 原文思想，结束 GEPA 接入、混合前沿与对照方案讨论。算法依据与本地代码差异见 [ADR-0014](../../adr/0014-mstar-paper-guided-memory-evolution.md)。
- 基础模型继续使用本地 Qwen3.8-27B。此处沿用实际本地配置名称，不重新推定型号。
- 其他实验条件通过逐项讨论确定。

### 已接受的数据边界

独立开发集运行标准 CoCo 供进化使用，最终测试在方案冻结后评价。论文、参考评审与衍生数据保持分区身份，同一研究的不同版本归属同一侧。开发内部各用途子集、具体规模、参考评审来源和去重规则待讨论。

`CONTEXT.md` 已区分历史内评、开发 CoCo 与封存最终测试；ADR-0008/0010 的冲突部分由 ADR-0011 取代。已查看过结果的历史 1300/902/805 集合标记为已暴露历史评测。本轮完成 Meta、完整正文和失败控制修复，并接通新协议清单、共享候选池及标准 CoCo 进化闭环。实际数据清单与数值参数待冻结，入口见 [共享实现](../../SHARED_MSTAR_IMPLEMENTATION.md)。

### 当前实现与待定条件

共享策略、五库独立、最终完整评审选优、M★ 原文机制、Table-1 `Paper.` 主指标、独立后处理和持续失败的终止规则均已确认。Meta 已改为版本化结构生成与逐项渲染，相关测试为 42 passed、19 subtests passed；旧进化运行器本轮接入完整评价凭据、持久化失败预算和恢复版本。实现及验证边界见第 3.1–3.2 节。

当前继续确定开发与测试来源及规模、各臂生成条件、扩展可变范围、搜索与重复实验预算。旧角色入口使用历史 `review_quality`；新 `shared-experiment` 使用完整标准 CoCo `Paper.` 与共享种群，`experiment-report` 独立生成统一报告。指标含义见第 5.2 节，报告职责见 [ADR-0015](../../adr/0015-paper-fitness-and-separate-results-report.md)。

## 2. 优先级最高的新发现：正文被重复 anchors 挤出上下文

实际调用 0915 的 `frozen_paper_context()`，按 QUAL 角色对所有冻结论文重建输入：

| 集合 | 论文数 | 原文超过 60000 字符 | 实际正文发生截断 |
|---|---:|---:|---:|
| development | 275 | 20 | 231 |
| CoCo 历史测试 | 1300 | 116 | 1127 |
| 与 native 共有的论文 | 902 | — | 767 |

Native 的 902 个源文件全部存在，逐篇正文与 AgentReview 冻结正文相同，生成记录的 `truncation` 全为空。问题发生在 AgentReview 的输入组装。

根因链：

1. [review_preparation.py:55](../../../AgentReview/agentreview/research_v2/review_preparation.py#L55) 每隔 1000 字符复制 500 字符，最多 40 个 anchors，重复内容接近 20000 字符。
2. [paper_context.py:168](../../../AgentReview/agentreview/paper_context.py#L168) 从整个 60000 字符预算扣除 metadata 和 anchors。
3. [paper_context.py:59](../../../AgentReview/agentreview/paper_context.py#L59) 只保留正文头尾，中间被替换成截断标记。
4. 同一函数供 Reviewer 与内部评审使用；两者可能共同看不到本应核查的正文细节。

截断是已复现事实。它造成多少 CoCo 损失仍需修复前后的同条件对照，不能把所有分差直接归给截断。

相关数据语义问题：fallback anchors 全部设置 `page_idx=0`，这些是合成文本定位片段，不是真实 PDF 页码；CoCo source 保存 `venue_year`，上下文却只取 `conference` 且固定 `year=""`，导致该集合的 venue/year 缺失。这会妨碍 POL 按当年会议规则判断。

建议：统一各臂候选可见正文；在真实 tokenizer 预算内保留完整正文或采用预先声明的相同处理策略；定位标记引用正文，避免重复正文挤占预算；页码只使用真实来源，合成定位使用字符/段落位置。

## 3. CoCo “分词失败”的实际机制

应区分 tokenizer 编码、Opinion Split 和 Opinion Classification。已确认的大规模失败发生在标签分类，不能统称为 tokenizer 故障。

| Arm | 输入 | Classify 成功 | 失败 |
|---|---:|---:|---:|
| memory_meta | 1300 | 1056 | 244 |
| memory_concat | 1300 | 1290 | 10 |
| no_memory_meta | 1300 | 1099 | 201 |
| no_memory_concat | 1300 | 1290 | 10 |

四臂交集为 902，排除 398 篇（30.6%）；native 在这 902 篇上又只成功分类 805 篇。Meta 失败率明显高于 concat，成功样本交集是结果产生后选出的集合，不能充当预先固定全样本主分析。

具体可核查例：memory_meta 的 `B1Igu2ogg` 的 `opinion[7]`（第 8 个）包含多句 ORIG 内容。三次分类都给出 `ORIG-MTH / ORIG-ANL / ORIG-COM / ORIG-EXP` 四个合法类别，格式解析成功，但超过协议允许的 1–3 类而被拒绝。这说明复合观点与分类约束存在实际接口问题，失败并非随机乱码。

进一步统计：memory_meta 的 274 个失败观点中，265 个三轮都输出超过 3 个合法标签；no_memory_meta 的 222 个失败观点中，213 个属于同样情况。两组失败观点全部只含一个预切 chunk。上述例子有 4 句话、81 个英文词；标准预切逻辑对不超过 150 词的段落保持整块，而 Split 模型通过组合 chunk ID 工作，无法继续拆开块内部的复合观点。因此，合并的多句观点 → 单一预切块 → 多于 3 个类别 → 整篇失败，是直接得到输入、代码及三轮输出支持的链路。

代码证据：[utils.py:1283](../../../../CoCoReviewBench/src/utils.py#L1283)、[_2_split.py:316](../../../../CoCoReviewBench/src/evaluation/_2_split.py#L316)、[_3_classify.py:256](../../../../CoCoReviewBench/src/evaluation/_3_classify.py#L256)。统计与示例见 [classify_failure_audit.json](classify_failure_audit.json)。

完整输入与阶段失败保留在结果账本中。修复针对 AgentReview 原始输出的观点组织和运行边界，标准 CoCo 的预切、分类上限、提示与评分公式保持原样；全样本失败以明确状态和空分数进入报告，正式比较依据完整且可比的评价凭据。

### 3.1 持续失败的终止与恢复规则（已接受）

用户已接受有限成本的恢复与终止规则，见 [ADR-0016](../../adr/0016-bounded-failure-recovery.md)。每个请求有明确重试预算，每个候选有修复上限，整轮累计修复与候选失败均有预算；耗尽后进入终止状态。再次恢复需要原因、修复和验证证据，保留前次尝试、已消费预算及原始输出。

| 已核实的原因 | 处置 | 结果身份 |
|---|---|---|
| 临时超时、连接或服务故障 | 按既定预算重试失败阶段；共享故障暂停相关运行，恢复后从检查点继续 | 等待恢复的评价任务，不解释为候选低分 |
| 论文正文或参考资产确有缺陷 | 优先在开跑前筛查；运行中发现则暂停受影响比较并修复来源，改变数据身份时建立新版本并统一重算受影响候选 | 原结果保留其数据版本，不能混用不同分母 |
| 候选违反预先冻结的确定性执行或输出契约 | 有限修复后仍违规，结束本次候选尝试；其他合格候选继续 | 无效候选及违规证据 |
| 公共评价链路存在可复现结构问题 | 保存最小复现，修复 AgentReview 的生成契约、输入适配或运行实现；标准 CoCo 保持原样，按统一版本重评受影响的全部候选和对照 | 原评价不可用；修复后的结果有明确版本 |
| 有效输出持续无法取得评分且归因未定 | 按共享故障暂停整轮并保留证据；预算耗尽后结束本轮，保存现有成功项及失败项 | 评价不可用，与无效程序分别记录 |

候选自身有重复失败，并不足以证明它违反契约。历史 Meta 的多合法标签失败首先体现分段与分类接口冲突，不能直接把它记作审稿质量低。跨候选出现同一问题可用来定位共享故障，但归因仍以原始输入、阶段输出和可复现检查为依据。

执行边界：

- 重试使用同一份已冻结原始 Review，复用已成功的阶段产物；重新生成或改写 Review 属于新候选或新运行，保留新的身份，不包装为评分重试。
- 预算在操作前持久化，恢复沿用已消费记录；实际模型请求、时间与修复次数分别记录。当前工程默认是每候选修复 1 次、整轮修复 10 次、整轮候选失败 5 次，可在运行配置中调整。这些数值具有工程配置身份，科学实验的样本量、搜索和调用预算继续单独确定。
- 标准 CoCo Judge 保留外层三次任务生命周期，每个生命周期的客户端又可尝试三次上游完成请求；分类保留固定三轮。[Judge 重试](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L1167)、[分类重试](../../../../CoCoReviewBench/src/evaluation/_3_classify.py#L221) 外围恢复消费自己的整轮预算，同时保留内部请求的成本身份。
- `Paper.` 记录真实有效的标准评分，失败位置保留状态和空分数；成功部分以诊断身份汇总并显示实际分母。正式排名要求完整预定论文集合、同一评价版本与相同评分身份。失败状态与标准评分分别保存。
- 运行约束检查通过且获得可比评分的低分候选，仍按 M★ 原文进入池中；“分数低”“程序违规”“评分不可用”使用不同身份。
- 标准 CoCo 的预切逻辑、分类上限、评分提示和公式保持原样。共享故障修复建立新的 Evaluation Epoch，原评价及其来源继续保留；全部受影响候选与对照完成统一重评后恢复比较。生成契约的修复产生新的输出身份，评分重试继续读取原身份下冻结的 Review。
- 正式运行前，先在开发侧用三种初始 seed 和无 memory 对照验证真实评审到 CoCo 的完整链路。所有必要对照和候选无法取得可比评分时，完整比较不具备宣布获胜策略的条件。
- 完整 Evaluation Receipt 绑定论文清单、程序与 Memory 身份、冻结输出、评价协议、逐篇文件及哈希和聚合结果。池准入与 winner 发布要求凭据完整且可比，共享故障期间整轮暂停。
- 整轮修复或候选失败预算耗尽时，以 `ended` 和具体结束原因持久化本轮状态，报告标明评价未完成；保存检查点、全部失败位置、原因和原有 Champion。新 winner 的发布以完整比较和可用预算为条件。
- 最终测试保持原定论文清单、冻结程序和原始输出；工程补评记录版本与原因，测试结果不用于更换 winner、重写 prompt 或挑选更容易成功的论文。

以上失败规则已经覆盖旧角色运行器和新共享运行器，各自保持评价协议身份。新运行器保存标准 CoCo 完整凭据，暂停后对共享候选及对照统一重评；逐篇与角色诊断由独立后处理读取。主指标和报告职责按 ADR-0015 执行。

### 3.2 Meta 根因修复与验证

历史 Meta 在固定角色 synthesis 段落中合并多个观点，标准 CoCo 对短段落的预切又把它们保留为单一 chunk。0915 从原始生成契约修复这一边界：`META_SYNTHESIS_Request_v2.md` 要求每个可独立讨论的观点分别输出 `statement`、`source_roles`、`evidence_anchors` 和 `suggestions`；五角色按固定顺序组织，每角色可有多项观点，整个评审保留 recommendation。

失败控制实现的最终验证为 **98 passed、41 subtests passed**，范围为 `test_research_v2_{failure_policy,fail_closed_evolution,validation_failure_scope,formal_recovery,probe,formal_evolution,evaluation_receipts,evolvable_winner,evaluation_batch,evaluation_failures}.py`。验证包括原始失败无质量分、完整评价准入、确定性错误停止、预算持久化、公共故障全局暂停、跨 epoch 缓存隔离、部分 pool 写入恢复、候选修复失败处置及 Champion 保留。十个文件通过同一条 pytest 命令合跑，耗时 7.21 秒；工程入口与恢复记录格式见 [ADR-0016](../../adr/0016-bounded-failure-recovery.md)。CoCoReviewBench 和 AgentReview_0831 的工作区保持干净，验证未调用部署模型。

宿主校验精确 schema、JSON 重复键、字段类型、单行边界、真实来源角色和 anchor ID；把来源角色绑定到实际 Specialist Review ID，保留引用 anchor 的原始描述。每项结论及建议独立渲染为以空行分隔的 Markdown 段落，输出项数和内容完整保留。建议也有独立边界，便于标准 CoCo 按原有逻辑预切。结构校验验证的是可复核的格式与引用，科学语义是否单一、最终是否满足分类上限仍需实际评价确认。

原始模型响应在解析前持久化，失败记录包含字段与约束，Final Review 保存结构化数据与确定性渲染文本；恢复时验证二者与 raw 输出一致。request、output、rendering 和冻结 Meta identity 均升级为 v2，历史 v1 产物保持原样。源码见 [meta.py](../../../AgentReview/agentreview/research_v2/meta.py)、[Meta v2 请求](../../../AgentReview/META_SYNTHESIS_Request_v2.md)、[冻结 identity](../../../AgentReview/configs/frozen_meta_synthesis_v2.json)。标准 CoCo 代码、标签上限和评分提示保持原样。

验证使用实际 `generate_final_review` 路径，并调用本地标准 CoCo 原有 Split 提示构建纯函数，检查多个观点在 CoCo 输入中有独立边界；覆盖原始内容保留、证据与来源绑定、非法引用及结构拒绝、raw 持久化和恢复篡改检查。Meta 专属及相关兼容测试结果为 **42 passed、19 subtests passed**，全程未请求模型。该结果对应下面五个测试文件，范围限于 Meta 及相关兼容行为。

在 `AgentReview_0915` 目录执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=AgentReview /data0/xyj/miniconda3/envs/env_shengao/bin/python -m pytest -q \
  AgentReview/tests/test_research_v2_meta_structure.py \
  AgentReview/tests/test_research_v2_meta_resume.py \
  AgentReview/tests/test_reviewer_identity.py \
  AgentReview/tests/test_research_v2.py \
  AgentReview/tests/test_research_v2_coco_review_batch.py
```

## 4. Native 比较：指标缺失与条件混杂

Native 的 [run_primary_evaluation.py:67](../../../../AgentReview_0831/artifacts/research_v2/qwen38_27b_native_coco_common902_v1/run_primary_evaluation.py#L67) 明确设置 `category_level_eval=False`，且 `require_complete=False`。所以 `Correct./Thoro./Ground./Verify./Clarity/Average/Complete.` 为 null 是没有计算，不是 0 分，也不是恢复失败。

注意：现有 Paper 评审本身从分类输出重构非 N/A 观点，因此不能简单删除 Classify 来声称保持了原 Paper 协议。

| 条件 | AgentReview | Native |
|---|---|---|
| thinking | false | true |
| temperature | 0 | 0.6 |
| 单次生成上限 | Specialist 8192，Meta 4096 | 32768 |
| few-shot | 五角色的冻结职责/策略 | CoCo AI-Scientist 3 个示例 |
| 输入 | 正文 + metadata + 重复 anchors，60000 字符限制 | 原始正文，token 预算处理 |
| 完整类别 CoCo 评分 | 有，部分指标仍有缺值 | 没有 |

证据：[formal config](../../../AgentReview/configs/research_v2_formal_v1.json)、[constants.py](../../../AgentReview/agentreview/research_v2/constants.py)、[native run_shard.py:92](../../../../AgentReview_0831/artifacts/research_v2/qwen38_27b_native_coco_common902_v1/run_shard.py#L92)。多次 AgentReview 调用也有成本，不能仅从单次输出上限推定哪个系统总算力更大。

### 从已有缓存恢复的同论文 Paper 对比

重建精确请求与缓存键，恢复逐篇评分；每臂重算均值与原聚合吻合，误差小于 4e-14。共同有效集合 803 篇：

| 系统 | Paper 均值 |
|---|---:|
| native | 4.2540 |
| memory_concat | 3.9781 |
| no_memory_concat | 3.9011 |
| memory_meta | 3.8304 |
| no_memory_meta | 3.8070 |

逐对 native 与 memory_concat 的共同 804 篇，native 高 0.2759；与 memory_meta 的共同 805 篇，高 0.4248。由此可排除“分差完全由论文数不同造成”的解释。上述仍是历史条件混杂的观察比较，不是架构或 memory 的因果效应估计。

脚本：[recover_paper_scores.py](recover_paper_scores.py)；含逐篇分数的结果：[paired_paper_scores.json](paired_paper_scores.json)。

## 5. 进化分数与 Memory 改进的证据

[evaluation.py:22](../../../AgentReview/agentreview/research_v2/evaluation.py#L22) 只要求 `review_quality` 为整体 1–5 分，没有各分值行为锚点或 CoCo 分类参考；[evolution.py:133](../../../AgentReview/agentreview/research_v2/evolution.py#L133) 用均值排序。

- 2675 个正式 static 分数中，2451 个为 4（91.63%），216 个为 5，其余仅 8 个。
- SIGN 的 550 个 static 分数里 544 个为 4。
- 五角色 winner 相对最佳 seed 的提升依次为 +0.08 / +0.16 / +0.08 / +0.08 / +0.08。
- 每角色只用固定 25 篇选择；0.04 对应总分多 1。反复搜索这一小集合，有选择过拟合风险。
- QUAL、ORIG、POL 分别有 3、7、5 个并列最高候选。代码用 candidate_id 字典序打破平局，没有对这些候选做稳定性重评。

逐源码核验 107 个 seed/candidate：源 SHA 全匹配 pool；92 个成功子代的“除 `plan_review` 外全部顶层 AST”均与各自原始 seed 相同。Memory 类、read/write 和四个指令常量均未改。角色成功子代数为 CLAR=17、ORIG=19、POL=19、QUAL=18、SIGN=19。

准确结论是：本轮在预设 Memory 算法之上演化了 Review strategy、S2 policy 与上下文使用方式。相同 Memory 代码可能仍生成不同归纳内容，不能扩大为所有 snapshot 字节相同。内部轻微涨分也无法证明最终 CoCo 提升。

Meta 另有目标断层：[META_SYNTHESIS_Request_v1.md:17](../../../AgentReview/META_SYNTHESIS_Request_v1.md#L17) 要求更严格的判断，并把五角色内容整理到固定 synthesis 结构；但它不参与进化评分。历史同集 concat 的分类 Average 高于 meta，且 meta 分类失败更多。内容压缩、多观点合并和判断偏移是值得做受控实验的机制，尚未分别证明其贡献。

### 5.1 五角色的获胜 seed 与共享策略提议

本次直接核对五份 `winner.json`、各自 `pool.json` 和实际 CoCo 生成批次的 `run_spec.roles`。五个部署 Program 的路径、源码 SHA 和 seed_kind 均与对应 winner 一致，pool 和冻结系统文件的 SHA 也匹配。详细数据见 [winner_seed_audit.json](winner_seed_audit.json)。

| 角色 | 实际选中 winner 的 seed | winner 内评分 | Vector Search 谱系最高内评分 |
|---|---|---:|---:|
| QUAL | LLM Summarizer | 4.12 | 4.12 |
| CLAR | Vector Search | 4.40 | 4.40 |
| SIGN | Vector Search | 4.08 | 4.08 |
| ORIG | Vector Search | 4.12 | 4.12 |
| POL | Vector Search | 4.12 | 4.12 |

实际记录为四个 Vector Search、一个 LLM Summarizer，Experience Learner 没有当选。Vector Search 谱系在五角色中都达到该角色的全池最高内评分。特别是 QUAL 有三个 4.12 候选：一个 Vector Search、两个 LLM Summarizer；最终按 `candidate_id` 字典序选择 LLM Summarizer，而不是它的分数高于 Vector Search。ORIG、POL 也存在 Vector/Summarizer 跨谱系并列。

| 角色 | Vector：初始→最高（成功子代） | Summarizer：初始→最高（成功子代） | Experience Learner：初始→最高（成功子代） |
|---|---|---|---|
| QUAL | 4.04→4.12（9） | 4.04→4.12（8） | 4.00→4.00（1） |
| CLAR | 4.24→4.40（12） | 4.12→4.28（5） | 4.16→4.16（0） |
| SIGN | 4.00→4.08（7） | 4.00→4.04（11） | 4.00→4.00（1） |
| ORIG | 4.00→4.12（10） | 4.04→4.12（7） | 4.00→4.00（2） |
| POL | 4.00→4.12（9） | 4.04→4.12（9） | 3.92→3.92（1） |

成功子代合计为 Vector 47、Summarizer 40、Experience Learner 5；这是实际入池探索量，不是预先给三个方案相同的搜索预算。旧分数的区分度及预算不均衡都限制了 seed 优劣结论。

同一 seed 家族也不代表最终审稿决策完全相同：四个 Vector winner 的 Memory AST 相同，但 `plan_review` 均不同；全部五个 winner 有五份不同的 Review Plan AST。旧实验实际支持“共用同类 Memory 实现”的可行性，尚未测量“一套共享审稿决策”与五套独立决策是否等效。

**已接受方案，见 ADR-0013：** 以共享记忆组织、构建、检索和使用规则作为一个进化候选，五角色保留固定职责并各自持有独立 Memory 数据；初始仍评价三种记忆方案和无 memory 对照。共享代码不共享 Memory 内容，角色专属的事实、经验和检索结果仍隔离。若把角色专属策略也写入共享程序的五个分支，仍需明确其是否属于允许的搜索空间。

这会把五个候选池合为一个，使整体选优更直接；每个共享候选仍需要运行五角色，因此不据此承诺模型计算量降为五分之一。用户随后确认以最终完整评审的开发集标准 CoCo 结果选优，角色评分用于诊断，主指标采用 Table-1 `Paper.`。

### 5.2 最终评审的 CoCo 主指标核对

这里的 `Paper.` 与类别 `Average` 都可由最终交付评审产生，区别在于标准评分流程如何比较该评审与人类参考。

| 指标 | 本地标准实现的含义 | 作为主指标的局限 |
|---|---|---|
| Table-1 Paper. | 将有效分类观点拼接，与正确人类参考整体比较，再按标准适用维度求均值、跨论文平均 | 输入经过 Split/Classify；需要保留失败论文及实际有效分母 |
| Table-1 Average | 在 AI 与正确参考共有类别内评分，先类别内汇总、再逐篇及跨篇平均 | 未覆盖参考类别不会自动记低分；没有成功类别的论文会被跳过 |
| Complete. | AI 类别数量除以人类参考类别数量，乘 100 | 不取交集，可超过 100%，单独提高它不等于覆盖了正确参考 |

用户已确认以 `Paper.` 为主，因为它与整体评审选优目标直接对应，且不把类别交集内的高分等同于完整评审能力。全部类别指标、实际有效分母和失败记录同时保存并报告；候选选优依据 [ADR-0016](../../adr/0016-bounded-failure-recovery.md) 要求的完整可比评价凭据，评分失败记录状态及空分数。

代码依据：[_4_evaluation.py:389](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L389) 为 Paper 输入重构；[651](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L651) 为 Table-1 适用维度均值；[873](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L873) 为类别与整篇聚合；[1257](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L1257) 为共有类别选择。

两个需要避免的字段误读：标准 `normal` 类型的 Verify 可合法为 `X`，因此 Table-1 正常可能按四个数值维度平均，`claim` 则按五个；合法不适用值应与执行失败、异常缺字段区分。另一个 `judge_scores["average"]` 字段还包含 actionability，其公式与 Table-1 的 `Average/Paper.` 不同；候选选择应绑定选定的 Table-1 定义。

### 5.3 独立汇总的已确认边界与历史可用数据

用户要求每个 seed 的全部论文分数及 CoCo 汇总放在一起，并补充“是另外生成，不是初步生成时就整合”。原始评审与评分先各自持久化，另一个后处理步骤生成报告；报告步骤不调用模型、不补跑评分、不修改源文件。初始方案、进化候选、最终策略与无 memory/Native 对照按各自身份汇总。开发与最终测试保持分区，报告中的统一入口不改变其数据边界。详细契约见 [ADR-0015](../../adr/0015-paper-fitness-and-separate-results-report.md)。

已审计的 0831 `formal-evolution-v1` 中，十五组初始 seed（五角色 × 三方案）在同一 25 篇 static 论文上共有 375 条评分，原始评分 `review_id` 与 Review 均可对应，组均值与 pool 中的初始分数一致。但它们的 `prompt_version` 均为 `internal-evaluator-v1`，属于历史角色内评。

对应路径为 `AgentReview_0831/artifacts/research_v2/evaluations/formal-evolution-v1-{role_lower}-seed-{seed_kind}-static-eval/{paper_id}/{ROLE}.json`，原始 Review 路径将 `evaluations` 和 `static-eval` 分别替换为 `reviews` 和 `static-review`。

完整进化记录还包含 2300 条子代 static 结果及 500 条父代 rotating 结果。其中一条子代 static 是保存的失败惩罚：CLAR 第 18 次尝试的论文 `BkluqlSFDS`，[`CLAR.paper_failure.json`](../../../../AgentReview_0831/artifacts/research_v2/evaluations/formal-evolution-v1-clar-a018-static-eval/BkluqlSFDS/CLAR.paper_failure.json) 的 `review_quality=1.0` 是失败记分，不是真实成功评分。后处理应保留这个状态，并将 rotating 记录归属于本轮父代。

该正式实验的 CoCo 批次使用四个 Vector 谱系 winner 与 QUAL Summarizer 谱系 winner 的组合，[批次 manifest](../../../../AgentReview_0831/artifacts/research_v2/runs/formal-coco1300-winner-v1/coco_review_batch.json)可复核。它没有为全 Vector、全 Summarizer、全 Experience Learner 三个初始共享方案分别建立完整评审与 CoCo 实验组，因此“每个初始 seed 的标准 CoCo 成绩”需要在新实验补齐。历史汇总中应写明该组未建立。

已有 [paired_paper_scores.json](paired_paper_scores.json)提供五组历史 CoCo 的 4409 条成功 `Paper.` 分数；按其 902 篇已暴露历史比较集合展开为五组面板时，应保留 4510 个位置中的 101 个缺失位置。这个后验比较面板不能代替原定 1300 篇的全样本报告，也不能把混合 winner 的 CoCo 分数归给单个初始 seed。

## 6. 0831 旧实现与本地 mstar 是否一致

**仅搜索骨架一致，不是等价复现。**

| 维度 | AgentReview 0831 历史实现 | 本地 mstar |
|---|---|---|
| 父选择 | static 均分 softmax，T=0.15 | 同类 softmax 搜索 |
| 候选 | 五角色分别进化，Meta 固定 | 任务级完整 KBProgram |
| Memory 状态 | 从固定 canonical corpus 为候选新建 | 为候选新建 KB，从 train 数据构建 |
| 变异 | V4A、一次 repair、受限生成调用 | V4A/完整源码，默认更多 repair 机会 |
| fitness | 单角色无锚定 1–5 内评 | 实际任务 scorer，例如 exact match/F1/任务 rubric |
| 反思反馈 | 当前父源码、五篇 rotating review/内评/轨迹 | failure/success、train examples、参考候选与轨迹 |
| static/rotating | 固定 25，rotating 顺序切片 | embedding 聚类选择 static/rotating |
| 经验来源 | canonical125 的固定 Review/内评 | offline 原文提炼；online 当前候选答题与训练反馈构建经验 |
| 最终目标 | 五角色单独选优，最终 CoCo 不回流 | 任务 scorer 直接评价候选 |

mstar 同样不继承父代运行时 Memory，也不能说它把 val/test 金标不断写入 Memory。online 写入发生在 train 阶段；见 [evaluator.py:814](../../../../mstar/src/mstar/evolution/evaluator.py#L814)。0915 的 [formal_evolution.py:1771](../../../AgentReview/agentreview/research_v2/formal_evolution.py#L1771) 固定用 canonical reviews/evaluations 重建。

另一个复现实验问题：0915 父选择使用 `Random(attempt)`，重复搜索的随机种子尚未成为完整实验因子；不能仅改变数据 split seed 就声称完成了多随机种子进化。

### 6.1 采用 M★ 原文后的核验补充

用户已决定遵循 M★ 原文。原文采用完整 MemoryProgram 的反思式种群搜索：固定开发评价集给出任务指标，轮换样本提供当前父代的运行反馈；有效低分候选同样入池，按静态分数 softmax 抽样；新程序按自身规则重新构建 Memory。[原文方法](../../../../mstar/paper/sections/method.tex#L15)、[算法](../../../../mstar/paper/sections/appendix.tex#L99)

本地 mstar 的父代反思优先复用入池时保存的 `reflection_result`，与原文每轮重新采样并评价当前父代不同；完整评价阶段修复耗尽后也可能继续将运行违规的候选入池，与原文约束失败丢弃规则不同。0915 按原文语义处理这两处，详见 [ADR-0014](../../adr/0014-mstar-paper-guided-memory-evolution.md)。跨候选参考程序、具体样本量和搜索配置等仍作为实验条件讨论；本轮有限恢复上限按 ADR-0016 使用可配置工程默认，保留其工程配置身份。

## 7. 已结束的 GEPA 方案研究

**当前决定：采用 M★ 原文机制，GEPA 方案研究结束。** 下列材料保留先前查证结果和方案历史；此前“增加 GEPA 对照”的建议已撤回。当前实验协议以 [ADR-0014](../../adr/0014-mstar-paper-guided-memory-evolution.md) 为准。

GEPA 原论文使用执行/评价反馈进行反思式变异，并通过样本级表现保留互补候选。它适合这里需要细粒度反馈的任务，但有效反馈是前提。[GEPA 原论文](https://arxiv.org/abs/2507.19457)

当前官方实现支持程序代码等文本参数；可用自定义 adapter 保留 MemoryProgram 搜索空间，接入编译、sandbox、Memory build、Review 和标准 CoCo。接口核心为 `evaluate` 与 `make_reflective_dataset`。[官方仓库](https://github.com/gepa-ai/gepa)、[Adapter 文档](https://gepa-ai.github.io/gepa/guides/adapters/)

本地 `mstar/scripts/run_gepa_baseline.py` 只优化 `ALWAYS_ON_KNOWLEDGE`，冻结其他 Memory 结构；直接搬用这个脚本会同时改变搜索空间，不能作为等价优化器比较。

此前对照方案建议固定：模型、初始程序、可变代码范围、论文与参考评审、CoCo 协议、失败规则和候选论文评分预算；记录反思/修复/生成/评分 tokens 与实际耗时。`max_metric_calls` 不是迭代数，也不是全部 GPU 成本。[官方 API](https://gepa-ai.github.io/gepa/api/core/optimize/)、[成本文档](https://gepa-ai.github.io/gepa/guides/cost-tracking/)

GEPA 可以通过 callable 接入本地反思模型。保留 Qwen 配置不构成技术障碍；反思质量仍需实测。[官方 FAQ](https://gepa-ai.github.io/gepa/guides/faq/)

“标准 CoCo”也不是一个天然唯一的优化标量。需要选择主指标、保留原始分项，并说明失败与 completeness 的处理。官方 GEPA 的默认 Pareto 选择追踪各样本/目标的最佳候选，并不自动解决指标权衡。[候选选择文档](https://gepa-ai.github.io/gepa/guides/candidate-selection/)

例如当前本地标准实现的 `Complete.` 为 `AI 类别数 / 人类参考类别数 × 100`（[_4_evaluation.py:790](../../../../CoCoReviewBench/src/evaluation/_4_evaluation.py#L790)），允许超过 100%；它不是正确观点召回率。历史 concat 约 190–196 的值不能读作“正确完整性达到 190%”，更不宜未经讨论直接最大化这个量。

开发集还必须具备标准 CoCo 所需的参考评审结构。`load_human_review_data` 使用 conflicts/rebuttal validation、opinion groups、categories 等内容；现有 OpenReview 开发论文不能仅更换 evaluator prompt 就成为标准 CoCo 开发集。可选方案是建立独立且具备这些参考资产的开发集，或明确划分带现成参考资产的可用 CoCo 数据；数据来源与历史暴露关系须先确认。

### 7.1 融合 GEPA 优势分支保留机制（已撤回提案）

用户曾提出：“这块可以融合 GEPA 进化树的思想吗？GEPA 只有一方面优势就保留。”随后阅读源码，并决定采用 M★ 原文。以下 GEPA 保留规则提案已撤回。

用户随后指定读取 `/data0/xyj/shengao/gepa`。本地源码及确定性函数验证见 [本地 GEPA 搜索策略核验](LOCAL_GEPA_SOURCE_REVIEW.md)：HEAD 为 `15ee314`，核心入口默认 `instance`，`optimize_anything` 的 GEPA 引擎默认 `hybrid`；两者默认反思准入均为小批次主分数严格提高，merge 默认关闭。另已核实内置 merge 是组件级重组，整体程序字符串需要另行设计代码内部融合；CoCo 数值范围也须与“已满分跳过”阈值明确匹配。

官方当前实现需要区分三个环节：

1. **新候选准入**：默认 `strict_improvement` 要求小批次总分相对父候选严格提高。官方同时提供自定义准入接口及“任一样本改善”“任一目标改善”的示例。仅启用 Pareto 父选择，局部改善但总分下降的子候选仍可能在准入时被筛掉。[准入规则](https://gepa-ai.github.io/gepa/guides/acceptance-criterion/)
2. **继续繁衍的候选来源**：`pareto` 使用按 key 记录的最佳候选集合；某候选在该 key 上达到或并列当前最佳，才进入对应映射。核心 `gepa.optimize` 默认 `instance`，即每个验证样本一个 key；`objective` 使用评价目标，`hybrid` 同时使用样本与目标，`cartesian` 使用样本与目标的组合。这些是不同选择，不能把默认样本前沿等同于五个 Reviewer 的角色分数。[候选选择](https://gepa-ai.github.io/gepa/guides/candidate-selection/)
3. **最终采用的策略**：候选有继续探索价值，与它成为最终交付策略是两个决定。保留搜索历史、进入活跃候选范围、最终入选应分别记录；最终仍按预先声明的开发集规则选择一个共享策略，然后冻结并运行封存测试。

源码补充核验固定在官方提交 `15ee314f9c7d34ec153b809d401f42f55c4dcd76`：默认父选择还会裁剪优势 key 全部由其他候选覆盖的冗余候选，因此进入并列最佳映射也不保证获得采样机会；这是 key 覆盖裁剪，不是完整分数向量的标准支配检验。[父选择源码](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/gepa_utils.py#L40-L134) 已接纳的均衡候选即使失去繁衍机会，仍可保存在候选池并按聚合验证分成为最终 `best_candidate`。[最终选择源码](https://github.com/gepa-ai/gepa/blob/15ee314f9c7d34ec153b809d401f42f55c4dcd76/src/gepa/core/result.py#L99-L142)

当时讨论的方案（已撤回）：

- 三个初始 Memory Seed 作为共享谱系的三个起点；每个节点是一套供五角色共同使用的 MemoryProgram，五角色分别构建、维护自身 Memory 库。无 memory 继续作为配对对照。
- 优势来源采用开发评价论文上的最终完整评审 CoCo 分数，以及同一组最终完整评审的 CoCo 质量分项。可借鉴 `hybrid`，同时保存样本专长与质量分项专长；五角色各自评分维持诊断用途。官方默认按夺冠 key 数量采样，论文 key 与指标 key 数量不同，`hybrid` 本身不保证两类探索均衡；具体采样权重另行确定。
- 活跃候选范围另外包含按最终选择主分数计算的总体最优候选，并为它提供明确的正采样概率。按 key 取最佳可能遗漏各项均衡但没有单项第一的策略；仅保存该候选的历史记录不能保证它获得繁衍机会。这一补充是本项目的设计提案，不将其称为 GEPA 默认行为，也不将按 key 取最佳称为完整数学意义的非支配前沿。
- 准入检查与前沿目标保持一致：程序和评分有效的前提下，某个已选样本或质量目标相对父代改善，可以触发进一步固定开发评价；是否具有继续繁衍资格由可比评价后的前沿规则决定。预算、改善阈值、并列候选处理及复评方案随后确定。
- CoCo 标准评分公式保持原义，变化发生在搜索器如何使用这些分数。具体纳入哪些质量分项仍需确定；合法 `X`、异常缺失与执行失败分别记录，使用固定评价身份和明确分母。`Complete.` 的类别数量比保持报告用途，暂不建议直接作为最大化目标。
- 整个谱系的父选择、反思与优势判断使用开发数据。所有候选与五个角色均遵循已确认的数据分区边界。

该历史方案属于“借鉴 GEPA 的混合搜索”，并非完整 GEPA 默认行为。当前采用 M★ 原文机制，此提案与 GEPA 对照均已结束；源码核验材料保留用于说明已讨论过的替代方案。

## 8. 数据和运行状态核验边界

- 开发集 275 篇正文非空，275 个源文件全部存在且 SHA 与冻结记录一致；不能把历史失败笼统描述为当前这些文件失效。
- 测试集 1300 篇冻结正文非空；与 native 共有的 902 个原文逐一检查存在且完全相同。未由此声称整个工作区所有历史数据集都健康。
- CoCo 1300 manifest 自称协议兼容重采样，不是作者原始 official IDs。
- 原开发集只做直接 ID/title/hash 去重，family/version 泄漏风险仍需后续审计。
- native manifest 存在非法尾字符和陈旧状态；历史产物保持原样，审计读取有效结果文件与缓存恢复真实状态。
- memory/no-memory 历史比较同时改变程序、S2 和额外调用，无法独立估计 Memory 效果。
- 模型家族相同的 Reviewer/Judge 结果只支持该 Judge 条件下的比较。是否加入独立 Judge 属于待讨论条件，本轮沿用用户指定本地模型。

## 9. 后续决策顺序与修复验收

逐项讨论，每次只提出一个决策：

1. 开发 CoCo 与最终测试严格隔离（已确认；来源、规模及开发内部子集待定）。
2. seed 指三种初始记忆方案；无 memory 保留同一审稿流程与其他条件，只关闭记忆（已确认）。
3. 共享记忆策略、五库独立、最终完整评审的 Paper. 主指标、Specialist 分数用于诊断，以及 M★ 原文机制（已确认）。统一结果由独立后处理生成，评分失败采用有限恢复、完整评价准入与整轮终止规则（均已确认，见 ADR-0015/0016）。
4. 各臂相同论文输入、thinking、few-shot 与生成预算；原生推荐设置与严格受控设置可分开报告。
5. Meta、S2 和 MemoryProgram 可变边界；随后确定 M★ 的搜索预算、温度与反馈配置。
6. 样本量、重复运行、搜索停止条件和总调用预算；工程故障的有限恢复及预算耗尽终止已确认，当前 10/1/5 上限保留可配置工程默认身份。

本轮已实现 Meta 结构化生成与逐项渲染、完整正文与来源位置、标准 CoCo 开发接口、seed/no-memory 配对、M★ 共享候选池和记忆程序变异，以及独立结果报告。验收覆盖源码逻辑、真实 Sandbox 与标准评分纯函数，模型返回使用测试替身。下一步是冻结实际实验参数和分区资产，完成本地 Qwen 小规模全链路验收后启动正式实验。

验收应证明：相同论文输入在各臂可见内容相同；全部预定 ID 都有成功或明确失败记录；标准指标齐全且每项有有效分母；每个初始记忆方案具有非记忆条件一致的对照；进化 fitness 可追溯到逐篇 CoCo Paper.；最终测试信息不进入反思或候选选择；统一报告可仅凭原始产物独立重建，逐篇表与同分区汇总相符。随机重复方案确定后再扩展相应基线与统计要求。

## 10. 可复核产物

- [audit_checks.py](audit_checks.py)：调用真实上下文构建函数，核对源正文、候选 AST、SHA 和历史指标 schema。
- [audit_checks.json](audit_checks.json)：本次输入与进化检查结果。
- [winner_seed_audit.json](winner_seed_audit.json)：实际部署 winner 的 seed、各谱系初始/最高分、并列候选和源文件身份。
- [recover_paper_scores.py](recover_paper_scores.py)：仅从历史缓存恢复同论文 Paper 评分。
- [paired_paper_scores.json](paired_paper_scores.json)：逐篇与配对聚合结果。
- [audit_classify_failures.py](audit_classify_failures.py) 与 [classify_failure_audit.json](classify_failure_audit.json)：重放分类缓存，统计协议失败和复合观点示例；使用相同历史评测 Python 环境。

运行方式：在此目录执行 `python3 audit_checks.py`；缓存恢复使用已有历史依赖环境：`/data0/xyj/shengao/AgentReview_0831/artifacts/research_v2/runtime/coco_eval_env_v1/bin/python3.10 recover_paper_scores.py`。脚本只读取历史实验，不调用模型；写入同目录诊断结果。本轮已获授权的代码修复限于 0915，Meta 测试见第 3.2 节；模型运行与新实验根据后续确定的实验条件安排，0831 历史产物保持原样。
