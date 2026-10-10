# 0930 小样本全流程实验计划

当前状态：已准备，等待用户明确开始。本轮仅修复与本地验证，不调用真实生成/评分模型，不启动正式实验。

新身份：`0930-smallflow-20261009-v2`。配置为 `config/0930-smallflow-20261009-v2/profile.json`，产出根目录为 `artifacts/smallflow-0930-20261009-v2/`。先前未运行的准备版本保留，不作为本次入口。

## 数据、对照与预算

| 项目 | 声明条件 |
|---|---|
| 构建集 | 原 pilot 的 4 篇；生成五角色构建经验，重新构建记忆 |
| 固定评价集 | 原 pilot 的完整 3 篇；全部完成后才产生可比较 Fitness |
| 旋转反馈池 | 原 pilot 的 5 篇；每次按种子抽 3 篇，最低 2 篇用于详细反思 |
| 开发留出集 | 原 pilot 的完整 3 篇；沿用接口中的 `sealed_test` 名称，结果明确标注开发与历史暴露 |
| 搜索 | 一个独立随机种子 0，3 次进化尝试；失败也消费尝试，不补充隐藏轮次 |
| 初始对照 | Vector Search、LLM Summarizer、Experience Learner，三个对应无记忆对照，CoCo 原生对照 |
| 留出比较 | 上述七组加冻结 Champion，共八组；所有有效子代也做留出轨迹诊断 |
| 模型 | 原 DeepSeek v4.1 Flash，原 Split/Classify 权重、端点和 TP；Qwen3-Embedding-8B 保持 CPU |
| 总预算 | 云端 HTTP 实际发送最多 2000 次，本地最多 10000 次；整个运行最多 12 小时 |
| 单次请求 | 从并发准入到连接、首块、流式结束共最多 600 秒；SDK 原有读取超时保留 |
| 重试 | 生成最多三次逻辑尝试、SDK 零重试；CoCo 官方应用与 SDK 重试保留，每次实际发送均计账 |
| 程序修复 | 初始反思响应加最多三次修复；候选/轮次失败预算沿用原规则 |

完整论文评分单元最多约 72 次：七组初始固定评价 21 次，三轮父代旋转评价 9 次，三轮子代固定评价 9 次，八组开发留出评价 24 次，三个有效子代留出诊断最多 9 次。身份一致的既有本次回执允许按现有控制器规则复用。HTTP 调用数与评分单元不同，取决于意见数、Judge 任务与实际重试；预算是硬上限，不是完成时长或成本承诺。

这次使用既有 pilot 数据身份、完整正文和参考资产，不打开正式实验的 1300 篇测试正文或参考。新目录不继承旧候选、种群、分数、检查点、随机状态或 Embedding 缓存；旧运行的消费与失败历史保留在原目录。正式实验保持停止，不自动衔接。

## 执行顺序

1. 核对冻结代码、数据闭合、服务、生产解释器、代理与沙箱，启动本次独立累计账本和监督进程。
2. 生成 4 篇 × 五角色构建评审，构建三个初始记忆方案；各角色独立保存与恢复。
3. 七组对照完成同一 3 篇固定评价集。Fitness 仅取同篇最终完整评审的原版 CoCo `Paper.`，按未舍入结果比较。
4. 执行三次父代采样、旋转反馈、真实反思、补丁解析和有限修复、五角色生命周期验证、完整固定评价、候选入池、Champion 更新和检查点提交。
5. 搜索冻结后执行八组开发留出比较与所有有效子代轨迹诊断。留出评分不得进入反思或重新选 Champion。
6. 逐个有效候选检查程序、角色快照、实际注入提示、五份新评审、最终评审、评分回执与控制器 `Paper.` 的身份链。重新读取并重放已完成检查点，确认调用账本、失败预算与种群不重复推进。
7. 输出配对逐篇分差、均值、胜/平/负、实际调用与耗时、失败原因，以及人工复核入口。

五角色中间评审不单独增加 CoCo 评分。有效低分子代按既定规则入池，只有满足既定比较规则才更新 Champion。缺失完成状态、截断、评分失败和不完整评价集均不补零，不排名、不入池，消费照常保存。评价期间不向记忆写回。

## 判断标准

全流程通过要求：声明的比较集合完整、至少一个真实进化候选完成完整固定评价和身份链检查、八组留出评价与有效子代诊断完整、检查点重放不产生新请求或重复记账。预算耗尽、必要评分缺失或没有有效子代时，保留产物并标记未完成，不宣称通过。

效果分别报告固定集与开发留出集上的 Champion/子代相对父代、最佳固定种子和对应无记忆对照的逐篇 `Paper.` 差值。提分不作为运行验收条件。三篇样本与一个搜索种子只能提供探索性结果；不能据此宣称记忆或进化有效/无效。类别级 `Correct.`、`Thoro.` 不用作论文级 `Paper.` 变化的直接原因。

## 产出位置

以下路径均相对 `artifacts/smallflow-0930-20261009-v2/`。准备产物现在已有；运行产物会在明确启动后产生。

| 内容 | 路径 |
|---|---|
| 计划、样本清单、配置与实际代码快照 | `plan.json`、`protocol.frozen.json`、`code-freeze.json`、`frozen-code/` |
| 日志、监督状态、累计调用账本 | `smallflow.log`、`supervisor-state.json`、`call-budget.json` |
| 运行阶段、控制器、失败预算 | `pilot-state.json`、`search/search.json`、`search/failure-policy.json` |
| 候选程序与反思修复 | `search/proposals/` |
| 五角色记忆及构建来源 | `search/bootstrap/`、各候选 `search/assessments/` 下的记忆和快照 |
| 最终完整评审、原版 CoCo 回执 | `search/assessments/`、`final/`、`round-test/` 内相应 `final_reviews/` 和 `coco/` |
| 搜索冻结和留出诊断 | `search/winner.json`、`final/final-test.json`、`round-test/final-rounds.json` |
| 最终身份链与零请求重放证明 | `completion-check.json` |
| 自动报告 | `report/自进化逐轮报告.md`、`report/流程耗时报告.md`、`report/paired-paper-deltas.csv`、`report/paired-summary.json` |
| 人工复核 | `report/人工复核.md`，独立保存，不由自动报告生成器覆盖 |

## 启动与查看（本轮不执行启动）

用户确认后，可使用相同生产环境启动独立服务：

```bash
cd /data/xyj/shengao/AgentReview_0930
systemd-run --user --unit=agentreview-0930-smallflow-20261009-v2 \
  --property=WorkingDirectory=/data/xyj/shengao/AgentReview_0930 \
  --property=KillSignal=SIGINT --property=TimeoutStopSec=15 \
  --property=RuntimeMaxSec=43200 --property=Restart=no \
  --property=StandardOutput=append:/data/xyj/shengao/AgentReview_0930/artifacts/smallflow-0930-20261009-v2/smallflow.log \
  --property=StandardError=inherit \
  /data/xyj/shengao/AgentReview_0930/deployment/run_runtime_0930.sh small pilot \
  --profile /data/xyj/shengao/AgentReview_0930/config/0930-smallflow-20261009-v2/profile.json
```

```bash
/data/xyj/envs/coco-original/bin/python -B deployment/runtime_0930_progress.py \
  --root artifacts/smallflow-0930-20261009-v2
tail -f artifacts/smallflow-0930-20261009-v2/smallflow.log
systemctl --user status agentreview-0930-smallflow-20261009-v2.service
```

暂停或异常恢复保留累计次数与原截止时间。增加预算或重新开启时间窗口需要明确说明并保存新授权，不能清空旧账本。修改已冻结实现或实验规模时创建新配置身份，不原地改旧回执或哈希。
