# AgentReview 0930 架构与根因修复

本文描述 2026-09-30 整理后的实际代码。主链由运行器、搜索、具体实验阶段、模型与沙箱、原版评分组成。当前协议面向 15 篇论文、五次进化尝试；本机 CoCo 部署与实时运行入口见[运行说明](PILOT_0930.md)。

## 模块职责

`PilotRun` 持有 `protocol_path` 与 `root` 两个路径，负责运行生命周期。每个实例拥有独立目录，`config/active-pilot.json` 选择当前实例。数据准备、服务配置和实验运行分别位于 `pilot_data.py`、`coco_services.py` 和 `pilot_0930.py`。

`SharedEvolution` 管理一个共享候选池和一条谱系。父代按同一固定评价集的完整 CoCo `Paper.` 成绩抽样；轮换集提供反思反馈。成功执行并完成评分的子代加入候选池，静态成绩决定 Champion。三个独立随机流分别记录父代、轮换样本与详细案例选择。

`APISharedBackend` 将阶段请求落实到 Memory、Review、Meta、CoCo 和 Reflection。`llm.py` 负责方舟调用，`experiment_requests.py` 负责请求身份、缓存、重试与原始响应，`token_accounting.py` 计算完整输入下的模型容量。Embedding 在独立模块中完成向量生成与缓存。

`reviewer_runtime` 执行候选记忆程序。固定角色与 `plan_review` 由宿主验证；候选在 bubblewrap 沙箱中使用列出的工具接口。每个角色分别构建与加载记忆。

`development_coco.py` 和 worker 调用官方 CoCo，并保存完整评分回执。官方源码、训练权重、提示、解析与评分算法沿用既有冻结版本。DeepSeek Judge 替换身份继续写入协议。

## 一轮自进化的数据流

| 阶段 | 输入 | 输出与记录 |
|---|---|---|
| 父代选择 | 候选池、完整固定集 Paper. 成绩、随机流 | 父候选 ID 与随机流位置 |
| 轮换评价 | 父程序、轮换论文、冻结记忆 | 五角色轨迹、Final Review、CoCo 回执 |
| 反思 | 父程序、真实开发反馈、谱系、当前接口说明 | 原始模型文本、修改声明、V4A 差异 |
| 程序应用 | 父 Python 源码与差异文本 | 完整候选源码及相对当前源码的 diff |
| 验证修复 | 候选源码、固定策略、实际沙箱输入 | 编译与写入检索结果、错误反馈、修复响应 |
| 候选评价 | 通过验证的程序、build 经验、固定论文集 | 五份独立记忆、Final Review、CoCo 逐篇成绩 |
| 选择与报告 | 完整评分回执、父子程序、前一最优成绩 | 共享池、Champion、逐轮配对差值 |
| 最终测试 | 搜索完成后的冻结系统、封存论文 | 独立测试成绩与最终比较 |

模型程序变异采用 M★ 的差异文本格式。宿主通过 `extract_program → apply_program_patch → validator` 生成和检查可执行程序。源码提取顺序为最后一个 V4A block，随后采用上游完整 Python block 表示。反思初次生成后最多修复三次；运行错误使用相同验证步骤。

## 已落实的根因修复

### 显式反思实现

旧适配在运行时修改 AST、构造临时模块、替换全局函数，并通过字符串替换修正原始提示。这使实际执行逻辑分散在载入器、上游文件与替换字符串之间。

现在 `mstar_adapter.py` 直接表达请求、提取、验证和修复循环；`mstar_prompts.py` 一次性描述实际宿主接口；`program_patch.py` 负责程序差异应用。provider 与 validator 通过参数传入。代码采用 M★ 上游流程的显式移植，上游 commit、文件哈希、许可和适配范围记录在 `vendor/mstar/manifest.json`。

### 完整顺序应用程序修改

原差异依赖在一次调用内将结果存为按文件名索引的映射。重复声明 `program.py` 时，后一操作覆盖了前一操作的结果。之前的防御规则通过额外模型请求重排格式。

现在每个文件操作接收前一操作的完整输出，在内存中按顺序执行。整组操作成功后才交付结果。历史失败响应现已一次重放成功，新增 schema、指令和读写逻辑全部进入最终源码，真实沙箱验证通过。记录见 [repeated-update-replay/result.json](../metadata/architecture-0930/repeated-update-replay/result.json)。

### 执行验证与模型容量各自负责

原静态切片推断将字符串解析、文本处理与输出截断混为同一种语法，合法的 JSON 围栏解析会触发误报。

当前静态检查负责结构、固定审稿策略与可用能力；隔离沙箱负责实际写入、查询和执行错误；token 预算负责完整请求的上下文容量。Python 切片按其语言语义执行。完整论文、原始模型响应与记忆条目选择继续由宿主保存和校验。

### 运行路径与结束状态

旧启动器通过修改模块全局路径切换部署，中断处理只覆盖普通异常，`KeyboardInterrupt` 后仍落盘为 `running`。

当前 `PilotRun` 显式接收路径并在新目录中建立锁。中断记录为 `stopped`，运行错误记录为 `paused`，服务前提状态记录为 `blocked`。`status` 将 PID 与进程开始时间共同核对，已经退出的进程显示 `interrupted`，原状态记录继续保留。

### 报告关联实际候选

反思耗尽修复时，搜索器需要保留父程序作为谱系依据；旧报告直接把这份占位程序当成本轮改动。

当前反思失败携带轨迹身份，报告读取最后一次实际验证的候选源码，展示 `validation_failed`、每次验证错误、原始响应和实际 diff。评分列来自完整 CoCo 回执，候选生成失败以失败状态展示。

## 当前目录与证据

| 路径 | 用途 |
|---|---|
| `config/active-pilot.json` | 当前协议和输出目录的选择 |
| `config/0930-pilot-local/protocol.frozen.json` | 当前代码、数据、本机模型、评分和轮次身份 |
| `artifacts/pilot-0930-local/` | 当前小规模实验产物与实时状态 |
| `config/current-pilot/protocol.frozen.json` | 架构整理完成时的协议快照 |
| `metadata/local-pilot-0930/` | 本机部署身份与实际 CoCo 验收 |
| `artifacts/architecture-0930-acceptance/` | 独立 API 适配验收 |
| `metadata/architecture-0930/` | 失败复现、回归、源码归档和保留校验 |
| `artifacts/pilot-0930-coco-upstream/` | 上一轮真实评分与失败记录 |

本次整理移除了动态载入的 M★ 执行副本、混合部署专用启动器、旧整套实验启动器、重复 systemd 模板与孤立的 InternalEvaluation 数据类。源码修复前归档覆盖本次涉及的运行文件和配置；现有 CoCo 文件与历史协议继续保留原字节。

当前提示、说明与新增接口文档采用肯定式职责描述。执行错误记录保留原始错误文本，便于重放与定位。

## 验证范围

行为测试覆盖多段差异顺序应用、完整 Python 表示、未知接口修复、三次修复耗尽、正常字符串解析、实际写入检索、运行中断、目录独立、失败候选报告及原有审稿评分契约。

整仓回归通过 **309 项测试和 101 项子测试，耗时 86.81 秒**。一次真实 DeepSeek 请求返回完整 Python 程序，**110.428 秒**完成生成和沙箱验证。该候选还通过五角色分别写入、非空检索及快照恢复验证；这组补充验证使用结构化模型测试响应和确定性测试 embedding。

15 篇完整基础输入全部通过容量审计。整仓回归、真实响应重放、独立 API 验收与来源校验统一记录在 [验收汇总](../metadata/architecture-0930/validation.json)。API 验收覆盖程序生成与执行；自进化效果以后续固定集和独立测试的 CoCo 配对成绩衡量。
