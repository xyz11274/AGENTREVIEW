# 0915 共享实验实现验收

日期：2026-09-15。分支：`0915`。范围：ADR-0011–0016 的共享 M★、严格用途分区、完整论文输入、标准 CoCo 评分、无记忆对照、冻结测试和独立结果报告。

## 结果

| 验证范围 | 结果 |
|---|---|
| 新共享模块与既有 Memory/Review/Meta、失败恢复、角色历史入口、Judge lease：28 文件组合 | 256 passed，96 subtests passed，135.43 秒 |
| 最后变更相关：CoCo、请求恢复、共享 backend/搜索/最终测试/Memory、协议与报告：8 文件 | 84 passed，25 subtests passed，24.30 秒 |
| `git diff --check` | 通过 |
| `shared-experiment --help`、`experiment-report --help` | 通过 |
| 0831 与 CoCoReviewBench 工作区 | 保持干净 |

第一组出现 3 条 Python multiprocessing fork 弃用提示，来自已有 Judge lease 多进程测试。测试成功完成。两组有重叠，计数分别报告。

## 核心行为证据

- 标准 CoCo 的真实 Table-1 函数确认 `Paper.` 与包含 actionability 的 judge average 分别计算，合法 `X` 保留标准分母。
- 真实 Memory/Sandbox/Review/Meta 流程中，最终 Paper 为 2、角色诊断为 5 时，候选分数仍为 2。
- 一个共享程序对应五个独立快照；关闭 memory 保持同一 plan/S2，并关闭 KI、Query 和摘要调用。
- 同一 epoch 中 static 与 rotating 使用该程序已构建的角色 Memory；每轮生成新的 rotating 评审反馈。
- 新 epoch 重建受影响开发资产，统一重评候选及对照。修复预算耗尽时停止后续请求，保留 Champion。
- 约束失败经过有限修复后留作失败尝试；有效低分候选留在共享种群。
- 恢复提交中断后可重放，修复预算按原记录消费；发布使用实时公共暂停状态检查。
- 封存测试在冻结及准入检查后读取资产，复用五个开发 Memory 快照。评分恢复保持已生成的角色和最终 Review 文件哈希，模型生成调用数保持相同。
- 真实 backend 的报告来源清单由独立工具读取，分别输出 seed、Champion、逐论文分项及角色诊断。
- 持久化请求在连续确定性错误后停止；终态文件写入中断也保持该停止规则。
- 模型权重使用实际文件 SHA 校验，同长度改写也能识别。

## 复现环境与最后一组命令

在 `AgentReview_0915` 执行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python -m pytest -q \
  AgentReview/tests/test_research_v2_development_coco.py \
  AgentReview/tests/test_research_v2_experiment_requests.py \
  AgentReview/tests/test_research_v2_shared_coco_integration.py \
  AgentReview/tests/test_research_v2_shared_final_test_boundaries.py \
  AgentReview/tests/test_research_v2_shared_evolution.py \
  AgentReview/tests/test_research_v2_shared_memory_contract.py \
  AgentReview/tests/test_research_v2_experiment_protocol.py \
  AgentReview/tests/test_research_v2_experiment_report.py
```

## 实验启动边界

本轮运行的是自动化测试，模型输出使用测试替身；标准评分函数与文件/快照/凭据交接保持真实逻辑。实际部署模型验收、论文表现提升及正式全流程模型实验仍待执行。

新实验使用 `shared-experiment`。待填写协议明确保留实际分区来源、样本规模、生成设置和搜索预算；测试身份清单、参考资产及版本家族审核必须与填写结果一致。当前共享实现支持 Memory 范围变异和零示例 CoCo Native；带示例 Native 需要额外可核验的分区来源清单。
