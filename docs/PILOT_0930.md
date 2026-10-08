# 0930 小规模实验运行说明

2026-09-30 17:10 已按用户要求继续实验，本机两个 CoCo 服务与实验由 systemd 托管。流式恢复先在新比较批次中重评 9 个有效组，然后继续第 4、5 轮与独立测试。历史结果及失败记录保留原路径，流式传输、阶段计时和恢复证据见[流式 API 与流程耗时](STREAMING_TIMING_0930.md)。

当前选择文件是 [config/active-pilot.json](../config/active-pilot.json)，指向 `config/0930-pilot-streaming-resume/protocol.frozen.json` 和 `artifacts/pilot-0930-streaming-resume/`。两个 CoCo 模型由本机 systemd 用户服务托管，实验的实时阶段记录在 [pilot-state.json](../artifacts/pilot-0930-streaming-resume/pilot-state.json)。

| 服务 | 本机 GPU | 端点 | 上下文容量 |
|---|---|---|---|
| Qwen3-8B-Split | 0、1，TP=2 | `127.0.0.1:13099/v1` | 65,536 tokens，原版 YaRN 参数 |
| Qwen3-8B-Classify | 2、3，TP=2 | `127.0.0.1:13098/v1` | 32,768 tokens |

两个服务均使用作者原始专用权重，vLLM 版本为 0.27.1。原版 CoCo 的提示、解析、评分与请求参数保持冻结；Judge 按既有授权使用 DeepSeek API。Embedding 沿用 Qwen3-Embedding-8B CPU 与已有缓存。部署身份见 [services.json](../config/0930-pilot-local/services.json)。

## 数据与比较

15 篇论文分别用于 build 4、static 3、rotating 5、sealed_test 3。构建集生成五角色经验，固定集决定候选选择，轮换集提供反思，封存集在系统冻结后生成最终比较。

一次共享搜索包含 Vector Search、LLM Summarizer、Experience Learner 三种初始记忆策略、对应的三个记忆关闭对照以及 CoCo Native 基线，计划五次进化尝试。最终比较包含 Champion。每轮报告展示模型声明、实际源码差异、验证错误、固定集逐篇差值和最优成绩变化。

## 检查

在仓库根目录执行：

```bash
/data/xyj/envs/coco-original/bin/python deployment/pilot_0930.py status
/data/xyj/envs/coco-original/bin/python deployment/pilot_0930.py validate
/data/xyj/envs/coco-original/bin/python deployment/pilot_0930.py report
```

`status` 解析当前选择并核对工作进程身份。`validate` 核对冻结协议、完整数据、实际代码与 CoCo 配置，产生预检记录。`report` 读取已保存结果并更新 Markdown、JSON、CSV。

服务就绪检查通过私有环境文件加载 API Key：

```bash
bash deployment/run_pilot_0930.sh check
```

结果中的 `ready` 表示服务与配置前提齐备；`blocked` 列出待就绪的服务。本次预检核对本机监听进程、实际模型路径、源码与权重哈希、API 凭据和沙箱。独立单篇评分验收记录在 [coco-acceptance.json](../metadata/local-pilot-0930/coco-acceptance.json)，正式实验另行构建记忆并计算成绩。

2026-09-30 单篇真实验收已完成 Split → Classify → Judge 全链路，耗时 246.442 秒，`Paper.` 为 4.0。此分数用于确认本机评分链路；正式五轮进化效果由逐轮报告中的配对成绩给出。部署与运行生命周期测试通过 6 项。

## 执行与停止

显式运行命令是：

```bash
bash deployment/run_pilot_0930.sh run
```

后台运行采用已安装的 [systemd 服务](../deployment/systemd/agentreview-0930-pilot.service)，用户 linger 已启用，SSH 连接结束后进程持续运行。实验终端日志通过 systemd journal 查看：

```bash
journalctl --user -u agentreview-0930-pilot.service -f
systemctl --user stop agentreview-0930-pilot.service
```

中断会保存 stopped 状态和已完成的账本。相同冻结协议在相同目录中恢复；新的代码或数据身份使用新的协议和产物目录。

两个模型的服务名分别是 `agentreview-0930-coco-split.service`、`agentreview-0930-coco-classify.service`。模型日志保存在当前产物目录的 `services/split.log` 和 `services/classify.log`。全部服务健康状态可用 `systemctl --user status agentreview-0930-pilot.service agentreview-0930-coco-split.service agentreview-0930-coco-classify.service` 查看。

## 文件位置

以下路径均相对于 `artifacts/pilot-0930-local/`。直接打开[自进化逐轮报告](../artifacts/pilot-0930-local/report/自进化逐轮报告.md)查看实际代码改动与 CoCo 配对效果。构建和基线阶段完成后进入五轮进化，报告随搜索检查点更新。

| 文件 | 内容 |
|---|---|
| `pilot-state.json` | 阶段、PID、进程开始时间和结束原因 |
| `readiness.json` | 模型服务与配置检查结果 |
| `search/search.json` | 候选池、轮次、父代与评分身份 |
| `search/proposals/aN-eE/` | 完整反馈、原始反思文本、每次源码和验证结果 |
| `search/repairs/eE/.../` | 实际运行错误触发的修复记录 |
| `report/自进化逐轮报告.md` | 可阅读的每轮改动与测试效果 |
| `report/evolution-rounds.json` | 完整配对成绩与证据路径 |
| `final/final-test.json` | Champion 冻结后的独立测试结果 |

指定另一份协议与输出目录时，直接提供 `--protocol` 和 `--output-root`。数据准备入口为 `deployment/pilot_data.py --source-root ... --data-root ...`；其输出是独立分区与溯源清单，随后由实验协议绑定。

实际模块、根因和验证范围见[架构说明](ARCHITECTURE_0930.md)。
