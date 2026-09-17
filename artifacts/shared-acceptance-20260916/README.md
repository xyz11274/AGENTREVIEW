# 共享记忆真实开发验收

2026-09-17：两道真实验收均通过，子代 r1 入池并冻结为 Champion，开发终评 `Paper.=3.2`；pilot 已启动，尚未完成。完整结果和限制见 [审计报告](audit-report.zh.md)，机器可复核证据见 [acceptance-inspection.json](acceptance-inspection.json)，启动状态见 [pilot-startup-check.json](pilot-startup-check.json)。

本目录对应 ADR-0017。开发验收和 pilot 都使用本地真实 Qwen3.8-27B、Qwen3-Embedding-8B、原版 CoCo Split/Classify/Judge；不存在用桩分数替代真实评价的执行路径。

执行入口：`deployment/shared_acceptance.py`。四个动作依次为 `prepare`、`gate`、`evolution`、`pilot`。最后一个动作要求前两道验收凭据完整、原始评分凭据通过复核，且绑定当前运行代码。

```bash
OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 HF_HUB_OFFLINE=1 PYTHONUNBUFFERED=1 \
  /data/xyj/envs/coco-original/bin/python deployment/shared_acceptance.py gate
```

实际状态以以下文件是否存在且验证通过为准，目录存在、进程退出或单个分数都不算通过：

| 阶段 | 验收凭据 |
| --- | --- |
| 共享 Memory → 五角色 → Meta → CoCo → Paper. | `gate-one-paper.json` |
| 真实改动子代入池、Champion 冻结、开发终评 | `gate-child-freeze-development-final.json` |
| pilot 已按前置验收启动 | `pilot-started.json`，以及 `pilot/run/search.json` 的实时状态 |

每次生成的请求、预检 token 数、实际响应、`finish_reason`、usage 和有限重试失败保存在各阶段的 `requests` / `review-requests` 目录。CoCo 每阶段有 `stages/`、`cache/<stage>/wire/` 和 `integrity/` 记录；`assessment.json` 是最终可校验的评价凭据。

五角色只共享程序，各自拥有 build 快照。所有论文从对应冻结快照重新实例化，运行中的可写状态不跨论文传递。只有最终完整评审进入 CoCo；失败分数为缺失值，不能入池，不补零。

初始单篇验收使用已暴露开发论文 `-EHqoysUYLx`。功能验收从已入池的共享种子执行一次真实 MSTAR 转移，并验证 Native JSON 输出边界；父代采样、反思、有限修复、评价入池和冻结均调用现有实现。状态记为 `acceptance_complete`，不声称完成三种子实验。三种子、匹配无记忆对照和 Native 对照的完整比较由 pilot 的 `SharedEvolution.run()` 执行。开发终评对冻结 Champion 重新生成并完整评分，仍使用已暴露开发论文，只验证链路。

pilot：build 4 篇、static 3 篇、rotating 4 篇、3 次进化尝试、每轮随机抽取 2 篇反馈。准备阶段冻结 `sealed_test` 的资产和身份，但不将 sealed 论文用于模型生成、检索或评分；历史暴露清单及家族检查的限制见协议和 `family-audit.json`。本目录不提供正式未见测试效果证据。

当前回归结果见 `current-tests.log`、`current-tests.xml`；模型真实运行结果与这些测试结果分别记录。历史失败、修复原因和验证保留在 `repair-001.json` 至 `repair-003.json`。每次公共代码修复均推进评价 epoch 并重新评价受影响对象；旧分数不能参与新 epoch 排名。

第三次修复来自真实子代：它将不存在的 `toolkit.llm()` 藏在广泛异常捕获内，静默返回备用文本。四个回归用例先复现漏洞，再验证宿主会拒绝未知接口、被吞掉的 LLM/Embedding 失败以及失败后的快照请求。旧子代 r0 保留供诊断，未入池；修复版本由真实 MSTAR 模型产生。
