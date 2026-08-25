# AgentReview_0811 代码库完整梳理

> 分析范围：`/data/xyj/shengao/AgentReview_0811`  
> 主要可运行项目根：`/data/xyj/shengao/AgentReview_0811/AgentReview`  
> 分析日期：2026-08-24（Asia/Shanghai）  
> 稳定提交基线：`cc1007f6768525b04341b9131447843b7699ed00`  
> 方法：静态代码、配置、数据 manifest、测试和 Git 产物只读审查；未运行会产生缓存、输出或网络调用的命令。

## 0. 阅读说明与结论摘要

除特别写出绝对路径外，本文中的源码路径均相对于内部项目根 `AgentReview/`；以 `../docs/...` 开头的路径位于外层仓库根。行号按文档生成前的当前工作树复核。

分析期间代码库被外部进程持续更新：起始状态是 `HEAD=4470af2` 且存在既有未提交改动，随后外部生成提交 `cc1007f` 并一度清空工作树；收尾时又有以下 4 个外部未提交修改：

- `agentreview/utility/authentication_utils.py`
- `memory_evolution/coco_adapter.py`
- `tests/test_coco_adapter.py`
- `tests/test_local_openai_runtime.py`

这些变化不是本次分析产生的。本文以 `cc1007f` 作为稳定基线，同时把上述工作树版本中已经确认的行为（例如 loopback endpoint 禁用代理继承）标为“当前工作树行为”。这 4 个文件后续若继续变化，其行号可能轻微漂移。

### 核心结论

1. 这是一个面向学术论文审稿的 Python 研究型模块化单体。固定运行拓扑是：

   ```text
   Paper
     ├─ QUAL：方法与实验质量
     ├─ CLAR：表达清晰度
     ├─ SIGN：意义与影响
     ├─ ORIG：原创性
     └─ POL：伦理、政策与可复现性
            ↓ 五份结果仅在这里汇合
          META：最终综合评审
   ```

   五个 specialist 彼此不可见，只有 META 看见五份完整输出。证据见 `README.md:3-20`、`agentreview/single_paper_review_roles.py:16-44`、`run_single_paper_review_cli.py:1287-1316,1830-1888`。

2. 项目包含两条强关联主流水线：

   - 离线 M★：为 `QUAL/CLAR/SIGN/ORIG/POL/META` 分别演化可执行的 MemoryProgram；
   - 在线审稿：加载 6 个已晋升 MemoryProgram，运行五 specialist + META，并回写审稿记忆。

   Formal final evaluation、effectiveness screening、两个 Gradio viewer 是外围能力。

3. M★ 只允许改变 MemoryProgram 的 schema、write/read logic 和 instructions；Reviewer rubric、Task Agent、模型权重、评测器和拓扑保持固定。证据见 `README.md:18-20`、`memory_evolution/program_protocol.py:7-37`、`memory_evolution/tasks.py:129-139`。

4. 数据面采用 A/B/C/D 四区时序隔离：A=2391 historical experience、B=299 evolution validation、C=300 effectiveness holdout、D=899 held-out test。当前 v2 manifest 的 family/title 跨区审计通过，见 `configs/coco_temporal_split_v2/manifest.json:2-16,318-345,6507-6522`。

5. “资产就绪”不等于“正式实验完成”。`configs/asset_manifest_v2.json:115-117` 的 `formal_ready=true` 只覆盖 pre-QUAL 资产和 A/B task bundles；`configs/formal_temporal_v3_qwen3e8b.json:32-44` 仍要求重新生成 selection/cache/readiness 资产，当前也未发现完整 v3 六任务 suite 和 final report。

6. 当前最紧急的问题是：本地忽略配置中存在 credential-shaped 非占位凭据、配置加载使用 `eval()`、effectiveness v2 处理 lineage 时发生文件覆盖/命名断裂、低层 `evaluate` 的“formal”分支没有把 formal 语义传进 evaluator，以及 Git 忽略规则造成关键审计 JSON 不入库而二进制 cache/敏感 trace 入库。详见第 9 节。

---

## 1. 项目整体目标和架构

### 1.1 项目目标

项目的直接目标是：给定 Markdown 论文或冻结的 paper-context bundle，让五个固定维度的 specialist 独立形成可定位到原文 source anchor 的审稿，再由 META 汇总冲突、权衡意见并生成最终结论。同时，通过 M★ 离线搜索演化每个角色独立的“记忆程序”，提升历史经验的写入、检索和利用方式，而不改变审稿任务本身。

关键约束：

| 约束 | 实现位置 |
|---|---|
| 五角色 rubric 唯一来源是 `REVIEW_Request.md` | `REVIEW_Request.md:1-97` |
| 运行时校验 rubric 固定 SHA-256 | `agentreview/single_paper_review_roles.py:45-80` |
| 六个独立 M★ task | `memory_evolution/tasks.py:16-18` |
| 候选必须实现固定 MemoryProgram 协议 | `memory_evolution/program_protocol.py:7-37` |
| specialist 相互隔离，META 后置 | `run_single_paper_review_cli.py:1287-1316,1830-1888` |
| candidate 不在 host 进程执行 | `memory_evolution/sandbox.py:75-192`、`memory_evolution/sandbox_worker.py:316-374` |
| static validation 不进入 Reflector；rotating feedback 去除题目/gold/回答文本 | `memory_evolution/search.py:1038-1071`、`memory_evolution/evaluator.py:15-45`、`memory_evolution/reflector.py:61-120,246-275` |
| Test 仅在 promotion 后经唯一 final suite 入口使用 | `memory_evolution/cli.py:1296-1569,1725-1734` |

### 1.2 架构形态

该项目不是常驻 Web 服务，也不是微服务集群。更准确的描述是“研究型模块化单体 + 多个批处理 CLI + 本地模型/分类服务依赖”。内部主要分成四层：

```text
┌──────────────────────────────────────────────────────────────┐
│ 编排层                                                       │
│ run_single_paper_review_cli.py / memory_evolution.cli        │
├──────────────────────────────────────────────────────────────┤
│ 领域与在线运行层                                             │
│ agentreview: roles, Player, backend, paper context, memory   │
├──────────────────────────────────────────────────────────────┤
│ M★ 演化与评测层                                              │
│ memory_evolution: protocol, search, sandbox, evaluator, gate │
├──────────────────────────────────────────────────────────────┤
│ 资产与外部能力层                                             │
│ temporal manifests / frozen S2 / Qwen embedding / CoCo       │
│ OpenAI-compatible generation / ReviewSplit / ReviewClassify  │
└──────────────────────────────────────────────────────────────┘
```

外围模块：

- `effectiveness_screening/`：晋升后 C 区三臂、多 lineage、两 replicate 的 proxy screen；
- `viewer.py`：在线审稿结果 viewer；
- `evolution_viewer/`：搜索 lineage、score、trace、代码 diff viewer；
- `docs/` 与外层 `docs/adr/`：协议、环境和决策记录。

### 1.3 两条主流水线的衔接

```text
                  离线 M★ 演化
 A/B 数据 + 冻结资产 + 3 seeds
              │
              ▼
  5 specialist 独立搜索并晋升
              │
              ▼
  OOF specialist outputs → META 搜索并晋升
              │
              ▼
 runs/<suite>/{QUAL,CLAR,SIGN,ORIG,POL,META}/best_program.py
              │
              ├───────────────┐
              ▼               ▼
        在线论文审稿       D 区 final suite
              │
              ▼
 reviews/memory/history/timing + global memory library
```

离线与在线共享角色 prompt、canonical paper context、Task Agent 语义、MemoryProgram 协议和 embedding identity；在线入口通过 `verify_promoted_program()` 再次校验晋升程序 checksum 与 embedding provenance，见 `memory_evolution/tasks.py:79-126`。

---

## 2. 目录结构与各模块职责

### 2.1 外层仓库

```text
AgentReview_0811/
├── AgentReview/                         # 实际 Python 项目根
├── docs/adr/                            # 架构决策记录
├── docs/                                # 文档索引、TODO、deprecated 注册表、审计
├── outputs_baseline/                    # 历史 baseline 输出
├── outputs_md/                          # 历史 Markdown 审稿输出
├── outputs_mstar/                       # 历史 M★ 输出
├── CONTEXT.md                           # 术语和实验主张
├── AgentReview M★ 代码架构全景解析.md   # 既有架构材料
├── PPT_PROJECT_CONTEXT.md / *.pptx      # 汇报材料
└── build_pptx.py                        # 汇报文稿生成脚本
```

外层 `docs/DOCUMENT_INDEX.md:17-61` 已主动标记权威、局部滞后和废弃文档，但该索引自己的基线是旧提交 `7256ffc`（`:1-5`），仍需结合当前代码判断。

### 2.2 内部项目根 `AgentReview/`

| 路径 | 当前职责 | 关键证据/备注 |
|---|---|---|
| `agentreview/` | 在线审稿领域层：角色、消息、模型 backend、paper context、source anchor、memory library | 固定拓扑索引 `AGENTS.md:11-23` |
| `agentreview/backends/` | 模型 provider adapter；当前主链使用 OpenAI-compatible backend | registry `agentreview/backends/__init__.py:12-32`；主实现 `openai.py:102-547` |
| `agentreview/environments/`、`arena.py`、`paper_review_arena.py` | 旧 Arena/作者 rebuttal/Area Chair 编排 | 已登记为 deprecated，`../docs/DEPRECATED.md:7-20` |
| `agentreview/openscholar_bridge/` | 旧 OpenScholar 集成；少量 S2 路径仍可达 | `../docs/DEPRECATED.md:32-36,72-74` |
| `memory_evolution/` | M★ 协议、数据准备、search、Reflector、质量门、sandbox、evaluator、formal gate、模型/embedding/CoCo adapter | `AGENTS.md:25-36` |
| `memory_evolution/seeds/` | 三个固定 seed：vector search、LLM summarizer、experience learner | `memory_evolution/search.py:723-805` |
| `effectiveness_screening/` | C 区 proxy screen 的准备、冻结、矩阵、分类/judge、blind pair、汇总 | `prepare.py`、`memory_cache.py`、`run_matrix.py`、`evaluate_proxy.py`、`summarize.py` |
| `evolution_viewer/` | 只读加载 search artifacts，展示 lineage、score、trace、Program 源码/diff | `evolution_viewer/data_loader.py:165-172,387-466` |
| `configs/` | asset manifest、时序 split、冻结 S2、formal identity、screening 配置 | 第 7 节详列 |
| `data/` | A/B task bundles、历史 pilot、effectiveness 数据等生成资产 | canonical formal 根见 `data/evolution_coco_v2_20260819/manifest.json:91-106` |
| `runs/` | M★ pool、score、lineage、trace、checkpoint、promotion 产物 | 规范布局见 `docs/memory_evolution.md:147-160` |
| `cache/` | selection/runtime embedding SQLite cache 与 sidecar audit | `memory_evolution/embedding_adapter.py:19-39,55-496` |
| `scripts/` | v2 temporal split、Frozen S2、embedding prewarm、formal readiness 等维护脚本 | `configs/asset_manifest_v2.json:39-49` 还固定了 split generator SHA |
| `tests/` | 主链、协议、formal boundary、search、embedding、CoCo、viewer-data 测试 | 当前 23 个 `test_*.py` |
| `evolution_viewer/tests/` | viewer loader、AST/code analysis、render 测试 | 当前 3 个 `test_*.py` |
| `test_data/`、`papers_eval20/` | 本地样例和历史评测输入 | 数据，不是文档 |
| `outputs/`、`outputs_eval20/` | 在线/历史审稿输出 | 运行产物 |
| `docs/` | M★ 复现契约、环境、effectiveness 和本地模型说明 | `docs/memory_evolution.md` 是核心协议文档 |

### 2.3 `agentreview/` 内部职责

| 模块 | 职责 |
|---|---|
| `single_paper_review_roles.py` | 五 specialist 与 META 的固定身份、rubric SHA、prompt/output schema |
| `paper_review_player_md.py` | Markdown/frozen bundle → canonical paper context |
| `paper_context.py` | candidate-visible metadata 白名单、上下文格式、裁剪与 anchor |
| `source_map.py` | 从 `content_list.json` 或 Markdown 建立 `SRC-*` source map |
| `agent.py`、`message.py` | `Player` 调用语义与角色消息模型 |
| `backends/openai.py` | OpenAI-compatible chat、节流、timeout、retry、token 日志 |
| `utility/authentication_utils.py` | endpoint/key/client 解析；当前工作树增加 loopback 代理隔离 |
| `memory_library.py` | legacy 词法检索和在线审稿记忆库更新 |
| `viewer_data.py` | 在线审稿 viewer 的磁盘数据解析 |
| `config.py` | 旧通用 Config 类型；仍可被 Player/backend 配置使用，含需要修复的 `eval()` |
| `arena.py`、`environments/*`、`database.py` | 被当前主 CLI 绕过的旧框架 |

### 2.4 `memory_evolution/` 内部职责

| 模块 | 职责 |
|---|---|
| `cli.py` | prepare/evolve/cache/evaluate/final-suite 总入口与 formal runtime gate |
| `agentreview_data.py` | A/B specialist、META、D test 数据构建及 human-reference leak audit |
| `models.py` | `Episode`、`EvalItem`、`EvalResult` |
| `program_protocol.py` | 候选 MemoryProgram 固定协议 |
| `tasks.py` | 六任务定义、程序定位、晋升 manifest/checksum 校验 |
| `task_agent.py` | 固定 rubric + candidate instructions 的结构化 LLM wrapper |
| `toolkit.py` | LLM、embedding、vector、SQLite、S2、classifier 能力和资源计数 |
| `embedding_adapter.py` | Qwen3 embedding 与 SQLite cache/audit |
| `search.py` | population、lineage、subset selection、mutation、admission、winner selection |
| `reflector.py` | candidate-safe feedback、V4A mutation patch、compile repair |
| `quality.py` | AST/import/协议/compile/smoke 质量门 |
| `sandbox.py`、`sandbox_worker.py` | bubblewrap 子进程和 JSON-RPC capability bridge |
| `evaluator.py` | A 区 review-then-write、B 区 read/evaluate、checkpoint、trace |
| `metrics.py` | specialist/META fitness |
| `benchmark_adapter.py`、`coco_adapter.py` | final benchmark 抽象和固定 CoCo ReviewSplit/Classify adapter |
| `formal_protocol.py`、`assets.py`、`checkpoint.py` | formal 资产、四区、身份和恢复门禁 |
| `meta_cache.py` | specialist 输出到 META 输入的 staged/OOF cache |
| `subset_selection.py` | embedding-based static/facility-location selection |

### 2.5 规模快照

收尾时内部项目约 9,012 个文件，其中 `configs/` 约 7,811 个文件、`data/` 约 176 个文件。磁盘大头为 `data/` 约 1.5 GiB、`cache/` 约 213 MiB、`configs/` 约 107 MiB、`runs/` 约 39 MiB。该分布说明仓库既承载源码，也承载大量实验资产和运行状态；第 9 节会说明其版本治理风险。

---

## 3. 核心入口

| 入口 | 用途 | 代码位置 |
|---|---|---|
| `run_single_paper_review_cli.py` | 当前单篇/批量在线审稿总入口 | `main():2056-2151`；单篇 `run_one_paper():1735-1975` |
| `run.sh` | 带本地 vLLM、M★ 参数的在线审稿 shell 包装 | `run.sh:4-41` |
| `python -m memory_evolution.cli` | 数据准备、六任务演化、开发评测、formal final suite | parser `memory_evolution/cli.py:781-917`；dispatch `:1572-1856` |
| `pilot_full.sh` | specialist → OOF META inputs → META evolution 的非正式小规模链路 | `pilot_full.sh:38-112` |
| `QUAL_test.sh` | unittest + QUAL smoke 包装 | `QUAL_test.sh:1-末尾` |
| `end2end_smoke.sh` | 目前主要覆盖 specialist evolution 的 smoke；名称比行为更宽 | `end2end_smoke.sh:1-末尾` |
| `viewer.py` | 在线 reviews/memory/history viewer | `viewer.py:22-41,698-713` |
| `python -m evolution_viewer` | M★ 搜索轨迹 viewer | `evolution_viewer/__main__.py:1-5`、`evolution_viewer/app.py:710-725` |
| `effectiveness_screening/prepare.py` | 选择 C 区样本、相关工作、冻结 paper bundle | `prepare.py:416-809` |
| `effectiveness_screening/run_matrix.py` | 冻结设计并生成 history/holdout matrix 命令 | `run_matrix.py:138-330,402-441` |
| `effectiveness_screening/evaluate_proxy.py` | 分类、judge、blind pair | `evaluate_proxy.py:97-153,231-384,387-450` |
| `effectiveness_screening/summarize.py` | 计算 proxy delta、bootstrap、gate | `summarize.py:147-295` |

### 3.1 当前主入口与旧 Arena 的关系

`run_single_paper_review_cli.py` 没有调用 `Arena`/`Environment`，而是直接创建 `Player`，自己管理 `ThreadPoolExecutor`、checkpoint、source-anchor 修复、完整性 gate 和持久化。因此当前真实主链是“直接编排”，`agentreview/arena.py` 与 `agentreview/environments/*` 是保留的旧框架，不应作为新成员理解现行行为的入口。证据见 `../docs/DEPRECATED.md:7-20`。

---

## 4. 核心调用链和数据流

## 4.1 在线单论文/批量审稿

### 总调用链

```text
main()
  ├─ parse_args()                         # 注意：解析时会创建输出目录
  ├─ discover_markdown_papers()
  └─ for paper in papers                  # 论文之间顺序执行
       └─ run_one_paper()
            ├─ build_single_paper_players()
            ├─ MarkdownPaperExtractorPlayer.act()
            │    └─ build_canonical_paper_context()
            ├─ build_reviewer_memory_contexts()
            │    ├─ none
            │    ├─ existing → lexical memory_library
            │    └─ mstar_program → 5 独立 Sandbox/MemoryProgram
            ├─ run_parallel_reviews()
            │    └─ 5 × Player → OpenAIChat.query()
            │         └─ source-anchor validate → 最多一次 repair
            ├─ validate_complete_specialist_reviews()
            ├─ META MemoryProgram
            ├─ Final Meta-Reviewer
            ├─ save_structured_outputs()
            └─ update_memory_library()
```

### 4.1.1 输入发现

- `discover_markdown_papers()` 支持单一 Markdown、目录 glob、frozen context manifest 和 checksum 绑定 bundle，见 `run_single_paper_review_cli.py:167-286`。
- `main()` 逐篇顺序调用 `run_one_paper()`；只有同一篇的五个 specialist 并发，论文级没有并行，见 `:2056-2109`。
- 参数解析会立即创建 `visual_dir` 和 `output_dir`，见 `agentreview/arguments.py:335-337`；因此 `parse_args()` 不是纯函数。

### 4.1.2 角色与 backend

- `build_single_paper_players()` 创建 extractor、五 specialist、META，见 `run_single_paper_review_cli.py:94-164`。
- `build_reviewer_agent_configs()` / `build_meta_agent_config()` 固定角色 prompt 与输出格式，见 `agentreview/single_paper_review_roles.py:83-200`。
- `Player` 经 `BackendConfig.backend_type` 从 registry 实例化 backend，见 `agentreview/agent.py:43-99`、`agentreview/backends/__init__.py:12-32`。
- 同步调用链为 `Player.__call__ → Player.act → backend.query`，见 `agentreview/agent.py:108-134`。
- `OpenAIChat.query()` 将 role description、全局 prompt 和可见历史转成 chat messages，再进入 provider request/retry，见 `agentreview/backends/openai.py:352-547`。

### 4.1.3 论文上下文

- `MarkdownPaperExtractorPlayer.act()` 校验 frozen bundle，或读取 Markdown/content list 后构建统一上下文，见 `agentreview/paper_review_player_md.py:88-184`。
- canonical builder 仅允许 `paper_id/title/venue/year/reviewer_role` 等 candidate-visible metadata，拒绝 manifest、split、内部路径等 provenance，见 `agentreview/paper_context.py:23-102`。
- Markdown、metadata 和 `SRC-*` anchor 被统一格式化并按总字符预算裁剪，见 `agentreview/paper_context.py:124-171,183-222`。
- anchor 优先从相邻 `content_list.json` 建立；缺失时才从 Markdown 生成 fallback，见 `agentreview/source_map.py:6-71`、`agentreview/paper_context.py:105-121,194-204`。

### 4.1.4 三种记忆模式

分派入口：`run_single_paper_review_cli.py:1114-1153`。

| 模式 | 数据流 | 代码位置 |
|---|---|---|
| `none` | 不给 reviewer 注入历史记忆 | `:1114-1153` |
| `existing` | 读 `memory_library.jsonl`，按角色、标题/正文 token overlap 选 top-k | `agentreview/memory_library.py:118-246` |
| `mstar_program` | 每个角色加载独立晋升程序，历史 review record → `Episode` → TaskAgent/KnowledgeItem → candidate KB；当前论文问题 → Query → `read()` context | `run_single_paper_review_cli.py:832-988`、`memory_evolution/integration.py:7-68` |

在 M★ 模式中，五个 specialist 的程序、KB 和检索上下文按 dimension 独立；程序 checksum 与 embedding provenance 在使用前验证，见 `run_single_paper_review_cli.py:882-925`、`memory_evolution/tasks.py:79-126`。

### 4.1.5 specialist 并发与 META 后置

- `run_parallel_reviews()` 只选择五个 `Reviewer ...` 角色，见 `run_single_paper_review_cli.py:1250-1284`。
- 每个 specialist 只收到自己的角色化 paper context 和自己的 memory message，见 `:1287-1316`。
- `ThreadPoolExecutor` 并发请求，结果再按固定 `REVIEWER_SPECS` 顺序排列，见 `:1338-1407`。
- 每份输出立刻做 source-anchor 校验；第一次失败只允许一次定向 repair，见 `:1193-1247`。
- 五份 specialist 均完整、合法后才构建 META memory，见 `:1830-1845`。
- META 的 memory query 和最终 observation 都包含五份 specialist 输出，见 `:1059-1086,1846-1888`。

### 4.1.6 在线输出

单论文目录：`<output_dir>/single_paper_review/<sample_id>/`，见 `run_single_paper_review_cli.py:289-297`。

| 文件 | 内容 | 写入位置 |
|---|---|---|
| `review_history.json` | extractor、specialists、META 对话历史和 provenance | `:1899-1930` |
| `reviews.json` | 结构化五 specialist + META 输出 | `:289-373` |
| `memory.json` | 可回写的 memory record | `:289-373` |
| `timing.json` | 阶段耗时、API/seed provenance | `:1941-1956` |
| `_specialist_checkpoint.json` | specialist 级恢复状态；成功后删除 | `:1168-1190,1372-1402,1974` |

全局输出：

- `<output_dir>/single_paper_review/memory_library.jsonl`
- `<output_dir>/single_paper_review/memory_index.json`
- `<output_dir>/single_paper_review/batch_manifest.json`

写入见 `agentreview/memory_library.py:70-111`、`run_single_paper_review_cli.py:1968-1974,2143-2146`。

## 4.2 离线 M★ 演化

### 总数据流

```text
CoCo temporal manifest + OpenReview/CoCo source + frozen S2
  └─ prepare-specialists
       ├─ A episodes.jsonl
       ├─ B validation.jsonl                 # candidate-visible，无 gold
       ├─ queries.jsonl
       └─ evaluator_only/...references.jsonl # host/evaluator-only

每个 task 的 _run_task()
  ├─ formal gate / asset identity
  ├─ candidate-visible B 与 evaluator-only ref 按 ID join
  ├─ task / reflector / judge / embedding toolkits
  └─ run_evolution()
       ├─ static B subset + rotating B subset
       ├─ facility-location A episode subset
       ├─ 3 independent seed lineages
       ├─ SandboxSession
       ├─ A: read prior memory → self-review → write experience
       ├─ B: restore same A snapshot → read → answer → metric
       ├─ candidate-safe rotating feedback
       ├─ Reflector V4A mutation → quality/compile repair
       ├─ static evaluation → child admission
       └─ repeated selection → best_program.py
```

### 4.2.1 数据准备

- `prepare_openreview_data()` 构建五个 specialist 的 A/B/queries/evaluator-only artifacts，见 `memory_evolution/agentreview_data.py:628-1084`。
- A/B allowlist 由 frozen temporal manifest 决定，见 `:660-718`。
- human-reference 禁止字段与 leak audit 见 `:381-508,953-973`。
- candidate artifact 和 evaluator reference 分开写入，见 `:792-1011`。
- META 数据由 `prepare_meta_data()` 在 specialist 冻结后构建，见 `:1087-1224`。
- D 区由 `prepare_held_out_test_data()` 单独物化，见 `:1227-1422`。

### 4.2.2 搜索控制器

- `_run_task()` 加载数据、执行 formal gate、构造 toolkits/metric/`EvolutionConfig` 后调用 `run_evolution()`，见 `memory_evolution/cli.py:925-1263`。
- `run_evolution()` 建立 pool、score、lineage、trace 和 controller checkpoint，见 `memory_evolution/search.py:421-543`。
- static validation 和 A episode subset 的选择逻辑见 `:605-689`；formal 模式不允许随机 fallback。
- 三个 seed lineage 初始化见 `:723-805`。
- rotating eval → candidate-safe feedback → mutation → quality/compile-fix → static eval → admission 见 `:1026-1235`。
- formal 预算公平性检查见 `:1247-1272`；最终 top-k 重复评估，并按 mean 优先、variance 次优选择 winner，见 `:1274-1366`。
- best program、lineage champion 和报告落盘见 `:1344-1439`。

### 4.2.3 candidate sandbox 与 capability

- `SandboxSession` 把 candidate 程序复制到临时目录，通过 bubblewrap 使用清空环境、只读 runtime、独立网络 namespace、CPU/内存/fd 限制启动，见 `memory_evolution/sandbox.py:75-192`。
- candidate 只能通过 host bridge 请求 `llm_completion`、`embed`、`retrieve_s2`、`log`，见 `:239-289`。
- worker 只允许 `:memory:` SQLite、白名单模块，并移除危险 builtins 后执行 candidate，见 `memory_evolution/sandbox_worker.py:316-374`。
- `quality.py` 在运行前做 AST/import/protocol/compile/smoke gate，见 `memory_evolution/quality.py:14-65,111-185,219-360`。

### 4.2.4 evaluator 的状态语义

`evaluate_program()` 的 formal 路径：

1. 校验 experience protocol 和 identity-bound checkpoint，见 `memory_evolution/evaluator.py:110-155`；
2. 对 A episode 执行“读 prefix memory → self-review → 生成 KnowledgeItem → writeback”，见 `:157-250`；
3. 完成 A 后保存 snapshot；每个 B item 前恢复同一 snapshot，避免 B 顺序污染，见 `:252-283`；
4. Query/read/answer 后通过 metric 或 benchmark adapter 评分，见 `:262-344`；
5. 汇总 score、case、trace、resource，见 `:346-391`。

### 4.2.5 晋升到在线运行

- specialist suite 将 primary outer seed 的五个 winner 复制为 `<output>/<TASK>/best_program.py`，并写 suite manifest，见 `memory_evolution/cli.py:1624-1689`。
- META winner 晋升为 `<output>/META/best_program.py`，见 `:1690-1724`。
- 在线运行要求目录中同时存在六个程序，见 `README.md:173-196`。

## 4.3 Formal D/Test 评测

- 唯一正式 D/Test 入口是 `_final_evaluate_suite()`；已有报告时拒绝覆盖，见 `memory_evolution/cli.py:1296-1314`。
- 它先运行五个 frozen specialist，见 `:1384-1455`；再组合五份输出形成 META 输入，见 `:1457-1525`。
- compact report 记录 benchmark adapter 未绕过、test feedback 未用于 evolution，见 `:1526-1559`。
- 普通 `evaluate --final-test` 被硬拒绝，见 `:1725-1734`。

## 4.4 Effectiveness screening

设计目标是 C 区上的 7 个实际 arm label：`no_memory`，3 个 `seed_<lineage>`，3 个 `evolved_from_<lineage>`；每个 arm 做两个 replicate，且每个 Program × replicate 从 fresh memory state replay A。生成逻辑见 `effectiveness_screening/run_matrix.py:66-135,218-324`，配置见 `configs/effectiveness_screening_v2.json:44-79`。

其理想数据流是：

```text
C selection → frozen paper bundles/programs
  → fresh historical replay per arm_label × replicate
  → holdout reviews
  → ReviewSplit/Classify proxy + judge
  → blind pair
  → aggregate / bootstrap / screening gate
```

但当前 classify/judge/summarize 没有一致使用 `arm_label`，该子系统不能按预注册设计正确完成，详见风险 R3。

---

## 5. 模块间依赖关系

### 5.1 主依赖图

```text
run_single_paper_review_cli.py
  ├─ agentreview
  │   ├─ roles / prompts
  │   ├─ paper_context / source_map
  │   ├─ Player / Message / OpenAI backend
  │   └─ memory_library
  └─ memory_evolution
      ├─ tasks / promoted program verification
      ├─ TaskAgent / MemoryToolkit
      └─ MemoryHarnessAdapter / SandboxSession

memory_evolution
  └─ agentreview
      ├─ fixed reviewer roles
      ├─ canonical paper context / source map
      └─ authentication / memory helpers

effectiveness_screening
  ├─ agentreview
  └─ memory_evolution

viewer.py
  └─ agentreview.viewer_data

evolution_viewer
  └─ local artifact parsers/renderers（基本独立）
```

### 5.2 依赖证据与含义

| 关系 | 证据 | 含义 |
|---|---|---|
| online CLI → 两个核心包 | `run_single_paper_review_cli.py:19-61` | 顶层脚本是在线领域与 M★ runtime 的集成点 |
| memory evolution → agentreview | `memory_evolution/agentreview_data.py:10-32`、`task_agent.py:6-11` | 离线搜索复用线上角色和 canonical context，减少语义漂移 |
| effectiveness → 两个核心包 | `effectiveness_screening/prepare.py:18-36`、`evaluate_proxy.py:12-18` | screening 是外围集成层，不是独立评测框架 |
| viewer → artifact | `evolution_viewer/data_loader.py:165-172,387-466` | viewer 不参与生成，但能读取源码和 raw traces |

静态 import 检查未发现活跃 `agentreview/` 主包反向导入 `memory_evolution`；核心包方向大体是 `memory_evolution → agentreview`，二者在顶层 CLI 汇合，而不是明显的包级循环依赖。真正的维护问题是顶层编排耦合和旧/新编排并存。

### 5.3 修改影响地图

| 修改区域 | 主要下游 |
|---|---|
| `REVIEW_Request.md` / role config | 在线五 specialist、META、TaskAgent、fitness 语义、rubric SHA gate |
| `paper_context.py` / `source_map.py` | 在线 review、A/B 数据准备、effectiveness bundle、source-anchor 验证 |
| `program_protocol.py` / `sandbox*` | 三 seed、所有 candidate、evaluator、在线 M★ memory runtime |
| `embedding_adapter.py` / embedding manifest | subset selection、candidate runtime retrieval、promotion verification、online deployment |
| `agentreview_data.py` / temporal manifest | A/B/META/D 数据、formal gate、所有 evolution 和 final evaluation |
| `metrics.py` / CoCo adapter | search ranking、promotion、development evaluate、final suite |
| `run_single_paper_review_cli.py` | 在线运行、effectiveness history/holdout 生成、输出格式和 memory writeback |

---

## 6. 配置、模型、数据、评测相关路径

## 6.1 配置来源

| 配置 | 用途 | 关键位置 |
|---|---|---|
| `REVIEW_Request.md` | 五维度 23 子类 rubric，冻结资产 | `:1-97` |
| `agentreview/arguments.py` | 在线 CLI、memory、并发、timeout、token、context、resume 参数 | `:10-409` |
| `memory_evolution/cli.py` | M★ 子命令、模型、classifier、embedding、formal/search 参数 | `:48-95,343-481,561-917` |
| `configs/asset_manifest_v2.json` | reviewer、seeds、split、S2、embedding identity 和 SHA | `:2-119` |
| `configs/formal_temporal_v3_qwen3e8b.json` | formal v3 identity、embedding、selection readiness | `:1-44` |
| `configs/coco_temporal_split_v2/manifest.json` | A/B/C/D family-level 时序划分和审计 | `:2-23,300-345,6507-6589` |
| `configs/frozen_s2_temporal_v2.jsonl(.manifest.json)` | 3889 target 的 time-safe S2 pool 与 provenance | manifest `:8-38` |
| `configs/effectiveness_screening_v2.json` | C 区 screening 设计、arm、replicate、metric/gate | `:1-80` |
| `requirements-runtime.txt` | formal runtime 锁定依赖 | `:7-17` |
| `requirements-optional.txt` | provider/viewer/dev 可选依赖 | `:4-34` |

在线参数要点：默认 `model_name=gpt-5-mini`、temperature `0.3`，见 `agentreview/arguments.py:73-82`；memory 默认 `none`，可选 `existing/mstar_program`，见 `:177-217`；paper context 默认 60,000 字符，见 `:266-269`；`run_seed` 只记录、不强制 provider 随机种子，见 `:276-279`。

认证/endpoint：显式参数优先，然后 `OPENAI_BASE_URL`，再到 legacy `OPENAI_API_BASE`，且无公有云默认 endpoint，见当前工作树 `agentreview/utility/authentication_utils.py:25-46`。主要环境变量包括 `OPENAI_API_KEY`、OpenAI base URL、Azure 三变量、`S2_API_KEY`、`HF_TOKEN` 和 `AGENTREVIEW_*` timeout/retry/节流设置。

## 6.2 模型和能力

| 角色/能力 | 当前身份 | 路径与门禁 |
|---|---|---|
| 在线 specialist/META | 默认 `gpt-5-mini`；`run.sh` 默认覆盖为本地 `qwen3.8-27b-fp8` | `agentreview/arguments.py:73-82`、`run.sh:19-41` |
| M★ task/reflector/judge | formal 默认三个逻辑名均为 `qwen3.8-27b-fp8` | `memory_evolution/cli.py:48-95` |
| generation runtime | OpenAI-compatible；formal 要求显式 private/loopback endpoint、model source/revision、vLLM version 和 preflight | `memory_evolution/cli.py:343-418` |
| embedding | `Qwen/Qwen3-Embedding-8B`，revision `1d8ad4...`，4096 维，max 32768，last-token pooling，normalize | `memory_evolution/embedding_adapter.py:19-39,575-796`、formal config `:4-13` |
| opinion classifier | 固定 CoCo ReviewSplit + ReviewClassify adapter | `memory_evolution/coco_adapter.py:19-88`、`memory_evolution/tasks.py:20-25` |
| judge/metric | `CoCoReviewFitness`，specialist 与 META 使用不同 group | `memory_evolution/metrics.py:119-314` |
| Semantic Scholar | 冻结 v2 pool，candidate 可选 capability，不默认拼入 prompt | `configs/asset_manifest_v2.json:9-19`、`agentreview/paper_review_player_md.py:172-183` |

Embedding runtime 的两类 cache：

- selection cache：`cache/qwen3_embedding_8b_cache.sqlite3`
- candidate runtime cache：`cache/qwen3_embedding_runtime.sqlite3`

正式 adapter 要求精确 model ID/revision、本地文件、fingerprint 和 token audit，见 `memory_evolution/embedding_adapter.py:595-775`。其 `trust_remote_code=True` 由精确 revision + local-only 缓解，但仍需纳入供应链审计。

## 6.3 数据路径与四区边界

| 区域 | 数量 | 作用 | 冻结源 | 物化路径 |
|---|---:|---|---|---|
| A Historical Experience | 2391 | 每个 candidate 从空状态 replay，自产 review experience 并写 memory | `configs/coco_temporal_split_v2/historical_experience.jsonl` | `data/evolution_coco_v2_20260819/<TASK>/episodes.jsonl` |
| B Evolution Validation | 299 | specialist/static/rotating 与 META validation；candidate-visible question 不含 gold | `.../evolution_validation.jsonl` | 每个 specialist `validation.jsonl`；gold 在 `evaluator_only/<TASK>/validation_references.jsonl` |
| C Effectiveness Holdout | 300 | promotion 后 proxy effectiveness measurement | `.../effectiveness_holdout.jsonl` | 设计目标 `data/effectiveness_screening_v2/`，当前尚不存在完整结果 |
| D Held-out Benchmark Test | 899 | 完全晋升后唯一 final suite | `.../held_out_benchmark_test.jsonl` | 由 `prepare-test` 物化到指定 sealed-test root |

v2 总计 3889 个 family representative，排除 11 条重复 family records；见 `configs/coco_temporal_split_v2/manifest.json:18-23,326-345`。Frozen S2 v2 manifest 记录 3889/3889 temporal audit 通过、1229 个受污染 query 重取、2660 个安全复用，见 `configs/frozen_s2_temporal_v2.jsonl.manifest.json:8-38`。

当前 canonical A/B 数据根：`data/evolution_coco_v2_20260819/`。每个 specialist 含：

- `episodes.jsonl`：2391 条；
- `validation.jsonl`：150 条 specialist B 子集；
- `queries.jsonl`：2540 条；
- `evaluator_only/<TASK>/validation_references.jsonl`：150 条 evaluator-only reference。

`data/evolution/manifest.json:8425-8427` 是旧/失败数据根，`formal_ready=false`，但 README 示例仍引用它，这是高风险文档漂移。

Human Reference Boundary 的实现较强，但不是文件权限隔离：candidate-visible artifacts 会扫描同论文 reference 泄漏，gold 单列 evaluator-only；D JSONL 本身仍包含 `answer`，操作者和同一文件系统可读，见 `memory_evolution/agentreview_data.py:381-508,1322-1348`。这是一种程序约定 + sandbox 隔离，不是 OS 级 sealed storage。

## 6.4 评测定义和路径

### specialist fitness

`CoCoReviewFitness` 将固定 judge 的五项质量分数与 frozen classifier category coverage 组合；quality 与 coverage 取 harmonic mean，candidate 自报 subcategory 不作为正式标签。见 `memory_evolution/metrics.py:119-157,170-182,195-314`。

### META fitness

META 对 adjudication、human-reference agreement、decision/calibration 三组分数取 harmonic mean，见 `memory_evolution/metrics.py:184-193,195-314`。

### Evolution 产物

```text
runs/<suite>/<TASK>/seed_<n>/
├── config.json
├── formal_gate.json
├── selection.json
├── search_controller_checkpoint.json
├── pool/*.py
├── scores.jsonl
├── lineage.jsonl
├── traces/*.jsonl
├── best_program.py
└── best_eval_report.json

runs/<suite>/<TASK>/best_program.py     # promoted winner
```

实现位置：`memory_evolution/search.py:421-543,605-689,1344-1439`、`memory_evolution/cli.py:1624-1724`。

### Final suite

正式报告由 `final-evaluate-suite` 写到用户指定路径且拒绝覆盖，见 `memory_evolution/cli.py:1296-1569`。当前仓库未发现完整 v3 六任务 promoted suite + final report；现有 `runs/formal_qual_embedding_runtime_mincheck/` 是 QUAL 最小检查，不应解读为正式结果。

### Effectiveness proxy

配置明确写 `official_benchmark_claim=false`，见 `configs/effectiveness_screening_v2.json:53-65`。其 `CoCo_Average_proxy`、Correctness proxy、blind judge 和 bootstrap 只应称 development/effectiveness measurement，不应称官方 CoCo correctness。当前实现还有 R3 所列阻断缺陷。

### gold 稀疏性

canonical manifest 中 specialist B reference 非空数不同：QUAL 150/150、CLAR 149/150、SIGN 134/150、ORIG 147/150、POL 53/150，见 `data/evolution_coco_v2_20260819/manifest.json:125-188`。POL 的有效 gold 很稀疏，结果解释必须同时报告有效样本量，不能只比较聚合 fitness。

## 6.5 依赖、测试与运行环境

- README 快速安装：`pip install -r requirements.txt`，见 `README.md:22-29`；该文件大多不锁版本。
- formal runtime 的锁版本在 `requirements-runtime.txt:7-17`，包括 `openai==1.109.1`、`torch==2.7.1`、`transformers==4.57.1` 等。
- `setup.py:21` 只声明 name/version，没有 packages、dependencies、`python_requires` 或 console entry points。
- 当前主测试 23 个文件、viewer 测试 3 个文件；当前工作树共检测到 163 个 `test_*` 方法。测试以 `unittest.TestCase` 为主，标准命令见 `README.md:22-27`。
- 未发现 GitHub Actions、GitLab CI、Jenkins、tox、pre-commit、coverage、mypy 或项目级 ruff 配置；也没有 Dockerfile、compose、Makefile 或自动部署流水线。
- 本次为零已有文件改动分析，未运行测试；测试状态只能从源码覆盖面判断，不能视为执行通过。

---

## 7. 关键类和函数

### 7.1 在线审稿

| 符号 | 职责 | 位置 |
|---|---|---|
| `discover_markdown_papers` | 发现单篇、目录、manifest/bundle 输入并校验 hash | `run_single_paper_review_cli.py:167-286` |
| `build_single_paper_players` | 创建 extractor、五 specialist、META | `:94-164` |
| `run_one_paper` | 单篇审稿总编排 | `:1735-1975` |
| `run_parallel_reviews` | specialist 并发、恢复、排序和失败传播 | `:1250-1414` |
| `build_reviewer_memory_contexts` | `none/existing/mstar_program` 记忆分派 | `:1114-1153` |
| `MarkdownPaperExtractorPlayer.act` | Markdown/frozen bundle → canonical context | `agentreview/paper_review_player_md.py:88-184` |
| `build_canonical_paper_context` | metadata 白名单、source anchors、裁剪 | `agentreview/paper_context.py:183-222` |
| `Player` / `Player.act` | 角色和 backend 的统一同步调用 | `agentreview/agent.py:43-134` |
| `OpenAIChat.query` | chat message 构造、请求、重试、日志 | `agentreview/backends/openai.py:102-547` |
| `update_memory_library` | memory JSONL/index 重建写入 | `agentreview/memory_library.py:70-111` |
| `retrieve_memory_context` | legacy token-overlap 记忆检索 | `agentreview/memory_library.py:173-246` |

### 7.2 M★ 搜索与运行时

| 符号 | 职责 | 位置 |
|---|---|---|
| `Episode`、`EvalItem`、`EvalResult` | 演化数据和评测结果模型 | `memory_evolution/models.py:5-51` |
| `MemoryHarnessAdapter` | 在线/host 面向 candidate 的 ingest/retrieve 接口 | `memory_evolution/integration.py:7-68` |
| `TaskAgent` | 固定 rubric 与 candidate instruction 的结构化 LLM wrapper | `memory_evolution/task_agent.py:73-218` |
| `MemoryToolkit` | LLM、embedding、vector、SQLite、S2、classifier capability | `memory_evolution/toolkit.py:329-596` |
| `SandboxSession` | bubblewrap/JSON-RPC candidate 隔离 | `memory_evolution/sandbox.py:75-410` |
| `EvolutionConfig` | 搜索预算、formal identity、runtime 选项 | `memory_evolution/search.py:30-97` |
| `ProgramCandidate` | pool candidate、parent、lineage、score、quality 状态 | `memory_evolution/search.py:100-114` |
| `run_evolution` | M★ population/search/selection 总控制器 | `memory_evolution/search.py:421-1442` |
| `mutate_program` | candidate-safe feedback → V4A mutation | `memory_evolution/reflector.py:246-343` |
| `check_program_path` | AST/协议/compile/smoke quality gate | `memory_evolution/quality.py:303-360` |
| `evaluate_program` | A replay/write、B read/evaluate、checkpoint、trace | `memory_evolution/evaluator.py:57-391` |
| `verify_promoted_program` | best program hash 与 embedding provenance 校验 | `memory_evolution/tasks.py:79-126` |

### 7.3 数据、模型与评测

| 符号 | 职责 | 位置 |
|---|---|---|
| `prepare_openreview_data` | 五 specialist A/B/queries/evaluator-only 数据 | `memory_evolution/agentreview_data.py:628-1084` |
| `prepare_meta_data` | specialist 冻结后构建 META 数据 | `:1087-1224` |
| `prepare_held_out_test_data` | 物化 D 区 held-out test | `:1227-1422` |
| `EmbeddingCache` | SQLite embedding cache、immutable mode、audit | `memory_evolution/embedding_adapter.py:55-496` |
| `Qwen3Embedding` | pinned Qwen3 last-token embedding | `:575-796` |
| `create_qwen3_embedding` | embedding factory | `:823-859` |
| `CoCoReviewFitness` | specialist/META fitness | `memory_evolution/metrics.py:119-314` |
| `FrozenCoCoAdapter` | 固定 CoCo checkout、ReviewSplit/Classify 客户端 | `memory_evolution/coco_adapter.py:60-末尾` |
| `CoCoMetricBenchmarkAdapter` | formal final suite benchmark 边界 | `memory_evolution/benchmark_adapter.py:42-末尾` |

### 7.4 Peripheral

| 符号 | 职责 | 位置 |
|---|---|---|
| `matrix_commands` | 生成 C 区 arm_label × replicate 命令 | `effectiveness_screening/run_matrix.py:218-262` |
| `classify_outputs` | ReviewSplit/Classify proxy | `effectiveness_screening/evaluate_proxy.py:97-153` |
| `judge_outputs` | proxy judge 和 paper metrics | `:231-384` |
| `build_blind_pairs` | blind A/B pair 与 key | `:387-450` |
| `summarize` | paired delta、bootstrap、screening gate | `effectiveness_screening/summarize.py:147-295` |
| `load_trace_diagnostics` | viewer 读取并展示 raw trace 摘要 | `evolution_viewer/data_loader.py:387-469` |

---

## 8. 当前 formal readiness 判断

为避免把几个不同层次的“ready”混为一谈，当前状态应拆成四层：

| 层级 | 状态 | 证据 |
|---|---|---|
| 冻结 reviewer/split/S2/seeds/A-B bundles | **已就绪** | `configs/asset_manifest_v2.json:2-119`，scope 明示在 `:115-117` |
| v3 Qwen selection/cache/readiness | **需要重新生成** | `configs/formal_temporal_v3_qwen3e8b.json:32-44` |
| 完整六任务 promoted suite | **当前仓库未确认存在** | `runs/` 主要是历史 pilot 和 QUAL mincheck；无可验证完整 v3 suite |
| D 区 final suite report | **当前仓库未确认存在** | 未发现完整 `final-evaluate-suite` 结果；README 仅给命令 `:215-253` |

因此，准确表述是：“pre-QUAL formal assets 与 canonical A/B bundles 已通过 manifest gate，但 v3 selection、完整 evolution、六程序 promotion 和 final evaluation 尚不能从当前仓库证明完成。”

---

## 9. 潜在风险和技术债

以下严重度结合了发生概率、影响范围与是否已有直接代码证据。标记为“推断”的条目是基于当前实现的工程判断，不表示已经发生事故。

### 9.1 高优先级风险

#### R1. 本地忽略配置含 credential-shaped 非占位凭据（高，立即处置）

- 位置：`.vscode/launch.json:20-21`。
- 只读审查确认存在形似 OpenAI key 的非占位字符串和非占位 endpoint；本文刻意不复述任何值。
- 文件被 `AgentReview/.gitignore:6` 的 `*.json` 忽略，不代表安全；当前权限为 `0664`，同组用户可读。
- 影响：若凭据仍有效，可能被本机其他用户、备份、IDE 同步或日志链路获取。
- 建议：立即轮换；launch config 只引用环境变量或 secret manager；将本地权限收紧。不要因为它未被 Git 跟踪而延后处理。

#### R2. 配置字符串通过 `eval()` 解析（高，条件性任意代码执行）

- `Config.__init__()` 对每个字符串执行 `dict(eval(value))`，异常才忽略，见 `agentreview/config.py:16-27`。
- 只要不可信 JSON/配置值进入 `Config.load()` 或 `init_config()`，即可在 host 进程执行任意 Python 表达式。
- 当前主链多数配置由内部代码构造，降低暴露概率，但这是明确的 code execution primitive，且位于旧/新代码共用基础类型。
- 建议：替换为显式 JSON schema；如只兼容 Python literal，至少用 `ast.literal_eval` 并限制预期类型。

#### R3. Effectiveness v2 的 lineage/arm 键不一致，当前子系统结果会覆盖或断链（高，子系统 blocker）

- `_program_arms()` 正确生成 7 个实际 label，见 `effectiveness_screening/run_matrix.py:66-85`。
- matrix 同时保留粗粒度 `arm` 与唯一 `arm_label`，见 `:218-262`。
- `classify_outputs()` 却按 `cell['arm']` 写 `seed.jsonl`/`evolved.jsonl`，三个 lineage 共用同一文件和 paper key，见 `effectiveness_screening/evaluate_proxy.py:110-147`。
- `judge_outputs()` 同样按粗粒度 arm 读写并按 paper ID 跳过，见 `:242-266`。
- blind pair 硬编码旧目录 `seed_experience`、`evolved`，见 `:387-425`；summarize 也硬编码旧 arm 名，见 `effectiveness_screening/summarize.py:147-202`。
- 配置本身仍指向已废弃 v1 temporal manifest，见 `configs/effectiveness_screening_v2.json:12` 与 `configs/asset_manifest_v2.json:82-97`。
- 影响：多 lineage 分类/评判互相覆盖或跳过，blind/summarize 找不到预期路径，无法形成预注册的 7-arm 比较。
- 范围：不影响 online review、M★ core 或唯一 D final suite，但 effectiveness screening 在修复前不可用于结论。

#### R4. Git 产物治理反向：关键审计 JSON 被忽略，二进制状态和敏感 trace 被提交（高）

- `AgentReview/.gitignore:6` 全局忽略 `*.json`。
- 当前 `runs/formal_qual_embedding_runtime_mincheck/` 的 `scores.jsonl`、`lineage.jsonl`、pool 和 traces 被追踪，但同目录 `config.json`、`formal_gate.json`、`selection.json`、`embedding_closure_smoke.json`、`search_controller_checkpoint.json` 被忽略。
- 当前 Git 还追踪 runtime SQLite、selection cache 的 WAL/SHM 与约 196 个 `runs/` 文件；cache 规则只针对旧名 `cache/embedding_cache.sqlite3*`，见 `AgentReview/.gitignore:82`。
- evaluator 的 on-disk trace 包含 `raw_text/question/gold/context/answer` 等字段，见 `memory_evolution/evaluator.py:161-165,262-266`；Reflector 收到的是脱敏版本，但磁盘 trace/viewer 仍可能暴露论文和评测信息。
- 影响：clone 后 formal run 无法身份验证或恢复；二进制 cache 膨胀；源码仓库承载高敏实验数据；名为 `formal_*` 的未完成 mincheck 易被误读为正式结果。
- 建议：建立 allowlist 式 artifact policy；跟踪小型 identity/audit manifest，排除 cache/WAL/SHM/raw trace；敏感结果进入受控 artifact store。

#### R5. README formal 示例指向 `formal_ready=false` 的旧数据根（高，操作风险）

- README prepare/evolve/meta/final 示例反复使用 `data/evolution`，见 `README.md:43-49,78-84,143-167,229-247`。
- 该路径 `data/evolution/manifest.json:8425-8427` 明确 `formal_ready=false`。
- 当前 canonical 根是 `data/evolution_coco_v2_20260819/manifest.json:91-106`。
- 影响：新成员按 README 运行会在 formal gate 失败，或在非正式模式误用陈旧数据。

#### R6. Formal readiness 口径容易被误读（高，实验结论风险）

- asset manifest 的 `formal_ready=true` 有明确局部 scope，见 `configs/asset_manifest_v2.json:115-117`。
- formal v3 config 同时声明 selection assets `regeneration_required`，见 `configs/formal_temporal_v3_qwen3e8b.json:32-44`。
- 当前没有可确认的完整六任务 v3 suite/final report。
- 影响：容易把“输入资产准备完成”写成“正式实验完成”，损害结果审计可信度。

### 9.2 中高风险

#### R7. 低层 `evaluate` 的 formal 外壳没有启用 evaluator formal 语义（中高）

- CLI 在 `memory_evolution/cli.py:1728-1765` 将非 pilot/smoke `evaluate` 判断为 formal，并要求 adapter/selection。
- 但调用 `evaluate_program()` 时没有传 `formal=True`、identity-bound checkpoint 或 benchmark adapter，见 `:1792-1801`。
- 因此会跳过 evaluator 的 protocol/checkpoint gate（`memory_evolution/evaluator.py:112-155`）和每个 B case 前恢复同一 A snapshot（`:252-260`）。
- 影响：有副作用的 `read()` 会使 B 顺序影响分数，开发比较可能被污染。
- 限定：README 已把该命令描述为 development/ablation，且 `--final-test` 被禁止；唯一 D final suite 不走这条分支，因此不是 final-test 绕过漏洞。

#### R8. Formal resume 身份没有直接绑定 validation/reference 文件内容（中高，推断）

- controller identity 包含 `asdict(config)`、seed SHA、episode IDs 和 validation IDs，见 `memory_evolution/search.py:464-473`。
- formal gate 计算 validation/reference SHA，见 `memory_evolution/formal_protocol.py:466-472`。
- `EvolutionConfig` 直接保存 historical manifest SHA，但没有单独保存这两个 SHA，见 `memory_evolution/cli.py:1226-1242`。
- 若 validation 内容改变但 ID 不变、外围 manifest/config identity 又未同步，旧 controller 可能不触发 mismatch。建议把 candidate-visible validation 与 evaluator-only reference 的内容 SHA 直接纳入 controller identity，并添加 resume 负向测试。

#### R9. Sandbox 没有整个 operation 的绝对 deadline（中高，推断）

- `_read_line()` 对每一条 JSON-RPC 消息单独重置 timeout，见 `memory_evolution/sandbox.py:198-217,266-289`。
- LLM 有每 operation 调用预算；embed/S2/log 没有统一总调用数或 wall-clock budget，见 `memory_evolution/toolkit.py:375-412,491-523`。
- 病态 candidate 可不断发有响应的 tool call，使单次 write/read 总时长无限拉长，同时消耗 host embedding/服务资源。
- 建议：以 operation 开始时间计算绝对 deadline；对每种 capability 加次数、字节与总耗时预算。

#### R10. 安装、打包与复现口径分裂（中高）

- README 使用未锁版本 `requirements.txt:2-15`；formal 则依赖锁定的 `requirements-runtime.txt:7-17`。
- `setup.py:21` 没有 packages、依赖、Python 版本或入口点；`pip install .` 不能代表完整安装。
- Python 代码使用 `str | Path` 等语法，实际至少要求 Python 3.10，但包元数据未声明；环境文档建议 Python 3.12。
- 未配置 CI/lock validation，OpenAI/Transformers/Gradio/torch/vLLM 的版本漂移只能在人工环境中发现。

### 9.3 中风险

#### R11. Online memory library 并发不安全、非原子

- `update_memory_library()` 读完整 JSONL、内存去重/排序，再直接 `write_text()`；随后单独覆盖 index，无锁、无临时文件、无事务，见 `agentreview/memory_library.py:70-107`。
- 两个 CLI 使用同一 output root 时可能丢更新或造成 JSONL/index 不一致；中途崩溃可截断文件。

#### R12. Viewer 默认暴露全部网卡且无认证

- `evolution_viewer/manage.sh:6-9` 默认 host `0.0.0.0`；`app.py:710-721` 的 Gradio launch 没有 auth。
- viewer 展示 Program source 和截断 raw trace，见 `data_loader.py:165-172,387-466`。
- 在共享服务器直接运行管理脚本会向同网段暴露论文、prompt、失败信息和候选代码。默认应改为 loopback，并将外网访问放到有认证的反向代理后。

#### R13. 绝对路径、固定端口和当前机器不一致

- manifest 外部根记录 `/data0/xyj/...`，见 `configs/asset_manifest_v2.json:110-114`。
- `run.sh:19`、`pilot_full.sh:12`、`QUAL_test.sh`、`end2end_smoke.sh` 和 `effectiveness_screening/run_matrix.py:18-27` 包含硬编码解释器 fallback。
- CoCo ReviewSplit/Classify 固定 localhost 端口，见 `memory_evolution/coco_adapter.py:19-20`。
- 收尾检查时这些 `/data0/...` 路径在当前机器不存在。迁移到新主机、容器或调度器需要手工 remap。

#### R14. 文档显著漂移

- `../docs/TODO.md:10,35,38-39` 仍把 v2 split/S2/canonical bundles 写成未完成 blocker，而 asset manifest 已标为局部 ready。
- `../docs/DOCUMENT_INDEX.md:36-39` 自己也声明 README/TODO 局部滞后，但它基于旧 HEAD。
- `docs/CLOUD_FORMAL_ALIGNMENT_20260820.md` 的部分 blocker 已被后续路径 remap/attempt cap 实现超越。
- 研究项目中，文档漂移会直接造成错误数据根、错误 blocker 和不可复现实验，而不只是阅读体验问题。

#### R15. Batch 缺少 per-paper 错误隔离

- `main()` 直接逐篇调用 `run_one_paper()`，没有 per-paper try/continue，见 `run_single_paper_review_cli.py:2073-2110`。
- 单一 specialist 失败会保存 checkpoint 后抛异常，见 `:1372-1402`。
- 一篇论文失败会终止后续批次；虽然可重启恢复，但长批次仍需要人工操作。

#### R16. Seed 只作 provenance，不能保证生成可复现

- 在线 `--run_seed` 明确“records but does not enforce”，见 `agentreview/arguments.py:276-279`。
- screening 配置也写 `seed_control_available=false`，见 `configs/effectiveness_screening_v2.json:47-50`。
- 两个 replicate 是流程重复，不是严格 deterministic replay。结果报告应避免把 seed 值当作模型采样可复现保证。

#### R17. evaluator-only/test 隔离主要靠程序边界，不是 OS 权限

- B gold 分离到 evaluator-only 文件是正确设计，但这些文件和 D answer 仍在同一项目文件系统；D 构建直接写 `answer`，见 `memory_evolution/agentreview_data.py:1322-1348`。
- candidate sandbox 不能访问 host repo，降低运行时泄漏；操作者、viewer、备份和其他 host 进程仍可读。
- 对严格 blind benchmark，gold/key 应放独立权限域，而不是只依赖命名和调用约定。

#### R18. POL reference 稀疏和 effectiveness 指标命名

- POL B gold 仅 53/150 非空，见 `data/evolution_coco_v2_20260819/manifest.json:185-188`，聚合时样本构成与其他维度不同。
- effectiveness 的 `Complete_official` 用 `len(ai_categories)/len(human_categories)`，未取交集，可能超过 100，见 `effectiveness_screening/evaluate_proxy.py:343-350`；真正 recall 另有字段。该命名易误导并可能影响 gate 解读。

### 9.4 结构性技术债

#### R19. 巨型 CLI/控制器职责过重

- `run_single_paper_review_cli.py` 约 2,151 行，同时承担发现、角色、memory、并发、验证、checkpoint、持久化和 batch。
- `memory_evolution/cli.py` 约 1,856 行；`search.py` 约 1,442 行；`agentreview_data.py` 约 1,422 行。
- 影响：高修改冲突、局部行为难单测、协议变化容易漏掉某个入口。建议按 application service、domain validation、repository/artifact IO、runtime adapter 拆层。

#### R20. 两套审稿编排模型并存

- 旧 `Arena/Environment/MessagePool` 与当前 `Player + ThreadPoolExecutor` 同时存在，但可见性、恢复、并发和校验不共享实现。
- deprecated 注册表已明确大量旧模块，见 `../docs/DEPRECATED.md:7-53`。
- 影响：新人容易从旧类推断错误架构，安全/行为修复也可能只覆盖一条路径。

#### R21. 大量确认死亡/legacy 代码仍在主包

- `../docs/DEPRECATED.md:7-53` 明列旧 Arena、PDF pipeline、旧实验配置、多 provider、OpenScholar 死亡部分；`:61-66` 还列出仍可达 legacy memory 路线。
- 死代码扩大依赖和安全扫描面、模糊真实入口，也使 `Config.eval()` 等旧问题继续留在可导入模块中。

#### R22. 异步 API 实现可疑

- `Player.async_act()` 调用 `self.backend.async_query(...)` 后直接返回，没有 `await`，见 `agentreview/agent.py:136-161`。
- 当前主链使用同步 `Player.__call__`，暂不影响现行审稿；任何新异步调用者都可能收到 coroutine 而非字符串。

#### R23. M★ online memory backend 隐含同模型假设

- specialist M★ context 构造时从第一个 specialist 取一个 backend 给五个 TaskAgent 使用，见 `run_single_paper_review_cli.py:841-843,887-912`。
- 当前五角色模型配置一致时成立；未来若按维度配置不同模型、reasoning effort 或 endpoint，该实现会悄悄抹平差异。

#### R24. 运行/源码边界混乱与仓库卫生

- 当前 Git 追踪约 196 个 `runs/` 文件、85 个外层历史 output 文件和 runtime SQLite。
- `evolution_viewer/=5.0,` 是被追踪的零字节异常文件；`.gradio/certificate.pem` 是公开根证书，不是私钥，但也反映运行资产边界不清。
- `runs/formal_qual_embedding_runtime_mincheck` 没有 `best_program.py`，其 score 显示 mutation failure；名称中的 `formal` 不代表完成或晋升。

### 9.5 建议处置顺序

#### P0：安全与结论正确性

1. 轮换 `.vscode/launch.json` 中的 credential-shaped secret，改为环境引用并收紧权限。
2. 移除 `Config.eval()`。
3. 修复 effectiveness 全链路统一使用 `arm_label`，更新 blind/summarize 命名并切换 v2 temporal manifest；在修复前不发布 screening 结论。
4. 修复低层 formal `evaluate` 调用参数，或明确将该入口强制降级为 non-formal development。

#### P1：formal 复现和数据治理

1. 把 validation/reference 内容 SHA 直接纳入 search controller identity。
2. 重整 `.gitignore` 和 artifact policy：保留身份/audit manifest，排除 raw trace、SQLite/WAL/SHM，并迁移已追踪大产物。
3. 明确四级 readiness，并重新生成 v3 selection/cache/readiness 后再运行完整六任务 suite。
4. README 全部切换 canonical 数据根，并同步 TODO/环境审计。
5. 为 sandbox operation 加绝对 deadline 和 capability budget。
6. 为 memory library 使用文件锁、临时文件 + `os.replace`，并让 JSONL/index 原子一致。

#### P2：可维护性

1. 统一依赖源和 Python/runtime contract，补全包元数据与 CI。
2. 将主 CLI、search controller、data preparation 拆成可单测服务。
3. 隔离/移除已确认死代码，消除双编排认知成本。
4. 默认 viewer 只监听 loopback；敏感 artifact 采用访问控制。
5. 统一外部 root/endpoint/解释器配置，避免散落绝对路径。

---

## 10. 新成员推荐阅读顺序

1. `CONTEXT.md`：术语与系统主张。
2. `AgentReview/AGENTS.md:1-55`：不可变源、固定拓扑和 M★ 不变量。
3. `AgentReview/README.md:1-20,127-201,203-275`：主流程概览；命令路径需结合本文的漂移说明。
4. `../docs/adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md`：固定/可演化边界。
5. `../docs/adr/0006-data-protocol-four-zones.md`：A/B/C/D 数据协议。
6. `../docs/adr/0007-evolution-semantics-fresh-state-lineage-budget.md`：fresh state、lineage 和预算语义。
7. `docs/memory_evolution.md:1-145`：M★ 复现契约。
8. `run_single_paper_review_cli.py:1735-1975`：在线真实主链。
9. `memory_evolution/search.py:421-1442` 与 `evaluator.py:57-391`：离线搜索与状态语义。
10. `memory_evolution/agentreview_data.py:628-1422`：数据边界。
11. `../docs/DEPRECATED.md:1-75`：不要误入旧架构。

### 最小心智模型

```text
Reviewer identity / task / evaluator / topology：冻结
MemoryProgram schema + write/read + instructions：可演化

A：产生 program-specific self experience
B：搜索/选择，只给脱敏 rotating feedback
C：晋升后 effectiveness proxy
D：最终唯一 formal suite，不回流搜索

五 specialist：互相不可见
META：只在五份合法 specialist 输出完成后运行
```

---

## 11. 分析边界与置信度

- 架构、入口、调用链、配置和风险结论来自当前源码与 manifest 的静态证据，置信度高。
- “当前没有完整 formal suite/final report”是对本地仓库可见产物的判断，不排除外部 artifact store 或另一台机器存在未同步结果。
- `.vscode/launch.json` 的风险只记录“credential-shaped 非占位值存在”，没有验证其有效性，也没有在本文泄露值。
- 未执行测试、模型 preflight、网络调用、bubblewrap、数据重建或评测，因此本文不声明运行通过、指标正确或外部服务可用。
- 分析期间有外部提交和工作树改动；除文首列出的 4 个动态文件外，关键行号以 `cc1007f` 为稳定基线。
- 本次遵守用户约束：不修改或删除任何已有代码、配置、数据、测试、文档或运行产物；仅新增本分析文档。
