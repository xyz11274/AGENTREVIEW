## 0. 一句话定位

这是一个**"五专家审稿 + Meta 汇总"的多智能体审稿系统**，其核心研究点是 **M★(M-star)记忆自进化**：保持审稿 rubric、拓扑、Task Agent、评估器完全冻结，只让每个审稿角色各自进化一个**可执行记忆程序（MemoryProgram = Schema + write/read Logic + Instructions)**，通过离线进化搜索找到最优的"记什么、怎么存、怎么取、怎么注入"策略。

系统因此天然分为**两个平面**:

```
┌─────────────────────────── 离线平面（进化，改代码）───────────────────────────┐
│ OpenReview 数据 → prepare-specialists → evolve-specialists (5任务×5种子)      │
│                → cache-meta-inputs → prepare-meta → evolve-meta              │
│                → final-evaluate-suite（封存测试，只跑一次）                    │
│  产物：runs/<suite>/{QUAL,CLAR,SIGN,ORIG,POL,META}/best_program.py           │
└──────────────────────────────────────────────────────────────────────────────┘
                                    │ 冻结晋升
┌─────────────────────────── 在线平面（审稿，不改代码）─────────────────────────┐
│ 论文 Markdown → Paper Extractor（锚点+S2检索）→ 5 个 specialist 并行评审      │
│              → META 汇总 → reviews.json / 追加在线 memory_library.jsonl       │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. 仓库布局

工作区根为 [AGENTREVIEW/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/)，代码主体在 [AgentReview/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/):

| 路径                                                                                                                                                                                                                                                                      | 角色                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| [agentreview/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/)                                                                                                                                                 | 核心运行时框架（ChatArena 血统）+ 审稿业务角色 + 在线记忆库    |
| [memory_evolution/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/)                                                                                                                                       | M★ 离线进化引擎（搜索、沙箱、反射变异、评估、数据准备）       |
| [effectiveness_screening/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/effectiveness_screening/)                                                                                                                         | 冻结三臂有效性筛选实验（CoCo 风格 proxy)                       |
| [evolution_viewer/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/evolution_viewer/)                                                                                                                                       | 进化过程审计台（Gradio,端口 7862)                              |
| [viewer.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/viewer.py) + [viewer_data.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/viewer_data.py)         | 审稿结果查看器（Gradio，端口 7861)                             |
| [run_single_paper_review_cli.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/run_single_paper_review_cli.py)                                                                                                             | **在线审稿主入口**(1970 行）                             |
| [REVIEW_Request.md](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/REVIEW_Request.md)                                                                                                                                       | **唯一 rubric 来源**:5 维度 23 子类，运行时 SHA-256 校验 |
| [AGENTS.md](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/AGENTS.md) / [docs/memory_evolution.md](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/docs/memory_evolution.md) | 架构契约与复现协议（具有规范效力）                             |
| [runs/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/runs/)                                                                                                                                                               | 进化运行产物（pool/scores/lineage/traces/best_program)         |
| [data/effectiveness_screening_v1/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/data/effectiveness_screening_v1/)                                                                                                         | 筛选实验的冻结输入（bundles、程序、审计）                      |
| [tests/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/tests/)                                                                                                                                                             | 单元测试（约 20 个测试文件）                                   |
| `outputs_baseline/ outputs_md/ outputs_mstar/`(AGENTREVIEW 根下）                                                                                                                                                                                                       | 三种模式的审稿输出                                             |

---

## 2. 核心运行时框架 [agentreview/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/)

该包源自 ChatArena 式多智能体框架，采用经典的**六层结构**,所有组件可配置序列化：

### 2.1 配置层 — [config.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/config.py)

`Config`（基于 `AttributedDict`)→ 派生 `EnvironmentConfig` / `BackendConfig` / `AgentConfig` / `ArenaConfig`;`Configurable` mixin 提供 `from_config()` / `to_config()` 双向序列化。**整个 Arena 状态可以从 JSON 配置往返重建**，这是依赖注入的核心机制。

### 2.2 后端层 — [backends/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/backends/)

```
IntelligenceBackend (base.py:10)  — query()/async_query()/reset()/to_config()
 ├─ OpenAIChat (openai.py, 主力，支持 gpt-5/o 系 reasoning_effort、限速、重试、空响应补偿、<think>剥离)
 ├─ Claude / CohereAIChat / Bard / TransformersConversational / LangChainOpenAIChat
 ├─ Human（人工输入）└─ Dummy（用于不调用 LLM 的 Paper Extractor)
```

注册表模式：`BACKEND_REGISTRY` + `load_backend(config)` 按 `backend_type` 分发。鉴权经 [authentication_utils.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/utility/authentication_utils.py) 从 `OPENAI_API_KEY/OPENAI_API_BASE/AZURE_*` 读环境变量。

### 2.3 消息层 — [message.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/message.py) / [paper_review_message.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/paper_review_message.py)

`Message`（带 `visible_to`、`msg_type`、SHA-256 `msg_hash`)+ `MessagePool`（按轮次和可见性过滤）。`PaperReviewMessagePool` 实现**阶段感知可见性**——环境状态与智能体感知彻底分离。

### 2.4 智能体层 — [agent.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/agent.py) / [paper_review_player.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/paper_review_player.py) / [paper_review_player_md.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/paper_review_player_md.py)

```
Agent(抽象) → Player(act=backend.query) → Moderator（终止条件判断）
                                        → AreaChair / Reviewer（旧会议流)
                                        → PaperExtractorPlayer（PDF)
                                        → MarkdownPaperExtractorPlayer（新流：MD+锚点+S2检索+冻结bundle校验)
```

### 2.5 环境层 — [environments/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/environments/)

| 环境                                               | type_name                                       | 用途                                                                          |
| -------------------------------------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------- |
| `Conversation` / `ModeratedConversation`     | `conversation` / `moderated_conversation` | 通用轮流对话                                                                  |
| `PaperReview` (paper_review.py:17)              | `paper_review`                                | **旧会议流**:5 阶段（提取→写评审→作者rebuttal→评审-AC讨论→AC写meta) |
| `PaperDecision` (paper_decision.py:13)          | `paper_decision`                              | 旧流第 5 阶段：AC 做录用决策                                                  |
| `SinglePaperReview` (single_paper_review.py:13) | `single_paper_review`                         | **新 M★ 流**:7 阶段（提取→QUAL→CLAR→SIGN→ORIG→POL→META)          |

### 2.6 编排层 — [arena.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/arena.py) / [paper_review_arena.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/paper_review_arena.py)

`Arena.step()` 循环：`get_next_player → get_observation → player(obs) → env.step`，非法动作重试 5 次。`PaperReviewArena` 按**名字前缀分发玩家类型**("Paper Extractor"/"AC"/"Reviewer")，并在阶段切换时动态改写 reviewer 的 `role_desc`。

### 2.7 关键观察：双范式共存

包内同时存在两套审稿业务：① 旧版会议模拟流（3 reviewer + AC + author + rebuttal,`role_descriptions.py`、`experiment_config.py` 的各种人设实验）;② 新版单篇专家流（M★ 用的 5 专家 + META)。两者共享底层框架，但 M★ 只与后者耦合。

---

## 3. 审稿业务：冻结 rubric 与五专家拓扑

[single_paper_review_roles.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/single_paper_review_roles.py) 是业务核心：

- **REVIEWER_SPECS**(:16):QUAL（技术质量）/CLAR（清晰度）/SIGN（重要性）/ORIG（原创性）/POL（政策合规），各带 focus 描述。
- **不可变锚定**(:45-62)：运行时对 `REVIEW_Request.md` 做 SHA-256 校验（`609eb930...be42`)，不符即 `RuntimeError`；再按正则 `^([1-5])\. ([A-Z]+)` 切出五个 rubric 段落。**rubric 文本只允许这一个来源，别处不得复制**。
- **结构化输出契约**(:83-108)：每个 specialist 必须产出 dimension score、confidence、以及原子化 Opinions(subcategory/polarity/claim/**evidence_anchors**/confidence/actionable_suggestion)，证据锚点只允许 `SRC-<digits>(page=<digits>)` 且必须逐字复制自输入的 SOURCE ANCHORS 列表——这是可验证性（grounding/verifiability）评估的基础。
- META 描述（:131）要求显式调解五方分歧、"比平均更严格"、输出 Synthesis/Final score/Final recommendation。

配套：[source_map.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/source_map.py) 从 `content_list.json` 生成最多 80 个 `SRC-NNN` 锚点；[paper_review_player_md.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/paper_review_player_md.py) 读 Markdown + 调 Semantic Scholar 检索相关工作，支持 `--frozen_context_bundle_path` 的 SHA-256 冻结输入包。

---

## 4. 记忆系统（两层）

### 4.1 在线记忆库 [memory_library.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/memory_library.py)

纯 Python 的 JSONL 存储 + JSON 索引：

- 产物：`<output_dir>/single_paper_review/memory_library.jsonl` + `memory_index.json`(by_sample_id / by_title_normalized / by_reviewer)
- `update_memory_library()`(:70)：审稿完成后追加记录（按 sample_id 去重）
- `retrieve_memory_context()`(:173):**token 重叠打分**检索（标题命中 +0.25、同 reviewer +0.2、同维度 +0.1)，渲染成 `=== REVIEW MEMORY CONTEXT ===` 文本块注入 prompt

这是 `memory_mode=existing`（默认）使用的简单基线记忆。

### 4.2 M★ 可执行记忆程序（进化的对象）

契约定义在 [program_protocol.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/program_protocol.py)（仅 38 行）——每个候选程序是一个导出 7 个符号的 Python 模块：

```python
INSTRUCTION_KNOWLEDGE_ITEM / INSTRUCTION_QUERY / INSTRUCTION_RESPONSE / ALWAYS_ON_KNOWLEDGE  # 4 段可进化 prompt
@dataclass KnowledgeItem      # 可进化 Schema:存什么
@dataclass Query              # 可进化 Schema:查什么
class KnowledgeBase:          # 可进化 Logic
    def __init__(self, toolkit): ...
    def write(self, item, raw_text) -> None      # 写入逻辑
    def read(self, query) -> str                 # 检索逻辑,必须 ≤3000 字符
```

三个不可变的初始种子 [seeds/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/seeds/):**vector_search**（分块嵌入+维度过滤）、**llm_summarizer**（全文按维度存储，读时 1 次 LLM 调用做查询聚焦摘要）、**experience_learner**（结构化 lesson/fact 抽取+维度桶检索，零 LLM 调用）。

---

## 5. M★ 离线进化引擎 [memory_evolution/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/)

### 5.1 CLI 编排 — [cli.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/cli.py)

9 个子命令构成严格流水线：`prepare-specialists → evolve-specialists → cache-meta-inputs → prepare-meta → evolve-meta → prepare-test → final-evaluate-suite`（另有 `evolve-task`、`evaluate` 用于单任务/消融）。正式配置硬编码为：20 迭代、static 25、rotating 5、温度 0.15、3 次编译修复、5 个进化种子（seed 0 为预注册主种子）。

### 5.2 进化搜索 — [search.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/search.py)（核心，645 行）

`run_evolution()` 四阶段：

1. **Setup**：验证集经 BGE-M3 嵌入 k-means 聚类选 25 条固定 static 子集；训练 episodes 用**贪心 facility-location** 选取（预算 = 5×static);
2. **Seeds**：三个种子程序质检→静态评估→入池；
3. **迭代循环**(×20):`softmax(static_score/0.15)` 选父 → 父在 5 条 rotating 样本上跑 → 构造 `EvalFeedback`（至多 2 个加权失败 + 2 个成功案例，**绝不包含 static 内容**)→ Reflector 变异 → 质检 + ≤3 次编译修复 → 子在 static 全集评估 → 入池并记录 lineage;
4. **晋升**:Top-3 候选各重复评估 3 次，按均值最高、方差最低选胜，复制为 `best_program.py`。

### 5.3 Reflector 变异 — [reflector.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/reflector.py)

LLM 驱动的代码变异器：输入父程序全文 + 反馈 + lineage + 池中 best/worst/median 三个参考程序（仅代码与分数）,**输出 V4A 上下文补丁**(`*** Begin Patch`/`@@` hunk，每块必须唯一匹配）。`mutation_scope` 支持 `full`/`schema_logic`/`instructions_only` 三种消融，用 AST dump 对比强制约束。`compile_fix_loop()` 做最小改动修复。

### 5.4 沙箱 — [sandbox.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/sandbox.py) + [sandbox_worker.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/sandbox_worker.py)

安全设计的重心：**候选代码绝不在宿主进程执行**。

- bubblewrap `--unshare-all --clearenv --die-with-parent`，只读挂载运行时，无网络，tmpfs `/tmp`;
- `RLIMIT_AS=1GB / RLIMIT_CPU=60s / RLIMIT_NOFILE=64`;
- Python `-I -S` 隔离模式；worker 内剥离 `open/eval/exec/compile/globals...` 内建，导入白名单（json/re/sqlite3/chromadb 等约 15 个）;
- **能力通道**：父子间 JSON-RPC(stdin/stdout)。候选程序只能通过 `CapabilityToolkit` 拿到：受限 LLM 调用（每次 write/read 至多 1 次）、embed、内存 SQLite(`:memory:`)、临时 Chroma 向量库、日志——**API key 和宿主文件系统对候选不可见**。

### 5.5 Task Agent 与评估

- [task_agent.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/task_agent.py)：固定不动的执行壳，三个方法（`extract_knowledge_item`/`generate_query`/`generate_response`）的行为**只受程序的 INSTRUCTION_* 影响**;system prompt 直接取自冻结的 reviewer role 描述。
- [evaluator.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/evaluator.py):write 阶段（episode→知识条目→`session.write`)+ read 阶段（question→query→`session.read`→≤3000 字校验→生成回答→打分）。
- [metrics.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/metrics.py):specialist 适应度 = **调和平均（5 维 judge 质量分， 类别召回率）**；类别由冻结的 ReviewSplit/ReviewClassify 分类器从候选文本独立提取（**忽略候选自报标签**，防止标签灌水），召回率 = |AI∩人工|/|人工|。META 适应度 = 三组（裁决/参考对齐/决策校准）均值再取调和平均。
- [quality.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/quality.py)：三层质检——AST 静态检查（符号、字段类型、导入/调用黑名单）→ 沙箱编译检查（7 符号、dataclass 约束）→ 冒烟测试（write/read 各一次，read ≤3000 字）。

### 5.6 数据隔离 — [agentreview_data.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/agentreview_data.py)

防泄漏是整个实验协议的骨架：

- **work family 并查集**(:48)：共享 paperhash 或规范化标题的记录并为一个 family,train/validation/test 严格 family 不相交；
- `prepare-specialists`：剔除所有与 test.jsonl 同族的训练记录，切出 train / specialist_validation(250 族）/ **meta_validation(250 族，specialist 进化永远看不到）**;
- 冻结 Semantic Scholar 缓存并记录 SHA-256,manifest 无哈希则拒绝正式运行；
- [meta_cache.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/meta_cache.py):META 的训练输入用 **5 折 out-of-fold** 生成——一篇论文的 specialist 输出绝不用包含其人类答案的记忆来产生；
- 验证问题只含论文与锚点，候选与 Task Agent 看不到 rebuttal/meta-review/decision/参考答案。

### 5.7 外部适配器（可替换、带版本钉死）

| 适配器                                                                                                                                                     | 作用                                                                   | 钉死方式                                |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------------- |
| [coco_adapter.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/coco_adapter.py)           | CoCoReviewBench 的 ReviewSplit(:13099)/ReviewClassify(:13098)vLLM 服务 | commit`2580687e...`                   |
| [embedding_adapter.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/embedding_adapter.py) | BGE-M3 嵌入（k-means/facility-location)                                | `--embedding-revision` 不可变 commit |
| [api_preflight.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/memory_evolution/api_preflight.py)         | 进化前检查 API 端点 DNS/TLS/鉴权                                       | —                                      |

---

## 6. 在线审稿主入口 [run_single_paper_review_cli.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/run_single_paper_review_cli.py)

1970 行，是"把进化产物用起来"的地方。`run_one_paper()`(:1685）流水线：

1. **发现论文**(:154)：四级优先级——冻结 bundle > context manifest > 单文件 > 目录 glob;
2. **提取**:`MarkdownPaperExtractorPlayer` 产出正文 + 锚点 + S2 相关工作；
3. **构建记忆上下文**(:1072)：三种模式——`none` / `existing`(memory_library 关键词检索）/ `mstar_program`（按维度加载 6 个 `best_program.py`，经 `MemoryHarnessAdapter` 在沙箱中 ingest 记忆库并检索；支持预编译 KnowledgeItem 缓存 `--memory_knowledge_cache_dir`);
4. **5 专家并行**(:1763):`ThreadPoolExecutor`,`--reviewer_workers=5`，每个专家收到裁剪后的论文 + "Memory Librarian" 记忆消息；`call_role_with_anchor_repair()`(:1151）校验锚点合法性并可要求修复一次；增量写 `specialist_checkpoint.json`（断点续跑）;
5. **META 汇总**(:1794)：唯一能看到全部五份评审的节点；
6. **持久化**:`reviews.json / memory.json / review_history.json / timing.json` + 输出校验 + 追加全局 `memory_library.jsonl`（除非 `--disable_memory_writeback`)。

并发模型：**论文级串行、专家级并行、META 最后**。

---

## 7. 有效性筛选 [effectiveness_screening/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/effectiveness_screening/)

进化之后的独立验证层，回答"进化真的有用吗"。设计由 [configs/effectiveness_screening_v1.json](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/configs/effectiveness_screening_v1.json) 冻结（`design_lock.json` 记录全部代码/输入/模型哈希，改动即拒绝覆盖）:

- **三臂 × 2 重复 = 6 条件 × 20 篇 = 120 份评审**:`no_memory` vs `seed_experience`（未进化种子）vs `evolved`（进化最优）；分层抽样（ICLR/NeurIPS × accept/reject 各 5 篇，含 10 篇争议稿）;
- POL 维度 seed 与 evolved 字节相同（强制校验），明确不计入进化收益——诚实性设计；
- 15 步流水线：抽样（5 条泄漏不变量必须为 0)→ S2 检索冻结 → bundle 构建 → 程序冻结 → 设计锁 → 历史记忆库生成 → 记忆库冻结 → 双臂知识缓存预编译 → 预注册矩阵 → 批量运行 → 审计 → CoCo 分类 → judge 打分 → 盲评对（SHA-256 定向，key 密封）→ 汇总；
- [summarize.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/effectiveness_screening/summarize.py) 的**筛选门**：`ΔCoCo_Average ≥ 0.1`、≥12/20 篇为正、双重复均为正、bootstrap CI 下界 > 0、盲评胜率 ≥ 0.6 等——过了才考虑跑 3900 篇的完整 CoCoReviewBench。

---

## 8. 查看器与辅助

- [viewer.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/viewer.py)(7861)：审稿结果只读查看器，7 个标签页；[viewer_data.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/viewer_data.py) **刻意只用标准库**，与评审生成代码完全隔离，带路径穿越防护和 50MB 限制；
- [evolution_viewer/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/evolution_viewer/)(7862):"M★ 程序转变审计台"，逐轮查看每次变异的 Prompt/Schema/write/read diff（词级）、诊断 traces、以及任意两程序间累计 A/B 对比（AST 级分区对比）;
- [openscholar_bridge/](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/openscholar_bridge/):**休眠代码**——整个 Flask API 与 OpenScholar 检索/重排/生成管线被三引号注释掉，主流程实际走的是 [s2_retrieval.py](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/agentreview/utility/s2_retrieval.py) 的直连 Semantic Scholar 调用。

---

## 9. 横切设计模式与不变量

1. **万物可配置**(Configurable/`from_config`):backend、player、environment、arena 全部可序列化重建；
2. **消息传递 + 阶段可见性**：环境持有真相，玩家只看到 `get_observation()` 过滤后的子集；
3. **哈希钉死一切**:`REVIEW_Request.md`、S2 缓存、paper bundle、design lock、晋升程序——任何漂移都在运行时爆炸而非静默改变实验；
4. **不可变/可进化边界清晰**:rubric、拓扑、Task Agent、评估器、种子数、默认超参全部冻结；进化的只有 MemoryProgram 的 Schema/Logic/Instructions;
5. **安全纵深**:AST 黑名单 → bubblewrap 进程隔离 → 资源限额 → 内建剥离 → 能力通道（LLM/embed 全部代理）;
6. **泄漏防控**:family 并查集、meta_validation 独立预留、out-of-fold META 输入、static 内容不进 Reflector、测试集只在晋升后跑一次且报告不可覆盖；
7. **六池独立**:QUAL/CLAR/SIGN/ORIG/POL/META 各自的数据、种群、分数、lineage、胜者，跨维度零共享。

## 10. 值得注意的工程现实（非批评，供上手参考）

- **新旧两套评审流并存**，旧流（PaperReview/PaperDecision/rebuttal）与新 M★ 流共享框架但业务无关，读代码时容易混淆；
- `openscholar_bridge` 是死代码，真正的检索在 `s2_retrieval.py`;
- 在线侧论文级是串行的，大批量跑靠多次启动或外部并行；
- 正式实验依赖外部服务：OpenAI 兼容 API、S2 API、ReviewSplit/ReviewClassify 两个 vLLM 服务（13099/13098)、BGE-M3 本地模型；
- 脚本 [pilot_full.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/pilot_full.sh)（完整 pilot)、[run.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/run.sh)（在线 mstar 审稿）、[QUAL_test.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/QUAL_test.sh) / [end2end_smoke.sh](vscode-webview://03g1k5je2q5mtu004qc7uh87tejeht7ildogphm5fg3uhiest648/AGENTREVIEW/AgentReview/end2end_smoke.sh)(smoke）是最快的上手路径；注意 `QUAL_test.sh`/`end2end_smoke.sh` 内**硬编码了 API key**，外发前需清理。

总体评价：这是一个**研究协议驱动**的代码库——工程结构（框架分层、沙箱、哈希冻结、泄漏隔离）全部服务于一个目的：让"记忆程序自进化是否提升审稿质量"这个问题可以被严格、可复现地回答。
