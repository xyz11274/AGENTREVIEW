# 流式 API 与流程耗时

2026-09-30 17:10 按用户要求继续实验，两个本机 CoCo 服务使用 GPU 0–3，实验由 systemd 托管。当前选择文件指向 `config/0930-pilot-streaming-resume/protocol.frozen.json`，输出目录为 `artifacts/pilot-0930-streaming-resume/`。原运行的协议、候选、失败记录和完整 CoCo 回执保存在 `artifacts/pilot-0930-local/`。

## 调用与结果

审稿、记忆、Meta、M★ 反思和方舟 Judge 统一使用 Chat Completions SSE。客户端即时读取 `content`、`reasoning_content`、结束原因及 usage，最后组装为原有完整响应。M★ 继续接收完整文本，再由本地生成和验证 Python 程序。

完整输入、深度思考设置和模型输出容量沿用既有协议。生成模型输出预算上限仍为 393,216 tokens；实际预算由完整输入与模型上下文共同计算。官方 CoCo Judge 请求仍使用作者的 32,768 token 预算。

`timeout_seconds=600` 表示 SDK 的连接、读取、写入和连接池等待超时。对流式读取，它约束一次等待网络数据的时间；持续收到数据的请求可以运行超过 600 秒。网络持续静默仍会触发超时，已收到的分片作为诊断证据保留，下一次请求使用独立的记录。完整生成响应通过原有结束原因和模型身份检查后进入成功缓存。

官方 CoCo checkout、Split/Classify 权重、提示、分类、恢复、重试与评分函数维持原样。Judge 只适配 API 流式传输；流中的结束原因和空内容原样交给官方逻辑处理。Split/Classify 继续调用本机原版服务，宿主记录其调用时间。

## 实时监控

| 产物 | 内容 |
|---|---|
| `report/流程耗时报告.md` | 当前阶段、进行中的操作、累计耗时和最近重试 |
| `report/runtime.json` | 同一份机器可读监控数据 |
| `telemetry/worker-events.jsonl` | 各进程、线程的开始、进度、结束和重试事件 |
| 请求目录中的 `attempt-N.progress.json` | 尝试状态、耗时、首包时间、最近分片时间、字符数、预算和实际 usage |
| 请求目录中的 `attempt-N.stream.jsonl` | 已收到的原始分片及时间，用于重放和定位中断 |

请求状态依次体现 `queued`、`waiting_first_chunk`、`streaming` 以及 `complete`、`failed` 或 `interrupted`。首包指首个 SSE 数据分片；正文字符数和思考字符数分别统计，token 用量采用服务端 usage。

分片到达后至多每秒刷新一次进度文件，首次分片和请求结束即时写入。运行器每 20 秒更新心跳和耗时报告，更新依据涵盖请求进度与重试。各阶段含嵌套与并发操作，阶段累计值用于归因，总运行时长以外层阶段为准。

覆盖阶段：启动检查、经验构建、角色记忆构建与检索、Embedding、五角色审稿、Meta 汇总、反思修复、程序验证、候选评价、单篇全流程、CoCo Split/Classify、文本指标、分类维度 Judge、整篇 Judge、API 并发等待和重试间隔、最终独立测试。

## 查看与新版本准备

当前流式恢复实验使用默认入口：

```bash
bash deployment/run_pilot_0930.sh status
bash deployment/run_pilot_0930.sh report
```

流式恢复版本的独立选择文件为 `config/0930-pilot-streaming-resume/profile.json`，协议已冻结，已通过实际服务、凭据、沙箱与源码身份检查：

```bash
bash deployment/run_pilot_0930.sh validate
bash deployment/run_pilot_0930.sh status
journalctl --user -u agentreview-0930-pilot.service -f
```

原实验停在第 4 轮反思请求耗尽三次超时尝试的位置。迁移逐一验证了 13 份完整历史评分回执，保留候选程序、已完成的进化尝试、随机流状态及失败预算。新搜索检查点绑定新协议，结果与源码继续引用原始证据路径。

恢复沿用 SharedEvolution 的正式修复入口：创建 epoch 1，重评 7 个基线组和第 1、3 轮的有效候选，共 9 组；统一提交后继续第 4、5 轮。各组仍使用原有 3 篇固定评分论文，15 篇数据划分与五次进化尝试保持原设置。第 2 轮候选失败记录继续保留。第 4 轮进入新 epoch 的请求目录，旧超时与重试证据保持原样。

重评期间，逐轮报告显示已提交的 epoch 0 配对成绩及 epoch 1 完成组数。统一提交后更新配对成绩；阶段与流式请求实时进度始终记录在 `report/流程耗时报告.md`。迁移证据见 `metadata/streaming-0930/resume/migration.json`，修复证据与冻结协议共同约束本次恢复。

## 验证

真实 DeepSeek 短请求流式验收返回 `STREAM_OK`，耗时约 1.95 秒，保留完整模型输出容量；记录见 `metadata/streaming-0930/live-api.json`，分片与耗时报告见 `artifacts/streaming-0930-acceptance/`。该验收在暂停阶段完成，覆盖实际 API 传输。

通过官方 CoCo 客户端与 Judge 适配器发出的真实流式短请求也返回 `STREAM_OK`，耗时约 2.10 秒，输出预算保持 32,768 tokens；记录见 `metadata/streaming-0930/live-judge.json`，分片与耗时报告见 `artifacts/streaming-0930-judge-acceptance/`。这次验收验证 Judge 的 API 传输，完整 CoCo 评分链路由本机模型和真实 Judge 执行。

本地 HTTP/SSE 测试覆盖持续接收超过读取超时值、中途断流、独立重试、完整结果准入，以及官方 CoCo 对 stop、length 和空内容的原有处理。监控测试覆盖检查点静止时的请求展示、重试、并发阶段计时和中断归因。源码归档、回归与停机证据位于 `metadata/streaming-0930/`。

回归测试通过 318 项测试及 101 项子测试；最终计时、传输和协议绑定调整后，相关 18 项测试全部通过。独立流式协议已通过配置与运行时代码身份校验，数据划分、进化设置和评分配置沿用原实验。汇总证据见 `metadata/streaming-0930/validation.json`。

继续实验前，恢复调度、证据完整性、重评期间的同 epoch 报告和进化恢复等 30 项测试通过，记录见 `metadata/streaming-0930/resume-tests.log`。实际就绪检查为 `ready`，记录见 `metadata/streaming-0930/resume-readiness.json`。
