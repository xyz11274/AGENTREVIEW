# AgentReview M★ 代码全览导读（2026-08-25）

> **基准**：git HEAD `0f79eaf`（202608251713_重组织md并忽略embedding缓存）。本文以代码为准，供人工通读代码使用；行号均以该 HEAD 为准。
> 术语定义见 [整理/术语定义.md](整理/术语定义.md)；系统边界见 [adr/0005](adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md)；数据协议见 [adr/0006](adr/0006-data-protocol-four-zones.md)；进化语义见 [adr/0007](adr/0007-evolution-semantics-fresh-state-lineage-budget.md)。
> 注意：本文写作时点晚于 `docs/待阅读md/AgentReview M★ 代码架构全景解析.md`（0820 版，基准 7256ffc）。此后代码发生三大变化：**Qwen3-Embedding-8B 替代 BGE-M3（08-22）、embedding 缓存修复（08-23）、reviewer_runtime 解耦重构（08-24）**。凡与 0820 版全景解析冲突处，以本文为准。

---

## 0. 一句话定位与总体拓扑

**五专科审稿（QUAL/CLAR/SIGN/ORIG/POL）+ META 汇总**的单论文评审系统。核心研究主张 **M★（M-star）记忆自进化**：审稿 rubric（`REVIEW_Request.md`，SHA-256 冻结）、角色拓扑（5+1）、Task Agent、评估器全部冻结，每个角色离线进化出一个**可执行 MemoryProgram**（Python 文件 = Schema dataclass + write/read Logic + 4 条 Instructions）。

```
┌─ 离线平面（进化）────────────────────────────────────────────┐
│ prepare-specialists → evolve-specialists (5任务×3种子lineage) │
│   → cache-meta-inputs → prepare-meta → evolve-meta            │
│   → final-evaluate-suite（D 区封存测试，只跑一次）             │
│ 产物：runs/<suite>/{QUAL,CLAR,SIGN,ORIG,POL,META}/best_program.py │
└──────────────────────── 冻结晋升 ─────────────────────────────┘
                            │
┌─ 在线平面（审稿，不改代码）───────────────────────────────────┐
│ 论文 md → MarkdownPaperExtractor → 5 专科并行评审（M★记忆注入）│
│   → META 汇总 → reviews.json / candidate_reviews.jsonl         │
│   → memory_library.jsonl 回写（成为后续论文的 A 区历史）        │
└───────────────────────────────────────────────────────────────┘
```

**08-24 解耦后的依赖方向**（唯一合法方向，见 [REVIEWER_EVOLUTION_DECOUPLING_20260824.md](REVIEWER_EVOLUTION_DECOUPLING_20260824.md)）：

```
memory_evolution（进化器：搜索/变异/选择/lineage/fitness/checkpoint）
        │ 调用
        ▼
agentreview.reviewer_runtime（稳定 Reviewer Core：Sandbox/TaskAgent/Toolkit/协议）
        │ 产出
        ▼
candidate_reviews.jsonl（agentreview-candidate-review-v1 统一观测契约）
        │ 交给
        ▼
agentreview.coco_eval / _coco_official_runner（CoCo 官方评测，完全独立）
```

`reviewer_runtime` 不 import 任何 `memory_evolution` 模块，也不含任何 zone（A/B/C/D）概念；反向依赖有测试兜底（`tests/test_reviewer_runtime_decoupling.py`）。

---

## 1. 推荐通读顺序

按依赖从上到下、从活代码到死代码：

1. **契约层**：`agentreview/single_paper_review_roles.py`（角色与 rubric 冻结）→ `agentreview/reviewer_runtime/program_protocol.py`（37 行，MemoryProgram 契约）→ `agentreview/reviewer_runtime/artifacts.py`（观测契约）
2. **稳定运行时**：`agentreview/reviewer_runtime/` 全包（engine → task_agent → sandbox/sandbox_worker → toolkit → program_loader → requests/episodes）
3. **在线管线**：`run_single_paper_review_cli.py`（2200 行主入口，按第 4 节的调用链走）
4. **进化闭环**：`memory_evolution/cli.py`（编排）→ `search.py`（核心控制器）→ `reflector.py` → `evaluator.py` → `metrics.py` → `quality.py` → `agentreview_data.py`（数据准备与防泄漏）→ `subset_selection.py` → `embedding_adapter.py`
5. **协议与门**：`memory_evolution/assets.py`（四区隔离）→ `formal_protocol.py`（formal 前置门）→ `meta_cache.py`（META 防泄漏）
6. **外层**：`effectiveness_screening/`（C 区实验组织）→ `agentreview/coco_eval.py`（D 区官方评测）
7. **辅助**：`scripts/`（数据构建）→ `viewer.py` / `evolution_viewer/` → `tests/`
8. 死代码（第 9 节清单）最后扫一眼即可，**读代码时可直接跳过**。

---

## 2. 仓库布局

仓库根 `/data0/xyj/shengao/AgentReview_0811`，代码主目录 `AgentReview/`（下文简写 `AR/`）：

| 路径                                         | 角色                                                                                     |
| -------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `AR/run_single_paper_review_cli.py`        | **在线审稿主入口**（2200 行，注意：旧文档写 ~1970 行已过时）                       |
| `AR/agentreview/`                          | 核心运行时框架 + 审稿角色 +**reviewer_runtime 稳定核**                             |
| `AR/memory_evolution/`                     | M★ 离线进化引擎（搜索、变异、评估、数据准备）                                           |
| `AR/effectiveness_screening/`              | C 区有效性筛选实验组织层                                                                 |
| `AR/runs/`                                 | 进化 run 产物（pool/scores/lineage/traces/best_program）                                 |
| `AR/data/`                                 | 进化输入数据（pilot 与 formal：`evolution_coco_v2_20260819`）                          |
| `AR/configs/`                              | 冻结资产清单、temporal split、S2 池、实验配置（见第 7 节）                               |
| `AR/cache/`                                | Qwen3 embedding SQLite 缓存（runtime 缓存已 gitignore）                                  |
| `AR/scripts/`                              | 数据构建与预热脚本                                                                       |
| `AR/viewer.py` + `AR/evolution_viewer/`  | 两个只读 Gradio 查看器（7861/7862 端口）                                                 |
| `AR/REVIEW_Request.md`                     | **唯一 rubric 来源**：5 维度 23 子类，运行时 SHA-256 校验，**不可移动/修改** |
| `AR/test_data/`、`AR/papers_eval20/`     | 冒烟输入 / 20 篇评估集                                                                   |
| 仓库根`outputs_baseline/`、`outputs_md/` | 历史对照实验评审产物（`single_paper_review/`）                                         |
| 仓库根`outputs_mstar/`                     | 两个空 run 目录（历史占位，无实际数据）                                                  |
| `AR/3072,`                                 | ⚠️ 0f79eaf 误提交的垃圾文件（内容 "Please ask your administrator."），可删             |

---

## 3. `agentreview/` 包：框架层 + 稳定 Reviewer Core

### 3.1 活代码速览

| 模块                                            | 职责                                                                                                                                                                                                                                                                   | 关键位置                                        |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `agent.py`                                    | `Agent`/`Player` 基类，`Player.act()` 经 backend 生成回复                                                                                                                                                                                                        | `agentreview/agent.py:43-131`                 |
| `arguments.py`                                | 全部 CLI 参数；`--memory_mode` 默认 **"none"**（可 none/existing/mstar_program，:178-182；旧文档写默认 existing，已过时）                                                                                                                                      | `agentreview/arguments.py:178`                |
| `cli.py`                                      | **新** Reviewer Core 独立 CLI：`python -m agentreview.cli review`，单任务或 C/D 批量（:226）；只接受 `effectiveness_holdout`/`held_out_benchmark_test` 两区（:16-19），C/D 必须显式给冻结历史 `--episodes-jsonl`（:231-234）                             | `agentreview/cli.py:226`                    |
| `coco_eval.py` + `_coco_official_runner.py` | **新** 独立 CoCo 官方评测：candidate_reviews.jsonl → 官方 `_2_split/_3_classify/_4_evaluation`，校验官方脚本 sha256                                                                                                                                           | `agentreview/coco_eval.py:148`                |
| `single_paper_review_roles.py`                | **角色与 rubric 冻结核心**：`REVIEWER_SPECS` :16-42、`EXPECTED_REVIEW_REQUEST_SHA256` :45、`load_review_request_sections()` :54（解析 REVIEW_Request.md 并校验哈希）、`build_specialist_reviewer_configs()` :164 / `build_meta_reviewer_config()` :190 | `agentreview/single_paper_review_roles.py:16` |
| `paper_context.py`                            | **候选可见的规范论文上下文**（benchmark 无关）：`build_canonical_paper_context()` :183、`candidate_visible_metadata()` :86（只暴露 paper_id/title/venue/year/reviewer_role 五字段）、预算 60000 字符 :23                                                     | `agentreview/paper_context.py:183`            |
| `paper_review_player_md.py`                   | `MarkdownPaperExtractorPlayer`：md → 规范上下文 + source anchors；frozen bundle 带 sha256 校验（:88-137）；S2 **永不默认注入**（:172-178）                                                                                                                    | `agentreview/paper_review_player_md.py:76`    |
| `source_map.py`                               | md/content_list 解析、SRC-NNN 锚点构建                                                                                                                                                                                                                                 | `agentreview/source_map.py:23`                |
| `memory_library.py`                           | Legacy "existing" 模式词面记忆库：`update_memory_library()` :70、`retrieve_memory_context()` :173（token 重叠打分）。同时是 memory_library.jsonl 读写层（mstar 模式的历史 episode 也由它构建）                                                                     | `agentreview/memory_library.py:173`           |
| `history_checkpoint.py`                       | **新位置**（原 memory_evolution/checkpoint.py）：身份绑定 checkpoint 通用件，`canonical_sha256()` :26、前缀/残留审计                                                                                                                                           | `agentreview/history_checkpoint.py:136`       |
| `backends/openai.py`                          | **当前唯一实际使用的 backend**（OpenAI 兼容/本地 vLLM）：限速 :110、重试、空/截断响应处理、reasoning_effort                                                                                                                                                      | `agentreview/backends/openai.py`              |
| `backends/__init__.py`                        | 注册表 6 个 backend，但存活调用只有 openai（+测试用 dummy）                                                                                                                                                                                                            | `agentreview/backends/__init__.py:13`         |
| `utility/s2_retrieval.py`                     | 存活的 Semantic Scholar 检索路径                                                                                                                                                                                                                                       | -                                               |
| `viewer_data.py`                              | viewer.py 数据层（刻意只用标准库，与评审生成隔离）                                                                                                                                                                                                                     | -                                               |

### 3.2 `agentreview/reviewer_runtime/`——稳定 Reviewer Core（08-24 新增）

**由来**：此前 MemoryProgram 运行时（sandbox/toolkit/task_agent/协议）长在 `memory_evolution` 里，进化器与运行时耦合。解耦方式是"搬家 + shim"：原 `memory_evolution/{sandbox,sandbox_worker,task_agent,toolkit,program_protocol,integration,checkpoint}.py` 的实现整体迁入本包，旧路径留 3 行再导出（如 `memory_evolution/sandbox.py:1-3`），旧 import 全部继续可用。

| 模块                                   | 职责                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `engine.py`（207 行）                | **核心**。`ReviewerSession` :32——一个 task 的活的 MemoryProgram 状态（sandbox + task_agent + loader 身份）；`ingest_episode()` :84（`self_generated_after_review` 协议：先审后写，:16-17）；`ingest_precompiled()` :78（免 LLM 重抽取的冻结 KnowledgeItem）；`review()` :122：generate_query → sandbox.read（≤3000 字符）→ generate_response。`ReviewerEngine.review(request, program)` :203 一次性入口 |
| `models.py`                          | `Episode` :8、`ReviewRequest` :31（paper_id+task_id+question+episodes）、`ReviewResult` :55（含 reviewer_identity / memory_program_identity / program sha256）                                                                                                                                                                                                                                                           |
| `program_protocol.py`（37 行）       | MemoryProgram 可执行契约（见第 5.1 节）                                                                                                                                                                                                                                                                                                                                                                                        |
| `program_loader.py`                  | `MemoryProgramLoader.resolve()` :33：接受种子文件 / `runs/.../best_program.py` / 未来 optimizer 直出 `.py` / promoted 套件目录；`verify_promoted_program()` :87 校验 manifest 的 program_sha256                                                                                                                                                                                                                        |
| `sandbox.py` / `sandbox_worker.py` | 进程级能力沙箱（见第 5.3 节）                                                                                                                                                                                                                                                                                                                                                                                                  |
| `task_agent.py`                      | **冻结的 Task Agent**：`TaskAgent` :73 用程序自带 instructions 组 prompt（`extract_knowledge_item()` :151 / `generate_query()` :166 / `generate_response()` :181）；`build_task_agent()` :199 把 system_prompt 钉为角色的冻结 role_desc——程序只能改 INSTRUCTION_* 常量，改不了 system prompt                                                                                                                 |
| `toolkit.py`（598 行）               | `MemoryToolkit` 能力面 + 冻结 S2 池加载/校验（`load_frozen_s2_pool()` :45 带 cutoff 过滤；`validate_frozen_s2_pool()` :109）                                                                                                                                                                                                                                                                                             |
| `requests.py`                        | `build_review_question()` :12：论文文本+可见元数据+anchors → 规范单任务评审问题                                                                                                                                                                                                                                                                                                                                             |
| `episodes.py`                        | `build_reviewer_memory_episode_text()` :10：canonical paper interface 格式化 episode                                                                                                                                                                                                                                                                                                                                         |
| `artifacts.py`                       | **统一观测契约**：`CandidateReviewRecord`（schema `agentreview-candidate-review-v1` :14）+ `CandidateReviewWriter` :130。进化、在线 CLI、CoCo 评测三方公共 JSONL 格式；`provenance` 是唯一可放 optimizer 专属字段的地方                                                                                                                                                                                          |
| `memory_runtime.py`                  | `MemoryHarnessAdapter` :8：旧 integration.py 的兼容壳，被在线 CLI 使用（ingest_episodes 直写完整文本，**不走** review-then-write，:30-36 有注释说明）                                                                                                                                                                                                                                                                  |
| `tasks.py`                           | 六任务身份常量 +`reviewer_identity()` :16（含 rubric_sha256）                                                                                                                                                                                                                                                                                                                                                                |

---

## 4. 在线评审管线端到端（`run_single_paper_review_cli.py`）

入口 `__main__` :2198（`project_setup()` → `main()` :2105）。`run_one_paper()` :1784 单篇全流程：

1. **论文发现** `discover_markdown_papers()` :176：三种输入——冻结 bundle（sha256 校验）、`--paper_context_manifest` 批量（必须标记 `contains_human_review_data=false`）、`--paper_md_path/dir` 直读。
2. **构建玩家** `build_single_paper_players()` :103：1 个 `MarkdownPaperExtractorPlayer` + 5 个专科 `Reviewer` + 1 个 META `Player`，角色配置来自 `single_paper_review_roles.py`（启动即校验 rubric 哈希）。
3. **抽取** :1801：产出规范论文上下文 + source_anchors。
4. **M★ 记忆上下文** `build_reviewer_memory_contexts()` :1163 → `_mstar_memory_contexts()` :881：
   - `_memory_embedding_factory()` :782 解析 `--memory_embedding_adapter`（正式工厂 `memory_evolution.embedding_adapter:create_qwen3_embedding`，run.sh:20）；
   - 从 `memory_library.jsonl` 历史记录按 reviewer 粒度构造 Episode（:907-929）；
   - 每专科：`resolve_program_path` 定位 `<MSTAR_PROGRAM_DIR>/<DIM>/best_program.py` → `verify_promoted_program()` 校验 manifest → `MemoryHarnessAdapter` 起沙箱 `ReviewerSession` → ingest → retrieve；
   - 注入格式 `INSTRUCTION_RESPONSE + ALWAYS_ON_KNOWLEDGE + read() 检索结果`，外包 `=== EVOLVED MEMORY PROGRAM CONTEXT ===`（:976-1018，上限 `--memory_max_chars` 5000）。
   - ⚠️ **易误读点**：在线管线只注入程序产出的**检索上下文**，不调 `generate_response` 生成评审正文——正文仍由冻结 Reviewer 角色产生。"程序经 TaskAgent 生成完整评审"的路径在进化评估（`evaluator.py`）与 Reviewer Core CLI（`agentreview/cli.py`）中。
5. **并行专科评审** `run_parallel_reviews()` :1299：ThreadPoolExecutor（`--reviewer_workers=5`）；每个 Reviewer 的 observation = [裁剪后论文上下文, "Memory Librarian" 名义的记忆消息 :1205]；`call_role_with_anchor_repair()` :1242 校验 `SRC-<digits>(page=<digits>)` 锚点合法性、非法则一次修复重试；逐个写 `specialist_checkpoint.json`（条件指纹 `runtime_condition_fingerprint()` :1537 防混用，断点续跑）。
6. **专科门** `validate_complete_specialist_reviews()` :620：五专科齐且锚点全过，否则 META 不启动。
7. **META 记忆** `_mstar_meta_memory_context()` :1040：第六个 META 程序，question 由**已冻结的五专科评审**拼接（:1108-1119）。
8. **META 评审** :1910-1934：唯一能看到全部五份评审的节点；空响应降级重试。
9. **产出** `save_structured_outputs()` :298：`reviews.json / memory.json / candidate_reviews.jsonl（每 reviewer 一行，含程序 sha256）/ review_history.json / timing.json` + `validate_generated_outputs()` :429 双重校验；除非 `--disable_memory_writeback`，`update_memory_library()` 回写 memory_library.jsonl（:2018-2019，成为后续论文的 A 区历史）。

并发模型：**论文级串行、专科级并行、META 最后**。另有严格历史回放 `_strict_history_state()` :2027（前缀校验、memory 快照恢复、非前缀目录即报错）。

---

## 5. `memory_evolution/` 包：M★ 离线进化引擎

### 5.1 MemoryProgram 契约（`agentreview/reviewer_runtime/program_protocol.py:7-37`）

每个 `.py` 候选程序必须导出：

- 4 个字符串常量：`INSTRUCTION_KNOWLEDGE_ITEM` / `INSTRUCTION_QUERY` / `INSTRUCTION_RESPONSE` / `ALWAYS_ON_KNOWLEDGE`；
- 2 个 dataclass：`KnowledgeItem`（write 侧：记什么）、`Query`（read 侧：查什么）；
- 1 个类 `KnowledgeBase(toolkit)`：`write(item, raw_text)` 与 `read(query) -> str`（≤3000 字符）。

三个种子在 `memory_evolution/seeds/`（三条独立 lineage 起点）：`vector_search.py`（分块嵌入+维度过滤）、`llm_summarizer.py`（全文按维度存、读时一次 LLM 摘要）、`experience_learner.py`（结构化 lesson/fact 抽取，零 LLM 调用）。

### 5.2 CLI 编排（`memory_evolution/cli.py`，1856 行）

9 个子命令（:787-911）：`prepare-specialists` / `prepare-test` / `prepare-meta` / `cache-meta-inputs` / `evolve-task` / `evolve-specialists` / `evolve-meta` / `evaluate` / `final-evaluate-suite`。默认模型三个均 `qwen3.8-27b-fp8`（:48-50）。

**formal 模式的重重前置**（读代码时注意这些是故意的硬门）：

- `_validate_formal_embedding_manifest()` :185：对照 `configs/formal_temporal_v3_qwen3e8b.json`（Qwen3-Embedding-8B@`1d8ad4ca...`）；
- `_validate_formal_embedding_cache_audit()` :209：缓存必须恰好 12705 向量、零截断、单/多卡逐位一致（≤1e-4）；
- selection 缓存与 candidate-runtime 缓存必须路径分离（:1076-1082）；
- formal 必须有 classifier adapter + immutable revision + manifest 钉住的 S2 pool sha（:1094-1133）；
- `search.py:432-435`：iterations 必须被 3 整除（对应三条 lineage 等额预算）。

### 5.3 进化搜索（`search.py::run_evolution` :421，核心 1458 行）

1. **配置与身份**：`EvolutionConfig` :31（默认 iterations=21、static_size=25、rotating_size=5、softmax 父选择、mutation_scope=full）；attempt 预算 `resolve_formal_attempt_budget()` :132。
2. **checkpoint 恢复**：controller 状态 `search_controller_checkpoint.json`（版本 `formal-search-controller-checkpoint-v2` :129）；恢复时校验 run_identity、截断 jsonl 至已提交行、恢复 RNG；无 checkpoint 但有残留即报错（:465-496）。阶段机：`subset_selection → seeds → search → search_inflight → selection → final → complete`。
3. **子集选择**（:630-660）：embedding 聚类选静态验证集 + episode 预算内选训练子集（`subset_selection.py:77/:142/:153`）；qwen3 时跑 `run_embedding_closure_smoke()` :266（真实走一次向量 write/read，namespace 必须是 `candidate_runtime_v1`）。
4. **种子注入**（:729-846）：三 seed 逐个过质量门 → 静态评估 → 入池；lineage 名 `evolved_from_{vector_search|llm_summarizer|experience_learner}`。
5. **搜索主循环**（:946-1250）：按 lineage 轮转（formal 要求每条 lineage 成功数相等 :979-986）；每轮：softmax(static_score/温度) 同 lineage 内选父（:360）→ rotating 评估父代 → `mutate_program()` 变异 → 质量门 →（失败）`compile_fix_loop()` 修复 → 子代静态评估（formal 先写 inflight checkpoint，崩溃可续 :1120-1135）→ `admit_child()` :854 入池并记 lineage。
6. **选择与晋升**（:1322-1374）：top-k × selection_repeats 重复评估，按 (mean, -variance) 选优；每条 lineage 另存 champion；产出 `best_program.py` + `best_eval_report.json`（完整晋升 provenance）。
7. `evolve-specialists`（cli.py:1624）外层按任务×seed 循环，primary-seed 胜者复制为 `<output>/<TASK>/best_program.py` 并写 `suite_manifest.json`；META 对应 `meta_manifest.json`。

### 5.4 变异与评估

- **`reflector.py`**（433 行）：`mutate_program()` :246——LLM 输出 commit message + V4A contextual patch（`_apply_v4a_patch()` :141，每 hunk 必须唯一匹配）；`validate_mutation_scope()` :231 支持 full/schema_logic/instructions_only 三档消融（AST dump 对比强制约束）；`compile_fix_loop()` :386 编译修复；`validate_candidate_feedback()` :79 反馈脱敏白名单（防 gold 泄漏进 mutation prompt）。
- **`evaluator.py`**（453 行）：`evaluate_program()` :62——沙箱内按时间序回放 A 区 episodes（review-then-write 协议 :187-222；B 区每题从同一 A 快照恢复 :280-283）、打分、写 traces/checkpoint/candidate_reviews。`candidate_safe_case/trace()` :20/:40 把 evaluator 侧状态裁剪成不可复现 gold 的反馈。
- **`metrics.py`**（349 行）：`CoCoReviewFitness` :119——专科 5 指标 / META 8 指标（:124-138）；候选举的类别标签不可信，`_candidate_categories` :159 经冻结 classifier 重新分类（防标签灌水）；judge 打 1-5 分取调和平均。
- **`quality.py`**（360 行）：三层质量门——`static_check()` :111（import 白名单 :14-29、必需符号 :31、禁用调用 :40）→ `compile_and_smoke_check()` :219（真实沙箱实例化 + 一次 write/read，read ≤3000 字符）→ `check_program_path()` :303。

### 5.5 Sandbox 执行机制（`reviewer_runtime/sandbox.py` + `sandbox_worker.py`）

**候选代码绝不在宿主进程执行**：

- `SandboxSession` :75：优先 bubblewrap（`--unshare-all --clearenv`，只读绑定，:124-173），不可用退回裸 `python -I -S`（:174-181）；rlimit 内存/CPU/NOFILE（:53-64）；父子 stdin/stdout JSON-RPC。
- **能力全部留在父进程**：`_handle_tool_call()` :239 只转发四种 RPC：`llm_completion` / `embed` / `retrieve_s2` / `log`。API key 与宿主文件系统对候选不可见。
- `sandbox_worker.py`（716 行，纯标准库）：白名单导入（:586）、剥离危险 builtins（:594）、write/read/schemas/**snapshot/restore**（:613，知识库状态序列化，是 evaluator checkpoint 与 CLI 历史回放的基础）。
- 能力面（`toolkit.py:331` `MemoryToolkit`）：`llm_completion`（每操作限 1 次 :401-409）、`embed`（无 client 时退化确定性 hash 向量 :546）、`create_vector_collection`（内存余弦 :285）、`retrieve_s2`（只读冻结池、只能查当前论文 :493-508）、`classify_review`（冻结 CoCo 分类器 :574）、`resource_snapshot()` 资源记账。

### 5.6 Embedding：Qwen3-Embedding-8B + SQLite 缓存（08-22/08-23 大改）

- `embedding_adapter.py`（928 行）：`QWEN3_MODEL_ID="Qwen/Qwen3-Embedding-8B"` :25，维度 4096、last_token pooling、normalize（:581-618）；revision 必须是 immutable commit SHA（`is_immutable_model_revision()` :51），当前钉 `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af`（`configs/formal_temporal_v3_qwen3e8b.json:6`）。`BgeM3Embedding` :505 仍在但正式路线已换 Qwen3——**旧文档写 BGE-M3 的部分全部过时**。
- **缓存**（`CachedDenseEmbedding`）：SQLite 表 `embeddings`，键 = (model, revision, semantics_version, text_sha256)，值含字节长度与向量 sha256（:249-344，防哈希碰撞）；formal 模式 `fail_on_cache_miss=True` 缓存 miss 直接报错；candidate 运行时 namespace `candidate_runtime_v1` 额外写审计表，校验失败的行删除重算（:302-343）。
- **两套缓存**：selection 缓存 `cache/qwen3_embedding_8b_cache.sqlite3`（`scripts/prewarm_embedding_cache.py` 预热，12705 向量 + audit）与 runtime 缓存 `cache/qwen3_embedding_runtime.sqlite3`（cli 强制路径不同；0f79eaf 起 gitignore）。

### 5.7 数据准备与防泄漏（实验协议骨架）

- **`agentreview_data.py`**（1373 行）：`prepare_openreview_data()` :579（A/B 划分、family 并查集 :65、episodes/queries/validation 构造、`assert_no_human_reference_leak()` :456、`audit_candidate_visible_artifacts()` :362）；`prepare_meta_data()` :1038；`prepare_held_out_test_data()` :1178（D 区）。
- **`assets.py`**（435 行）：`FOUR_ZONES` :21-26；`audit_four_zone_isolation()` :104（paper/title/family 互斥 + max(A,B,C) ≤ min(D) 时间隔离，协议 `four-zone-temporal-isolation-v3` :171）；`AssetManifest` :380。
- **`meta_cache.py`**（203 行）：`build_meta_input_cache()` :67——META 训练输入 5 折 out-of-fold，一篇论文的 specialist 输出绝不用含其人类答案的记忆产生。
- **`formal_protocol.py`**（574 行）：`run_formal_protocol_gate()` :235（formal 前置门：Asset Manifest 校验、temporal v2 强制 + v1/v2 对比审计 :254-257、四区隔离、S2 sidecar）；`run_formal_test_gate()` :488（D 区一次性测试门）。
- 外部适配器（版本钉死）：`coco_adapter.py` `FrozenCoCoAdapter` :62（冻结 ReviewSplit/ReviewClassify，按 commit sha 校验）、`coco_reference.py` :30（官方人类参考，evaluator-only）、`benchmark_adapter.py` :25、`api_preflight.py` :19（进化前探测端点）。

### 5.8 兼容 shim（读代码时跳过，但 import 仍有效）

`memory_evolution/{sandbox, sandbox_worker, task_agent, toolkit, program_protocol, integration, checkpoint}.py`——各 3-14 行，全部只是 `from agentreview.reviewer_runtime.X import *` 的再导出。**实现去 `agentreview/reviewer_runtime/` 读**。

---

## 6. `effectiveness_screening/` 包（C 区实验组织层）

| 模块                            | 职责                                                                                                                                      | 状态   |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| `prepare.py`（828 行）        | 预注册与数据准备：`load_config()` :206（校验 design_version）、分层抽样、冻结 paper bundle、S2 检索                                     | 活     |
| `run_matrix.py`（442 行）     | 预注册矩阵：`freeze_design()` :138、`matrix_commands()` :218、`audit_runs()` :333                                                   | 活     |
| `evaluate_proxy.py`（472 行） | CoCo 风格 proxy 评测：`classify_outputs()` :97、`judge_outputs()` :231（官方 judge prompt）、`build_blind_pairs()` :387（盲评配对） | 活     |
| `summarize.py`（305 行）      | 汇总 + bootstrap CI（:81）+ 盲评摘要                                                                                                      | 活     |
| `preflight.py`（195 行）      | 运行前检查                                                                                                                                | 活     |
| `memory_cache.py`（313 行）   | **Frozen Library 路线，已废弃**（`freeze_memory_library` :90 等，DEPRECATED.md 登记），源码保留勿用                               | Legacy |

定位：C 区有效性实验的组织层；实际逐篇评审仍调 `run_single_paper_review_cli.py`；评测可走 proxy（本包）或新的独立 CoCo 评测（`agentreview/coco_eval.py`）。

---

## 7. 数据与配置（`AR/configs/` 与四区）

| 文件                                                        | 用途                                                                                                                                                                                                                                                            |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `asset_manifest_v1/v2.json`                               | 冻结资产清单（rubric、S2 池、seeds、temporal manifest 的 sha256/split/version）；**v2 为当前正式版**，formal 门校验                                                                                                                                       |
| `coco_temporal_split_v1/`、`v2/`                        | 四区 temporal split（每区 jsonl + manifest；v2 另有 paper_families.jsonl）。**v2 审计数据：A=2391 / B=299 / C=300 / D=899**，development_max_cutoff=2022-12-31、test_min=2023-12-31。**formal 只接受 v2**（`formal_protocol.py:254-257` 拒绝 v1） |
| `frozen_s2_temporal_v1/v2.jsonl`（+.manifest.json/.work） | 冻结 S2 检索池（`scripts/build_frozen_s2_pool_v2.py` 生成，带 cutoff/provenance），运行时只读加载                                                                                                                                                             |
| `effectiveness_screening_v1/v2.json`                      | 筛选实验配置（v2 指向 temporal v1 manifest 与`runs/post_temporal_protocol_authoritative`）                                                                                                                                                                    |
| `formal_temporal_v3_qwen3e8b.json`                        | **正式 embedding 身份**（Qwen3-Embedding-8B@`1d8ad4ca...` + fingerprint），formal 门强制比对                                                                                                                                                            |
| `benchmark_reserved_venue_year_v1.json`                   | 已 superseded（文件头自声明），仅历史                                                                                                                                                                                                                           |
| `configs_README.md`                                       | 空文件（0 字节）                                                                                                                                                                                                                                                |

**四区在代码中的体现**（`memory_evolution/assets.py:21-26`）：

- **A 区** historical_experience：进化训练 episodes（时序回放）+ 在线 CLI 的 memory_library 历史；
- **B 区** evolution_validation：进化 fitness 的静态/rotating 验证集（M★ 专属）；
- **C 区** effectiveness_holdout：只经独立 Reviewer CLI（`agentreview/cli.py:16-19`）或筛选框架；
- **D 区** held_out_benchmark_test：只经 `final-evaluate-suite`（`memory_evolution/cli.py:1286`，一次性、禁覆写）或独立 CoCo 评测；永不进入进化。
- `reviewer_runtime` 本身零 zone 概念（解耦边界）。

---

## 8. 脚本、查看器与测试

### 8.1 Shell 脚本

- `run.sh`（41 行）——**当前正式在线评审入口**：要求 `MSTAR_PROGRAM_DIR` + `MSTAR_EMBEDDING_REVISION`，默认模型 `qwen3.8-27b-fp8`，`--memory_mode mstar_program --memory_embedding_adapter memory_evolution.embedding_adapter:create_qwen3_embedding`，默认数据 `test_data/` → `outputs/`。
- `pilot_full.sh`（112 行）——pilot 全链路：prepare-specialists（temporal v2）→ evolve-specialists → cache-meta-inputs → prepare-meta → evolve-meta → pilot_report。
- `QUAL_test.sh`（30 行）/ `end2end_smoke.sh`（15 行）——冒烟（后者 = unittest discover + 30 训练族 prepare + evolve-task QUAL --smoke）。⚠️ 脚本内**硬编码 API key**，外发前需清理。

### 8.2 `scripts/`

`build_coco_temporal_split.py`（774 行，四区 split 构建）、`build_frozen_s2_pool.py` / `_v2.py`（冻结 S2 池）、`finalize_formal_readiness.py`（350 行，formal-ready 资产校验）、`prewarm_embedding_cache.py`（224 行，Qwen3 缓存预热：多 GPU 分片、单/多卡一致性校验、12705 向量 + audit）。

### 8.3 查看器（经裁决保留的人工分析工具）

- `viewer.py`（713 行）+ `agentreview/viewer_data.py`（496 行，刻意只用标准库）：浏览评审产物，端口 7861。
- `evolution_viewer/`（独立 Gradio 应用）：M★ 程序转变审计台——逐轮变异 diff、traces、任意两程序 AST 级 A/B 对比，端口 7862。

### 8.4 测试（`AR/tests/`，26 个文件）

重点：`test_temporal_protocol.py`（25 测试，四区/formal gate）、`test_agentreview_data.py`（15，防泄漏）、`test_embedding_cache.py`（15，Qwen 缓存）、`test_review_context.py`（12）、`test_search_loop.py`（9，含 checkpoint 与 embedding closure smoke）、`test_reviewer_runtime_decoupling.py`（7，解耦验证：Core 不加载 memory_evolution、新旧语义 parity、未来 optimizer 程序加载）、`test_coco_independent.py`（独立 CoCo 评测）。运行：`python -m unittest discover -s tests`。

---

## 9. 死代码地图（读代码时跳过；登记于 DEPRECATED.md，以标记为主不删除）

- **旧多阶段审稿体系**（已被单论文 5-specialist 管线取代）：`paper_review_arena.py`、`arena.py`、`database.py`、`ui/cli.py`、`environments/*`（paper_review/paper_decision/single_paper_review/conversation/base）、`paper_review_message.py`。
- **PDF 路径与旧实验框架**：`paper_processor.py`、`experiment_config.py`、`paper_review_settings.py`、`utility/experiment_utils.py`、`utility/{text,metrics,data}_utils.py`、`dataset/process_submissions.py`、`download_openreview_paper.py`。
- **openscholar_bridge 死部分**：`openscholar_api.py`、`config.py`、`example_usage.py`、`src/open_scholar.py`、`src/instructions.py`（存活路径是 `utility/s2_retrieval.py` → `src/use_search_apis.py`）。
- **未使用 backends**：`anthropic.py`、`cohere.py`、`hf_transformers.py`、`human.py`、`bard.py`、`langchain.py`（`dummy.py` 经裁决保留作测试）。
- **部分死亡的存活文件**：`paper_review_player.py`（仅 `Reviewer` 类存活）、`role_descriptions.py`（仅 `get_paper_extractor_config` 存活）、`config.py`（`ArenaConfig`/`EnvironmentConfig` 死）、`const.py`。
- **Legacy 路线**：`--memory_mode=existing`（memory_library 词面检索，非默认）、Frozen Library 路线（`effectiveness_screening/memory_cache.py`）。
- **数据产物警告**：`runs/pilot_full_iter5_202607300117` 因 candidate-visible episode 含真人参照（违反 Human Reference Boundary），仅历史分析用，不作 effectiveness 结论权威。

---

## 10. 横切不变量（读任何模块前先记住这七条）

1. **哈希钉死一切**：REVIEW_Request.md（`single_paper_review_roles.py:45`，多处校验）、S2 缓存、paper bundle、design lock、promoted 程序、CoCo 官方脚本——任何漂移运行时爆炸而非静默改变实验。
2. **冻结/可进化边界**：rubric、拓扑、Task Agent、评估器、种子数、默认超参全部冻结；进化的只有 MemoryProgram 的 Schema/Logic/Instructions。
3. **依赖方向单向**：memory_evolution → reviewer_runtime → candidate_reviews.jsonl → coco_eval；reviewer_runtime 无 zone 概念、无进化概念。
4. **Human Reference Boundary**：真人 review/gold 只进 external evaluator、evolution fitness、benchmark evaluation 三处；反馈经 `candidate_safe_case/trace()` 脱敏后才进 Reflector。
5. **Self-Generated Experience + fresh-state**：每个 Program 从空 Memory 出发按时序回放 A 区；先审后写（`self_generated_after_review`）；继承的是 code lineage 不是 Memory State（ADR-0007）。
6. **四区隔离**：A≠B≠C≠D，paper/title/family 零重叠 + max(A,B,C) ≤ min(D)；formal 入口强制审计，不靠目录名。
7. **六池独立**：QUAL/CLAR/SIGN/ORIG/POL/META 各自的数据、种群、分数、lineage、胜者，跨维度零共享；formal 下三条 lineage 等额预算（iterations 被 3 整除）。

---

## 11. 近期架构变更史（7256ffc → 0f79eaf，读旧文档前的必备对照）

| commit                           | 变更                                                                                                                                                                                                                                                                                                                                                 |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `82c8c60`（08-22）             | **Qwen3-Embedding-8B 替代 BGE-M3**；新增 `configs/formal_temporal_v3_qwen3e8b.json`；prewarm 大改。→ 旧文档所有 "BGE-M3" 表述过时                                                                                                                                                                                                           |
| `4470af2`（08-23）             | 修复缓存嵌入命中失败（invalid 行删除重算 + runtime namespace 区分）；search 加`run_embedding_closure_smoke`；cli 强制 selection/runtime 两套缓存分离                                                                                                                                                                                               |
| `cc1007f`/`8dd4f8f`（08-24） | **reviewer_runtime 解耦落地**：sandbox/sandbox_worker/task_agent/toolkit/program_protocol/integration/checkpoint 七模块迁入 `agentreview/reviewer_runtime/`（旧位留 shim；integration → memory_runtime.py，checkpoint → history_checkpoint.py）；新增 `agentreview/cli.py`（review CLI）、`coco_eval.py`、`_coco_official_runner.py` |
| `0f79eaf`（08-25，HEAD）       | docs 重组（CONTEXT.md/AGENTS.md 等移入 docs/待阅读md/）；runtime embedding 缓存 gitignore；⚠️ 误提交垃圾文件`AR/3072,`                                                                                                                                                                                                                           |

其余：`117c382` 本地 qwen3.8 evolution runtime 对齐、`24cc31d`/`cd24bfe` QUAL smoke 排查修复、`c0b9a0d` requirements 整理。当前 `runs/formal_qual_seed0_20260825/` 是 HEAD 时点正在进行的 formal QUAL seed0 run（仅种子阶段产物），说明 formal 路线正按新架构实跑验证。

---

## 12. 与既有文档的冲突点速查

- **任何写 "BGE-M3" 的文档**（含 0820 版全景解析 §5.7）→ 已被 Qwen3-Embedding-8B 取代（82c8c60）。
- **任何把 sandbox/toolkit/task_agent/program_protocol 描述为 memory_evolution 组成部分的文档**（含 0820 版全景解析、DEPRECATED.md 部分表述）→ 实现已迁至 `agentreview/reviewer_runtime/`，memory_evolution 侧只剩 shim（08-24）。
- **写 `--memory_mode` 默认 "existing" 的文档** → 默认已是 "none"（`arguments.py:178`）。
- **写 run_single_paper_review_cli.py "~1970 行" 的文档** → 已 2200 行，且新增 candidate_reviews.jsonl 产物。
- **写 "evolve 20 迭代" 的文档** → EvolutionConfig 默认 iterations=21，formal 要求被 3 整除。
- 各文档详细的新旧判定见 [DOCUMENT_INDEX.md](DOCUMENT_INDEX.md)（2026-08-25 更新版）。
