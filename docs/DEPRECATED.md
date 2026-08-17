# DEPRECATED 注册表

已废弃或遗产代码的登记处。现阶段以**标记和隔离为主，不删除源码**；删除时按本表执行。登记时间：2026-08-17。死亡证据均为"从 `run_single_paper_review_cli.py` / `memory_evolution` / `effectiveness_screening` 出发无存活引用链"。

## 一、确定死亡（标记，待删除）

### 旧多阶段审稿体系（已被单论文 5-specialist 管线取代）

| 路径 | 说明 |
|---|---|
| `AgentReview/agentreview/paper_review_arena.py` | 旧 Arena（含 AreaChair、authors），零引用 |
| `AgentReview/agentreview/arena.py` | Arena 基类，仅被死代码引用 |
| `AgentReview/agentreview/database.py` | Arena 结果数据库层，零引用 |
| `AgentReview/agentreview/ui/cli.py` | 交互式 TUI，仅被 `arena.py` 引用 |
| `AgentReview/agentreview/environments/paper_review.py` | 完整多阶段环境（含作者 rebuttal、AC） |
| `AgentReview/agentreview/environments/paper_decision.py` | AC 决定环境 |
| `AgentReview/agentreview/environments/single_paper_review.py` | 被绕过的单论文环境原型（经裁决：标 deprecated） |
| `AgentReview/agentreview/environments/conversation.py`、`base.py` | 环境基类，仅被上述死环境引用 |
| `AgentReview/agentreview/paper_review_message.py` | `PaperReviewMessagePool`，仅被死环境引用 |

### PDF 审稿路径与旧实验框架

| 路径 | 说明 |
|---|---|
| `AgentReview/agentreview/paper_processor.py` | PDF 提取（pdfminer），已被 markdown 预处理取代 |
| `AgentReview/agentreview/experiment_config.py` | 旧实验配置，零引用 |
| `AgentReview/agentreview/paper_review_settings.py` | 旧实验设置注册表，零引用 |
| `AgentReview/agentreview/utility/experiment_utils.py` | 旧实验运行工具 |
| `AgentReview/agentreview/utility/text_utils.py`、`metrics_utils.py`、`data_utils.py` | 零引用工具模块 |
| `AgentReview/agentreview/dataset/process_submissions.py`、`download_openreview_paper.py` | 旧 OpenReview 下载/处理脚本（新协议从 `/data0/xyj/shengao/OpenReview` 生成） |

### openscholar_bridge 死亡部分

| 路径 | 说明 |
|---|---|
| `openscholar_bridge/openscholar_api.py`、`config.py`、`example_usage.py`、`src/open_scholar.py`、`src/instructions.py` | 零引用；存活路径是 `utility/s2_retrieval.py` → `openscholar_bridge/src/use_search_apis.py`（经裁决：暂不迁移，原地保留活文件） |

### 未使用的 backends

| 路径 | 说明 |
|---|---|
| `backends/human.py`、`hf_transformers.py`、`anthropic.py`、`cohere.py` | 已注册但无任何存活 role 配置使用 |
| `backends/bard.py`、`langchain.py` | 连注册都没有，完全孤儿 |
| ~~`backends/dummy.py`~~ | **经裁决：保留**，作测试/调试用 backend |

### 部分死亡的存活文件（只标记，不做 partial deletion — 经裁决）

| 路径 | 死亡部分 |
|---|---|
| `AgentReview/agentreview/paper_review_player.py` | `AreaChair`、`PaperExtractorPlayer` 类（`Reviewer` 存活；整文件保留） |
| `AgentReview/agentreview/role_descriptions.py` | 除 `get_paper_extractor_config` 外的 AC/author 相关函数 |
| `AgentReview/agentreview/utility/utils.py` | `format_metareviews`、`get_rebuttal_dir`、`load_llm_ac_decisions`、`print_colored` |
| `AgentReview/agentreview/config.py` | `ArenaConfig`、`EnvironmentConfig` |

### 工作区顶层（项目外，待归档 `attic/`）

- `/data0/xyj/shengao/AgentReview*.zip`（21 个归档 zip）、`annot_md.zip`
- 散置脚本：`test.py`、`_init_.py`、`generate_interview_ppt.py`、`AgentReview_0811/test_s2_apikey.py`
- 调试转储：`dialogue_two_27b.*`、`vllm_remote_dialogue_result.*`、`evolution_finish.log`

## 二、Legacy 但仍可达（标记，不删）

| 路径 | 说明 |
|---|---|
| `--memory_mode=existing`（`arguments.py`，`memory_library.retrieve_memory_context`） | Legacy Memory，见 CONTEXT.md。正式实验必须显式指定 memory mode；默认值去留另行决定 |
| Frozen Library 路线：`effectiveness_screening/memory_cache.py` 的 `freeze_memory_library` / 预编译 cache、`history_generation_no_memory/`（仅 1/20 篇产物）、`agentreview_data.py::build_reviewer_memory_episode_text` 的预消化 episode 模式 | 经裁决（2026-08-17，ADR-0005）：违反 Self-Generated Experience 原则，不再补完剩余 19 篇；被统一 rich paper interface 取代 |

## 三、经裁决保留（曾疑似废弃）

| 路径 | 裁决 |
|---|---|
| `AgentReview/viewer.py`、`AgentReview/evolution_viewer/` | 保留，人工分析工具 |
| `AgentReview/agentreview/backends/dummy.py` | 保留，测试用 |
| `openscholar_bridge/src/use_search_apis.py`、`utils.py` | 存活的 S2 检索路径，原地保留 |
| `AgentReview/runs/` | 数据非代码。注意：`pilot_full_iter5_202607300117` 因 candidate-visible episode 含真人参照（违反 Human Reference Boundary，ADR-0005），仅作历史 evolution 分析用，不作为 effectiveness 结论的权威 Program；历史 run artifact 不原地修改，路径解析走 Asset Manifest 的 legacy resolver（ADR-0003） |
