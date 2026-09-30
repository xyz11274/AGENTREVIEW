# 0930 API 适配记录

当前架构与运行入口见 [ARCHITECTURE_0930.md](ARCHITECTURE_0930.md) 和 [仓库 README](../README.md)。本文保留 2026-09-29 的阶段记录。

当前运行说明以根目录 README 为准。本次改动没有启动模型服务或正式研究实验。

## 模型与原文核对

核对 `/data/xyj/shengao/CoCoReviewBench_2605.07905v2.pdf`：
§4.4 描述训练 Qwen3-8B 用于拆分/分类；§5.1 明确 Judge 使用 GPT-5-Mini，temperature=0。
按照用户要求，保留原本地拆分、分类模型，把 GPT Judge 换为方舟 DeepSeek。
原有 Qwen3.8-27B Judge 是此前工程替代方案，不是原文 Judge，已删除。

| 阶段 | 当前实现 |
|---|---|
| Specialist、Memory、反思、CoCo Native、Meta | 方舟 DeepSeek-V4.1-Flash，thinking disabled，temperature=0 |
| CoCo Judge | 同一 API 模型，thinking enabled，reasoning_effort=high，temperature=0 |
| CoCo Split/Classify | 原作者训练的两个本地 Qwen3-8B 模型，权重与请求行为保留 |
| BLEU/ROUGE/BERTScore、Complete.、聚合 | 原 CoCo 函数、参考数据、分母和 left-out-one 规则 |
| 记忆 embedding | 原 CPU Qwen3-Embedding-8B |

Judge 的 upstream `medium` 显式映射为 Ark `high`；这不是相同模型推理行为的证明。
评分配置、实验身份及结果报告包含 Judge 替换声明，不与原论文分数混为同一设置。
参考 API 文档：[Chat](https://docs.volcengine.com/docs/ark/chat-api?lang=zh)、
[深度思考](https://docs.volcengine.com/docs/ark/deep-thinking?lang=zh)。

## 调用与身份

单一 `ModelConfig` + OpenAI 兼容 SDK，固定默认 Plan model name
`deepseek-v4.1-flash`，校验返回 model 为 `deepseek-v4-1-flash-260910`、base URL `https://ark.cn-beijing.volces.com/api/plan/v3`。
发送 `max_tokens` 和 `extra_body.thinking`，开启推理时再发送 `reasoning_effort`。
结构化记忆/反思请求直接传递 JSON response_format，不探测签名、不静默丢弃字段。
不发送旧 vLLM chat_template_kwargs、top_p 或不支持的惩罚参数。

模型身份包括 endpoint、版本化 model ID、实际解码配置、上下文/输出容量和固定 tokenizer。
密钥名称与传输超时属于连接配置，密钥值仅从环境读取。
新运行根为 `artifacts/study-0930-api`，不复用旧候选、请求或评分缓存。
三份协议、评分配置、study 和运行源码哈希均重新绑定。

三次搜索、8 个实验组、角色职责、分区、Memory-only 变异和先全部冻结再测试的屏障保持。
Meta 的 rendering 清单从过期 v2 同步为代码实际使用的 v3，未改写 Meta 提示词或渲染算法。

## 计数、失败和并发

使用官方固定 revision 的 DeepSeek tokenizer 与消息编码器，只下载 tokenizer/编码源码及 MIT
许可，无模型权重、远程代码自动加载或运行时下载。出处和哈希见 vendor 目录。
全文、记忆条目及拼接消息使用该 tokenizer；不再借用 CoCo Judge 的 Qwen tokenizer，
不再回退到 UTF-8 字节计数。云端实际 usage 作为计费依据，预估标记为 local-estimate。

保留完整输入/输出预算；扩大云模型上下文配置，没有继续强制本地 64K/128K 容量。
输出预算仍按任务显式配置，finish_reason=length 的内容不能进入下游。
1575 篇离线审计无超限，最大 Meta 输入容量估计 105415 tokens。
该审计不代表已运行 1575 篇模型评测。

并发以完整 endpoint 为键跨进程共享；没有固定端口集合、paper_workers=2 或 128 上限的校验。
默认云总并发 8 是可调整的运行配置。没有添加猜测的 RPM/TPM、每日消费、论文数量等限制。
有限请求日志是唯一生成重试所有者；SDK 重试为 0；暂时错误遵守 Retry-After 或指数退避。
无模型降级、异常吞掉后补分、假云密钥或截断后标记成功。
损坏的缓存响应会被拒绝。CoCo 的原始失败占位文本不能成为有效评分。

候选程序仍在原有 sandbox 内运行，env 清空且网络隔离；仅 Judge 子进程收到云 API Key，
Split/Classify 子进程不会收到。保留科学完整性和执行隔离校验，不提供跳过验证的 CLI 开关。

## 清理

删除只服务于旧工作流的 bootstrap、内部 evaluator、role-local evolution/formal transition、
旧 CoCo seal/scoring/runtime、Qwen Judge lease，以及对应历史专用测试。
从旧 evolution/runtime 中提取当前流程仍需的程序校验与本地专用服务身份检查。
删除旧 GPU 轮转、弹性资源、Qwen Judge trial、历史流水线及过期吞吐脚本。
当前 CLI 只保留 shared-experiment 和 experiment-report；独立入口提供 check、api-smoke、run。

测试数量下降来自删除退役工作流及其测试，不能理解为旧版所有失败都已逐项修复。
仍覆盖完整正文、分区隔离、五角色记忆、评分原始函数对照、冻结和恢复边界。
本次验证摘要记录在 `metadata/api-adaptation-validation.json`；原迁移历史证据保持原样。

## 尚需运行条件

已使用用户提供的密钥验证 Plan 端点，两种 thinking 模式与 JSON 输出均成功；密钥仅注入验证进程环境，未写入仓库。后续启动进程仍需设置 ARK_API_KEY。
未启动/重启本地 Split/Classify，也未修改共享 CoCo 源码、权重或 Python 环境。
正式运行前还需现有专用服务可用；运行时会验证源文件、模型权重、环境与本地服务实例身份。

Plan 接口的模型名遵循[官方接入文档](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-opencode?lang=zh)。已实测返回固定版本；若返回版本改变，请建立新的实验身份，不自动接受升级或换模。
