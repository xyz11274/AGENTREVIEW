# 0930 长度限制修复与实验修订

当前架构与运行入口见 [ARCHITECTURE_0930.md](ARCHITECTURE_0930.md) 和 [仓库 README](../README.md)。本文保留 2026-09-29 的阶段记录。

后续更新：本轮后来因宿主 CoCo 覆盖率校验暂停。该额外门槛已移除，现使用独立官方源码修订接续小规模实验；最新路径及验收见 [CoCo 官方原则与修复记录](COCO_UPSTREAM_POLICY_0930.md)。下文保留长度修复时的记录，其中生成请求的 `stop` 约束不再施加到 CoCo 评分客户端。

原混合部署实验暂停的直接原因是宿主给 `memory_summarizer` 设置了 `max_tokens=4096`。两条请求各重试两次，均返回 `finish_reason=length`。下游记忆上下文同时存在 4096-token 检查，因此只修改 API 请求还不能解决全部问题。

## 新的长度策略

角色评审、记忆摘要、Meta、Native 生成和反思提案统一使用模型容量，不再设置各阶段的 4096、8192、16384 输出上限。请求预算为：

```text
实际 max_tokens = min(模型最大输出, 模型上下文 - 完整输入 token 数)
```

DeepSeek V4.1 Flash 的公开规格为 1M 上下文、384K 最大输出；当前冻结配置分别为 1048576 和 393216。规格依据：[DeepSeek 官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)。方舟的 `max_tokens` 包含回答和思考 token，不能把默认输出值当成模型硬上限，见[方舟 Chat API 文档](https://docs.volcengine.com/docs/ark/chat-api?lang=zh&redirect=1)。

火山端点已经接受 `max_tokens=393216` 的真实请求。短探针只验证参数接受，不代表已经生成过 384K 文本。完整输入采用固定版本的官方 tokenizer/编码器估算，API 的实际用量仍是计费依据。

- 下游完整记忆的 4096 限制一并移除，改按模型上下文检查。最终请求会对论文、提示、记忆的合并输入重新计数。
- 不截断论文、摘要或 API 返回；如果完整输入已耗尽物理上下文，明确记录失败。
- 仍要求 `finish_reason=stop`，超长截断不会被当作成功结果或有效评分。
- 记忆选取数量、API 重试次数和五轮搜索预算保持原实验设置；这些不属于本次长度问题。
- CoCo 的作者原版 Split/Classify、评分提示、指标聚合和评分调用参数保持原冻结方案。新长度策略作用于待评审文本的生成与自进化。

## 实际失败请求的重放

保持两条原始请求的消息逐字不变，只调整输出预算，两条均得到完整返回：

| 请求 | 原预算 / 原状态 | 修复后实际输出 | 结束原因 |
|---|---|---:|---|
| `0ebf035a…` | 4096 / length | 3274 token | stop |
| `fa82d25d…` | 4096 / length | 6611 token | stop |

原提示本身含有旧的简短输出要求，模型也不保证重放文本完全相同。因此这些数字证明两条真实请求现在能够完整结束，不代表分数或质量提升。新初始摘要程序同时去掉了依赖旧长度上限的提示。

证据：[真实重放记录](../metadata/model-capacity-live-replay.json)、[参数探针](../metadata/pilot-output-capacity-probe.json)、[回归测试日志](../metadata/model-capacity-regression.log)。回归覆盖超过 4096 的摘要及记忆、长输入的剩余空间、物理上下文耗尽、截断输出拒收与失败账本。

全量回归结果为 **295 passed，101 subtests passed**。两条真实重放文本还通过了实际 tokenizer 和下游 MemoryToolkit 的完整性检查，其中 6611-token 文本被整段保留。[验收汇总](../metadata/model-capacity-validation.json)同时验证旧协议、检查点、失败账本、归档哈希以及新旧数据划分和 CoCo 评分绑定。

## 如何接续实验

长度和摘要程序改变了生成条件，因此新建冻结修订，重新生成构建材料并评估全部对照，不能直接把旧分数拼接进新进化曲线。15 篇论文的划分、搜索种子、最多五次提案及最终测试规则不变。

旧目录 `artifacts/pilot-0930-hybrid/` 保留原始结果和失败记录。修复前源码和配置已归档；旧搜索尚无进化提案，三个随机流的抽样次数均为 0，候选失败和修复预算均未使用。[修订记录](../artifacts/pilot-0930-hybrid/repairs/model-capacity/revision.json)保存旧协议、检查点及源码快照的哈希。只复用身份一致的向量缓存，不导入旧评分、生成响应或记忆快照。

当前协议：[protocol.frozen.json](../config/0930-pilot-model-capacity/protocol.frozen.json)。新实验输出：`artifacts/pilot-0930-model-capacity/`。

- [实时状态](../artifacts/pilot-0930-model-capacity/pilot-state.json)：整体阶段和运行状态。
- [逐轮报告](../artifacts/pilot-0930-model-capacity/report/自进化逐轮报告.md)：每轮实际程序 diff、固定集和独立测试效果；尚未完成的轮次不填写分数。
- [启动日志](../artifacts/pilot-0930-model-capacity/launcher.log)：进程输出和异常。
- `search/search.json`、`search/failure-policy.json`：候选、随机流、评分引用和失败记录。

后台运行由 `agentreview-0930-pilot-model-capacity.service` 管理，脱离交互式 SSH。实验失败时保存诊断并暂停，避免自动重复付费请求；CoCo 服务与 SSH 转发继续使用已有独立守护服务。

修正版于 **2026-09-29 16:44:32 CST** 启动。启动验证时已产生 8 条完整构建评审响应，实际请求使用 `max_tokens=393216`，尚无失败记录；这一数字是启动快照，后续进度以实时状态和逐轮报告为准。

```bash
systemctl --user status agentreview-0930-pilot-model-capacity.service
bash deployment/run_pilot_0930.sh report \
  --config-dir config/0930-pilot-model-capacity \
  --output-root artifacts/pilot-0930-model-capacity
```

此次仅接续小规模测试，不启动完整实验。每轮效果仍以完整 CoCo 回执为准，部署验收或真实请求重放成功均不等于自进化产生提升。
