# AgentReview 项目汇报上下文（供 Codex / Claude Code 生成汇报 PPT 使用）

> 生成日期：2026-08-20。基准 commit：`7256ffc`（AgentReview 仓库）。
> 本文是唯一应交给 PPT 生成器的上下文；与早期草稿冲突时以本文为准。
> 术语权威：`AgentReview_0811/CONTEXT.md`；代码架构权威：`AgentReview_0811/AgentReview M★ 代码架构全景解析.md`（0820 版）；运行权威：`AgentReview_0811/AgentReview/docs/CLOUD_FORMAL_ALIGNMENT_20260820.md`。

---

## 1. 项目是什么

**AgentReview (M★)**：一个通过 **Evolution 自进化**获得更好审稿能力的 LLM 审稿系统。

核心机制 **M★ (M-star)**：让不同 Seed 程序对「一个好的审稿系统应该怎么构建」形成不同假设，通过进化搜索找到能产生更好 Review 的 Reviewer，由**系统最外层的独立 Benchmark**（当前为 CoCoReviewBench）评价效果。

一句话设计原则：

> **给 Evolution 足够大的「如何成为一个好 Reviewer」的设计自由，但锁定 Reviewer 的基本职责（Frozen Reviewer Identity），并把所有 Benchmark 留在系统最外层作为独立裁判。**

要解决的问题：当前 LLM Reviewer 能生成流畅的 review，但

- 没有长期经验积累（每次评审从零开始）；
- 缺少针对不同评审维度的专业能力；
- 与人类 reviewer 评价存在差距，且没有任何自我改进机制。

**当前实现的可进化对象是 MemoryProgram（记忆程序）**；Memory 是当前的进化抓手，不是最终目的。workflow / critique / repair 等更广的 Review Harness 进化属于目标架构，尚未实现（PPT 不得写成已具备）。

---

## 2. 系统架构（五平面）

```
外部数据源（显式配置，只读）
  OpenReview 论文+人审 | CoCoReviewBench 3900 条 submissions | Semantic Scholar API
        |
        v
① 资产冻结平面（scripts/，一次性）
  build_coco_temporal_split.py  -> family 去重 + 时序切分 A/B/C/D
  build_frozen_s2_pool_v2.py    -> 时间安全 S2 冻结检索池（3889 篇，top-k=10）
  finalize_formal_readiness.py  -> 只读协议审计后登记 asset_manifest_v2
        |
        v
② 离线进化平面（memory_evolution/，约 9900 行）
  evolve-task / evolve-specialists
  21 次成功子代评测（3 lineage × 7）
  产物：每个任务一个 best_program.py（5 specialist + 1 META）
        |
        v
③ 有效性筛选平面（effectiveness_screening/）
  三臂 × 2 replicates 预注册命令矩阵 + 盲评 proxy 评测
        |
        v
④ 在线评审平面（run_single_paper_review_cli.py，主实验入口）
  单篇论文 -> 5 specialist 并发评审 -> META 综合 -> 锚点校验 -> 结构化输出
        |
        v
⑤ 最外层 Benchmark（Program 完全冻结后）
  Benchmark Adapter -> CoCoReviewBench 官方 pipeline（正式成绩只在这里产生）
```

### 2.1 审稿角色（6 个，不是 5 个）

| 角色 | 职责 |
|---|---|
| QUAL | 技术质量 |
| CLAR | 写作清晰度 |
| SIGN | 重要性 |
| ORIG | 原创性 |
| POL | 规范性 |
| **Meta-Reviewer** | 综合五份 specialist review 产出 meta-review（不是 AC） |

五 specialist 的基础职责属 **Frozen Reviewer Identity**（由 `REVIEW_Request.md` 的 5 维度 23 子类 rubric 定义，SHA-256 运行时校验），永久锁定、不得被 Evolution 修改——否则实验对象本身漂移。

### 2.2 进化平面模块要点

| 模块 | 职责 |
|---|---|
| `search.py` | 进化主循环：softmax parent 选择 -> LLM 突变 -> 质量门 -> 沙箱评测 -> 分数/谱系持久化（断点续跑） |
| `sandbox.py` / `sandbox_worker.py` | bubblewrap namespace + rlimit 隔离执行候选程序；**宿主进程永不执行候选代码** |
| `reflector.py` | LLM 突变（contextual patch + 编译修复循环，max_fix_attempts=3） |
| `quality.py` | 静态质量门：import 白名单、协议合规、编译冒烟 |
| `evaluator.py` | 单候选沙箱评测（A 区回放积累 memory -> B 区冻结评审 -> fitness） |
| `toolkit.py` | MemoryToolkit：冻结 S2 池加载/校验、内存向量集合、LLM 调用——进化程序唯一可用 API 面 |
| `coco_adapter.py` | FrozenCoCoAdapter：固定 CoCo git commit 加载官方评测实现 |
| `embedding_adapter.py` | BGE-M3 嵌入 + SQLite 持久缓存 |

---

## 3. 核心创新点（PPT 应重点讲的 6 点）

### 3.1 MemoryProgram：可执行的、在沙箱中进化的记忆程序

一个 MemoryProgram 是**可执行的 Python 文件**，不是 prompt 模板或配置。四个可进化部分：

1. `KnowledgeItem`——记什么（经验条目的结构）；
2. `KnowledgeBase` 的 write/read——怎么存、怎么取；
3. instruction 字符串——怎么提供给审稿 Agent。

候选程序由 LLM（reflector）突变产生，经静态质量门 + 编译冒烟后，在 bubblewrap 沙箱（无网络、宿主只读、rlimit、clearenv）中执行评测。

### 3.2 三条独立 Seed lineage：给进化真实的假设分叉

| Seed | 文件 | 假设 |
|---|---|---|
| `seed_vector_search` | `seeds/vector_search.py` | 向量检索组织记忆 |
| `seed_llm_summarizer` | `seeds/llm_summarizer.py` | LLM 摘要压缩记忆 |
| `seed_experience_learner` | `seeds/experience_learner.py` | 结构化经验学习 |

三条 lineage 全程独立记录（parent seed、program SHA256、fitness、promotion provenance），从三条 lineage 中选 global champion。

### 3.3 Fresh-State Replay：严格的时序经验协议（硬规则）

每次 fitness evaluation：

```
fresh empty Memory State
  -> 按冻结顺序遍历 A 区历史论文
  -> 对 Paper_i：读取已有 Memory（只含 Paper_1..i-1 的经验）
     -> 自己审稿
     -> 审稿完成后才把 Paper_i 的经验写入 Memory
  -> B 区 Evolution Validation 冻结评审 -> fitness
```

- 当前论文不能先写 Memory 再审自己，不能读自己刚产生的经验；
- 不同 Program 之间不共享 Memory；Candidate **不继承**父 Candidate 的运行时 Memory State——继承和突变的是 Program / code lineage，不是 Memory State；
- 每篇成功后原子 checkpoint，可恢复自身 verified prefix。

这保证「经验」真的来自 Program 自己做过历史任务（Self-Generated Experience），而不是继承或预生成。

### 3.4 四区数据隔离（Four-Zone Isolation）

CoCo 3900 条 submissions -> family 去重 -> 3889 个 representative targets -> 时序切分：

| 区 | 用途 | 数量 | 年份 |
|---|---|---|---|
| A: Historical / Experience Set | Program 回放积累自己的经验 | 2391 | ≤2022 |
| B: Evolution Validation | 进化选择/突变/晋升的 fitness | 299（QUAL 任务 materialize 为 150 validation） | 2023 |
| C: Effectiveness Holdout | Program 冻结后第一次独立 measurement（proxy） | 300 | 2023 |
| D: Held-out Benchmark Test | 官方 CoCo 终验 | 899 | 2024–2025 |

硬约束：四区 paper ID / normalized title / family **两两零重叠**，且 max(A/B/C cutoff) ≤ min(D cutoff)；每区独立 manifest + SHA256，formal 入口强制审计。防止三类泄漏：训练/测试重叠、时序泄漏（用未来审未来）、family 重复。

### 3.5 Human Reference Boundary + 最外层独立裁判

- 真人 review / gold 只允许进入三处：external evaluator、evolution fitness computation、benchmark evaluation；
- **禁止**进入：candidate-visible episode、Reviewer prompt、MemoryProgram 输入、检索到的 Memory、mutation prompt 等全部 candidate 侧；
- Reflector（突变者）只收到 scalar fitness、不含 gold 的 structured metrics、错误类型—— evaluator 的文字 rationale 不得回灌给 Candidate；
- 评测适配层 Benchmark Adapter 使系统 benchmark-agnostic：换 benchmark 只换 adapter，不改 Reviewer 核心。

### 3.6 冻结 S2 检索池：外部知识的公平供给

- 每篇论文按稳定 paper identity 统一执行 Semantic Scholar 检索，结果冻结、版本化（`frozen_s2_temporal_v2.jsonl`，3889 篇，top-k=10）；
- 时间安全：publication date 晚于该论文 cutoff 的结果拒绝入库；
- 同一篇论文的所有 Seed / Candidate 面对完全相同的检索池——外部知识来源不因 Program 不同而变化；
- **不默认注入 Reviewer context**：是否读、何时读、怎么用 S2 结果属于 Program 的策略自由度。

---

## 4. 端到端数据流

```
冻结资产（configs/）
  coco_temporal_split_v2 四区 jsonl + manifest（SHA256 锚定）
  frozen_s2_temporal_v2.jsonl（S2 池）
  asset_manifest_v2.json（formal 资产注册表）
        |
        v
任务数据（data/evolution_coco_v2_20260819，已冻结，勿重建）
  <TASK>/{episodes,queries,validation}.jsonl     -- candidate 可见
  <TASK>/evaluator_only/.../validation_references.jsonl  -- 仅评测侧
        |
        v
进化运行（runs/<run>/<TASK>/seed_0/）
  lineage.jsonl / scores.jsonl / pool/program_*.py / best_program.py
        |
        v  (MSTAR_PROGRAM_DIR)
在线评审（run_single_paper_review_cli.py）
  5 specialist 并发 + META + 锚点校验 -> outputs*/
        |
        v
最外层 Benchmark（Program 完全冻结后）
  Benchmark Adapter -> CoCoReviewBench 官方 pipeline
```

Candidate Agent 读到的输入 = 论文 Markdown 全文 + Candidate-Visible Metadata（paper_id/title/venue/year/role）+ source anchors；不含任何 human review、gold、taxonomy、judge 信息。B 区 gold 由 CLI 在评测侧按 ID join（要求 `evaluator_only=true`、`candidate_forbidden=true`）。

---

## 5. 评测体系

### 5.1 CoCoReviewBench（第一个正式 Benchmark，最外层独立裁判）

评估 AI 审稿的完整性（completeness）与正确性（correctness）。系统对 CoCo 的复用方式（2026-08-19 收敛后的准确口径）：

- Candidate 数据面直接复用 CoCo 论文 Markdown，只做 identity + allowlisted projection；
- formal evaluator 直接调用 CoCo 官方 `load_human_review_data`；不复制 CoCo 的算法；
- AgentReview 只保留 specialist schema projection（从 CoCo 已算出的 category_correct 取五维度）和 evaluator-only artifact 访问控制；
- Manifest 钉住 CoCo 官方实现及 left-out artifact 的 SHA-256。

### 5.2 评测服务部署（云端 gpu3，vLLM OpenAI-compatible API）

| 服务 | 模型 | 端口 | 用途 |
|---|---|---|---|
| ReviewSplit | Qwen3-8B-Split | 127.0.0.1:13099 | review 切句 |
| ReviewClassify | Qwen3-8B-Classify | 127.0.0.1:13098 | 句子维度分类 |

两个服务各占一张 A100；Evolution 主进程是 CPU-only job（`CUDA_VISIBLE_DEVICES=""`），Kimi 走远程 API，BGE-M3 可跑 CPU。

### 5.3 Embedding 的三个用途 + 持久缓存

BGE-M3（钉死 immutable revision `5617a9f6...`，1024 维 CLS + L2 norm，maxlen 8192）用于：

1. **Episode 子集选择**：A 区 2391 个 episode 文本 embedding -> Facility Location 选 125 篇/次；
2. **Validation 选择**：static 25 + rotating 5（正式模式无随机回退）；
3. **运行时记忆检索**：query embedding -> 向量相似度 -> 检索记忆（vector_search lineage 的程序内行为）。

**SQLite 持久缓存**（2026-08 新增，`cache/embedding_cache.sqlite3`，schema `embedding-cache-sqlite-v1`）：

- cache key = `(model_name, model_revision, semantics_version, text_sha256)`；
- 向量以 float32 BLOB 存储，带 vector SHA256 校验；
- 效果：固定语料（episodes/validation）只需计算一次，后续 run 全部 cache hit，显著缩短进化启动时间；不经过 LLM API、不影响实验 identity。

---

## 6. 实验设计

### 6.1 Three Arms 对照（比较层级）

| 臂 | 含义 |
|---|---|
| `no_memory` | 无记忆基线 |
| `seed` | 三条 seed program 原始版 |
| `evolved` | 进化后的 best_program |

`no_memory -> seed` 验证 memory 本身是否有用；`seed -> evolved` 验证 evolution 是否带来额外收益。实验记录落到 lineage 粒度（哪条 seed lineage、哪个 candidate）。

### 6.2 当前阶段：先做 QUAL（最小闭环）

先在 QUAL 一个维度上跑通完整闭环（进化 -> 晋升冻结 -> Holdout），再扩展到其余 specialist 与 META。`evolve-task QUAL` 不依赖 META，无阻塞。

### 6.3 正式 QUAL evolution 参数（formal seed0，parser 已支持）

```
iterations=21            # 21 次成功子代评测 = 3 lineage × 7（失败不占槽位）
static_size=25, rotating_size=5
episode_budget=125       # 每次 fitness 回放的 A 区子集
seed=0                   # 单个 outer seed
temperature=0.15
selection_repeats=3, selection_top_k=3, parent_policy=softmax
mutation_scope=full, max_fix_attempts=3
task / reflector / judge 模型均为 kimi-k3（Volcengine API）
reasoning_effort=low, max_tokens=8192, retry_max_tokens=16384
embedding: BAAI/bge-m3 @ 固定 revision
```

公平性保障（同一次 formal run 内）：相同 Base LLM、相同 API/runtime 能力边界、相同冻结数据与 S2 池、相同评测服务；但 context 组织、实际读多少 memory、是否用 S2 属于 Harness 策略自由度，只记录资源使用（LLM calls / tokens / context size / S2 usage / latency）用于 performance-cost 分析，不强制对齐。

### 6.4 后续测量（严格顺序）

1. **Effectiveness Holdout（C 区 proxy）**：Program 冻结后第一次独立 measurement；2 replicates 全链路独立（各自 fresh state 重放 A 区）；Blind Judging（judge 看不到臂标签/memory mode/program path，两臂同一脱敏管道）；结果只称 development / effectiveness measurement，**不称正式 CoCo 成绩**。
2. **Official CoCo 终验（D 区）**：Program fully frozen 后经 Benchmark Adapter 跑官方 pipeline；看过 D 后回改 Program 即失去 untouched-test 身份。

---

## 7. 当前进度（2026-08-20）

**已完成：**

- ✅ 四区 family-level temporal split v2 冻结（A/B/C/D=2391/299/300/899，SHA 锚定）
- ✅ 冻结 S2 v2 检索池（3889 篇，time-safe，query-bound）
- ✅ canonical 五任务数据 bundle（`data/evolution_coco_v2_20260819`，约 1.5GB，每任务 episodes=2391 / validation=150，已核 SHA；**不要再跑 prepare-specialists**）
- ✅ Human Reference Boundary 落地 + 泄漏审计（candidate 侧无 human answer；reference 全部在 evaluator_only/）
- ✅ Fresh-state replay、时序协议、三条 lineage tracking、strict checkpoint/resume——08-18 审计逐条代码级验证通过
- ✅ Formal protocol gate（资产 SHA / 四区隔离 / 时序 / 泄漏审计，fail-closed）
- ✅ CoCo evaluator 复用官方实现（pinned commit + SHA 钉扎）
- ✅ 两个 Qwen3-8B vLLM 评测服务部署且接口联通（13099/13098）
- ✅ Embedding SQLite 持久缓存（代码完成，本地测试通过）
- ✅ Evolution smoke 已启动并进入进化主循环（日志确认数据加载成功：episodes=2391、validation=150，Kimi/BGE/CoCo adapter 均可达）

**进行中 / 待完成（按顺序）：**

1. 🔄 QUAL smoke 完整跑通并按 PASS 标准验收（真实 mutation attempt 完成、生成 best_program.py 等产物、日志无 fallback）
2. Formal readiness 注册 + `--check-only` 复核（当前 asset_manifest_v2 仍是 `formal_ready=false` 旧状态；需先建旧 CoCo 绝对路径的 symlink/bind 兼容）
3. **修复 D-3（formal 搜索无 attempt/failure 上限）后**启动 formal QUAL seed0（21-iteration 计费长跑，审计建议修复后再开）
4. Effectiveness Holdout 三臂两重复测量
5. 其余 specialist + META evolution，最终 Official CoCo 终验

**已知运行风险（审计记录，PPT 可作为「工程严谨性」素材）：**

- CoCo adapter 严格 pin commit `2580687...`，当前云端 checkout HEAD 不一致时需建 pinned clone（smoke blocker）；
- 环境变量分裂 B1：需同时设置 `OPENAI_API_BASE` 与 `OPENAI_BASE_URL` 为相同值；
- BGE CPU 性能：vector_search lineage 每次 replay 大量调用 BGE，无缓存时慢（SQLite 缓存正是为此）；
- 沙箱 bwrap 需集群允许 user namespace。

---

## 8. 已解决的关键问题（体现工程难度）

| 问题 | 解法 |
|---|---|
| Human reference 泄漏（pilot 曾把人审拼进 candidate episode） | candidate/evaluator 数据物理分离 + leak audit + formal gate fail-closed；旧 pilot 产物降级为仅历史分析 |
| 进化不公平（Program 继承父代 Memory） | 继承 code lineage 不继承 Memory State；每次 fitness 从 fresh empty state 完整重放 |
| 数据泄漏（train/test 重叠、时序泄漏） | 四区隔离 + family 去重 + year-level cutoff + SHA 锚定 |
| 外部知识不公平（不同 Program 实时查 S2） | 冻结 S2 池统一供给，使用策略留给 Program |
| 候选程序安全 | bubblewrap namespace + rlimit + import 白名单 + 宿主永不执行候选代码 |
| 复现性 | 全链路 SHA256 钉扎（数据/seed/模型 revision/CoCo commit/REVIEW_Request rubric）+ 身份绑定 checkpoint + resume 一致性校验 |
| Embedding 重复计算拖慢每次进化启动 | SQLite 精确文本缓存（key 含 model/revision/semantics/text-hash） |
| 换 benchmark 需改系统 | Benchmark Adapter 抽象，benchmark-agnostic |

---

## 9. PPT 叙述建议

**受众**：算法工程师 / 研究组汇报。避免泛泛的 LLM Agent 介绍，全部篇幅给系统设计和公平性机制。

**建议结构（9–10 页）：**

1. **背景与主张**——LLM Reviewer 无经验积累；M★ 一句话原则（大设计自由 + 锁定职责 + 外层独立裁判）。
2. **整体架构**——五平面图（冻结 -> 进化 -> 筛选 -> 在线评审 -> 外层 Benchmark）；强调「进化离线、部署后程序不变」。
3. **Reviewer 分解**——5 specialist + Meta-Reviewer；Frozen Reviewer Identity vs 可进化外围的边界。
4. **MemoryProgram 进化**——可执行 Python 程序、沙箱执行、LLM 突变 + 质量门；三条 seed lineage 的假设分叉；一次 fitness 的 fresh-state replay 时序图（这是最有技术含量的图，建议单独一页）。
5. **数据安全与公平性**——四区隔离表格 + Human Reference Boundary + 冻结 S2 池。这一页是审稿人最容易挑战的地方，要给硬细节（两两零重叠、cutoff 约束、SHA 锚定、evaluator_only 物理分离）。
6. **评测体系**——CoCoReviewBench 最外层独立裁判；Qwen3-8B Split/Classify 服务拓扑；Blind Judging；Three Arms 设计。
7. **Embedding 与缓存**——三个用途 + SQLite 缓存设计（key 构成、一次计算多次复用、不影响实验 identity）。
8. **实验设计**——QUAL 最小闭环先行；formal 参数表（21=3×7、static 25/rotating 5、episode budget 125、kimi-k3 全角色）；资源使用记录原则。
9. **当前进度**——已完成清单（§7 的 ✅）+ 下一步里程碑（smoke 验收 -> readiness -> formal seed0 -> Holdout -> 官方终验）。
10. （可选）**工程严谨性**——审计驱动的开发流程：两轮独立审计、fail-closed 门禁、已知风险清单。

**表述红线（PPT 中不得出现的说法）：**

- ❌ 「系统已实现 workflow / critique / repair 等全 Harness 进化」——当前只实现了 MemoryProgram 进化；
- ❌ 「已有进化后效果提升的结论」——formal 进化尚未完成，无 effectiveness 结论；`runs/pilot_full_iter5_202607300117` 因边界违规已作废，不得引用其数字；
- ❌ 「在 CoCoReviewBench 上取得 X 分」——正式 CoCo 成绩只在 Program 冻结后的官方 pipeline 产生，当前最多是 Holdout 上的 development measurement；
- ❌ 「在线/持续进化」——当前是 Offline Evolution，在线属 future work；
- ❌ 用「20 iterations × 5 seeds」等旧口径——正式预算是 21 次成功子代评测（3 lineage × 7）。

---

## 10. 给 Codex / Claude Code 的任务描述

基于以上上下文与代码仓库（`AgentReview_0811/AgentReview/`），生成一份面向算法工程师的技术汇报 PPT：

1. **先读权威文档再动手**（按序）：
   - `AgentReview_0811/CONTEXT.md`（术语）
   - `AgentReview_0811/AgentReview M★ 代码架构全景解析.md`（代码架构，含 mermaid 图可直接改用）
   - `AgentReview_0811/docs/adr/0005、0006、0007`（系统边界 / 数据协议 / 进化语义）
   - `AgentReview_0811/AgentReview/docs/CLOUD_FORMAL_ALIGNMENT_20260820.md`（运行现状与命令）
2. 自动分析代码结构，核实本文的架构描述；如与代码冲突，以代码为准并在 PPT 中修正。
3. 架构图从全景解析的 mermaid 改编，不要凭空画；fresh-state replay 建议画成时序图。
4. 重点解释：MemoryProgram 进化机制、三条 Seed lineage、四区数据隔离、Human Reference Boundary、CoCoReviewBench 外层评测、Embedding 缓存、Three Arms 实验设计。
5. 严守第 9 节表述红线；所有数字（2391/299/300/899、21=3×7、150 validation、static 25/rotating 5、episode budget 125）不得改动。
6. 产出格式：HTML slides（如 reveal.js）或 PPTX 均可；中文、工程师向、图多于字。
