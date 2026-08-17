# AgentReview (M★) 项目架构全景解读

> 基于对当前仓库代码的 import 链追踪生成（2026-08-12），所有结论均来自代码本身。
> 链接相对于 `/data0/xyj/shengao` 工作区根目录。

---

## 1. 项目是干什么的

这是一个 **AI 自动论文评审系统**，在原始 AgentReview 项目（多智能体模拟会议审稿）基础上，演化成了现在的 **M★ 架构**：

```text
论文 (Markdown) → 5 个独立专家审稿人 → 1 个 Meta 审稿人
                   QUAL CLAR SIGN ORIG POL      META
                     └── 每个角色挂一个独立演化出来的 M★ MemoryProgram ──┘
```

- 5 个专家角色及其 23 个子类别的定义**唯一来源**是 [REVIEW_Request.md](AGENTREVIEW/AgentReview/REVIEW_Request.md)，运行时做 SHA-256 校验防篡改（[single_paper_review_roles.py:45-62](AGENTREVIEW/AgentReview/agentreview/single_paper_review_roles.py#L45-L62)）
- **M★（Memory Evolution）** 是一个离线演化子系统：用类似遗传编程的方式，为 6 个角色各演化出一个"记忆程序"（怎么写记忆、怎么检索记忆、给 LLM 的指令文本），提升审稿质量
- 项目分**两条线**：① 运行时评审管线（用演化好的程序审论文）；② 离线演化管线（用 OpenReview 数据训练/筛选记忆程序）

---

## 2. 程序从哪里启动

有 **3 类入口**，按用途分：

| 入口 | 用途 |
|---|---|
| [run_single_paper_review_cli.py](AGENTREVIEW/AgentReview/run_single_paper_review_cli.py)（1970 行） | **主入口**：运行完整评审管线。`main()` 在 L1920，便捷脚本是 [run.sh](AGENTREVIEW/AgentReview/run.sh) |
| `python -m memory_evolution.cli`（[cli.py](AGENTREVIEW/AgentReview/memory_evolution/cli.py)，9 个子命令） | **演化入口**：数据准备 → 演化 5 专家 → 演化 META → 密封测试。[pilot_full.sh](AGENTREVIEW/AgentReview/pilot_full.sh) 是一键 pilot |
| [viewer.py](AGENTREVIEW/AgentReview/viewer.py)（端口 7861）/ `python -m evolution_viewer.app`（端口 7862） | 两个只读 Gradio 查看器：评审结果 / 演化过程 |

---

## 3. 一次完整任务怎么流转（运行时主流程）

`main()` → `discover_markdown_papers()` 发现论文 → 逐篇 `run_one_paper()`（L1685）：

```text
阶段0 提取    MarkdownPaperExtractorPlayer.act()（不调 LLM）
              读 .md + content_list.json → 生成 SRC-001 锚点（source_map.py）
              → Semantic Scholar 检索相关工作（s2_retrieval.py → openscholar_bridge）
              → 输出 "论文 + 锚点表 + 参考文献" 三段文本
     │
阶段1 记忆    build_reviewer_memory_contexts()（L1072）按 memory_mode 分发：
              none → 空；existing → memory_library 关键词检索；
              mstar_program → 每个维度在沙箱中跑各自的 MemoryProgram，
              把历史评审 ingest 进向量库，再检索出 ≤3000 字符上下文
     │
阶段2 专家    run_parallel_reviews()（L1208）：ThreadPoolExecutor 并行 5 个 Reviewer
              每人收到: 截断论文(35000字符) + 自己的记忆上下文 → LLM
              输出后经 SRC 锚点校验，失败自动修复重试一次
     │
阶段3 META    等 5 个全部完成并校验后，META 串行执行：
              输入 = 论文(30000字符) + META记忆 + 5 份完整评审 → LLM → 终审意见
     │
阶段4 落盘    reviews.json / memory.json / review_history.json / timing.json
              + 全局记忆库 memory_library.jsonl 追加（供下次评审检索）
```

**关键架构事实**：主流程**完全绕过了**原项目的 Arena/Environment 框架——CLI 自己手写编排，只复用了 `Player` / `Message` / `Backend` 三层抽象。

---

## 4. 核心模块与调用关系

```text
run_single_paper_review_cli.py        ← 编排者（核心中的核心）
 ├─ agentreview/single_paper_review_roles.py   ← 6 个角色定义，校验 REVIEW_Request.md
 ├─ agentreview/paper_review_player_md.py      ← Extractor（MD 解析 + S2 检索）
 ├─ agentreview/paper_review_player.py         ← Reviewer 类（5 专家用它）
 ├─ agentreview/agent.py                       ← Player 基类：act() → backend.query()
 ├─ agentreview/backends/openai.py             ← 唯一实际使用的 LLM 后端（重试/限流/推理模型适配）
 ├─ agentreview/source_map.py                  ← SRC 锚点系统（证据引用机制的基石）
 ├─ agentreview/memory_library.py              ← 运行时记忆库（jsonl + 关键词索引）
 └─ memory_evolution/                          ← M★ 演化子系统（离线）
     ├─ cli.py / search.py                     ← 演化主循环（变异→评估→选择）
     ├─ reflector.py                           ← 用 LLM 生成程序变异 patch + 编译修复
     ├─ sandbox.py / sandbox_worker.py         ← bubblewrap 沙箱执行候选程序
     ├─ integration.py (MemoryHarnessAdapter)  ← 运行时在沙箱里跑 MemoryProgram 的桥
     ├─ metrics.py / coco_adapter.py           ← 适应度：LLM judge + 冻结分类器
     └─ seeds/                                 ← 3 个种子程序（向量检索/LLM摘要/经验学习）
```

---

## 5. 关键数据结构

| 结构 | 定义位置 | 作用 |
|---|---|---|
| `Message` (dataclass) | [message.py:28](AGENTREVIEW/AgentReview/agentreview/message.py#L28) | 全链路消息载体：agent_name/content/turn/visible_to |
| `AgentConfig` / `BackendConfig` | [config.py:92](AGENTREVIEW/AgentReview/agentreview/config.py#L92) | 角色配置（role_desc + backend） |
| `Episode` / `EvalItem` / `EvalResult` | [models.py:6](AGENTREVIEW/AgentReview/memory_evolution/models.py#L6) | 演化用训练样本 / 评估样本 / 评估结果 |
| `KnowledgeItem` / `Query` / `KnowledgeBase` | 每个 MemoryProgram 内定义，协议见 [program_protocol.py](AGENTREVIEW/AgentReview/memory_evolution/program_protocol.py) | 程序自定义的记忆 schema + 读写逻辑 |
| `ProgramCandidate` | [search.py:76](AGENTREVIEW/AgentReview/memory_evolution/search.py#L76) | 演化种群个体（含谱系、分数） |
| memory record (dict, 11 字段） | [memory_library.py:42](AGENTREVIEW/AgentReview/agentreview/memory_library.py#L42) | 一篇论文全部评审结果的存档记录 |
| `ReviewTask` | [tasks.py:28](AGENTREVIEW/AgentReview/memory_evolution/tasks.py#L28) | 6 个任务（QUAL…POL/META）的冻结定义 |

---

## 6. LLM / Agent / Tool / Memory 各在哪里

- **LLM 调用**：[backends/openai.py](AGENTREVIEW/AgentReview/agentreview/backends/openai.py) `OpenAIChat`——`chat.completions.create` + 3 次指数退避重试 + 空响应重试（自动加倍 max_tokens）+ 全局限流（`AGENTREVIEW_API_MIN_INTERVAL_SECONDS`）+ gpt-5/o 系列推理参数适配
- **Agent**：[agent.py:43](AGENTREVIEW/AgentReview/agentreview/agent.py#L43) `Player`——持有 backend，`act(observation: List[Message])` → LLM。没有工具调用循环，就是"单次 prompt → 单次回答"模式
- **Tool**：这个系统**没有**通用 tool-use 机制。两个例外：① Extractor 的 S2 检索是硬编码进 `act()` 的，不是 LLM 自主调用；② 沙箱内 MemoryProgram 可通过 `CapabilityToolkit` 用 5 个受限能力（`llm_completion`/`embed`/`create_vector_collection`/内存 SQLite/logger，[sandbox_worker.py:199](AGENTREVIEW/AgentReview/memory_evolution/sandbox_worker.py#L199)）
- **Memory**：分三层——① 演化出的 `MemoryProgram`（write/read/指令，每角色独立）；② 运行时 `memory_library.jsonl`（评审存档，关键词检索）；③ Extractor 的 `last_memory` dict（论文提取产物）

---

## 7. 核心 vs 工具代码（最终判定）

**核心（主链路实际执行）**：`run_single_paper_review_cli.py`、`single_paper_review_roles.py`、`paper_review_player_md.py`、`paper_review_player.py`（仅 `Reviewer` 类）、`agent.py`、`message.py`、`config.py`、`arguments.py`、`source_map.py`、`memory_library.py`、`backends/openai.py` + `dummy.py`、`utility/`（仅 utils/general_utils/authentication_utils/s2_retrieval）、`openscholar_bridge/src/`、整个 `memory_evolution/`。

**遗留（原 AgentReview 框架，主链路不用，已逐一验证无 import）**：`arena.py`、`paper_review_arena.py`、`paper_review_message.py`、整个 `environments/`（含 `single_paper_review.py`——名字像核心但 CLI 不走它）、`database.py`（Supabase）、`paper_processor.py`（PDF 路径）、`experiment_config.py`、`paper_review_settings.py`、`role_descriptions.py` 的 95%（只有 `get_paper_extractor_config` 被用）、`backends/` 里的 anthropic/cohere/hf/human/bard/langchain（后两个甚至没注册）、`utility/` 的 data/metrics/experiment/text_utils、`dataset/`。

**辅助工具**：`viewer.py` + `viewer_data.py`（结果查看）、`evolution_viewer/`（演化可视化）、`effectiveness_screening/`（三臂有效性实验框架，以子进程方式调主 CLI）、`tests/`、`run.sh`/`pilot_full.sh` 等启动脚本、`data/` 与各 `outputs*/`（实验产物）。

---

## 8. 重新上手的建议路径

先读 [README.md](AGENTREVIEW/AgentReview/README.md) + [AGENTS.md](AGENTREVIEW/AgentReview/AGENTS.md)（仓库自带的地图，与代码核对无误），然后从 `run_one_paper()`（[run_single_paper_review_cli.py:1685](AGENTREVIEW/AgentReview/run_single_paper_review_cli.py#L1685)）顺着阶段 0→4 读一遍主链路，再按需深入 `memory_evolution/search.py` 的演化循环。`environments/`、`arena.py` 那套可以直接跳过。

---

## 附录 A：M★ 演化管线细节

### CLI 子命令链（memory_evolution/cli.py）

1. `prepare-specialists` — 从 OpenReview `train_clean.jsonl` 按 work-family 去测试集重叠，为 5 个专家各生成 `episodes.jsonl` / `validation.jsonl` / `queries.jsonl`
2. `evolve-specialists` — 每个专家 5 个独立演化种子，各自独立种群/谱系，产出 `best_program.py`
3. `cache-meta-inputs` — out-of-fold 生成 META 训练输入（防数据泄露）
4. `prepare-meta` / `evolve-meta` — META 在 5 专家冻结后才演化
5. `prepare-test` / `final-evaluate-suite` — 密封测试，报告不覆写

### 演化循环（search.py）

- 3 个种子程序：`vector_search`（向量检索+维度过滤）、`llm_summarizer`（LLM 在线摘要）、`experience_learner`（结构化经验卡片）
- 每轮：softmax(score/0.15) 选父代 → 轮动验证集收集反馈 → Reflector LLM 生成 V4A patch → 质量门（AST 静态检查 + 沙箱 smoke test）→ 失败最多 3 次编译修复 → 静态验证集打分 → 入种群
- 晋升：top-3 候选各重复评估 3 次，按 (mean, -variance) 选最优为 `best_program.py`

### 适应度（metrics.py）

- 专家：LLM judge 五项均分（alignment/completeness/grounding/verifiability/clarity）与类别召回（冻结分类器打标签，`|AI∩human|/|human|`）的**调和平均**；候选自报标签不被信任
- META：裁判组（4 项）、参考对齐组（2 项）、决策组（2 项）三组组内均值后再调和平均

### 沙箱（sandbox.py / sandbox_worker.py）

bubblewrap `--unshare-all --clearenv` 隔离子进程，JSON-RPC over stdin/stdout；资源限制（1GB 内存 / 60s CPU）；导入白名单；禁止 `open/eval/exec/__import__` 等；LLM 调用每次 read/write 最多 1 次。

---

## 附录 B：运行时关键参数默认值（arguments.py）

| 参数 | 默认值 |
|---|---|
| `--model_name` | gpt-5-mini |
| `--reviewer_workers` | 5（全并行） |
| `--memory_mode` | existing（可选 none/existing/mstar_program） |
| `--reviewer_paper_max_chars` | 35000 |
| `--meta_paper_max_chars` | 30000 |
| `--review_max_tokens` | 8192 |
| `--meta_review_max_tokens` | 4096 |
| `--review_temperature` | 0.3 |

## 附录 C：输出产物清单

单篇论文输出目录 `{output_dir}/single_paper_review/{sample_id}/`：

| 文件 | 内容 |
|---|---|
| `reviews.json` | {评审员名: 评审文本} |
| `memory.json` | 论文元数据、S2 检索结果、source anchors、记忆配置 |
| `review_history.json` | 完整消息历史 + experiment_setting + timing |
| `timing.json` | 起止时间与耗时 |
| `specialist_checkpoint.json` | 专家阶段断点（全部完成后删除） |

全局：`memory_library.jsonl` + `memory_index.json` + `batch_manifest.json`。
