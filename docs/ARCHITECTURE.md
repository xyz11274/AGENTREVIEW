# AgentReview 代码仓完整架构

最近核验：2026-08-31

代码基线：git HEAD 0406eae

实现进度：[STATUS.md](STATUS.md)

操作入口：[RUNBOOK.md](RUNBOOK.md)

领域语言：[../CONTEXT.md](../CONTEXT.md)

## 1. 架构范围与状态语言

本仓库包含一条当前目标路径和多条可达的 legacy/optional 路径。本文使用五种状态描述模块：

| 状态 | 定义 |
|---|---|
| 已实现 | 代码表达完整的本地语义，并有针对性测试 |
| 已接线 | 生产组合根或公开 CLI 实际调用该实现 |
| 合成验证 | Fake transport 或 synthetic data 已贯通调用链 |
| 接口保留 | 文件、CLI 或 Adapter 可被调用，当前目标路径暂未组合 |
| 正式物化 | 真实输入、身份、manifest、结果和门禁工件已形成闭包 |

当前总状态是：**Core v1 核心算法与领域模块主体已实现，G1 组合接线完成合成验证；正式语义闭包仍包含代码接线项，G2 与 G3 等待物化。**

## 2. 仓库级结构

~~~text
AgentReview_0827/
├── AgentReview/
│   ├── agentreview/                 # 领域数据、Core 数据模块、Reviewer runtime、旧 Arena
│   ├── memory_evolution/            # Core v1 组合/搜索与 legacy M★
│   ├── scripts/                     # 数据、Corpus、索引、capacity 物化工具
│   ├── effectiveness_screening/     # 保留的 legacy 三臂实验 workflow
│   ├── evolution_viewer/            # 只读 legacy 演化 Viewer
│   ├── configs/                     # temporal、资产和 legacy 配置
│   ├── tests/                       # 主测试
│   ├── papers_eval20/, test_data/   # 论文输入
│   ├── runs/, outputs*/             # legacy 运行结果
│   ├── artifacts/                   # 工程内阶段工件
│   ├── run_single_paper_review_cli.py
│   ├── viewer.py
│   └── README.md
├── artifacts/                       # 仓库级 Core/运行证据
├── docs/
│   ├── adr/                         # 当前架构决策
│   ├── protocol/                    # 冻结或待接线实验接口
│   ├── ARCHITECTURE.md
│   ├── STATUS.md
│   ├── RUNBOOK.md
│   └── DOCUMENT_INDEX.md
├── CONTEXT.md
└── README.md
~~~

论文 Markdown 是输入数据，artifact Markdown 是运行证据。它们与说明文档采用不同生命周期。

## 3. 总体依赖方向

~~~text
CLI / scripts / external application factory
                    │
                    ▼
        composition and orchestration
       memory_evolution/core_*.py
                    │
          ┌─────────┼───────────┐
          ▼         ▼           ▼
   Reviewer       Evolution   Suite / Official
   session        semantics       seam
          │         │           │
          └─────────┼───────────┘
                    ▼
        domain records and stores
  Corpus / Review Memory / Evidence / B-local
                    │
                    ▼
         filesystem and provider adapters
~~~

深模块把复杂状态封装在窄接口后：

- CorpusMaterialization 封装完整评审、projection、fragment 与 manifest。
- ReviewMemoryStore 封装角色索引、查询、hydrate、预算和 ledger。
- CoreV1ReviewerSession 封装 candidate sandbox 与 Host 执行 seam。
- CoreRoleEvolutionRunner 封装单角色搜索与评分生命周期。
- CoreV1FormalApplication 封装完整 CLI 生命周期。
- FinalReviewSuite 与 ProtectedOfficialAccess 封装冻结和受保护输入授权。

依赖从组合层指向领域层。Legacy 代码通过兼容 Adapter 保持可达，并与 Core v1 协议分支分离。

## 4. Research Validity Core v1

### 4.1 生命周期

~~~text
A-zone papers
    │
    ▼
625 no-memory Full Reviews
    │ canonical materialization
    ├──────────────► projections ─► fragments ─► five exact-flat indices
    │
    ▼
Core runtime config ─► validate-inputs
                           │
             ┌─────────────┴────────────────┐
             ▼                              ▼
      B-local manifest                Frozen Evidence
             │                              │
             └─────────────┬────────────────┘
                           ▼
              run-role × five roles
                           │
             three v2 seeds / shared pool
                           │
                 static-25 admission
                           │
              20 rotating-feedback attempts
                           │
                    global top-3
                           │
                  B-local full pool
                           │
                    one role winner
                           │
          QUAL + CLAR + SIGN + ORIG + POL
                           │
                    freeze-suite
                           │
            Protected Official Access
                           │
                 C/Human/CoCo inputs
                           │
                    run-official
~~~

### 4.2 独立 CLI 与组合根

memory_evolution/core_cli.py 提供六类命令：

- integration-smoke：使用 synthetic dependencies 验证 G1。
- closure-audit：分别汇总 G1、G2、G3。
- validate-inputs：构造 application 并验证正式输入互相一致。
- run-role：运行一个指定角色。
- freeze-suite：读取内存结果或五份 durable winner，冻结 Suite。
- run-official：读取冻结 Suite，授权受保护输入并调用 Official Adapter。

closure-audit 的退出码只依据 G1：G1 PASS 时进程返回 0，即使 G2/G3 仍为 PENDING。完整闭包判断必须读取 JSON 中三个独立状态。

真实命令从两种 composition seam 选择一种：

1. --config 调用 core_runtime.load_core_application_from_config()；
2. --application-factory 加载 MODULE:CALLABLE，由外部 factory 返回 application。

CoreV1FormalApplication 的公开生命周期是：

~~~text
validate_inputs()
run_role(role)
freeze_suite()
run_official()
close()
~~~

构造函数会立即执行输入验证。close() 关闭 application 内共享的 Qwen service。

### 4.3 正式配置接口

core_runtime.py 接受 schema research-validity-core-v1-runtime-config-v1。顶层字段采用闭集：

| 字段组 | 内容 |
|---|---|
| Corpus/Memory | corpus_directory、五角色 review_memory_indices |
| B/Evidence/Rubric | b_local_manifest、frozen_evidence_pool、五角色 role_rubrics |
| Embedding | embedding_query_factory、embedding_query_config |
| Qwen | model、base_url、采样参数、超时、retry 与 reasoning 配置 |
| Limits | Review Memory budget、context/summary/query token、capacity、sandbox 限额 |
| Search | run_seed、attempts、require_bubblewrap |
| Official | protected_input_paths、official_runner |

Embedding factory 接受一个 dict 并返回 callable；返回值同时暴露 token_count(text)。Official runner 接受 FinalReviewSuite 与 lazy-loaded protected inputs，并返回 Mapping。仓库当前的 legacy embedding 与 CoCo Adapter 采用其他接口形状，正式路径需要 Core-compatible Adapter。

review_memory_indices 与 role_rubrics 的 JSON 对象键顺序必须精确为 QUAL、CLAR、SIGN、ORIG、POL；loader 同时校验覆盖范围与插入顺序。

### 4.4 单角色进化

CoreRoleEvolutionRunner 固定角色语义：

1. 按固定顺序加载 vector_search_v2、llm_summarizer_v2、experience_learner_v2。
2. 三枚 seed 在同一 static-25 上评价并进入 shared candidate pool。
3. 默认执行 20 个 attempt，实际数量必须与 B-local rotating schedule 等长。
4. 每个 attempt 以 tau=0.15 的 binary64 softmax 选择 parent。
5. Parent 在本 attempt 的 rotating batch 上生成聚合反馈。
6. CoreReflector 根据 role、overall_fitness、subdimension_scores、failure_codes、evaluable_paper_count 生成 child Program。
7. Program 通过语法、Core protocol、closed read mode、sandbox runtime 和行为身份验证。
8. Child 在 static-25 上评价；Program identity 去重后决定准入。
9. 全局 eligible pool 按 static fitness 选择三个唯一 Program finalist。
10. 三个 finalist 在同一 B-local full pool 上重评，确定唯一 role winner。

Candidate-originated failure 会占用当前 attempt 并形成 disposition/error code。Role formal validity failure 与基础设施异常向上抛出，角色进程采用 fail-stop。

当前 production composition 把 Reflector 固定为 max_mutation_attempts=1、max_compile_fix_attempts=0。这两个值尚未进入 runtime config 或身份 manifest，属于正式 provenance 缺口。

### 4.5 Candidate × paper 数据流

~~~text
Candidate Program bytes
        │ immutable SHA path
        ▼
CoreV1ReviewerSession
        │
        ▼
SandboxSession worker
(bubblewrap supported; config controls requirement)
        │ build_retrieval_plan only
        ▼
closed RetrievalPlan
        │
        ▼
Host ReviewMemory executor
        ├─ vector fragments
        ├─ structured experience
        └─ retrieved fragments → one Host summary call
        │
        ▼
bounded memory context
        │
        ▼
fixed TaskAgent / Qwen Reviewer
        │
        ▼
candidate review
        ├─ candidate-blind applicability
        └─ candidate-specific scoring
~~~

Candidate sandbox 获得 paper-local plan 输入，只生成 retrieval plan。Host 持有 Corpus、Embedding、LLM、token counter 和 filesystem 能力。每次 Candidate × paper × role 都创建 fresh session。SandboxSession 支持 bubblewrap、clearenv、只读 bind、namespace 与 RLIMIT；CoreRuntimeConfig 原样传入 require_bubblewrap，G1 smoke 使用 false，正式门禁需要显式冻结并验证该值。

### 4.6 Review Memory

agentreview/review_corpus.py 定义：

- Canonical Full Review Record；
- 五角色与 23 个 subdimension；
- primary projection；
- fragment；
- deterministic JSON identity；
- CorpusMaterialization 的保存、加载和校验。

agentreview/review_memory.py 定义：

- 五角色隔离的 exact-flat float32 index；
- ReviewMemoryBudget；
- paper-local ledger；
- search、hydrate 和结构化经验读取；
- 原子预算检查与扣减；
- query token 和 delivered token 约束。

reviewer_runtime/memory_program_v2.py 定义三种 closed read mode。Summary mode 在检索完成后由 Host 执行一次 Qwen 摘要；其余模式保持 model-free memory read。

### 4.7 Frozen Evidence

agentreview/frozen_evidence.py 实现 immutable record、set manifest、pool load/save 和 evaluator context rendering。Core application 当前把目标论文的完整 Evidence context 传给 Applicability 与 Scoring。

协议还定义 Candidate-facing Evidence API、调用预算和访问 audit；当前组合根尚未把该 API 接入 Candidate session。它属于 G2 接线项。

### 4.8 Evaluator 与 fitness

memory_evolution/role_evaluator.py 实现两阶段接口：

1. Applicability 读取论文、结构上下文、Frozen Evidence 与 role rubric，并输出 subdimension 决策。
2. Scoring 引用 canonical applicability identity，读取 Candidate review，输出每个适用 subdimension 的 0–4 分数与事件。

ApplicabilityConsistencyGate 保证同一 paper × role 的 Candidate 共享适用集合。Fitness 先聚合单篇适用 subdimension，再跨论文聚合，最终使用固定精度 Decimal 字符串。

当前代码的数据模型包含 CanonicalApplicabilityRecord 与 CanonicalScoringRecord。Core application 的 durable winner audit 尚未保存 evaluator records 与 raw attempts；协议中的 CanonicalPaperEvaluationRecord、完整 machine registry 和 durable evaluator record 属于待物化接口。

### 4.9 Qwen 调度

core_scheduler.py 包含三层能力：

| 模块 | 当前使用 |
|---|---|
| GlobalQwenService | 已接 Core application；在单个 application 进程内执行共享容量控制与 role round-robin/FIFO |
| ScheduledToolkitProxy | 已接 Core application；给 Reviewer、Evaluator 和 Reflector 的 LLM call 加入逻辑角色 |
| GlobalQwenScheduler | 批式调度接口；当前由测试覆盖 |
| run_bounded_paper_dags | paper DAG 与 active-paper window 接口；当前由测试覆盖 |

run-role 每次启动独立 application，因此多个 CLI 进程各自拥有 service。CoreRoleEvolutionRunner 当前逐 paper 串行评价。跨五角色单一 scheduler 与 production paper DAG 属于待接线能力。

### 4.10 Suite 与 Official

agentreview/final_suite.py 定义 RoleWinner、FinalReviewSuite、freeze_final_suite() 和 ProtectedOfficialAccess。

- Suite 要求五个角色各有一个 winner。
- Role 顺序固定为 QUAL、CLAR、SIGN、ORIG、POL。
- meta_enabled 固定为 false。
- Suite identity 从 canonical payload 计算。
- ProtectedOfficialAccess 先校验 Suite，再调用 lazy protected loader。

core_application.py 把 winner、Suite 和 Official result 原子写入磁盘。当前 authorization 生命周期保存在进程内，run-official 可在新进程再次执行；协议定义的 durable authorization journal 与一次性发布语义属于待接线项。

## 5. Core v1 模块地图

### 5.1 memory_evolution

| 文件 | 职责 | 依赖/接口 | 状态 |
|---|---|---|---|
| core_cli.py | 独立 Core CLI | config loader 或 external factory | 已接线 |
| core_runtime.py | 严格磁盘配置与 provider composition | Core runtime config | 已接线；真实配置待提供 |
| core_application.py | 完整生命周期组合根 | Corpus/Memory/B/Evidence/Qwen/Official | 已接线 |
| core_formal.py | 单角色语义、评价批次、winner | core_search、role_evaluator | 已接线 |
| core_search.py | shared pool、attempt、softmax、dedup、top-3 | callback interfaces | 已接线 |
| core_reflector.py | 聚合反馈到 Program mutation | Qwen completion、program validator | 已接线 |
| role_evaluator.py | applicability、scoring、fitness | role rubric、completion | 已接线 |
| b_local_manifest.py | role view、static/rotating/full、threshold | frozen JSON manifest | 已接线；正式 manifest 待生成 |
| core_scheduler.py | capacity、role fairness、paper DAG | toolkit proxy | service/proxy 已接；DAG 接口保留 |
| capacity_gate.py | 多次 capacity 结果选择 | benchmark result records | helper 已实现；正式结果待生成 |
| core_closure.py | G1/G2/G3 汇总 | smoke report、artifact root | 已接线 |
| core_smoke.py | synthetic production composition smoke | Fake Qwen/hash embedding | 合成验证 |

### 5.2 agentreview Core 领域模块

| 文件 | 职责 | 状态 |
|---|---|---|
| review_corpus.py | canonical Corpus、projection、fragment、身份与 materialization | 已实现 |
| review_corpus_builder.py | no-memory Reviewer completion 到 Corpus record | 已实现 |
| corpus_bootstrap.py | A 区请求与上下文 bootstrap | 已实现 |
| corpus_checkpoint.py | Corpus build checkpoint/commit | 已实现 |
| corpus_freeze_audit.py | Corpus cardinality、identity 与 freeze audit | 已实现 |
| corpus_transport.py | Corpus generation transport | 已实现 |
| corpus_role_prompts_v4.py | 五角色 Corpus prompt | 已实现 |
| review_memory.py | role index、store、budget、ledger | 已实现 |
| frozen_evidence.py | immutable Evidence pool | 已实现；正式数据待生成 |
| final_suite.py | winner、Suite、Protected Official seam | 已实现 |

### 5.3 reviewer_runtime

| 文件 | 职责 | 路径 |
|---|---|---|
| memory_program_v2.py | Core retrieval-plan protocol 与 CoreV1ReviewerSession | Core |
| sandbox.py / sandbox_worker.py | 支持 bubblewrap、clearenv、只读 bind、RLIMIT、JSON-RPC worker；config 决定是否要求 bubblewrap | Core 与 legacy 分支 |
| models.py | Episode、ReviewRequest、ReviewResult | 共享接口 |
| toolkit.py | LLM、embedding、vector collection、冻结 S2、资源计数 | Host Adapter |
| task_agent.py / tasks.py | 固定 Reviewer 任务执行 | 共享实现 |
| engine.py | 旧 ReviewerEngine/ReviewerSession | standalone legacy |
| program_protocol.py | 旧 KnowledgeItem/Query/KnowledgeBase 协议 | legacy |
| memory_runtime.py | 旧 MemoryHarnessAdapter | legacy |
| episodes.py / requests.py | 旧 episode/request 构造 | legacy |
| artifacts.py | optimizer-independent CandidateReviewRecord 与 append writer | standalone、direct review、legacy evaluator 和独立 CoCo 的共享输出 Interface；Core composition 当前未接 writer |

顶层 memory_evolution/checkpoint.py、integration.py、program_protocol.py、sandbox.py、toolkit.py、task_agent.py、sandbox_worker.py 保留兼容导出，继续服务测试和 legacy caller。

## 6. 正式数据准备工具

scripts/ 下的 Core 工具按依赖关系排列：

| 阶段 | 脚本/输入 | 产物或关系 |
|---|---|---|
| 输入 | 预注册 A selection | 由外部数据准备阶段提供；bootstrap 脚本消费该输入 |
| 1 | materialize_core_v1_corpus_bootstrap.py | A manifest、625 个请求与结构上下文 |
| 2 | preflight_core_v1_corpus_service.py | Corpus Qwen service preflight |
| 3 | build_core_v1_review_corpus.py | checkpointed 625-review generation |
| 3A | supervise_core_v1_corpus_bootstrap.py | 与已启动 builder 并行；监督状态，并在成功后调用 materializer |
| 3B | materialize_core_v1_review_corpus.py | 手工路径在 builder 成功后调用；生成 canonical Corpus、projection、fragment |
| 4 | build_core_v1_review_memory_index.py | 五角色 exact-flat index |
| 5 | build_core_v1_capacity_workload.py | mixed workload |
| 6 | benchmark_core_v1_qwen_capacity.py | capacity measurements |
| 7 | select_core_v1_capacity.py | capacity selection |

build_coco_temporal_split.py、build_frozen_s2_pool*.py、prewarm_embedding_cache.py 和 finalize_formal_readiness.py 属于 temporal/legacy formal 资产工具。当前仓库还需要 B-local materializer、Evidence materializer、Evaluator production smoke、完整 runtime profile/final preflight 与 Core Official Adapter 才能完成 G2 composition。

Review Memory index builder 当前输出 qual.review_index.json、clar.review_index.json、sign.review_index.json、orig.review_index.json、pol.review_index.json；closure 要求 review_memory/QUAL.json、CLAR.json、SIGN.json、ORIG.json、POL.json。正式提交阶段需要统一生成器与 closure 的 canonical 命名并重新校验内容；当前仓库尚无该提交 Adapter。

## 7. 持久化与恢复

### 7.1 Core 当前实现

- Program bytes 写入 programs/<sha256>.py；相同路径内容保持不可变。
- 每个完成角色写入 role_winners/<ROLE>.json。
- Suite 写入 final_suite.json。
- Official Adapter 返回值写入 official_result.json。
- JSON 通过临时文件、flush、fsync 和 os.replace 原子提交。
- freeze-suite 可从五份 durable winner 恢复。
- run-official 可从 final_suite.json 恢复。

Core search 的 attempt、pool 和 RNG 当前保存在内存中。角色中断后从三枚 seed 重新执行；Candidate failure 作为已消费 attempt 保存于完成后的 role audit。最小 append-only controller journal 仍是正式闭包缺口。

### 7.2 Legacy M★

legacy search.py 实现 formal-search-controller-checkpoint-v3，覆盖 subset selection、seed、search、selection、final 和 complete 阶段。恢复时验证 identity、截断 JSONL 到 committed length，并恢复 pool、RNG、attempt 和 selection。

这套 checkpoint 绑定旧 M★ 数据模型；Core v1 保持独立恢复语义。

## 8. G2/G3 预期工件

core_closure.py 按以下布局检查：

~~~text
<formal-artifact-root>/
├── corpus/
│   ├── manifest.json
│   ├── full_reviews.jsonl
│   ├── projections.jsonl
│   └── fragments.jsonl
├── review_memory/
│   ├── QUAL.json
│   ├── CLAR.json
│   ├── SIGN.json
│   ├── ORIG.json
│   └── POL.json
├── b_local/manifest.json
├── evidence/pool.json
├── capacity/
│   ├── results.json
│   └── selection.json
├── runtime/main_formal_runtime_profile.json
├── preflight/
│   ├── evaluator_smoke.json
│   └── final_preflight.json
├── formal/
│   ├── role_winners/<ROLE>.json
│   └── final_suite.json
└── official/
    ├── authorization.json
    ├── official_result.json
    └── final_report.json
~~~

G2 对 Corpus 额外检查 625 条记录。其余 closure 条目主要检查文件存在；stage-specific loader、preflight 与身份验证负责内容正确性。

Core application 的直接 output_dir 布局是 role_winners/、final_suite.json、official_result.json。若把 output_dir 指向 <formal-artifact-root>/formal，winner 与 Suite 会落入 closure 期望位置，而 Official result 会落入 formal/official_result.json；closure 期望 official/official_result.json。外层正式 controller 需要把 Official 阶段写入 official/，并同时提交 authorization.json 与 final_report.json。当前代码尚未完成这层布局组合，同一个 CLI output_dir 目前无法直接满足完整 G3 closure 布局。

## 9. 其他可达运行路径

### 9.1 Standalone Reviewer

入口 python -m agentreview.cli review：

~~~text
single request or temporal C/D requests
    → ReviewerEngine
    → legacy ReviewerSession
    → sandboxed KnowledgeBase.write/read
    → CandidateReviewWriter
~~~

该 session 接受旧 Memory Program protocol，并主动拒绝 Core v1 Program。它是兼容 Reviewer 接口，也是 candidate_reviews.jsonl 的生产者。

### 9.2 Legacy direct review

入口 run_single_paper_review_cli.py：

~~~text
Markdown discovery / frozen context bundle
    → canonical paper context + source anchors
    → optional legacy memory context
    → five specialist Reviewer calls
    → optional META
    → per-paper JSON artifacts
    → optional memory-library writeback
~~~

输入支持单 Markdown、目录 glob 和 manifest。默认 memory_mode=none、enable_meta=false、五个 reviewer worker。每篇输出 reviews.json、candidate_reviews.jsonl、memory.json、review_history.json、timing.json；批级输出 batch_manifest.json。

### 9.3 Legacy M★

入口 memory_evolution.cli 提供 prepare-specialists、prepare-test、prepare-meta、cache-meta-inputs、evolve-task、evolve-specialists、evolve-meta、evaluate、final-evaluate-suite。

该路径使用旧 KnowledgeItem/Query/KnowledgeBase、Human/CoCo fitness、candidate-local history、lineage 和 META。它继续服务历史实验复现，并与 Core v1 的 reference-free/shared-pool/no-META 语义分开。

### 9.4 独立 CoCo 评价

agentreview.coco_eval 接受 candidate review 文件并驱动 legacy CoCo 评价。agentreview/_coco_official_runner.py 提供 CoCoReviewBench CLI bridge。二者当前都采用 legacy 调用接口，与 Core Official runner 的二参数 callable seam 形状不同。

## 10. 保留的接口与工具

| 接口 | 位置 | 当前身份 |
|---|---|---|
| Old Arena | arena.py、paper_review_arena.py、environments/、ui/ | 休眠库接口；无当前 shell 主链 |
| Provider Adapters | backends/ | OpenAI 当前使用；Human/Cohere/Transformers/Claude/Dummy 等保留 |
| Database Adapter | database.py | 旧 Arena 的可选 Supabase 输出 |
| OpenScholar bridge | openscholar_bridge/、utility/s2_retrieval.py | use_search_apis.py、utils.py 与 utility helper 可执行但主链未接；其余主体文件是三引号历史快照，只形成模块文档字符串 |
| Result Viewer | viewer.py、viewer_data.py | 只读 legacy direct-review 输出 |
| Evolution Viewer | evolution_viewer/ | 只读 legacy M★ artifacts |
| Effectiveness screening | effectiveness_screening/ | legacy 三臂实验接口；配置与 arm/META 语义待统一 |
| Paper/data tools | dataset/、paper_processor.py、source_map.py | 输入准备与 canonical context |

暂未启用的接口以清晰状态保留。它们的存在只证明可扩展 seam，正式路径状态由 STATUS 与工件门禁说明。

## 11. Effectiveness screening

该 workflow 的设计链是：

~~~text
selection
  → live S2 freeze
  → context bundles
  → Program/history freeze
  → generation matrix
  → proxy classify/judge
  → blind pairs
  → summary gate
~~~

它调用 run_single_paper_review_cli.py，属于 legacy direct-review 实验。当前实现与配置在 temporal manifest、META 输出和 arm/lineage identity 上存在结构性失配，详细根因见 STATUS.md。代码与 CLI 作为暂未启用接口保留。

## 12. Viewer

- viewer.py 读取 outputs/single_paper_review/<sample>/。它当前按包含 META 的完整输出判断完整度，默认五 specialist 输出会显示 partial。
- evolution_viewer 读取 lineage.jsonl、scores.jsonl、pool/*.py 和 traces，只做 AST/文本分析。
- 两个 Viewer 都是 read-only consumer，并保持运行时依赖单向。

## 13. 测试架构

主测试位于 AgentReview/tests，覆盖：

- Corpus、projection、freeze audit 与 checkpoint；
- Review Memory、三种 Core read mode、budget 和 isolation；
- B-local manifest、Frozen Evidence 和 capacity selector；
- reference-free evaluator、canonical fitness 和 applicability consistency；
- shared-pool search、role runner、scheduler 和 Final Suite；
- Core application integration smoke 与三层 closure；
- legacy Reviewer/M★、CLI、temporal、direct review 等兼容路径。

Evolution Viewer 在 evolution_viewer/tests 拥有独立 10 项测试。测试可证明模块合同与 synthetic wiring；正式 G2/G3 状态由真实工件证明。

## 14. 变更影响地图

| 变更目标 | 首要模块 | 同步检查 |
|---|---|---|
| Corpus schema | review_corpus.py | builder、freeze audit、ReviewMemoryIndex、G2 manifest |
| Read mode/plan | memory_program_v2.py | v2 seeds、sandbox worker、budget tests |
| 角色/subdimension | ROLE_SUBDIMENSIONS、rubric | B-local、indices、Evaluator、Suite、协议 anchors |
| Search 语义 | core_search.py、core_formal.py | Candidate identity、attempt audit、top-3/winner tests |
| Qwen capacity | core_scheduler.py、capacity_gate.py | workload、benchmark、runtime config |
| Official seam | final_suite.py、core_application.py | lazy protected loader、authorization artifacts、Official Adapter |
| Runtime config | core_runtime.py | config schema、factory Adapter、RUNBOOK |
| Legacy Program | reviewer_runtime/program_protocol.py | memory_evolution.cli、standalone Reviewer、Evolution Viewer |
| Direct review output | run_single_paper_review_cli.py | viewer_data.py、effectiveness screening、CoCo adapter |

设计新 seam 时，优先让复杂实现隐藏在窄接口后，让调用方依赖稳定领域对象与 canonical artifacts。
