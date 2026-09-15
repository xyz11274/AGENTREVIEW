---
status: accepted
---

# ADR-0016：有限失败恢复与完整评价准入

2026-09-15，用户接受持续失败的终止与恢复规则，并授权在 0915 修复根因。标准 CoCo 保持原样；修复发生在 AgentReview 的生成契约、数据准备、适配和运行控制。旧实验及原始输出保留原有身份。

## 决定

每个请求和候选修复均有有限预算，整轮累计修复与候选失败也有上限。预算在操作前持久化，恢复沿用已消费记录。临时服务故障按预算重试；共享数据、评价、运行故障及归因未定的故障暂停整轮。只有可复核的候选契约违规才结束对应候选尝试，其他合格候选继续。

旧角色运行器的工程默认值为：每候选修复 1 次、整轮修复 10 次、整轮候选失败 5 次，均可配置。新共享入口要求协议显式填写并冻结这些上限。旧默认值具有工程配置身份；论文样本量、搜索次数、模型调用预算及重复实验方案继续单独确定。

共享故障恢复需要保存原因、修复和验证证据，建立新的 Evaluation Epoch，并对受影响比较中的全部候选及对照统一重评。每个候选的完整 Evaluation Receipt 绑定原定论文清单、冻结输出、程序与 Memory 身份、评分协议、逐篇产物和聚合结果。只有同一评价版本下完整且可比的结果，才具备候选准入、排序及发布资格。运行有效的低分候选按 M★ 原文入池。

同一确定性错误连续出现时停止该请求的重试并记录原因分类。修复后的重评再次失败时，下一次恢复需要新的修复验证记录；进程中断则沿用当前 epoch、已保存请求和预算。候选提案的反馈缓存及新程序身份随 epoch 更新，先前缓存继续作为历史证据保存。

修复耗尽时整轮结束，记录结束原因并保留原有 Champion 及全部检查点；本轮保留未完成身份。新 winner 的发布以完整比较和剩余运行预算为条件。失败位置记录状态和空分数，标准 `Paper.` 字段仅保存真实有效评分；成功子集的均分使用诊断身份和实际分母。

评分重试读取同一份已冻结 Review。生成契约或候选程序改变后，输出具有新的生成身份。最终测试保持原定论文清单、冻结策略和原始输出，测试反馈只进入最终报告。独立汇总继续遵循 [ADR-0015](0015-paper-fitness-and-separate-results-report.md)，负责读取和展示已保存结果。

## Meta 根因修复

历史 Meta 把多项结论合在短段落里，标准 CoCo 预切保留该段为一个 chunk，后续 Split 只能组合 chunk ID；由此出现分类三轮均输出超过 3 个合法类别的结构性失败。0915 使用版本化的结构化 Meta 契约：每个观点分别保存科学结论、来源角色、证据 anchor 与建议，校验实际来源后逐项、逐段确定性渲染为 Final Review。

结构校验验证字段、边界和引用身份；科学语义是否独立仍由生成模型和实际评测体现。该修复保留每项原始结论与建议，标准 CoCo 的预切逻辑、分类上限、提示和公式保持原样。新 Meta 的 request、output、rendering 和冻结 identity 均为 v2，历史 v1 产物继续保留。

## 实现范围与验证

本轮先完成 Meta 生成边界与旧运行器失败控制，随后接通 ADR-0013–0015 的共享候选池、完整 MemoryProgram 搜索和标准 CoCo `Paper.` 进化闭环。新入口使用持久化请求预算、五库与完整最终评审绑定、标准 CoCo Receipt、共享暂停与候选及对照统一重评。旧 `review_quality` 凭据仅用于历史角色运行入口；新实验使用 `shared-experiment`。实现与配置见 [共享 M★ 实验](../SHARED_MSTAR_IMPLEMENTATION.md)。

最新共享与兼容性组合验收为 **256 passed、96 subtests passed**；最后变更相关组合为 **84 passed、25 subtests passed**，两组分别统计。范围、复现命令与真实模型验收边界见 [验收记录](../audit/20260915/SHARED_IMPLEMENTATION_VALIDATION.md)。下文 42 与 98 项结果保留前一批 Meta/旧角色修复的范围身份。

Meta 专属及相关兼容测试共 **42 passed、19 subtests passed**，包含通过本地标准 CoCo 原有 Split 提示构建纯函数验证逐项边界，无模型请求。测试范围和命令见 [系统重审记录第 3.2 节](../audit/20260915/SYSTEM_REASSESSMENT.md#32-meta-根因修复与验证)；这个结果仅对应 Meta 相关测试集。

## 运行入口

新实验使用 `shared-experiment --action preflight|run|final-test --protocol ... --output ...`。公共故障修复后用同一运行目录加 `--repair-evidence ...`；修复记录和预算跨进程恢复。最终测试使用 `--frozen-system`，在读取封存资产前检查开发运行的准入状态、当前评价版本与冻结生成身份。完整 Review 在评分恢复时复用，新的评分结果保留新 epoch。

### 历史角色运行入口

`formal-evolution --action run-role` 与 `run-all-roles` 接受 `--candidate-repairs`、`--round-repairs`、`--candidate-failures`。新轮次保存这些上限，恢复使用原轮次的预算记录。

公共故障修复后，以原运行参数加 `--repair-evidence /absolute/path/repair.json` 恢复。该 JSON 包含非空的 `cause`、`repair`、`validation`，分别记录根因、实际修复和验证证据。运行器完成数据预检，重建受影响候选的 Memory 并在新 epoch 生成完整评价；全部准备完成后统一更新候选池。暂停、重评中或预算耗尽时，CLI 返回退出码 2。

实现入口：[failure_policy.py](../../AgentReview/agentreview/research_v2/failure_policy.py)、[formal_recovery.py](../../AgentReview/agentreview/research_v2/formal_recovery.py)。运行恢复、预算、发布门槛、评分、Sandbox 公共故障归因和监控相关的十个测试文件最终合跑为 **98 passed、41 subtests passed**。测试覆盖中途写入恢复、已加载候选池的即时暂停、相同证据换路径的识别，以及候选修复失败后的终态处置与其余候选统一重评。验证未调用部署模型。
