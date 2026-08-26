# 文档索引与新旧判定（2026-08-25）

> 目的：`AgentReview_0811` 路径下 md 文档较多且新旧混杂，本文给出每份文档的分类与可信度判定，作为「读哪份」的入口。
> 判定基准：git HEAD `0f79eaf`（2026-08-25）与代码实测，并反映 2026-08-25 的文档归位移动（`AgentReview/docs/*.md` 六份已并入本目录）。
> **代码架构的现行权威**：[AgentReview_Mstar_代码全览导读_20260825.md](AgentReview_Mstar_代码全览导读_20260825.md)。

## 阅读顺序建议

1. [待阅读md/CONTEXT.md](待阅读md/CONTEXT.md) -- 术语与系统主张（先读；注意其中两处待改，见下表）
2. [AgentReview_Mstar_代码全览导读_20260825.md](AgentReview_Mstar_代码全览导读_20260825.md) -- 代码架构（现行权威）
3. [adr/0005](adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md)、[adr/0006](adr/0006-data-protocol-four-zones.md)、[adr/0007](adr/0007-evolution-semantics-fresh-state-lineage-budget.md) -- 三份现行核心决策
4. [待阅读md/AGENTS.md](待阅读md/AGENTS.md) + [memory_evolution.md](memory_evolution.md) -- 仓库契约与进化规范
5. [LOCAL_QWEN_OPENAI_ALIGNMENT.md](LOCAL_QWEN_OPENAI_ALIGNMENT.md) -- 最新运行指引（本地 qwen3.8 + Qwen3-Embedding 正式 run 必读）
6. [REVIEWER_EVOLUTION_DECOUPLING_20260824.md](REVIEWER_EVOLUTION_DECOUPLING_20260824.md) -- 08-24 解耦后的依赖方向与公共 API

## 分类清单

### A. 仍然权威（以最新版本为准）

| 文档 | 性质 |
|---|---|
| `AgentReview_Mstar_代码全览导读_20260825.md` | 代码架构现行权威（基准 0f79eaf） |
| `待阅读md/CONTEXT.md` | 统一语言表 / 术语权威（🟡 两处待改，见 B 表） |
| `adr/0003` | 路径纪律与 Asset Manifest |
| `adr/0005` | 系统边界唯一权威（supersede 0001/0002 的过时部分） |
| `adr/0006` | 四区数据协议：A/B/C/D = 2391/299/300/899 |
| `adr/0007` | 进化语义（fresh-state、lineage、21 迭代预算） |
| `待阅读md/AGENTS.md` | 仓库地图与不变量契约（不变量逐项核对通过，已含 Qwen3 表述） |
| `AgentReview/REVIEW_Request.md` | 五维度 23 子类 rubric（不可变冻结资产，SHA `609eb930…7be42` 运行时校验，**不得移动/修改**） |
| `memory_evolution.md` | M★ 复现契约（搜索协议/产物布局/数据隔离）（🟡 未反映解耦，见 B 表） |
| `DEPRECATED.md` | 已废弃代码注册表（活文档）（🟡 需补解耦条目，见 B 表） |
| `LOCAL_QWEN_OPENAI_ALIGNMENT.md` | 本地 qwen3.8-27b-fp8 双 replica + Qwen3-Embedding 正式运行手册（逐项核对通过） |
| `REVIEWER_EVOLUTION_DECOUPLING_20260824.md` | Reviewer Core 解耦设计（与 HEAD 逐项核对全部通过） |
| `effectiveness_screening.md` | 冻结三臂筛选预注册手册（模块/参数/judge 配置逐项通过） |
| `AgentReview/evolution_viewer/README.md` | 进化查看器使用说明 |
| `待阅读md/GROUP_MEETING_REPORT.md` | 历史归档（自带横幅，定位正确） |
| `adr/0001`、`adr/0002` | 已被 0005 部分取代（文档内已声明），决策沿革 |

### B. 基本权威、局部滞后（仍可用，读时注意）

| 文档 | 滞后点 |
|---|---|
| `待阅读md/CONTEXT.md` | ①「Human Reference Boundary 已知违反待修复」应改**已修复（2026-08-17）**，仅旧 run 产物保留历史分析身份；②「Evolution Validation 300 篇」应为 **299**（与同文 Source Pool 条目 2391/299/300/899 自相矛盾） |
| `待阅读md/AGENTS.md` | M★ source 一节仍把 sandbox/task_agent 等指向 memory_evolution（现为 shim），真实实现在 `agentreview/reviewer_runtime/` |
| `memory_evolution.md` | 未反映 08-24 解耦：`candidate_reviews.jsonl`、`agentreview.cli review`、`agentreview.coco_eval`、MSTAR->Reviewer Core 依赖方向均未提及；12705 行缓存现为 gitignored 本地资产 |
| `DEPRECATED.md` | 需补：①七模块已迁 `agentreview/reviewer_runtime/`（旧位 3 行 shim，非死代码）；②`--memory_mode` 默认已定 `none`（`arguments.py:178`，待决已闭环）；③ embedding 主路线 BGE-M3->Qwen3-Embedding-8B 的语境 |
| `TODO.md` | 多项已完成未回填：S2 v2 池（3889 行）、split v2 冻结、canonical bundles（`data/evolution_coco_v2_20260819`）；`runs/formal_qual_seed0_20260825` 进行中未登记；qwen3/缓存修复/解耦未登记 |
| `RUN_AUDIT_20260818.md` | 已修：B1、D-2、D-3（加 lineage 预算）、F-1、F-3（强化）、QUAL_test.sh 硬编码 key；未修：H1、M1、M3-M5、L1-L6、F-2、F-4、S-1、S-2、D-1；B2 机制仍在但语境过时。§2.1 运行指令基于旧 run.sh，仅作历史记录 |
| `adr/0004` | 「D manifest 900 篇」应为 899（split v2 manifest 实测） |
| `ENVIRONMENT_AUDIT.md` | 核心结论仍有效（requirements-runtime.txt 未变）；行号/文件位置漂移（sandbox 实现在 reviewer_runtime；.py 计数 245->291；缺 reviewer_runtime 新包） |
| `AgentReview/README.md` | 命令/参数全部有效；缺解耦后的新入口（`agentreview.cli review` / `coco_eval` / `candidate_reviews.jsonl`）；引用的 cache 文件已 gitignored |
| `待阅读md/CODEBASE_ONBOARDING_ANALYSIS.md` | 三份长分析中基准最新（2026-08-24 / `cc1007f`），值得保留；但写于解耦前（0 次提及 reviewer_runtime），行数/位置描述现指 shim；移动后相对路径失效 |
| `20260820_ppt/PPT_PROJECT_CONTEXT.md` | 08-20 PPT 配套快照：三处路径死链（CONTEXT.md / 全景解析 / CLOUD_FORMAL 均已移动）；BGE-M3、kimi-k3、formal_ready=false、D-3 等表述已被 08-22~25 进展超越；与 pptx 配套保留 |

### C. 已过时 / 被取代（勿作现行依据）

| 文档 | 说明 |
|---|---|
| `AgentReview M★ 代码架构全景解析.md`（顶层 245 行） | ⚠️ 0f79eaf 复活的 **0811-0812 旧版**（内容 = 98bb5ce），正是 08-20 版开头明令「请勿引用」的那份旧文，且无任何版本标注、通篇 vscode-webview 死链。**建议删除或移入待阅读区加横幅**。现行权威见 0825 导读 |
| `AGENTREVIEW_架构解读.md`（顶层 179 行） | 08-12 快照的**无横幅裸拷贝**（待阅读md 里的同内容版本带「已被取代」横幅）；`--memory_mode` 默认值等主张过时，链接全死。建议删除或补横幅 |
| `AgentReview0811架构.md`（141 行） | 0811-0812 时代分析：行数/子命令数（7 vs 实际 9）/三臂口径（`seed_experience` 臂名已禁用）/judge 模型（gpt-5-mini）全滞后，链接指向 0722。建议归档 |
| `待阅读md/AgentReview M★ 代码架构全景解析.md`（293 行） | 08-20 版（基准 7256ffc），沿革参考中相对最可用；滞后：kimi->qwen3.8-27b-fp8、BGE-M3->Qwen3、CLI 2135->2200 行、七模块成 shim、缺 reviewer_runtime 等新模块、B1 与硬编码 key 已修。已被 0825 导读取代 |
| `待阅读md/AGENTREVIEW_架构解读.md`（181 行） | 同顶层版内容但带取代横幅，定位正确，仅沿革参考 |
| `CLOUD_FORMAL_ALIGNMENT_20260820.md` | ❌ 作为操作手册已过时，被 `LOCAL_QWEN_OPENAI_ALIGNMENT.md` 取代（运行时已从 Kimi+BGE 切到本地 qwen3.8+Qwen3）；其第 7/8/9 节命令会被当前 formal 门拒绝。**仍有效部分**：冻结资产哈希实测全部匹配、D-3 无限 retry 风险提示、CoCo 严格 commit 检查说明 |

### 已废弃路线（文档层面，勿再引用）

- Frozen Library Route：`frozen_memory_library.jsonl` -> 二次加工（违反 Self-Generated Experience，见 ADR-0005 / DEPRECATED.md）
- `seed_experience` 臂名（CONTEXT.md 禁用；现用三条 lineage 名）
- `coco_temporal_split_v1` 与「venue-year 全量 benchmark_reserved」规则（ADR-0006 禁止进入新 formal run）
- `runs/pilot_full_iter5_202607300117` 产物（违反 Human Reference Boundary，仅历史分析用）
- BGE-M3 作为正式 embedding（82c8c60 起正式轨道为 Qwen3-Embedding-8B；`BgeM3Embedding` 代码保留）

## 数据类 md（不属于文档，盘点时排除）

`AgentReview/test_data/`、`AgentReview/papers_eval20/`、`AgentReview/data/**/paper.md` 下的 `md_content*.md` / `paper.md` 均为论文语料/实验输入，不是项目文档；`docs/整理/`（qual_prompt、拼接后的review）为实验材料，同样不是说明文档。

## 2026-08-25 文档归位记录

- `AgentReview/docs/` 六份（CLOUD_FORMAL_ALIGNMENT、effectiveness_screening、ENVIRONMENT_AUDIT、LOCAL_QWEN_OPENAI_ALIGNMENT、memory_evolution、REVIEWER_EVOLUTION_DECOUPLING）已 `git mv` 至本目录；`AgentReview/README.md` 相关链接已同步修复。
- 原仓库根 `CONTEXT.md`、`AgentReview/AGENTS.md` 等已于 0f79eaf 移入 `docs/待阅读md/`（本文路径已按新位置书写）。
- 不移动项：`AgentReview/REVIEW_Request.md`（SHA 运行时校验）、`AgentReview/README.md`（仓库入口）、`AgentReview/evolution_viewer/README.md`（组件自带）、`AgentReview/configs/configs_README.md`（0 字节占位）。
