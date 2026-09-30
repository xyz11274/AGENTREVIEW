# Embedding API 实测与 CoCo 暂停原因

检查日期：2026-09-29。下文保留初次独立探测和离线诊断时的结论。随后按用户“保留官方源码和行为”的要求，已修复包装层并通过真实全流程验收，最新状态见 [CoCo 官方原则与修复记录](COCO_UPSTREAM_POLICY_0930.md)。Embedding 模型尚未切换。

## Embedding：现有 Plan Key 可以使用，实测明显快于当前 CPU 路径

实测接口：`https://ark.cn-beijing.volces.com/api/plan/v3/embeddings`。请求名为 `doubao-embedding-vision`，实际返回模型为 `doubao-embedding-vision-251215`，每条输出 2048 维。Key 仅从私有环境读取，不写入探测结果。

模型支持纯文本输入，见[方舟向量化文档](https://docs.volcengine.com/docs/ark/vectorization?lang=zh)。官方也列出了 Plan 的 Embedding 模型支持，见[功能发布记录](https://docs.volcengine.com/docs/ark/coding-plan-personal-feature-release?lang=zh)；本机 `/api/plan/v3/embeddings` 的可用性则由本次实际请求验证。

从当前 `seed-vector_search` 的五个角色快照各取第一条完整记忆，长度 4972–7809 字符，未截断、未修改文本。顺序执行五次单条请求，再执行一次五条批量请求：

| 样本 | 输入字符数 | API 输入 token | 请求耗时 |
|---|---:|---:|---:|
| QUAL | 7809 | 2058 | 0.348 秒 |
| CLAR | 6714 | 1823 | 0.225 秒 |
| SIGN | 6741 | 1674 | 0.289 秒 |
| ORIG | 7440 | 1852 | 0.471 秒 |
| POL | 4972 | 1285 | 0.210 秒 |
| 五条批量 | 33676 | 8692 | 0.407 秒 |

单条中位耗时为 0.289 秒。批量返回五条独立向量，索引 0–4 对齐，全部 2048 维且数值有限。这是一次小样本墙钟耗时测量，不代表长期吞吐、P95、首次服务端计算耗时或检索质量；批量请求重复了刚测的文本，不能排除服务端缓存影响。

当前本地 Qwen3-Embedding-8B 使用 CPU FP32、四线程、batch_size=1，首次构建二十条记忆经历约十八分钟，后续命中缓存则很快。本次 API 结果足以支持“这一部署选择能显著减少当前向量化等待”的判断，但不应把不同模型、不同缓存状态、不同执行阶段的时间直接换算成同模型加速倍数。

证据：[短文本探针](../metadata/ark-embedding-probe.json)、[五条真实记忆及批量实测](../metadata/ark-embedding-real-memory-benchmark.json)。原始实验向量缓存未被写入。

### 推荐的适配方式

1. 增加单一的方舟文本向量适配器，沿用现有 Plan 环境凭据。请求 `encoding_format=float`，核对返回模型版本、数量、索引和维度，拒绝不完整响应。
2. 用独立缓存保存 2048 维向量。缓存身份包括模型版本、维度、输入指令、规范化策略和完整文本哈希。不能与现有 Qwen 4096 维缓存混用。
3. 显式执行一致的 L2 归一化。本次返回向量范数约为 0.9981–1.0016，不应假定恰好等于 1。
4. 角色间汇聚独立请求做小批次。保留全部输入，不引入人为截断；本次只测了最长约 2058 token 的单条输入，Plan 接口的长输入边界仍需单独验证。不能把其他接口的上下文配置直接当作已实测容量。
5. 更换 embedding 会改变检索空间，属于实验条件变更。需要冻结新的 embedding 身份、重建向量与角色记忆，并对依赖记忆的比较统一评估。现有构建评审和历史记录应保留。

本次成功调用证明现有套餐权限可用。未读取账户账单或 AFP 抵扣记录，不把公开按量单价当作该 Plan 的实际扣费。计费口径见[官方价格说明](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh)及[Agent Plan AFP 说明](https://docs.volcengine.com/docs/ark/agent-plan-personal-afp-credits-billing-rules?lang=zh)。

## CoCo：模型漏标是触发条件，额外宿主校验是暂停根因

出错样本为固定集论文 `f-FQE1fjPK`，实验组为 `seed-llm_summarizer`。暂停时间为 17:35:33。

实际链路：

1. 原版 `build_prompt()` 生成 95 段完整输入，第 95 段包含 POL 评审及最终建议，确实有正文内容。
2. 三个原版请求分别使用 presence_penalty 0、0.5、1。模型均返回 `finish_reason=stop`，输出为 1809、1809、1833 token；没有遇到 32768 的输出上限。原始标注均覆盖 94/95 段，仅缺第 95 段。
3. 原版解析器正确解析已有的 94 段，没有丢失一条实际返回的标注。
4. 作者原版 `_2_split.py` 第 525–532 行把缺失编号加入 `##N/A`，在没有全覆盖候选时选择原版补齐结果。随后转换为从 0 开始的分组。实际落盘的 `opinions` 包含 `[94]`，最终覆盖全部 95 段。
5. 本项目的 `coco_integrity.py` 第 137–138 行额外要求原始模型响应覆盖率必须为 1，否则抛出 `fallback is not admissible`。它在原版已完成补齐后拒绝了该结果，导致整次搜索暂停。

CoCo 源仓库当前提交为 `f9b16fa`，`src/evaluation/_2_split.py` 相对该提交没有改动。诊断没有修改作者代码。

### 复现与证据

用保存的三条模型返回替换网络客户端，执行作者原版 `split_all()`：生成结果与原先保存的 JSONL 完全一致，覆盖 95/95 段。随后调用实际宿主 `audit_split()`，稳定得到同一 ValueError。全过程无需请求模型。

```bash
/data/xyj/envs/coco-original/bin/python metadata/diagnose_coco_split_0930.py --require-host-admission
```

当前退出码为 1：它明确捕获“作者正常输出被宿主拒绝”这一问题，不是新的网络失败。脚本和机器可读证据分别为 [diagnose_coco_split_0930.py](../metadata/diagnose_coco_split_0930.py) 与 [coco-split-diagnosis.json](../metadata/coco-split-diagnosis.json)。

上一轮状态答复只解释了模型漏段，未说明宿主检查比原版更严格；应以本次完整调用链的诊断为准。

### 修复位置与验证要求

修复应恢复作者已有的漏段处理规则：不以原始模型覆盖率不足单独判失败；将它记录为诊断信息。宿主应核对原版处理后的完整句段、有效编号、输出一致性和原始请求回执，记录是否走过原版补齐分支，不能删除整个完整性检查。

必要回归包括：本次 94/95 的原版补齐结果应通过；最终结果确实丢段、编号越界或与原始文本不一致时仍应拒绝；已有 95/95 原始响应的正常路径保持结果一致。

本次已完成原因定位和可重放证据，修复尚未应用。实验仍处于暂停状态，已完成的两组评分、失败回执和零次进化状态均保留。
