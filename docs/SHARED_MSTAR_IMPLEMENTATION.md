# 0915 共享 M★ 实验实现

本实现承接 ADR-0011–0016，当前配对实验的数据用途规则由 [ADR-0019](adr/0019-existing-benchmark-paired-evolution.md) 更新。实验以一个共享 MemoryProgram、一个候选池和一条进化谱系运行；QUAL、CLAR、SIGN、ORIG、POL 分别构建和读取自己的记忆库。仅最终完整评审进入标准 CoCo，Table-1 `Paper.` 决定父代抽样和 Champion；不再单独评分五角色。

## 执行流程

1. 读取显式冻结实验协议，检查 build、static、rotating、sealed_test 的身份、版本关系及各自资产。本轮允许为用途/版本和完整输入预算核对读取资产；最终生成与评分必须等全部待比较系统冻结，测试参考评审只供 CoCo。
2. 在 build 集生成相同固定审稿策略的无记忆角色经验。每个 seed 或新 MemoryProgram 从这些获准角色经验构建五个独立快照。
3. 三个 memory seed 分别生成 static 集五角色评审及最终评审，按原标准 CoCo 评分。各 seed 的无 memory 对照保持审稿策略、S2、模型和生成条件相同，关闭记忆操作及注入。CoCo 原始提示基线单列。
4. 在同一 static 集的完整 `Paper.` 成绩上进行 softmax 父代抽样。每轮重新评价所选父代的 rotating 样本，保存当前五角色轨迹、完整最终评审及标准反馈。
5. 反思生成完整的新程序源码，搜索 KnowledgeItem/Query Schema、KnowledgeBase 读写逻辑和记忆交互指令。宿主验证固定非记忆审稿策略与角色库边界，再构建新快照、生成评审并执行标准评分。
6. 完成有效评价的子代进入共享池，包括成绩下降的有效子代。候选契约错误按有限预算修复；公共故障暂停整轮，修复后在新 epoch 统一重评候选和对照。
7. 完整比较后保存版本化 winner 与冻结系统。独立 final-test 入口复用冻结程序和开发记忆，在封存集生成、评分；测试结果进入测试产物与报告。
8. 另外运行报告工具，读取预定论文清单与已保存评分，生成每个 seed、无 memory、Native、候选、Champion 的逐论文表、CoCo 汇总与谱系；配对入口另存逐篇差值和不确定性报告。

论文正文完整进入审稿上下文；真实字符位置和已有来源 anchor 用于引用。请求超过冻结输入或模型总 token 预算时记录显式失败。生成记录包括模型输出、请求、来源与预算身份。

## 新入口

以下命令中的协议文件由实际讨论结果填写并冻结；`shared_memory_experiment.pending.json` 是待填写模板。样本量、温度、进化轮数、调用预算和 Meta/S2 条件从该协议读取。

当前共享入口支持 Memory 范围的变异和 Native 零示例提示。CoCo 自带 few-shot 评审缺少可核验的论文/版本家族身份；启用这些示例前需要补充分区审核清单。低层 Native helper 保持原标准函数，此限制记录在新实验的数据协议中。

```bash
PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action preflight --protocol /absolute/path/experiment.frozen.json \
  --output /absolute/path/development-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action run --protocol /absolute/path/experiment.frozen.json \
  --output /absolute/path/development-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action run --protocol /absolute/path/experiment.frozen.json \
  --output /absolute/path/development-run --repair-evidence /absolute/path/repair.json

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action final-test --protocol /absolute/path/experiment.frozen.json \
  --frozen-system /absolute/path/development-run/winner.json \
  --output /absolute/path/final-test-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 experiment-report \
  --manifest /absolute/path/development-run/report-input.json \
  --output /absolute/path/reports/development-snapshot
```

测试结果报告使用 `--report-stage final`；报告 manifest 可列出多个分区 scope，将开发和测试分别展示。报告工具读取冻结产物，生成统一报告的操作由用户单独执行。运行器只保存结果来源清单 `report-input.json`。

旧 `formal-evolution` 与 `run-evolution` 入口保留历史角色内评运行协议。0915 共享标准 CoCo 实验使用 `shared-experiment`。

## 失败与恢复

请求尝试在发送前落盘。瞬态失败按冻结次数重试；相同确定性错误重复时终止请求，恢复读取已消费预算。候选程序契约违规执行有限修复；其他故障暂停整个比较。完整 CoCo Receipt 绑定原定论文顺序、评分协议、epoch、原始评分、Review、候选代码及五份快照。

修复记录包含非空 `cause`、`repair`、`validation`。整轮修复预算或失败预算耗尽时结束本轮并保留当前已验证 Champion。统一重评期间保持原 Champion，待受影响集合全部完成后更新。完成的 winner 使用 `winner.eN.json` 保留评价版本，`winner.json` 指向当前完成结果。

最终测试冻结生成算法、模型、程序和 Memory。评分修复使用新的评价 epoch，并复用已完成的原始 Review；评分控制实现与生成身份分别记录。生成条件改变需要新的系统冻结与实验身份。

## 实现与验证入口

- [实验协议、数据隔离与正文输入](SHARED_EXPERIMENT_PROTOCOL.md)
- [标准 CoCo 适配](STANDARD_COCO_ADAPTER.md)
- [CoCo Native 基线](COCO_NATIVE_BASELINE.md)
- [独立报告格式](EXPERIMENT_REPORT.md)
- [共享搜索运行器](../AgentReview/agentreview/research_v2/shared_evolution.py)
- [真实阶段适配与冻结测试](../AgentReview/agentreview/research_v2/shared_experiment.py)

本轮自动化验证覆盖本地文件、真实 Sandbox、Memory/Review/Meta 逻辑、标准 CoCo 纯函数与完整评价凭据；模型返回使用测试替身。2026-09-17 的反馈传递、token 预算及缓存修复见 [ADR-0020](adr/0020-complete-memory-experience-and-token-budgets.md)。复用已完成的真实全流程验收，本轮不新增 pilot；完整规模启动须使用明确的搜索数值预算。

最终组合结果和可复现命令见 [实现验收记录](audit/20260915/SHARED_IMPLEMENTATION_VALIDATION.md)：28 文件组合 256 项测试、96 项子测试通过；最后变更相关 8 文件组合 84 项测试、25 项子测试通过。
