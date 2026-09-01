# AgentReview M★ 代码仓全架构

> Legacy snapshot：本文描述 2026-08-31 旧六任务实现，不再是 Research Core v2 的目标协议。新设计及待决项见 [`RESEARCH_CORE_V2_DESIGN.md`](RESEARCH_CORE_V2_DESIGN.md)；冲突边界由 [`ADR-0008`](adr/0008-stage-and-evaluation-boundaries.md) 取代。

本文是 `AgentReview_0811` 的现行架构说明，依据 2026-08-31 的源码、配置、资产 manifest 与测试建立。它描述当前可执行系统、正式实验边界、生成产物以及保留的待启用接口。

术语定义见 [`CONTEXT.md`](../CONTEXT.md)，运行入口见 [`AgentReview/README.md`](../AgentReview/README.md)，架构决策见 [`adr/`](adr/)。

## 1. 系统定位与当前状态

AgentReview M★ 由三层组成：

1. **审稿系统**：五个 Specialist Reviewer 独立审稿，Final Meta-Reviewer 汇总。
2. **Reviewer Core**：以固定协议加载并隔离运行 MemoryProgram，管理历史经验、目标检索、审稿响应和可恢复状态。
3. **M★ Evolution**：以三个 Seed Program 为起点，在冻结数据和能力边界内搜索 MemoryProgram，记录候选、血统、评分与提升结果。

当前状态分层如下：

| 层级 | 状态 | 权威入口或资产 |
|---|---|---|
| 部署审稿 | 可运行 | `AgentReview/run_single_paper_review_cli.py`、`AgentReview/run.sh` |
| Reviewer Core | 可运行 | `python -m agentreview.cli review` |
| M★ 数据准备与演化 | 可运行 | `python -m memory_evolution.cli` |
| temporal v2 与 Frozen S2 v2 | 已冻结、校验通过 | `configs/asset_manifest_v2.json` |
| 正式演化 selection 资产 | 等待重新生成 | `configs/formal_temporal_v3_qwen3e8b.json` |
| Effectiveness Screening v2 | 接口完整、等待激活条件 | `configs/effectiveness_screening_v2.json` |
| D 区密封套件测量 | 在六任务 Program 提升后执行 | `final-evaluate-suite` |
| CoCo 官方评测 Adapter | 可运行的独立接口 | `python -m agentreview.coco_eval` |
| 注册的可选 Provider | 可选 Adapter | Anthropic、Cohere、Transformers、Human、Dummy registry |
| Arena、OpenScholar core、Bard/LangChain、BGE、OpenReview 导入 | 保留的待启用 Interface 或快照 | 对应文件、Adapter factory 与独立 CLI |

`configs/asset_manifest_v2.json` 的 `formal_ready=true` 覆盖 pre-evolution assets 与 A/B task bundles。`configs/formal_temporal_v3_qwen3e8b.json` 单独记录 selection assets 的 `regeneration_required` 状态。这两个状态描述不同阶段，并共同构成正式运行的完整准备度。

## 2. 顶层系统图

```text
                          ┌──────────────────────────────┐
Markdown / Frozen Data ──► Canonical Paper Context      │
                          │ paper identity + anchors     │
                          └──────────────┬───────────────┘
                                         │
                ┌────────────────────────┴────────────────────────┐
                │                                                 │
                ▼                                                 ▼
┌──────────────────────────────┐                  ┌──────────────────────────────┐
│ Production Orchestrator      │                  │ Reviewer Core CLI            │
│ five Specialists in parallel │                  │ MemoryProgram full protocol  │
│ META aggregation             │                  │ review/write/query/read/resp │
└──────────────┬───────────────┘                  └──────────────┬───────────────┘
               │                                                  │
               └────────────────────┬─────────────────────────────┘
                                    ▼
                     ┌────────────────────────────┐
                     │ reviewer_runtime          │
                     │ protocol + sandbox +      │
                     │ toolkit + session + output│
                     └──────────────┬─────────────┘
                                    │
                ┌───────────────────┴────────────────────┐
                ▼                                        ▼
┌──────────────────────────────┐          ┌──────────────────────────────┐
│ M★ Evolution                 │          │ External Evaluation          │
│ seed → select → mutate       │          │ Proxy C / Sealed D / Official│
│ quality → evaluate → promote │          │ distinct metric identities   │
└──────────────────────────────┘          └──────────────────────────────┘
```

关键依赖方向：

```text
run_single_paper_review_cli ─┐
agentreview.cli ─────────────┼──► agentreview.reviewer_runtime
memory_evolution ────────────┘

effectiveness_screening ───────► deployment CLI + promoted programs
official benchmark ────────────► Benchmark Adapter ► persisted reviewer artifacts
viewers ───────────────────────► persisted artifacts
```

Reviewer Core 是 MemoryProgram 运行语义的实现所有者。`memory_evolution` 中的 `checkpoint.py`、`program_protocol.py`、`sandbox.py`、`sandbox_worker.py`、`task_agent.py` 与 `toolkit.py` 是稳定旧导入路径的 Adapter。

## 3. 架构不变量

### 3.1 Reviewer 身份

- 五个 Specialist Task 固定为 `QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL`。
- Final Meta-Reviewer 固定为 `META`。
- `AgentReview/REVIEW_Request.md` 的六个有序章节是 Frozen Reviewer Identity 内容源；source SHA256 为 `ecd42d44c1df2cb27f606c48cb5071eea844e9dc93a7b8a36bb78352806d148d`。
- `agentreview/reviewer_identity.py` 是六任务 prompt、身份、manifest 与 META request 的唯一渲染 Implementation。
- `configs/frozen_reviewer_identity_v2.json` 固定 `frozen-reviewer-identity-v2`；manifest SHA256 为 `a480246958ec0371e577c89a946ddf1e92bad766a076ec55201cbe1c111f7cf2`，composite suite identity SHA256 为 `84d1bab744e2d63f4d2168b5c0bd8d9a3bdf3543b69bd11ad5e81d931a3acaa4`。
- Specialist 使用 `specialist-review-request-v1`；template SHA256 为 `3c56e5bcf6f71488f34515192c625162c028ce09e726c207d3ac99618d295dc5`。
- META 使用 `meta-synthesis-request-v2`；template SHA256 为 `85263700054ef329e55bf103bba66d93b6537263ec867ea9d6c304e079257502`。
- Evolution 改变 Review Harness 与 MemoryProgram；Reviewer 职责和 rubric 维度保持稳定。

六个最终 system prompt SHA256：

| Task | SHA256 |
|---|---|
| QUAL | `97af9eeaff3bce5592f0bfb07628f6f053f665337db6db90b457da2c669617de` |
| CLAR | `0522995d3894d290606d9ff83f8d8afec45ffc3840ab0a458fe6df2744b5c56c` |
| SIGN | `ee5a46ebb1f5a96d6d9ade23ea3461080d03670bd385dd34d8c5f29cadbfb8fe` |
| ORIG | `77059ebb252aa58f47647c086feea176d9e9f27875d92e9cc959cc349a86c890` |
| POL | `90967f810d88e7336d85f7693a28c6e25ba6c598a2dee363cb6822a1307ee837` |
| META | `5ae9b86bf8a8e036d0565e6d224d142ecc0babafa8171663399b682652a10bd3` |

META v2 request 依次包含 paper ID、同一论文的 Canonical Paper Context（正文与 Source Anchors）、按 `QUAL/CLAR/SIGN/ORIG/POL` 排列的五份 Specialist Review，以及完整最终 Meta Review 指令。部署、META A/B 数据物化、META cache 与 D 区套件均调用 `build_meta_review_question()`；Reviewer Core TaskAgent、Evolution provenance、Effectiveness design lock 与 formal gate 读取同一 Reviewer Suite。

### 3.2 候选可见边界

- Candidate 接收论文正文、candidate-visible metadata、source anchors 和自身 Memory 检索结果。
- Human review、gold、taxonomy、benchmark score 与 judge rationale 位于 evaluator-only 边界。
- Reflector 接收 scalar fitness、安全数值指标、预定义错误类别与资源统计。
- Benchmark 位于 Reviewer 外层，通过 Adapter 消费 Reviewer 已生成的不可变 `ReviewSubmission` 或持久化 Candidate Review。

### 3.3 历史经验时序

当前协议名为 `self_generated_after_review`：

```text
empty Program state
  → read historical Paper_1
  → generate Review_1
  → construct KnowledgeItem_1
  → write Memory_1
  → ...
  → read historical Paper_n
  → generate Review_n
  → write Memory_n
  → save baseline snapshot
```

每个 Program 以独立空状态按当前执行面的冻结顺序 replay 历史子集。Program 继承源码 lineage；每次 fitness evaluation 重建运行时 Memory。Evolution 默认使用 125 个 A 区 Episode，Effectiveness 使用 20 个 A 区目标，Reviewer Core 使用调用方传入的 Episodes，部署 history 使用当前运行的冻结 paper prefix。每个目标从该执行面的同一历史基准快照开始。

### 3.4 数据与时间边界

- 正式单位是 Paper Family 的 representative target。
- 当前 temporal v2 将 3900 条 CoCo source records 归并为 3889 个 family。
- A/B/C/D family 数量固定为 `2391/299/300/899`。
- 每个 family 只属于一个 Zone。
- Frozen S2 结果遵循目标论文的 `paper_review_cutoff`。
- 当前 year-level cutoff 使用 venue year 前一年的 12 月 31 日。

### 3.5 资产与运行身份

- 仓内资产使用项目相对路径、版本号与 SHA256 建立身份。
- OpenReview 与 CoCoReviewBench 根目录通过 external-root resolver、CLI 或环境配置进入运行时。
- 正式 Program promotion 在 `suite_manifest.json` 与 `meta_manifest.json` 中记录源码 SHA、相对来源 run、selection 与 suite identity；resolver 以 manifest 所在目录定位路径。
- checkpoint 绑定 run identity、Program SHA、数据 manifest 与配置身份。
- 运行产物写入生成目录；正式归档通过 manifest 与 hash 固定身份。

## 4. 仓库结构

```text
AgentReview_0811/
├── CONTEXT.md                         # 当前领域词汇表
├── docs/
│   ├── ARCHITECTURE.md                # 本文，唯一全架构说明
│   ├── DOCUMENT_INDEX.md              # 当前说明文件清单与所有权
│   └── adr/                           # 现行架构决策
└── AgentReview/
    ├── README.md                      # 安装、入口和运行概览
    ├── REVIEW_Request.md              # 冻结 Reviewer rubric
    ├── run_single_paper_review_cli.py # 部署审稿编排
    ├── run.sh                         # 部署审稿 shell 入口
    ├── pilot_full.sh                  # 小规模端到端演练
    ├── end2end_smoke.sh               # Specialist Evolution smoke
    ├── viewer.py                      # 部署结果查看器
    ├── agentreview/                   # 审稿应用与稳定 Reviewer Core
    ├── memory_evolution/              # M★ 数据、搜索与评测
    ├── effectiveness_screening/       # C 区对照筛选
    ├── evolution_viewer/              # M★ 运行查看器
    ├── scripts/                       # 冻结资产构建与审计
    ├── configs/                       # 版本化正式配置和 manifest
    ├── tests/                         # 主测试套件
    ├── test_data/                     # 当前 smoke 论文 fixture
    ├── papers_eval20/                 # 20 个受控 ICLR 2024 Markdown 输入
    ├── data/                          # 物化数据与实验输入
    ├── cache/                         # 可再生 embedding cache
    ├── runs/                          # 可再生 Evolution 运行产物
    └── outputs*/                      # 可再生审稿输出
```

Markdown 论文正文属于数据资产。`test_data/**/md_content*.md` 服务 smoke 与测试，`papers_eval20/ICLR2024_*/md_content.md` 提供 20 个受控论文输入；Effectiveness `prepare.py` 在激活后向 output root 的 `paper_context/<split>/<paper_id>/paper.md` 物化冻结论文副本。

## 5. 三条现行执行面

### 5.1 部署审稿执行面

入口：

```bash
python run_single_paper_review_cli.py --help
bash run.sh
```

完整流程：

```text
CLI arguments / environment
  → load Markdown or frozen paper-context manifest
  → build and validate Canonical Paper Context
  → resolve per-task memory context
  → run QUAL/CLAR/SIGN/ORIG/POL concurrently
  → validate specialist bundle
  → build META v2 request from canonical full-paper context + anchors + five reviews
  → run META
  → persist review, provenance, timing and candidate records
  → optional global history writeback
```

部署编排的模块分工：

| Module | Interface | Implementation responsibility |
|---|---|---|
| `run_single_paper_review_cli.py` | CLI、paper loop、checkpoint | 参数解析、五任务并发、META、恢复、产物提交 |
| `paper_review_player_md.py` | Markdown paper extractor | Canonical Paper Context 与 bundle provenance |
| `reviewer_identity.py` | Frozen Reviewer Identity deep module | 六任务 prompt、task/suite SHA、META v2 request 与 manifest 校验 |
| `single_paper_review_roles.py` | deployment Adapter | 将统一 Frozen Reviewer Suite 转为六个 `AgentConfig` |
| `paper_review_player.py` | Reviewer Player | 把 prompt/context 交给 backend |
| `agent.py` | backend invocation seam | message 构造、query 与响应封装 |
| `paper_context.py` | canonical context schema | 字段校验、规范化、格式化和内容 hash |
| `source_map.py` | evidence anchor seam | 论文段落定位与 anchor 解析 |
| `history_checkpoint.py` | resume interface | prefix 身份、snapshot、sidecar 审计和原子保存 |

五个 Specialist 使用独立 Player 与独立 memory context。已有完整 Specialist checkpoint 可恢复到当前 paper 的剩余任务。META 在五份 Specialist Review 完整后运行。

当前 `memory_mode`：

| Mode | Context source | Runtime behavior |
|---|---|---|
| `none` | 空 memory context | 直接生成审稿 |
| `existing` | `memory_library.py` JSONL/index | 使用 legacy 词法检索经验 |
| `mstar_program` | 提升后的 MemoryProgram | sandbox 内 query/read，Player 使用检索结果生成文本 |

`mstar_program` 在部署执行面承担 context-provider Implementation。最终文本由固定 Player/backend 生成。该语义与 Reviewer Core CLI 的完整 MemoryProgram 协议保持清晰分层。

部署产物按论文目录持久化：

| Artifact | Content |
|---|---|
| `reviews.json` | 五份 Specialist Review 与 META Review |
| `candidate_reviews.jsonl` | `agentreview-candidate-review-v2`、task prompt/request template/suite identity、Program SHA 和 provenance |
| `memory.json` | 本次 memory mode、context、paper bundle 与来源身份 |
| `review_history.json` | 审稿过程记录 |
| `timing.json` | 各阶段耗时与执行统计 |
| `specialist_checkpoint.json` | 论文内 Specialist 恢复点，成功提交后清理 |

直接 Markdown extractor 分支构造空 `retrieved_papers`，并记录 `s2_usage.used=false` 与 `default_injection=false`。冻结 bundle 分支保留 bundle 内的 `retrieved_papers` 和 `references_text`，同时保持 `s2_usage.used=false`；该状态表达 Frozen S2 资产留在独立 capability，Canonical Paper Context 由论文正文与 Source Anchors 构成。

### 5.2 Reviewer Core 执行面

入口：

```bash
python -m agentreview.cli review --help
```

Reviewer Core CLI 直接执行完整协议：

```text
load Program source + verify SHA
  → start sandboxed ReviewerSession
  → replay historical Episodes
       TaskAgent.review(paper)
       TaskAgent.extract(paper, generated_review)
       KnowledgeBase.write(item, raw_text)
  → snapshot baseline state
  → for each target
       restore baseline snapshot
       TaskAgent.query(target)
       KnowledgeBase.read(query)
       TaskAgent.response(target, memory context)
  → append strict candidate_reviews.jsonl
```

该入口由 MemoryProgram 与固定 TaskAgent 共同完成最终 Review。它是 Evolution evaluator 与候选运行语义的直接参考实现。

### 5.3 M★ Evolution 执行面

入口：

```bash
python -m memory_evolution.cli --help
```

子命令按生命周期组织：

| Command | Lifecycle stage |
|---|---|
| `prepare-specialists` | 从 temporal v2 A/B 构建五个 Specialist task bundle |
| `prepare-test` | 物化正式 D 区 target bundle |
| `prepare-meta` | 从 Specialist outputs 构建 META 数据 |
| `cache-meta-inputs` | 冻结 META 输入及来源身份 |
| `evolve-task` | 演化单个 Specialist 或 META task |
| `evolve-specialists` | 执行五 Specialist suite 与提升 |
| `evolve-meta` | 执行 META suite 与提升 |
| `evaluate` | 单任务开发评估 |
| `final-evaluate-suite` | 通过 formal gate 后执行六任务 `sealed_d_coco_style` 测量 |

正式生命周期为：`prepare-specialists → evolve-specialists → prepare-meta → cache-meta-inputs → evolve-meta → prepare-test → final-evaluate-suite`。`evolve-task` 与 `evaluate` 提供单任务开发入口。`end2end_smoke.sh` 先在 `data/specialist_smoke` 准备 temporal v2 小样本，再执行 `evolve-specialists --smoke`，属于 Specialist Evolution 结构冒烟。

密封 D 区套件入口 `final-evaluate-suite` 在生成每份 Review 后内联运行 CoCo-style Adapter，并在结尾写入紧凑报告。CoCoReviewBench 自带三阶段评测由 `python -m agentreview.coco_eval` 读取已持久化的 `candidate_reviews.jsonl` 独立执行。

## 6. Reviewer Core 深模块

`agentreview/reviewer_runtime/` 把复杂运行机制收敛为一个深模块。公开 Interface 主要是：

```text
MemoryProgram source/path
ReviewRequest / Episode
MemoryToolkit
ReviewerSession or review_once()
CandidateReviewWriter
```

调用方接触稳定对象；进程隔离、逐行 JSON command/capability 协议、状态恢复、能力计数、schema 校验和 provenance 由内部 Implementation 管理。

### 6.1 文件所有权

| File | Ownership |
|---|---|
| `../reviewer_identity.py` | 六任务 prompt、META request、task/suite identity 与 semantic manifest |
| `models.py` | `ReviewRequest`、`ReviewResult`、历史 Episode 与 provenance 值对象 |
| `program_protocol.py` | 指令、`KnowledgeItem`、`Query`、`KnowledgeBase` 协议 |
| `program_loader.py` | Program 路径解析、源码 SHA、promotion manifest 校验 |
| `tasks.py` | 六任务 ID 与统一 Frozen Reviewer task/suite identity Adapter |
| `task_agent.py` | LLM JSON schemas、extract/query/response Adapter |
| `engine.py` | ReviewerSession、history replay、target review、snapshot/restore |
| `sandbox.py` | bwrap 启动、资源边界、宿主 capability dispatch |
| `sandbox_worker.py` | stdlib-only Program worker、内存状态与 NDJSON 命令循环 |
| `toolkit.py` | LLM、embedding、S2、vector store、classifier capabilities |
| `artifacts.py` | `agentreview-candidate-review-v2`、完整 task/request/suite identity 校验和线程安全 JSONL writer |
| `episodes.py` | Episode 数据转换 |
| `requests.py` | Canonical Specialist question 与 fallback Source Anchors 构造；`models.py` 拥有 `ReviewRequest.validate()` |
| `memory_runtime.py` | 部署编排与 Reviewer Core 的适配层 |

### 6.2 MemoryProgram 协议

候选 Program 提供：

- 指令文本；
- `KnowledgeItem` 与 `Query` 类型；
- `KnowledgeBase(toolkit)` 构造器；
- `write(item, raw_text)`；
- `read(query) -> str`；
- 可序列化状态。

固定 TaskAgent 负责把自然语言论文与任务变成 Program 协议对象。当前 extract 与 query 由 LLM 依据严格 JSON schema 生成，response 由冻结 task system prompt、Program response instruction、原始 question 与 memory context 共同生成。Evolution 的搜索变量是完整 MemoryProgram 源码。

### 6.3 Sandbox 与 capability

正式候选执行要求 Linux bubblewrap。sandbox 使用：

- 只读代码 bind；
- 空环境与隔离 Python 启动；
- `python -I -S`；
- 地址空间、CPU 与超时限制；
- import 和 builtin allowlist；
- 内存 SQLite；
- 自定义 newline-delimited JSON command/capability 通道：`command → result`，期间可嵌套 `tool_call → tool_result`；
- snapshot/restore 命令。

宿主 capability 集合：

| Capability | Purpose | Boundary |
|---|---|---|
| `llm_completion` | TaskAgent 与 Program 使用的 LLM 操作 | Program `read/write` 位于 operation-scoped quota；TaskAgent extract/query/response 位于该 quota 外；全部调用进入资源统计 |
| `embed` | 文本 embedding | adapter identity、dimension、fingerprint |
| `retrieve_s2` | 当前 paper 的 Frozen S2 查询 | paper scope、pool hash、cutoff |
| `log` | 结构化运行日志 | 受控记录接口 |

Toolkit 还提供 vector store 与 ReviewSplit/Classify seam。正式运行记录 capability 用量和模型/Adapter provenance。

### 6.4 Session 状态机

`ReviewerSession` 构造时校验 task、解析 Program identity、重置 vector collections、启动 sandbox 并读取 Program schemas。调用方随后按需执行 history ingest、snapshot、restore 与 target review；context manager 在成功或异常路径关闭 worker。

历史阶段的单个 Episode 状态：

```text
paper loaded → review generated → KnowledgeItem extracted → write committed
```

目标阶段状态：

```text
baseline restored → Query generated → Memory read → response generated
```

checkpoint 以冻结顺序 prefix 表达完成度。恢复条件同时覆盖 protocol version、完整 run identity、严格 completed prefix、匹配的 `next_paper_index`、Memory snapshot 内容身份与 sidecar 集合；通过校验后从下一个 Episode 开始。

## 7. M★ Evolution 架构

### 7.1 搜索空间与固定边界

M★ 搜索完整 MemoryProgram，包括：

- memory schema；
- write-time abstraction；
- retrieval/index strategy；
- query organization；
- read-time compression；
- capability 使用策略。

固定边界包括 Reviewer Identity、TaskAgent Interface、candidate/evaluator 隔离、正式能力集合、Base LLM 身份和同一实验的生成参数。

### 7.2 三个 Seed Program

| Seed | Write strategy | Read strategy |
|---|---|---|
| `vector_search` | 保存完整经验 | embedding 相似度检索 |
| `llm_summarizer` | 保存完整经验 | 面向目标 Query 的 LLM 压缩 |
| `experience_learner` | 写入时抽象为结构化经验 | 结构化 recall 与目标匹配 |

源码位于 `memory_evolution/seeds/`，资产 manifest 固定每个 Seed 的 SHA256。

### 7.3 当前搜索循环

默认流程包含 3 次 Seed evaluation 和 20 次 Evolution Attempt：

```text
evaluate three Seeds
  → admit quality-passing Seeds with completed Static Evaluation to global pool
  → repeat 20 attempts
       choose parent from global pool by score softmax
       collect rotating feedback
       Reflector emits complete child source
       protocol/source/compile/quality gates
       static evaluation
       commit outcome and consume attempt
       admit quality-passing child with completed Static Evaluation to global pool
  → rank Static Score top 3 and evaluate each candidate 3 times
  → select highest repeated mean with variance tie-break
  → select highest-Static-Score champion in each provenance lineage
  → promote best_program.py with report and manifest
```

当前默认值：

| Parameter | Value |
|---|---:|
| Evolution Attempts | 20 |
| Static Selection | 25 |
| Rotating Selection | 5 |
| Selection Repeats | 3 |
| Selection Top K | 3 |
| Parent Temperature | 0.15 |

Attempt 是固定预算单位。Reflector/变异生成、协议门禁、编译、质量检查与评估的每个结果都消费一次 attempt。Lineage 表达候选来源；父代选择使用跨 lineage 的全局已评估合格池。

### 7.4 Evolution 模块所有权

| Module | Responsibility |
|---|---|
| `assets.py` | 资产版本、manifest 解析与身份校验 |
| `agentreview_data.py` | 论文、Episode、Validation 与 reviewer memory 文本构建 |
| `subset_selection.py` | frozen static/rotating subset 与 embedding selection |
| `evaluator.py` | fresh-state A replay、B snapshot restore、候选评审与评分 |
| `search.py` | controller、候选池、attempt、parent policy、lineage、promotion |
| `reflector.py` | 允许反馈到完整子代源码的生成 Adapter |
| `quality.py` | 协议、AST、源码、编译和行为门禁 |
| `metrics.py` | fitness 组合与任务指标 |
| `embedding_adapter.py` | Qwen3 embedding 与 fingerprint seam |
| `coco_adapter.py` | ReviewSplit、ReviewClassify、judge 进程 Adapter |
| `coco_reference.py` | evaluator-only CoCo reference 读取 |
| `benchmark_adapter.py` | 外部 benchmark 通用接口 |
| `formal_protocol.py` | 正式资产、数据、Program、运行配置统一 gate |
| `meta_cache.py` | META 输入缓存及 specialist suite provenance |
| `api_preflight.py` | 模型端点和能力预检 |
| `pilot_report.py` | pilot 运行报告汇总 |
| `cli.py` | 生命周期命令编排与正式/开发模式选择 |

### 7.5 候选评估

每次候选评估执行：

1. 建立独立 ReviewerSession。
2. 从空状态 replay `selection.json` 固定的 A 区 Episodes；默认 25 个 Static targets 对应 125 个历史 Episodes。
3. 保存 A 区基准 snapshot。
4. 对每个 B 区目标恢复同一 snapshot。
5. 生成候选 Review。
6. evaluator-only Adapter 读取 reference 并计算指标。
7. 写入 candidate records、scores、trace 与资源统计。

Static Selection 负责跨候选可比选择。Rotating Selection 负责提供新的、受边界约束的变异信号。

### 7.6 Evolution 产物

| Artifact | Meaning |
|---|---|
| `runtime_resources.json` | `mstar-runtime-resources-v1` 与 `sandbox_memory_mb` |
| `llm_runtime.json` | logical model identity、endpoint preflight/base URL history 与 runtime embedding namespace |
| `config.json` | 归一化 Evolution 配置 |
| `formal_gate.json` | 正式协议校验结果 |
| `selection.json` | frozen evaluation Episode selection |
| `embedding_closure_smoke.json` | Program write/read、snapshot/restore 与 embedding 闭环验证 |
| `pool/program_*.py` | Seed 与子代完整源码 |
| `scores.jsonl` | 每个候选提交后的评分记录 |
| `lineage.jsonl` | parent、child、Seed 来源和 attempt provenance |
| `traces/*.jsonl` | static/rotating evaluator trace |
| `candidate_reviews.jsonl` | `agentreview-candidate-review-v2` records |
| `evolution_budget.json` | attempt 消耗与结果分类 |
| `lineage_champions/` | 每条来源 lineage 的最佳 Program |
| `lineage_champions.json` | lineage champion 索引 |
| `best_program.py` | overall promoted Program |
| `best_eval_report.json` | 最佳候选、指标和来源汇总 |
| `search_controller_checkpoint.json` | run identity、phase、attempt cursor、RNG、candidate prefix 与日志边界 |
| `checkpoints/<candidate>/` | identity-bound 历史 replay checkpoint 与 committed sidecars |

Suite promotion root 另写 `suite_manifest.json` 与 `meta_manifest.json`，并保留 `<TASK>/seed_<n>/{config.json,selection.json}`、`<TASK>/best_program.py` 以及 `lineages/<lineage>/<TASK>/best_program.py`。manifest writer 记录套件内相对 `program` 与 `source_run`；consumer 以 manifest 目录解析路径，再读取 `<source_run>/config.json` 和 `selection.json`。

Controller checkpoint 原子记录候选提交前缀、attempt cursor、RNG state、selection repeats 和日志边界。

## 8. 数据、资产与正式协议

### 8.1 四区数据模型

| Zone | Family count | Candidate use | Evaluator use |
|---|---:|---|---|
| A — Historical Experience | 2391 | 顺序 replay，自生成经验并写入 Memory | 提供冻结论文顺序 |
| B — Evolution Validation | 299 | 从 run-scoped history snapshot 独立审稿 | fitness、selection、mutation、promotion |
| C — Effectiveness Holdout | 300 | Program 冻结后的独立对照生成 | proxy screening 与 blind comparison |
| D — Held-out Benchmark Test | 899 | 六任务套件与配置冻结后的最终生成 | inline sealed CoCo-style suite measurement；持久化 Candidate Review 可进入 Official Adapter |

family identity 使用规范标题与 paperhash 的传递闭包。代表目标选择依次考虑 accepted/published 状态、venue year、PDF 显式版本、稳定 paper ID 与 source hash。

当前 materialized 规模：

| Asset or flow | Count |
|---|---:|
| CoCo source records / Paper Families | 3900 / 3889 |
| Specialist bundle（每个 task） | 2391 A Episodes + 150 B Validation |
| META B reserve | 149 families |
| Frozen S2 v2 | 3889 target records，`top_k=10` |
| Qwen3 selection cache | 12,705 vectors，4096 dimensions，0 truncations |
| Effectiveness selection | 20 A history + 20 C targets |
| Effectiveness target packages | 7 arms × 2 replicates × 20 = 280 |
| Effectiveness history commands | 6 Program arms × 2 replicates = 12 |

### 8.2 Temporal split v2

`scripts/build_coco_temporal_split.py` 是当前 split 生成器：

- A/D 按 year 边界分配；
- B/C 使用冻结 v2 assignment；
- `audit_family_accounting` 校验 3900 records 到 3889 families 的完整归并；
- paper ID、normalized title 与 family 的跨区 overlap 为零；
- manifest 记录生成规则、计数、来源与审计结果。

`configs/coco_temporal_split_v2/manifest.json` 是冻结的当前产物。其历史比较字段属于资产 provenance。

### 8.3 Frozen S2 v2

`scripts/s2_search.py` 统一：

- canonical query hash；
- publication time 解析；
- Semantic Scholar 响应规范化；
- 429/网络重试；
- title search。

`scripts/build_frozen_s2_pool_v2.py` 为 temporal v2 的每个 target 构建 `top_k=10` 冻结记录。`--check-only` 直接审计当前 pool：

- 3889 个 target 完整覆盖；
- target identity 与规范 query 一致；
- record hash 与 sidecar 一致；
- publication time 满足 cutoff；
- 每条结果归属当前 target。

正式运行按 target scope 读取 Frozen S2。pool、sidecar 与资产 manifest 分别记录内容 SHA。

### 8.4 Qwen3 embedding

正式 embedding identity：

| Field | Value |
|---|---|
| Provider | `qwen3_embedding` |
| Model | `Qwen/Qwen3-Embedding-8B` |
| Revision | `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af` |
| Dimension | 4096 |
| Max length | 32768 |
| Pooling | last token |
| Normalization | enabled |
| Serialization | `agentreview-episode-text-v1` |
| Fingerprint | `f5fd62cb75e46177a293477c66cd9f2dfa0b3a7dad70d14ee7f0ada0130cb829` |

`scripts/prewarm_embedding_cache.py` 负责缓存预热。当前 `cache/qwen3_embedding_8b_cache.audit.json` 记录五个 Specialist 的 `5 × (2391 + 150) = 12,705` 个向量，维度为 4096，最大 token count 为 24,422，截断数量为 0。正式 selection cache 与 candidate-runtime cache 使用独立 namespace，并通过 audit 与 formal experiment manifest 固定身份。

### 8.5 Formal Protocol Gate

正式演化启动顺序：

```text
asset manifest version/hash
  → Frozen Reviewer Identity manifest + task/suite prompt hashes
  → temporal v2 family/zone/temporal audit
  → A/B task bundle membership and canonical preprocessing
  → Frozen S2 coverage/query/time/hash audit
  → Seed Program SHA
  → model endpoint and generation provenance
  → embedding identity/cache audit
  → sandbox capability
  → run identity and output commit
```

密封 D 区测量再加入两份 promotion manifest、D 区 test bundle、ReviewSplit/ReviewClassify revision 与 `CoCoReviewFitness` 身份校验。官方 CoCo Adapter 独立校验 CoCo 源码、benchmark 文件、prompt 与三阶段运行参数。

## 9. 评测架构

### 9.1 四类评测身份

| Layer | Data | Purpose | Reporting identity |
|---|---|---|---|
| Evolution Fitness | B | 候选选择、变异与提升 | internal fitness |
| Effectiveness Proxy | C | seed/evolved/baseline 独立效果筛选 | CoCo-style proxy |
| Sealed D Suite | D | 生成 Review 后内联运行冻结 CoCo-style metric，结尾写聚合报告 | `sealed_d_coco_style` |
| Official CoCo Adapter | persisted `candidate_reviews.jsonl` + pinned CoCo files | 调用 CoCo 自带三阶段程序并持久化阶段产物 | official CoCo evaluation |

四类结果使用清晰的 metric identity。`final-evaluate-suite` 使用进程内 `ReviewSubmission`、`CoCoMetricBenchmarkAdapter` 和 `CoCoReviewFitness`；该 fitness 以 CoCo 的指标设计为参考，执行路径持久化紧凑 suite report。`agentreview.coco_eval` 从严格 Candidate Review v2 artifact 启动，执行 CoCoReviewBench 自带 `_2_split.py`、`_3_classify.py` 与 `_4_evaluation.py`，并持久化 candidate input、split、classify、selection、protocol identity 与 evaluation。

### 9.2 Official CoCo Adapter

`memory_evolution/coco_adapter.py` 固定 Evolution 和密封 D 路径的 ReviewSplit/ReviewClassify 服务边界：

| Service | Default port |
|---|---:|
| ReviewSplit | 13099 |
| ReviewClassify | 13098 |

密封 D 的 judge 使用正式运行参数指定的模型 endpoint。独立 `agentreview.coco_eval` 默认使用 Split 13099、Classify 13098、Judge 13097，并允许 CLI 显式配置三个 endpoint。它校验 CoCoReviewBench protocol files、benchmark files 与 prompt hash；`agentreview/_coco_official_runner.py` 加载官方 evaluator 和 shipped left-out selection。

## 10. Effectiveness Screening v2

当前配置状态是 `inactive`。激活要求：

1. temporal-v2 A/B evolution inputs 完整物化并带冻结 provenance；
2. Specialist 与 META lineage promotions 完成并生成 `suite_manifest.json`、`meta_manifest.json`；
3. C 区 paper context、Frozen S2 与 Program manifest 完成冻结。

激活后的固定规模为 20 个 A 区历史目标、20 个 C 区评测目标、7 个 arms 与 2 个 replicates。六个 Program arms 各自执行每 replicate 一次历史 replay，共 12 条历史命令；14 个 target matrix commands 各处理 20 篇论文，共形成 280 个完整目标审稿包。

### 10.1 Arm 身份模型

`effectiveness_screening/arms.py` 定义 `effectiveness-arm-v1`：

| Field | Meaning |
|---|---|
| `arm_id` | 跨目录、记录、缓存、评分与盲评的唯一身份 |
| `arm_kind` | `no_memory`、`seed`、`evolved` |
| `lineage` | `vector_search`、`llm_summarizer`、`experience_learner` 或 baseline 空值 |

每个 replicate 包含 7 个 arm：

```text
no_memory
seed_vector_search
seed_llm_summarizer
seed_experience_learner
evolved_from_vector_search
evolved_from_llm_summarizer
evolved_from_experience_learner
```

### 10.2 Screening 流程

```text
select 20 A-zone history papers + 20 C-zone targets and proxy references
  → build 40 canonical paper-context bundles and Frozen S2 audit
  → freeze six lineage Programs
  → freeze design lock
  → preregister 20 paper × 7 arm_id × 2 replicate matrix and 12 history commands
  → replay A-zone history independently per Program arm × replicate
  → generate holdout reviews with target writeback disabled
  → audit all 280 review packages
  → classify each <arm_id>.jsonl
  → judge each <arm_id>.jsonl
  → build blinded seed/evolved pair per lineage
  → run blind judge
  → summarize paired deltas, bootstrap and gate per lineage
```

模块所有权：

| Module | Responsibility |
|---|---|
| `prepare.py` | C 区采样、论文物化、Frozen S2 与 evaluator-only references |
| `arms.py` | canonical arm identity 与 lineage pair 校验 |
| `memory_cache.py` | lineage Program freeze；历史 cache 兼容接口 |
| `run_matrix.py` | fresh replay、7-arm matrix、design lock 与完整性审计 |
| `evaluate_proxy.py` | per-arm 分类、judge、盲评 pair 与复用 provenance |
| `summarize.py` | per-lineage paired delta、bootstrap、blind summary 与 gate |
| `preflight.py` | 激活资产和外部能力预检 |

顶层 gate 策略为 `all_lineages_meet_preregistered_thresholds`。每条 lineage 独立报告 `evolved − seed`、`seed − baseline` 与 `evolved − baseline`。

## 11. Viewer 架构

两个 Viewer 读取不同产物，保持独立：

| Viewer | Entry | Input | Scope |
|---|---|---|---|
| Result Viewer | `python viewer.py` | 部署审稿 `output_dir` | 论文、Specialist、META、memory provenance |
| Evolution Viewer | `python -m evolution_viewer.app` | M★ `runs_dir` | pool、lineage、scores、source diff、trace |

`agentreview/viewer_data.py` 是 Result Viewer 的只读数据 Adapter。`evolution_viewer/` 拥有独立 loader、render、代码分析和测试。

## 12. `agentreview` 应用模块地图

| Module or cluster | Status | Responsibility |
|---|---|---|
| `reviewer_runtime/` | active core | MemoryProgram 的唯一运行 Implementation |
| `cli.py` | active | Reviewer Core CLI |
| `reviewer_identity.py` | active deep module | 六任务 prompt、META v2 request 与 task/suite identity |
| `paper_context.py`、`source_map.py` | active | canonical paper schema 与证据 anchors |
| `single_paper_review_roles.py` | active Adapter | Frozen Reviewer Suite 到 deployment `AgentConfig` |
| `paper_review_player_md.py` | active | Markdown/bundle extractor |
| `paper_review_player.py`、`agent.py` | active | 部署 Reviewer 与 backend seam |
| `history_checkpoint.py` | active | 历史 replay 恢复 |
| `memory_library.py` | compatibility interface | `existing` memory mode 的 JSONL/index |
| `backends/openai.py` | active Adapter | OpenAI-compatible 与 Azure OpenAI transport |
| `backends/anthropic.py` | registered optional Adapter | Anthropic provider Interface |
| `backends/cohere.py` | registered optional Adapter | Cohere provider Interface |
| `backends/hf_transformers.py` | registered optional Adapter | Transformers provider Interface |
| `backends/human.py` | registered optional Adapter | human-in-loop provider Interface |
| `backends/dummy.py` | registered utility Adapter | 测试与本地 Interface |
| `backends/bard.py`、`backends/langchain.py` | dormant interface files | 独立 provider seam |
| `arena.py`、`paper_review_arena.py` | inert snapshot | ChatArena orchestration；`from_config()` 当前构造签名与必需 `args` 存在版本差异 |
| `environments/`、`ui/`、`database.py` | inert snapshot cluster | Arena environment、UI、Supabase persistence |
| `openscholar_bridge/config.py`、`openscholar_api.py`、`src/open_scholar.py`、`src/instructions.py` | inert string snapshots | OpenScholar core/API 历史源码 |
| `openscholar_bridge/src/use_search_apis.py`、`src/utils.py` | compatibility helpers | 可导入 Semantic Scholar/PES2O search helper；legacy wrapper 位于 `utility/s2_retrieval.py` |
| `dataset/` | dormant independent CLI | OpenReview 下载与 submission 整理 |
| `_coco_official_runner.py`、`coco_eval.py` | active evaluation Adapter | 官方 CoCo 独立执行 |
| `viewer_data.py` | active read Adapter | 部署结果 viewer 数据 |
| `memory_evolution/embedding_adapter.py:create_bge_m3` | dormant Adapter factory | BGE-M3 1024 维替代 embedding；正式配置选择 Qwen3 4096 维 Adapter |

注册 Provider 保持 `agentreview.backends` 的 registry 完整。休眠 Interface、替代 embedding Adapter、独立数据 CLI 与历史快照保留各自接缝和状态标识。

## 13. 配置、依赖与部署

### 13.1 依赖层

| File | Role |
|---|---|
| `requirements.txt` | 正式依赖兼容入口 |
| `requirements-runtime.txt` | 运行与正式实验固定依赖 |
| `requirements-optional.txt` | Viewer、可选 Provider、OpenScholar、数据导入、开发工具 |
| `evolution_viewer/requirements.txt` | Evolution Viewer 独立 UI 依赖 |

仓库从源码树执行，CLI 使用 `python -m ...` 或顶层脚本入口。

### 13.2 运行配置来源

配置优先通过 CLI、环境变量和 versioned JSON manifest 进入。常用环境变量：

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | OpenAI-compatible 认证 |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint |
| `MSTAR_PROGRAM_DIR` | 六任务 promoted Program root |
| `MSTAR_EMBEDDING_REVISION` | embedding immutable revision |
| `PAPER_MD_DIR` | 部署论文输入目录 |
| `REVIEW_OUTPUT_DIR` | 部署结果目录 |
| `OPENREVIEW_ROOT` | OpenReview 外部数据根 |
| `COCOREVIEWBENCH_ROOT` | CoCoReviewBench 外部仓根 |

凭据通过运行时环境变量或对应入口的显式 CLI 参数注入。配置和说明文件保存变量名、endpoint identity 与 revision。

### 13.3 生成目录

以下路径承载可再生状态：

- `runs/`：Evolution pools、traces、checkpoints 和 promotions；
- `outputs*/`：部署审稿结果；
- `cache/*.sqlite3*`：embedding cache；
- `configs/*.jsonl.work/`：Frozen S2 构建 checkpoint；
- `.gradio/`：Viewer runtime；
- Python、pytest 与 Ruff caches。

版本控制保存源代码、冻结输入、现行 manifest 和测试。外部正式归档保存完成的 run artifacts 与对应 hash。

## 14. 扩展 Seam

### 14.1 新增 MemoryProgram

1. 实现 `program_protocol.py` 定义的 KnowledgeBase 接口。
2. 使用 Reviewer Core sandbox 执行。
3. 通过 quality、compile、snapshot/restore 和 candidate artifact 测试。
4. 以源码 SHA 和 promotion manifest 注册。

### 14.2 新增模型 Provider

1. 实现 `agentreview.backends.base` 接口。
2. 注册 provider type。
3. 映射统一 message、sampling、token 和 error 语义。
4. 增加 transport 与响应规范化测试。

### 14.3 新增 embedding

1. 实现 embedding Adapter factory。
2. 暴露完整 spec 与稳定 fingerprint。
3. 定义维度、pooling、normalization、serialization 和 revision。
4. 为 selection/runtime 建立独立 cache namespace 与 audit。

### 14.4 新增 Benchmark

1. 实现 `benchmark_adapter.py` 的外部评价接口。
2. 固定 evaluator revision、prompt hash 和模型身份。
3. 选择明确的运行边界：inline 模式消费进程内不可变 `ReviewSubmission`，artifact-first 模式消费已持久化 Candidate Review。
4. 用独立 Zone 或 frozen manifest 建立数据边界。

### 14.5 新增论文输入格式

1. 新 Adapter 输出 Canonical Paper Context。
2. 保持 paper identity、内容 SHA、candidate-visible metadata 与 source anchors。
3. 在 `paper_context.py` 边界完成 schema 校验。
4. 部署编排和 Reviewer Core 继续消费同一规范对象。

### 14.6 新增 Effectiveness arm

1. 在 `arms.py` 扩展版本化身份规则。
2. 让唯一 `arm_id` 贯穿 program、history、holdout、classified、judged、blind key 与 summary。
3. 明确其 baseline/lineage 比较关系。
4. 扩展完整 paper × arm × replicate 覆盖测试。

## 15. 变更定位指南

| Change goal | Primary owner | Related validation |
|---|---|---|
| 修改审稿职责或 rubric | `REVIEW_Request.md`、`reviewer_identity.py`、`frozen_reviewer_identity_v2.json` | source/task/suite SHA、META request 与 protocol boundary tests |
| 修改论文规范上下文 | `paper_context.py`、extractor Adapter | review context tests |
| 修改 MemoryProgram 合约 | `reviewer_runtime/program_protocol.py` | memory program protocol tests |
| 修改历史/目标时序 | `reviewer_runtime/engine.py`、`evaluator.py` | runtime decoupling 与 fidelity tests |
| 修改 sandbox 能力 | `reviewer_runtime/sandbox*.py`、`toolkit.py` | protocol boundaries 与 quality gates |
| 修改 Evolution 策略 | `search.py`、`reflector.py` | search loop 与 M★ fidelity tests |
| 修改数据 split | `build_coco_temporal_split.py` | temporal protocol/split tests |
| 修改 S2 freeze | `s2_search.py`、`build_frozen_s2_pool_v2.py` | frozen S2 tests |
| 修改正式 readiness | `formal_protocol.py`、`finalize_formal_readiness.py` | formal protocol tests |
| 修改 C 区 arms | `effectiveness_screening/arms.py` | effectiveness arm pipeline tests |
| 修改 Result Viewer | `viewer.py`、`viewer_data.py` | viewer data tests |
| 修改 Evolution Viewer | `evolution_viewer/` | viewer component tests |

## 16. 验证入口

从 `AgentReview/` 执行：

```bash
# 全仓单元与集成测试
python -m unittest discover -s tests -v

# Evolution Viewer 独立测试
python -m unittest discover -s evolution_viewer/tests -v

# temporal v2 / Frozen S2 完整性
python scripts/build_frozen_s2_pool_v2.py --check-only

# CLI surface
python -m agentreview.cli --help
python -m memory_evolution.cli --help
python run_single_paper_review_cli.py --help
```

正式运行还执行 external-root、模型 endpoint、bwrap、CoCo revision 和 embedding cache audit。快速开发运行通过显式 pilot/smoke 参数建立独立的非正式身份。

## 17. 文档所有权

| Subject | Authoritative document |
|---|---|
| 代码仓全架构 | 本文 |
| 领域术语 | `CONTEXT.md` |
| 使用入口 | `AgentReview/README.md` |
| 冻结 Reviewer Identity | `AgentReview/REVIEW_Request.md`（内容）、`AgentReview/agentreview/reviewer_identity.py`（渲染与校验）、`AgentReview/configs/frozen_reviewer_identity_v2.json`（语义 manifest） |
| 路径与资产身份 | ADR-0003 |
| Proxy 与 Official 分层 | ADR-0004 |
| Reviewer/Memory/Benchmark 边界 | ADR-0005 |
| A/B/C/D 数据协议 | ADR-0006 |
| 搜索与预算语义 | ADR-0007 |
| Seed Program 设计 | `AgentReview/memory_evolution/seeds/README.md` |
| Evolution Viewer 使用 | `AgentReview/evolution_viewer/README.md` |

源码、测试和 versioned manifest 是运行事实的最终校验源。文档变更与对应实现、测试、配置在同一变更中保持一致。
