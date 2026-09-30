# AgentReview Python 运行包

本目录实现当前共享记忆审稿系统。运行入口、当前协议和服务状态见[仓库 README](../README.md)，模块调用链见[架构说明](../docs/ARCHITECTURE_0930.md)。

| 路径 | 输入与输出 |
|---|---|
| `agentreview/research_v2/` | 冻结协议与论文资产，产出记忆、审稿、评分、候选和实验回执 |
| `agentreview/reviewer_runtime/` | 候选 Python 程序与角色任务，产出隔离执行结果和工具轨迹 |
| `agentreview/reviewer_identity.py` | 冻结的五角色和 Meta 定义，产出生成消息与身份 |
| `memory_evolution/embedding_adapter.py` | 完整文本，产出固定向量模型的 embedding 与缓存记录 |
| `tests/` | 程序行为、分区隔离、评分身份、API 调用和运行生命周期的验证 |

核心命令提供单次搜索与冻结测试：

```bash
PYTHONPATH=AgentReview python -m agentreview.research_v2 --help
```

小规模流程通过仓库根目录的 `deployment/pilot_0930.py` 统一调度。五个固定角色为 QUAL、CLAR、SIGN、ORIG、POL；每个角色持有独立记忆，候选池共享同一份可执行记忆策略。
