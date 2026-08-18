# AgentReview M★

一个通过 Evolution 获得更好 Review 的自进化审稿系统。Memory 是当前主要的可进化组件，不是最终目的；最终评价由最外层的独立 Benchmark（当前为 CoCoReviewBench）执行。本文件是项目的统一语言表：术语保持英文代码原名，说明用中文。只收录本项目特有的概念。

## Language

### 核心主张

**M★ (M-star)**:
本项目的核心机制与研究主张：让不同 Seed 对「一个好的审稿系统应该怎么构建」形成不同假设，通过 Evolution 找到能产生更好 Review 的 Reviewer，由外部 Benchmark 独立评价。一句话原则：**给 Evolution 足够大的「如何成为一个好 Reviewer」的设计自由，但锁定 Reviewer 的基本职责，并把所有 Benchmark 留在系统最外层作为独立裁判。**
_Avoid_: M-star 系统、自进化框架（作为整体系统名时）

**MemoryProgram**:
一个可执行的 Python 文件，定义某个审稿角色如何记取经验。包含四个可进化部分：`KnowledgeItem`（记什么）、`KnowledgeBase` 的 write/read（怎么存、怎么取）、instruction 字符串（怎么提供给审稿 Agent）。在沙箱（sandbox）中运行。Memory 只是当前的可进化抓手，不是最终目的。
_Avoid_: memory 配置、记忆模块、prompt 模板

### 系统边界（见 docs/adr/0005）

**Frozen Reviewer Identity**:
五类 Reviewer 的基础定义、核心审稿职责和对应子维度，永久锁定、不得被 Evolution 修改：QUAL 永远负责技术质量，ORIG 永远负责原创性，等等。否则实验对象本身漂移。注意这不是「整个 Reviewer prompt 冻结」——prompt 中属于 Harness 的部分可进化。

**Review Harness**:
围绕 Frozen Reviewer Identity 的可进化外围（目标架构）：Memory、review workflow、context organization、evidence strategy、retrieval strategy、Semantic Scholar 的使用、review generation / critique / repair strategy，以及后续合理的 Harness 组件。**注意区分**：Current implementation = MemoryProgram Evolution（当前唯一已实现的 Harness 进化）；Target architecture = Evolvable Review Harness（workflow / critique / repair / S2 自主策略等尚未实现，文档与对外表述不得写成已具备的能力）。

**Human Reference Boundary**:
真人 review / gold opinions 只允许进入三处：external evaluator、evolution fitness computation、benchmark evaluation。**不得进入**：candidate-visible Episode、Reviewer prompt、MemoryProgram input、KnowledgeItem extraction input、Self-Generated Experience、retrieved Memory、Program-specific Memory、candidate mutation prompt。**旁路规则**：evaluator / judge 的 textual rationale 若直接引用、摘要或明显复述 human gold，不得原样回灌给 Candidate；Evolution 只能获得 scalar fitness、不泄漏答案的 structured metrics、不含 gold 内容的错误类型。已知违反：`pilot_full` 的 episode 把 `VERIFIED HUMAN REFERENCE OPINIONS` 拼进了 candidate-visible 文本（待修复，见 docs/TODO.md）。

**Engineering Budget**:
工程运行边界与资源记录机制。

同一次正式实验内所有 Seed / Candidate 使用相同 Base LLM、相同 API / runtime 能力边界和相同工具可用性，以保证不存在某个 Candidate 获得额外系统权限。

但 Review Harness 如何组织 Context、实际读取多少 Memory、是否使用 S2、实际消费多少 token，属于策略行为，不要求不同 Program 强制相同。

系统只设置模型 / API 的物理限制以及防止异常失控所需的安全上限。

正式实验记录实际资源使用：

- LLM call count
- prompt / completion tokens
- actual context size
- memory context size
- S2 usage
- latency / cost（如可获得）

这些指标用于分析 performance–cost tradeoff，而不是把不同 Harness 强制压成相同资源使用模式。

**Benchmark Adapter**:
评测适配层：`Self-Evolving Reviewer → 生成 Review → Benchmark Adapter → CoCoReviewBench / 其他 Benchmark`。系统 benchmark-agnostic：Reviewer 和 MemoryProgram 的正式推理输入中不得出现任何 benchmark 的 gold、taxonomy、judge 信息；不得为某个 benchmark 硬编码 review 策略；换 benchmark 只换 adapter，不改 Reviewer 核心。

**Semantic Scholar Capability**:
统一的 Review Harness 外部知识能力。

系统对需要处理的论文根据稳定的 paper identity / paper ID 统一执行 Semantic Scholar 检索，并将结果冻结、版本化和缓存，形成 **paper-specific S2 Retrieval Pool**。

同一篇论文对应的所有 Seed / Candidate / Program 面对完全相同的 S2 Retrieval Pool，因此外部知识来源不因 Program 不同而变化。

S2 Retrieval Pool **不默认注入 Reviewer context**。Review Harness / Program 自主决定：

- 是否读取 S2 结果；
- 在审稿流程的哪个阶段读取；
- 如何选择、组织和利用已冻结的结果。

Program 不通过自行发起不同实时 query 来改变底层检索语料。检索结果本身是统一冻结的实验资产，使用策略才属于 Harness 自由度。

系统层 cache 的职责是：
`paper identity → frozen S2 retrieval result`

而不是：

- 自动把 related work 塞入 episode；
- 强制 Program 使用；
- 为不同 Program 返回不同外部知识。

_Avoid_: 默认 related-work 注入、Program-specific live S2 query、Memory 专属 S2 工具

### 审稿角色

**Specialist Reviewer**:
五个并行审稿的专家 agent：`QUAL`（质量）、`CLAR`（清晰度）、`SIGN`（重要性）、`ORIG`（原创性）、`POL`（规范性）。其基础职责属 Frozen Reviewer Identity。
_Avoid_: 子审稿人、维度 agent

**Meta-Reviewer**:
综合五个 specialist review 产出 meta-review 的最终 agent（`Final Meta-Reviewer`）。
_Avoid_: AC、Area Chair（本项目单论文管线中没有 AC 阶段）

### 经验与记忆

**Shared Raw Material**:
所有 Seed / Program 公平共享的同一批历史材料：**论文全文 + candidate-visible metadata + source anchors**。共享的是题目，不是答案，也不是提前加工好的经验。related work **不属于**默认共享材料（见 Semantic Scholar Capability）。当前正式 Historical source zone 是 frozen temporal manifest 的 A 区 2400 篇；具体 run 只能按 manifest 顺序冻结其可审计子集。旧 `memory_corpus.jsonl` 20 篇仅属 deprecated screening artifact，不是当前正式 Historical Set。

**Candidate-Visible Metadata**:
Reviewer 合理需要知道的论文描述信息：`paper_id`、`title`、`venue`、`year`、当前 Reviewer role / task（QUAL / CLAR / SIGN / ORIG / POL），以及其他确实影响审稿任务且不造成 benchmark 泄漏的论文属性。只有这类 metadata 可以进入 episode / prompt。

**Internal Provenance Metadata**:
系统内部 metadata：`paper_md_path`、dataset split、SHA256、asset version、source path、run_id、manifest 信息。用于实验复现与资产管理，**默认不得进入 Reviewer / MemoryProgram 的 candidate-visible prompt**。episode 不得把 metadata 作为整体拼入——必须按 Candidate-Visible 与 Internal Provenance 二分。

**Self-Generated Experience**:
每个 Seed / Candidate / Program 在共享 Historical / Experience Set 上运行自己的审稿策略，自产 review / trajectory / evidence，并自主决定记什么、怎么组织、怎么检索。不允许由固定的 no-memory Reviewer 预生成 AI review 供二次加工（见 Frozen Library Route）。**时序协议**（硬规则，见 docs/adr/0007）：每个 Program 从 **empty Memory State** 开始，按冻结顺序遍历历史论文；对 `Paper_i`：`读取 Paper_i → 可读 Paper_1..Paper_{i-1} 的已有 Memory → 自己审稿 → 审稿完成后才把 Paper_i 的经验写入 Memory`。当前论文不能先写 Memory 再审自己，不能读自己刚产生的经验；不同 Program 之间不共享 Program-specific Memory；Candidate 不继承父 Candidate 的运行时 Memory State——**继承和 mutation 的是 Program / code lineage，不是 Memory State**。每次 fitness evaluation 都是 `fresh empty state → replay Historical Set → 自产 Experience / Memory → Evolution Validation`。

**Episode**:
写入 MemoryProgram 的候选可见输入单位。必须遵守 Human Reference Boundary：不含真人 review / gold opinions。

### 数据协议（见 docs/adr/0006）

**Source Pool**:
系统可使用的原始数据来源通过 `OPENREVIEW_ROOT` / `COCOREVIEWBENCH_ROOT` 或 Asset Manifest 的 external-root resolver 显式配置，不在活跃脚本与文档示例中绑定机器绝对路径。

Source Pool 不等于实验 split。CoCoReviewBench 的 3900 篇是 benchmark source pool，经 `configs/coco_temporal_split_v1/manifest.json` 按 paper family 和时间冻结为 A/B/C/D；3900 不等于正式 D 的规模。旧“整个 CoCo/其 venue-year 全量 benchmark_reserved”规则已 superseded，禁止进入新 formal run。

**Historical / Experience Set**:
数据区 A：让各 Program 真正「做过历史任务并积累自己的经验」。Candidate 可见论文全文、Candidate-Visible Metadata、source anchors、可选且 time-safe 的 S2 Capability；不可见 human review / gold / taxonomy / judge。当前 frozen source zone 为 2400 篇（≤2022）；具体 run 可冻结其子集，但必须保持 manifest 顺序与 SHA。每篇成功后原子 checkpoint；同一 run 可恢复自身 verified prefix，不得跨 Candidate/replicate 继承 Memory。
_Avoid_: memory_corpus.jsonl（那只是论文选择清单）、frozen_memory_library（废弃路线）、训练集

**Evolution Validation Set**:
数据区 B：Evolution 判断 Candidate 优劣（selection / mutation / promotion）的论文集。当前 frozen source zone 为 300 篇（2023）；task materialization 可按任务引用可用性取其冻结子集。每篇 B review 只进入 evaluator/fitness；评估前恢复相同 A snapshot，B 不写 Historical Memory，顺序不改变后续 B 状态。

**Four-Zone Isolation**:
硬约束：`Historical ≠ Evolution Validation ≠ Effectiveness Holdout ≠ Held-out Benchmark Test`，两两 paper ID、normalized title、paper family 零重叠；同时要求 `max(A/B/C cutoff) <= min(D cutoff)`。每区独立 manifest / SHA256，formal 入口强制审计，不靠目录名。

### 进化与部署

**Offline Evolution**:
当前阶段的进化形态：离线运行进化搜索产出 `best_program.py`，晋升后部署；部署后的 Program 在使用过程中不变。在线/continual evolution 明确属于 future work。
_Avoid_: 在线进化、在线学习（指当前系统时）

**Seed Program**:
三种独立的初始假设，必须保持三条独立 lineage：`seed_vector_search`（`vector_search.py`）、`seed_llm_summarizer`（`llm_summarizer.py`）、`seed_experience_learner`（`experience_learner.py`）。每个 Seed 对「如何构建好的审稿系统」给出不同答案：记什么、怎么组织、怎么检索、是否利用检索能力，允许真实分叉。
_Avoid_: seed_experience（模糊单数，禁止再用）、初始版本、v0

**Program Lineage**:
实验记录的 lineage 单位：`evolved_from_vector_search` / `evolved_from_llm_summarizer` / `evolved_from_experience_learner`。每条记录必须携带 parent seed、lineage、candidate/program SHA256、fitness、promotion provenance；从三条 lineage 选 global champion 时同样保留。Three Arms（no_memory / seed / evolved）只是比较层级，具体实验记录必须带 lineage——Three Arms 与 Three Seeds 不混为一谈。

**Evolved Program**:
经离线进化搜索选出并晋升部署的 MemoryProgram。注意：`runs/pilot_full_iter5_202607300117` 的产物因 candidate-visible episode 含真人参照（违反 Human Reference Boundary），仅作历史分析用，**不作为 effectiveness 结论的权威 Program**；正式结论需在修复边界后重新进化。

**Three Arms**:
比较层级（不是实验记录单位）：`no_memory` / `seed` / `evolved`。`no_memory → seed` 验证 memory 本身是否有用；`seed → evolved` 验证 evolution 是否带来额外收益。具体实验记录必须落到 Program Lineage 粒度（哪条 seed lineage、哪个 candidate）。
_Avoid_: baseline 组（含义含糊）、seed_experience（用具体 lineage 名）

**Frozen Library Route (legacy)**:
已废弃路线：`frozen_memory_library.jsonl`（固定 no-memory Reviewer 预生成 AI review）→ 所有 Seed 二次加工。违反 Self-Generated Experience 原则。不再补完剩余 19 篇 history generation。

**Legacy Memory (`existing` mode)**:
`memory_library.py` 中基于 token 重叠打分的旧 memory 检索实现。仅作历史保留；正式实验必须显式指定 memory mode，不依赖默认值。
_Avoid_: seed_experience（二者不是一回事）

**Markdown Preprocessing (`md`)**:
论文输入的统一预处理方式（markdown 提取），由所有实验臂共享，不是独立实验变量。

### 评估

**CoCoReviewBench**:
评估 AI 审稿完整性（completeness）与正确性（correctness）的基准，是本系统的**第一个正式 Benchmark**，经 Benchmark Adapter 接入，处于系统最外层。其 gold / taxonomy / judge 信息不得进入 Reviewer 或 MemoryProgram 的推理输入。
_Avoid_: 评估指标（泛指时）、benchmark（不特指时）

**Effectiveness Validation**:
当前阶段的首要任务：比较 Seed 与 Evolved 在独立 Effectiveness Holdout 上的 development effectiveness，观察 Evolution 是否在未参与 selection 的新论文上带来真实提升。**不设 success gate**，结论由结果驱动。

当前最小闭环先做 QUAL；Program 在 Evolution Validation 上完成 selection / promotion 后冻结，再进入 Effectiveness Holdout。Effectiveness Holdout 的结果属于 development measurement，不称为正式 CoCoReviewBench 成绩。

正式 CoCoReviewBench 结果只在 Program 完全冻结后，通过最外层 Official Benchmark pipeline 获得。

**Effectiveness Holdout (proxy)**:
数据区 C：Evolution 完成、Program 冻结后的**第一次独立 effectiveness measurement**。当前 frozen source zone 为 300 篇（2023），screening 可在其中预注册子集；不参与 selection / mutation / promotion / Memory 构建，结果不反馈当前 Program。2 replicates 各自有 run identity/seed provenance；当前后端记录 `seed_control_available=false`。

**Official Benchmark**:
数据区 D：系统最外层的独立裁判。当前第一个为 CoCoReviewBench。

CoCoReviewBench source pool 共 3900 篇；当前 frozen D manifest 为 900 篇（2024–2025），且从未进入 A/B/C。D 只在 Program fully frozen 后经 Benchmark Adapter 运行；看过 D 后回改 Program 会使该 D 结果失去 untouched-test 身份。

**Temporal Leakage Boundary**：每篇论文都携带 `paper_review_cutoff`。当前源记录缺精确日期，因此 v1 明确采用保守 year-level cutoff（venue year 前一年 12-31）。S2 每个 target 缓存必须记录 cutoff、method/version、top_k/config、result SHA；publication date 缺失或晚于 cutoff 的结果拒绝。formal mode 无 provenance 或 future-leak audit 不通过即停止。

`Program fully frozen → Reviewer 生成 Review → Benchmark Adapter → 官方 pipeline`

Official Benchmark 不参与 Memory 构建、经验生成、Evolution、selection、mutation、promotion、prompt tuning。

**Blind Judging**:
强制评测协议（不可选）：judge 不得看到 Seed/Evolved 标签、memory mode、program path 或任何实验臂信息；送评前剥离 memory context 痕迹，两臂经同一脱敏管道。2 replicates + 固定生成参数；同一 program 多次运行的轻微 LLM 波动视为正常实验噪声。

### 工程原则

**Unified Interface**:
统一参数化接口原则：切换 specialist、实验臂、数据 split、运行模式都通过配置或参数完成，禁止硬编码分支。Evolution、Historical Experience Generation、Deployment / Review 使用**同一个 Canonical Rich Paper Interface**：同一 canonical paper-content builder，candidate-visible 基础输入统一为「论文全文 + Candidate-Visible Metadata + source anchors」，且 clipping algorithm、truncation algorithm、context budget、preprocessing 走同一实现——不允许 evolution 60K vs deployment 35K、两边不同裁剪方式这类分裂。context budget 可参数化，但同一次实验必须一致并记录 provenance。核心闭环：`同一批原始历史论文 → 每个 Seed 自己形成审稿经验和 Memory → Evolution → Review → 外部 fitness；Program 冻结后到独立 Benchmark 考试`。

**Asset Manifest**:
资产锚定机制：记录每项资产最终解析后的路径、SHA256、版本和数据 split，不依赖目录名推断资产。项目内部路径从 project root 相对解析；外部数据根经统一配置传入（见 docs/adr/0003）。

**OpenReview (data root)**:
由 `OPENREVIEW_ROOT` / CLI external root 显式配置的论文与真人审稿数据库；路径本身不是数据 split 身份，正式 A/B/C/D 身份只由 frozen temporal manifest 决定。

**Deprecated (marked, not yet deleted)**:
已废弃代码注册在 `docs/DEPRECATED.md`，现阶段以标记和隔离为主，不删除源码。

**Base LLM Boundary**:
Base LLM 不是 Frozen Reviewer Identity 的组成部分。同一次正式对照实验内所有 Seed / Candidate 必须使用相同 Base LLM 与对应冻结生成条件，以保证公平归因；跨独立实验可以更换 Base LLM，用于验证 M★ / Review Harness 的跨-backbone 泛化能力。
