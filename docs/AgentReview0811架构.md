分析完成。该项目实现的是 **AgentReview M★** 架构：**论文 → 五个独立专科评审（QUAL/CLAR/SIGN/ORIG/POL）→ 最终 META 元评审**，每个评审角色挂载一个通过 M★ 演化算法离线进化出的可执行 MemoryProgram。整个仓库围绕"评审生成"与"记忆演化"两个闭环，外加冻结的有效性筛选实验和两个只读查看器。

---

## A. 文件树概览

```
AgentReview/  
├── run_single_paper_review_cli.py   (1970行) 评审系统主入口：单篇/批量论文评审 CLI
├── run.sh                           在线评审启动脚本（mstar_program 记忆模式）
├── pilot_full.sh                    M★ 演化端到端 pilot 流水线脚本
├── end2end_smoke.sh / QUAL_test.sh  冒烟测试脚本
├── viewer.py                (713行) 只读 Gradio 评审结果查看器（端口 7861）
├── REVIEW_Request.md                五份评审 rubric 的唯一不可变来源（运行时 SHA-256 校验）
├── configs/effectiveness_screening_v1.json   有效性筛选实验的唯一配置源
├── docs/                            memory_evolution.md（M★ 复现契约）、effectiveness_screening.md
├── agentreview/                     核心评审框架（ChatArena 风格多智能体库）
│   ├── agent.py / arena.py / message.py / config.py / database.py
│   ├── paper_review_arena.py / paper_review_player.py / paper_review_player_md.py
│   ├── single_paper_review_roles.py 五专科+META 冻结角色定义（含 rubric 哈希校验）
│   ├── memory_library.py            在线记忆库（memory_library.jsonl）读写与检索
│   ├── source_map.py                Markdown → SRC-xxx(page=y) 锚点构建
│   ├── arguments.py                 主 CLI 参数解析
│   ├── backends/                    LLM 后端抽象（openai/anthropic/cohere/hf/human/dummy）
│   ├── environments/                对话环境（conversation/paper_review/paper_decision/single_paper_review）
│   ├── utility/                     认证、S2 检索、数据/实验/文本工具
│   └── openscholar_bridge/          OpenScholar 相关工作检索桥
├── memory_evolution/                M★ 记忆演化子系统
│   ├── cli.py              (1004行) 演化 CLI：prepare/evolve/evaluate 七个子命令
│   ├── search.py                    演化主循环（种群、softmax 父代选择、静态/轮转验证）
│   ├── evaluator.py / metrics.py    程序评估 + CoCoReviewFitness 适应度
│   ├── sandbox.py / sandbox_worker.py  候选程序的 bubblewrap 进程沙箱（JSON-RPC）
│   ├── reflector.py                 LLM 变异 + V4A patch + 编译修复
│   ├── quality.py                   AST 白名单静态检查 + 编译 + 冒烟质量门
│   ├── agentreview_data.py  (729行) OpenReview 语料 → 防泄漏 episodes/validation 构建
│   ├── integration.py               MemoryHarnessAdapter：在线侧加载演化程序
│   ├── subset_selection.py / meta_cache.py / embedding_adapter.py / coco_adapter.py
│   └── seeds/                       三个不可变种子程序：vector_search / llm_summarizer / experience_learner
├── effectiveness_screening/         冻结三臂有效性筛选（no_memory / seed_experience / evolved）
│   ├── prepare.py / run_matrix.py / memory_cache.py / evaluate_proxy.py / summarize.py / preflight.py
├── evolution_viewer/                独立只读 Gradio M★ 演化查看器（端口 7862）
├── data/  outputs/  runs/  tests/
```

---

## B. 主调用链条

### B1. Shell → Python 入口

| 脚本                                                                                                                                   | 调用                                                                                                                                         | 用途                       |
| -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------- |
| [run.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/run.sh)                     | `run_single_paper_review_cli.py --memory_mode mstar_program ...`                                                                           | 在线批量评审（带演化记忆） |
| [pilot_full.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/pilot_full.sh)       | `memory_evolution.cli prepare-specialists → evolve-specialists → cache-meta-inputs → prepare-meta → evolve-meta` → `pilot_report` | M★ pilot 全流程           |
| [end2end_smoke.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/end2end_smoke.sh) | `memory_evolution.cli evolve-specialists --smoke`                                                                                          | 结构冒烟                   |

### B2. `run_single_paper_review_cli.py` 执行流程（评审主链路）

1. **参数解析**：[arguments.py:10](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/arguments.py#L10) `parse_args` — `--memory_mode {none,existing,mstar_program}`、`--memory_program_dir`、`--memory_knowledge_cache_dir`、`--reviewer_workers=5` 等
2. **论文发现**：[run_single_paper_review_cli.py:154](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/run_single_paper_review_cli.py#L154) `discover_markdown_papers` — 三种模式（单篇+冻结 bundle 校验 / manifest 批量冻结 / 目录 glob）
3. **构建玩家** `build_single_paper_players`(:81)：
   - `MarkdownPaperExtractorPlayer`（提取器）
   - 5 个 `Reviewer`（配置来自 [single_paper_review_roles.py:164](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/single_paper_review_roles.py#L164)，rubric 先经 SHA-256 校验）
   - 1 个 META `Player`
4. **论文提取**：[paper_review_player_md.py:88](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/paper_review_player_md.py#L88) `act()` — 读 Markdown → `source_map` 生成 SRC 锚点 → [use_search_apis.py:96](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/openscholar_bridge/src/use_search_apis.py#L96) `search_paper_via_query` 调 Semantic Scholar 检索相关工作 → 组装单条大消息
5. **记忆上下文** `build_reviewer_memory_contexts`(:1072) → `_mstar_memory_contexts`(:810)：定位 `<dir>/<TASK>/best_program.py` → 优先加载预编译知识缓存，否则 `verify_promoted_program` 校验后构造 `MemoryHarnessAdapter`（沙箱 write/read）→ 拼成 `EVOLVED MEMORY PROGRAM CONTEXT` 段
6. **专科并行评审** `run_parallel_reviews`(:1208)：`ThreadPoolExecutor` 并行 5 个专科 → `call_role_with_anchor_repair`(:1151) → `Player.act` → `backend.query`；锚点校验失败自动重试一次；每个写 `specialist_checkpoint.json` 支持断点续跑
7. **完备性门** `validate_complete_specialist_reviews`(:550) — 五专科全过才允许 META
8. **META 评审**(:1794-1830)：观察 = 论文 + META 记忆 + 5 条专科消息；空响应时去论文上下文重试
9. **校验落盘**：`validate_review_payload`(:573) → 写 `reviews.json / memory.json / review_history.json / timing.json`
10. **记忆回写**：`commit_global_memory`(:353) → [memory_library.py:70](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/memory_library.py#L70) 追加 `memory_library.jsonl`

### B3. `memory_evolution/cli.py` 子命令链（演化链路）

```
prepare-specialists → agentreview_data.prepare_openreview_data(:331)
                      work-family 并查集防泄漏切分 → 每 TASK 写 episodes/validation/queries.jsonl
evolve-task / evolve-specialists / evolve-meta → _run_task(:407)
  → _toolkits(:151) 建 task/reflector/judge 三个 toolkit
  → search.run_evolution(:187):
      三个种子入池 → quality.check_program_path(AST白名单+沙箱冒烟) → evaluator.evaluate_program 打分
      循环: _select_parent(softmax) → 轮转集评估 → Reflector.mutate_program(LLM产V4A patch)
           → 质量门(失败则 compile_fix_loop ≤3次) → 静态集评估入池 → scores.jsonl/lineage.jsonl
      末段 top-k 重复评估 → best_program.py
cache-meta-inputs → meta_cache.build_meta_input_cache(:67)  out-of-fold 生成 META 输入（防泄漏）
prepare-meta → prepare_meta_data(:515)  构建第六个任务（META）数据
evaluate / final-evaluate-suite  密封测试集上一次性评估，拒绝覆盖报告
```

### B4. effectiveness_screening 流程（冻结三臂实验）

```
prepare.py:  select(分层抽样20篇+泄漏不变量=0) → retrieve-related(冻结S2检索) → build-bundles(冻结bundle+checksum)
run_matrix.py:  freeze-design(SHA-256锁定design_lock.json) → matrix(生成120条评审命令) → audit-runs
memory_cache.py:  freeze-programs(程序快照) → freeze-memory-library → compile-cache(离线预编译KnowledgeItem)
evaluate_proxy.py:  classify(冻结CoCo适配器) → judge(gpt-5-mini, CoCo judge prompt) → build-blind-pairs
summarize.py:  配对delta + 20000次bootstrap CI + 盲评胜率 → screening_gate 十二项闸门判定
```

### B5. 查看器数据流

- **viewer.py**：`viewer_data.scan_output_dir` → `load_sample` 只读加载评审产物 JSON → Gradio 多 Tab 渲染
- **evolution_viewer**：`data_loader.scan_runs` 发现 `lineage.jsonl/scores.jsonl` → `code_analysis.analyze_program`（AST 提取 prompt/schema/write/read）→ 父子程序 diff + 分数曲线。不调 LLM、不执行候选程序。

---

## C. 类继承关系

```
agent.py:    Agent(:20) → Player(:43, act()→backend.query)
                ├── Moderator(:172)
                └── Reviewer / AreaChair / PaperExtractorPlayer (paper_review_player.py)
                └── MarkdownPaperExtractorPlayer (paper_review_player_md.py:77, 当前主路径)

backends/base.py:  IntelligenceBackend(:10)
                ├── OpenAIChat (openai.py:112)  ← 主路径唯一实际使用
                ├── Claude / CohereAIChat / TransformersConversational / Bard / Human
                注册表: backends/__init__.py BACKEND_REGISTRY → load_backend()

environments/base.py:  Environment(:28) → Conversation
                ├── PaperReview / PaperDecision / SinglePaperReview
                ⚠ 主 CLI 不走 Arena/Environment，手工编排（Arena 是旧版多论文模拟遗留）

memory_evolution:  Episode/EvalItem/EvalResult (dataclass)；三个种子程序鸭子类型满足
                program_protocol.py 的 KnowledgeBase.write/read 协议
```

## D. LLM 后端调用方式

- **评审链路**：`Player.act` → [openai.py:472](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AgentReview_0722/AgentReview/agentreview/backends/openai.py#L472) `query` → `_get_response`（tenacity 3 次重试）→ `client.chat.completions.create`；空响应抛 `EmptyModelResponseError` 并翻倍 max_tokens 重试；全局锁限速（`AGENTREVIEW_API_MIN_INTERVAL_SECONDS`）
- **演化链路**：`cli._backend` 直接实例化 `OpenAIChat`(temperature=0) 包进 `MemoryToolkit`；沙箱内候选程序不持客户端，经 JSON-RPC 由宿主 `SandboxSession._handle_tool_call` 代发 `llm_completion/embed`（受每操作预算约束）
- **在线 M★ 调用**：`MemoryHarnessAdapter` = 评审者 backend + 冻结 BGE-M3 embedding + 沙箱：写侧 `TaskAgent.extract_knowledge_item`（一次 LLM JSON 抽取）→ 沙箱 `write`；读侧 `generate_query` → 沙箱 `read`（≤3000 字符）

---

## 关键设计要点

1. **不可变性贯穿全库**：`REVIEW_Request.md` 的 SHA-256 在角色定义、演化 config、筛选 design lock 三处强制校验；晋升程序/缓存/bundle 全部 checksum 溯源
2. **六条独立演化血脉**：QUAL/CLAR/SIGN/ORIG/POL/META 各有独立种群、lineage 与 `best_program.py`，互不共享反馈
3. **防泄漏**：work-family 传递并查集切分、out-of-fold META 输入、密封测试只在晋升后评估一次
4. **沙箱隔离**：候选代码永不在宿主执行——bubblewrap + 清环境 + rlimit + AST import 白名单 + 每次读/写至多 1 次 LLM 调用
5. **遗留双轨**：`Arena`/`PaperReview`/`PaperDecision` 是旧版多论文模拟路径；当前主路径（`run_single_paper_review_cli.py`）只用其中的 `Player`/`Reviewer`/`Message`/backend 基础设施，手工编排「提取 → 五专科并行 → META」
