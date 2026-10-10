# 0930 运行故障修复与真实验收

本轮保留旧 pilot 的协议、回执、候选、失败预算、随机状态与人工报告。当前选择文件仍指向旧 pilot；新运行使用显式配置路径。修改前快照和保护文件哈希位于 `metadata/recovery-20261008/before.json`，原有未提交代码另存于 `before/`。

## 已确认的问题与修复

| 问题 | 证据 | 修复 |
|---|---|---|
| Judge 旧 worker 作为脚本启动，包内相对导入失败 | 历史真实任务为 16 个、每个 3 次本地 ImportError；不能把日志重复行当调用次数 | 保留已有模块入口修复；生产使用固定 Python、cwd、PYTHONPATH，保存 worker 身份 |
| 候选持久化契约漏检 | 原候选在五角色快照均出现 `frozenset` 及具体字段路径，见 `original-frozenset-rejection.json` | 五角色各自工具包、多次和重复写入、逐次快照、独立新进程恢复、检索及计划一致性验证 |
| 补丁错误为空，首次运行时修复格式错误立即终止 | 回归用例先复现失败 | 补丁错误包含操作序号、文件、异常类型和上下文；反馈使用最新错误 |
| 自动报告覆盖人工修改 | 无标记历史报告回归用例 | 人工和历史内容保留；仅原子替换唯一有序标记内的自动区块；异常标记拒绝覆盖 |
| 正式配置登记的启动器缺失 | `config/0930/study.frozen.json` 指向不存在的 `deployment/three_search_study.py` | 补齐三搜索运行器；三个搜索全部冻结后才允许打开测试集 |
| 旧冻结实现与当前有效修复代码不同 | 旧 `PilotRun.validate()` 拒绝实现身份 | 新配置、新产物、新实验身份；不手工改旧哈希，不继承旧分数和预算 |

M★ 宿主协议升级为 `mstar-program-evolution-v3`：首次解析失败也进入原有有限修复循环，上限仍为初始响应加三次修复。完整 Python 围栏替换原本已存在，保持该格式。原 CoCo 源码、提示、解析、恢复、回退和指标公式保持原样。

生成侧保留逻辑请求最多三次尝试、SDK 重试为零。CoCo 保留官方三次应用层尝试和 SDK 默认重试；HTTP 观察器记录每次实际发送，避免把一层尝试数误报成真实请求数。流式残片分尝试落盘，缺少终止状态或截断的生成不会进入候选和完整评审。首块、首段有效内容、结束原因、响应 ID、输入输出规模和异常原因链均有记录；历史非流式超时缺少这些细分证据，不能断言排队或首块等待是当时根因。

历史时间线见 `historical-service-journal.log`：9 月 30 日 14:42 主运行器失败，14:43 才停止评分服务；恢复运行在 17:32 再次停止推进，两个服务到 10 月 7 日才退出。当前服务状态不能反推历史业务根因。

## 真实验收

协议：`config/0930-runtime-20261008/acceptance.protocol.frozen.json`。独立输出：`artifacts/evolution-acceptance-20261008/`。

按原顺序各选一篇已暴露的 build/static/rotating 论文，保持正文和原版人工参考不变；七个固定基线与无记忆/原生对照均执行，一次真实反思生成候选，候选完成整个声明的固定评价集合后才入池。五角色中间评审没有单独 CoCo 评分。验收不读取 sealed test，不用于论证提分。

预算：7200 秒，云端 HTTP 尝试最多 250，本地最多 1500。SDK 内部重试也计入发送预算；预算文件不清空。系统服务还有整个进程的两小时上限。

在候选完整评分后、控制器保存结果前注入一次中断，重新创建后端及搜索实例读取检查点。验证模型发送次数不增加、修复及失败预算不重复消费、候选只入池一次、Champion 按未舍入成绩更新，随后完整状态再次重新读取。完整身份链、最终评审、评分回执与检查点记录在 `acceptance.json`；运行途中该文件为 `running` 不代表通过。

```bash
/data/xyj/envs/coco-original/bin/python -B deployment/runtime_0930_progress.py --root artifacts/evolution-acceptance-20261008
tail -f artifacts/evolution-acceptance-20261008/acceptance.log
systemctl --user status agentreview-0930-acceptance-20261008.service
```

## 正式实验

验收通过后，`prepare_runtime_0930.py formal` 才能冻结新正式协议。继承已确认的三个种子 0/1000/2000、每次 20 次进化尝试、125/25/125/1300 分区、原模型与评分配置、父代温度与有限失败停止规则。正式过程重新构建记忆，不继承旧 pilot 的候选、分数或种群。测试分区保留同论文不同版本和历史暴露标注，结果属于既有 benchmark 配对比较，不宣称严格未见测试。

本机 GPU 0–3 已被其他用户任务使用。新 Split/Classify 分别使用空闲 GPU 4–5/6–7，TP 仍为 2，权重和上下文容量不变。服务身份和脱敏环境记录见 `service-identities.json`。验收使用完整生产评分路径；本轮没有新增跨身份“只重跑 Judge”复用机制。

当前三篇旧固定集及两个完成候选仅支持“尚未观察到提升”。`Correct.`/`Thoro.` 是类别层指标，`Paper.` 来自独立论文层 Judge，不能将类别层变化直接解释为论文层分数变化的原因。

## 10 月 9 日服务重启和验收恢复

用户关闭的 Split/Classify 已在 GPU 4–5/6–7 上重新启动，端口仍为 13099/13098。已核对服务进程的解释器、工作目录、GPU 环境、TP 参数、实际权重目录和 `/v1/models`，记录在 `metadata/recovery-20261009/restarted-services.json`。GPU 0–3 的其他任务保持运行。

10 月 8 日验收在服务关闭之前已因参考集合不闭合暂停：准备脚本将参考 `paper_ids` 筛成一篇，但 benchmark、human 和 reviewer selection 资产仍含原三篇。真实保存的最终评审复现了此错误，新增回归先失败后通过，相关验证为 `27 passed`。修复准备脚本，使新目录中的三种参考资产同步筛选，保持原 JSONL 行、评审分配和原版 CoCo 的闭合校验。

恢复身份为 `0930-runtime-acceptance-recovery-20261009`，协议在 `config/0930-runtime-20261008/acceptance.recovery-20261009.frozen.json`，产物在 `artifacts/evolution-acceptance-20261009`。这是旧失败验收的有证据迁移；控制器使用 epoch 1 统一评价七个固定对照，并保留原 RNG、候选身份、尝试数、修复和失败预算。累计调用继承已消费的 16 次云端发送，限额仍为云端 250/本地 1500；本次显式继续操作开启新的 7200 秒运行窗口，旧时限和来源留档。未继承任何有效成绩。失败验收的 160 个历史文件和旧 pilot 另有哈希保护，原文件不覆盖。

```bash
/data/xyj/envs/coco-original/bin/python -B deployment/runtime_0930_progress.py --root artifacts/evolution-acceptance-20261009
tail -f artifacts/evolution-acceptance-20261009/acceptance.log
systemctl --user status agentreview-0930-acceptance-20261009.service
```

该任务完成真实反思候选、完整评价和评分后中断恢复验证，才会生成 `acceptance.json` 的 `status=complete`。正式实验仍受这个实际代码身份的验收回执约束。

新目录报告程序需要根目录中的协议原文。恢复准备阶段补齐此副本，并修正了此前将控制器封装格式写成报告协议的接口不匹配；当前真实产物已验证报告可生成。正式准备也为每个种子生成相同格式的完整协议副本。控制器在 `search/protocol.json` 使用的封装格式保持原样，旧失败验收根目录的历史协议不覆盖。

原 `frozenset` 失败候选的持久化补充证据在 `metadata/recovery-20261009/original-candidate-lifecycle/evidence.json`：原文件仍在五角色快照中被准确拒绝；另存诊断副本仅将实例签名保存为排序列表，比较时临时重建 `frozenset`，Jaccard 策略和检索上限不变。五角色分别用独立进程写入两条内容不同的经验及重复经验，再启动新进程恢复，验证快照相同、两条经验各自可检索、检索结果相同、恢复后重复写入不改变状态。该本地类型契约验证使用确定性工具包嵌入，无模型调用、CoCo 分数或候选入池，不代表真实闭环验收。

## 真实验收完成和正式实验启动

恢复验收 `status=complete`，用时 3689.69 秒（约 61.5 分钟）。七个固定对照、完整旋转反馈、真实 M★ 反思、五角色恢复和候选固定集合评价全部完成。反思初始响应及三次有限修复分别保存，最后使用原本支持的完整 Python 表示，通过静态和生命周期校验。新候选程序哈希为 `8053379d3543eed5f5ab28ca76228e2752746ae7333086386c7be45b75370117`，区别于父代；程序、角色快照、实际提示经验、五个新评审、最终汇总、CoCo 完整回执与控制器分数的链路均通过校验。

候选 `candidate-0001` 的 `Paper.=3.8`，按既定规则入池一次；Champion 为 `seed-llm_summarizer`，`Paper.=4.4`，按严格大于的规则保持。验收不要求提分，这个暴露样本结果不用于推断进化有效性。评分成功后、检查点结果及入池推进前的真实中断已恢复，再次完整读取和重放没有额外 HTTP 发送、重复入池、预算重复消费或 RNG 决策重抽。累计云端 189 次、本地 752 次发送，含此前失败验收的 16 次云端发送，未超过 250/1500 与 7200 秒的预算。

| 证据 | 路径（仓库相对路径） |
|---|---|
| 完整验收及五角色对应关系 | `artifacts/evolution-acceptance-20261009/acceptance.json` |
| 模型生成候选和全部反思修复记录 | `artifacts/evolution-acceptance-20261009/search/proposals/a1-e1/program.py`、同目录 `mstar-trace.json` |
| 最终完整评审 | `artifacts/evolution-acceptance-20261009/search/assessments/e1/candidate-0001/r0/final_reviews/candidate-0001/f-FQE1fjPK.json` |
| 完整评分回执 | `artifacts/evolution-acceptance-20261009/search/assessments/e1/candidate-0001/r0/coco/assessment.json` |
| 检查点及评分后中断证据 | `artifacts/evolution-acceptance-20261009/search/search.json`、`artifacts/evolution-acceptance-20261009/interruption.json` |
| 旧历史保留验证 | `metadata/recovery-20261009/final-preservation.json`：旧 pilot/官方源码等 2169 个文件、失败验收 160 个文件，变更数均为 0 |

正式协议已冻结至 `config/0930-runtime-20261008/study.frozen.json`，对应实际运行身份 `agentreview-0930-study-20261009`。产物使用新目录 `artifacts/study-0930-runtime-20261009`，日志为同目录 `study.log`。保留原三种子 0/1000/2000、每种子 20 次尝试、125/25/125/1300 分区、原模型/评分配置和停止条件；三个搜索全部冻结前禁止测试评价，不继承旧 pilot 或本次验收的候选、种群、成绩、失败预算和随机状态。实际执行代码的 67 个文件已复制并校验，记录在正式目录 `code-freeze.json` 和 `frozen-code/`；未复制凭据。当前种子 0 的构建评审已有实际请求完成并落盘，正式运行处于推进状态，尚未完成整个实验。

```bash
/data/xyj/envs/coco-original/bin/python -B deployment/runtime_0930_progress.py --root artifacts/study-0930-runtime-20261009
tail -f artifacts/study-0930-runtime-20261009/study.log
systemctl --user status agentreview-0930-study-20261009.service
```

本轮关键代码文件（包含保留并验收的已有修改）：

| 范围 | 文件和行为 |
|---|---|
| CoCo 生产启动、回执及运行身份 | `AgentReview/agentreview/research_v2/development_coco.py`、`development_coco_worker.py`、`coco_integrity.py`：固定模块入口、生产解释器路径、阶段身份和失败分类；官方源码不修改 |
| 候选持久化与加载 | `research_v2/program_validation.py`、`reviewer_runtime/sandbox.py`、`sandbox_worker.py`：多次写入、五角色独立工具包、新进程恢复及检索一致性、实际加载哈希 |
| 补丁和有限修复 | `research_v2/program_patch.py`、`mstar_adapter.py`、`mstar_prompts.py`：操作/异常上下文、当前源码、首次格式失败进入有限修复、v3 协议 |
| 调用观察和预算 | `research_v2/llm.py`、`execution_telemetry.py`、新 `transport_observer.py`：SDK 实际发送、阶段时间、流式终止状态、部分输出和预算；保留 CoCo SDK 默认重试 |
| 报告和恢复展示 | `deployment/evolution_round_report.py`、`runtime_report.py`、`pilot_0930.py`：保护人工内容、正确展示 epoch/完成回合/冻结 winner；新 `runtime_0930_progress.py` 展示控制器恢复阶段 |
| 验收、准备和正式运行 | 新 `deployment/prepare_runtime_0930.py`、`run_runtime_0930.sh`、`validate_evolution_runtime.py`、`three_search_study.py`：闭合参考资产、证据迁移、继承累计预算、真实中断恢复、报告协议副本与三搜索冻结门控 |

相关核心测试为 `343 passed, 101 subtests passed`；后续传输检查为 `10 passed`，本次参考集合/评分/运行控制回归为 `27 passed`，均有独立日志。本机实时队列也保存了 Classify 的 8 个执行、8 个服务端等待，以及宿主 16 个已发送请求与发送前等待，证明本次配置和并行参数一致。历史非流式超时的连接/排队/首块/读取细分仍缺原始证据，不据本次成功运行倒推历史根因。
