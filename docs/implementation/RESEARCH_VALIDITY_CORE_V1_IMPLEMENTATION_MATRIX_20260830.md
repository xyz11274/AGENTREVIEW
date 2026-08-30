# Research Validity Core v1 最小实现矩阵

状态：implementation baseline（2026-08-30）。本文只把已冻结协议映射到当前代码与实施任务，不新增协议 identity、状态机或研究语义。复杂 Journal recovery、mirror、replacement、publication/archive transaction 全部归入 `DEFER_HARDENING_V2`，不得阻塞首次 Core v1 formal experiment。

## 1. Baseline audit（实施前）

本节与第2节的 `Current implementation` 列记录开始改造时的基线缺口；实施后的权威状态见第6节。

当前主干尚不满足 Core v1，关键原因不是缺少审计包装，而是三条研究主路径仍是旧语义：

1. `memory_evolution/evaluator.py` 对每个 Candidate 重新按 A-paper 顺序执行 `review(previous memory) -> KnowledgeItem LLM -> write`，没有独立的 625 条 no-memory Full Review Corpus；
2. `agentreview/reviewer_runtime/task_agent.py` 仍用 LLM 生成 Memory Query 和 KnowledgeItem，三个 seed 仍以 candidate-local write/read Memory 为中心；
3. `memory_evolution/formal_protocol.py`、`memory_evolution/cli.py` 与 `memory_evolution/metrics.py` 仍加载 Human/CoCo validation references，并以旧 CoCo fitness 参与 Evolution。

现有可复用基础包括：Reviewer Core、Reflector、sandbox、source anchors、temporal family split、candidate-safe feedback过滤、shared-pool softmax基本结构、单篇五Reviewer线程池与checkpoint。但这些基础必须从旧数据流中解耦后才能进入 Core formal。

## 2. Core requirement matrix

| Requirement | Classification | Current implementation | Evidence / target seam |
|---|---|---|---|
| A区冻结125 papers | MUST_VERIFY_BY_TEST | temporal split与prepare已有family/zone逻辑，但当前正式数据目录并不证明新Corpus manifest恰好125 | `memory_evolution/agentreview_data.py`; `configs/coco_temporal_split_v2/` |
| 125 × 5 = 625 no-memory Canonical Full Reviews | MUST_IMPLEMENT_NOW | 不存在独立Corpus build；当前每Candidate重放A并读取已有memory | add `agentreview/review_corpus.py`; modify `memory_evolution/agentreview_data.py`, `memory_evolution/cli.py` |
| Corpus build禁止读取旧Memory/External Evidence | MUST_IMPLEMENT_NOW | `ReviewerSession.ingest_episode()`先review已有memory，违反要求 | replace A replay in `memory_evolution/evaluator.py`; add no-memory Reviewer request path |
| Canonical Full Review closed schema与raw/canonical records | MUST_IMPLEMENT_NOW | 仅有自由文本 `CandidateReviewRecord`，无role固定subdimension sections | add corpus schema/parser/materializer in `agentreview/review_corpus.py` |
| Full Review Corpus immutable manifest | MUST_IMPLEMENT_NOW | 不存在 | add corpus manifest, hashes and load-time validation |
| role/subdimension Primary Projection | MUST_IMPLEMENT_NOW | 旧Vector seed按500字符切片，无subdimension projection | `agentreview/review_corpus.py` |
| 仅超长section继续Fragment，阈值由配置冻结 | MUST_IMPLEMENT_NOW | 旧实现所有文本固定500字符切片 | deterministic paragraph/sentence/token fallback fragmenter |
| 五个role-specific indices | MUST_IMPLEMENT_NOW | Candidate临时collection，无冻结Corpus index | add `agentreview/review_memory.py` exact-flat role index |
| Full Review仍可hydrate | MUST_IMPLEMENT_NOW | 无统一Review Memory interface | `ReviewMemorySession.vector_search/get_projection/get_full_review_record` |
| Embedding/index确定性与identity | MUST_VERIFY_BY_TEST | Qwen embedding adapter、cache与fingerprint已有，但index/dot-product语义不满足新Corpus contract | reuse `memory_evolution/embedding_adapter.py`; test canonical vectors/order |
| Memory query construction无LLM | MUST_IMPLEMENT_NOW | `TaskAgent.generate_query()`调用structured LLM | replace with Program deterministic query method; deny LLM capability during query/retrieval |
| retrieval/rerank/filter/hydration无隐藏LLM | MUST_IMPLEMENT_NOW | toolkit向所有Program暴露通用`llm_completion`; Summarizer在`read()`内静默调用并fallback | capability-scoped Review Memory runtime |
| 删除KnowledgeItem generative LLM | MUST_IMPLEMENT_NOW | `TaskAgent.extract_knowledge_item()`始终调用LLM；Experience seed生成lesson/fact | remove A write path and generative KnowledgeItem requirement |
| `DIRECT_RETRIEVAL` | MUST_IMPLEMENT_NOW | Vector seed近似但使用candidate-local 500-char chunks | v2 seed over immutable Review Memory interface |
| `POST_RETRIEVAL_LLM_SUMMARY` | MUST_IMPLEMENT_NOW | 旧Summarizer混合存储/检索/LLM且silent fallback | v2 seed; explicit post-retrieval summary capability only |
| `STRUCTURED_EXPERIENCE_RECALL` | MUST_IMPLEMENT_NOW | 旧Experience Learner生成lesson/fact | deterministic grouping of retrieved immutable review bytes |
| Reviewer LLM保留 | ALREADY_SATISFIED | `TaskAgent.generate_response()`与online specialist players调用Reviewer模型 | `agentreview/reviewer_runtime/task_agent.py`; `run_single_paper_review_cli.py` |
| Evolution Reflector LLM保留 | ALREADY_SATISFIED | `mutate_program()`与`compile_fix_loop()` | `memory_evolution/reflector.py` |
| Reflector只看聚合、预注册反馈 | MUST_VERIFY_BY_TEST | 已有field allowlist，但仍传case-level score arrays及population source/score | tighten `memory_evolution/evaluator.py`, `search.py`, `reflector.py` tests |
| META默认关闭，META-LLM仅显式实验 | MUST_IMPLEMENT_NOW | online与sealed test默认执行META | `run_single_paper_review_cli.py`; `memory_evolution/cli.py`; config defaults |
| 五角色reference-free evaluator | MUST_IMPLEMENT_NOW | formal强制CoCo references、ReviewSplit/Classify与旧CoCo metric | add role evaluator module; replace `coco_review` Evolution metric |
| Human/OpenReview/CoCo不进入Evolution | MUST_IMPLEMENT_NOW | `formal_protocol.py`与CLI明确加载B Human references | delete Evolution reference join; keep Official-only adapter |
| role-specific applicable-subdimension macro fitness | MUST_IMPLEMENT_NOW | 当前score为0..1旧metric mean | add canonical evaluator records, applicability + 0..4 aggregation |
| Candidate independence/frozen denominator | MUST_VERIFY_BY_TEST | B restore同A snapshot，但新Corpus后需fresh paper sessions与applicability一致性 | evaluator isolation tests |
| shared-pool M★，3 seeds + global 20 attempts | MUST_IMPLEMENT_NOW | 已有shared candidate list/global softmax/20 loop；仍保留per-lineage counters与旧final selection | simplify `memory_evolution/search.py` without changing binary64 softmax order |
| rotating只给聚合Reflector feedback | MUST_IMPLEMENT_NOW | rotating不直接promotion，但feedback仍含per-case arrays/traces | aggregate to role subdimension vector/failure codes only |
| strict Program identity/dedup/candidate birth | MUST_IMPLEMENT_NOW | 仅file SHA与序号ID，无admission-time strict dedup/canonical birth | `memory_evolution/search.py` and focused identity helpers |
| global top-3 finalist/full-pool winner | MUST_IMPLEMENT_NOW | 当前selection仍使用lineage champions/repeated selection | replace with global unique top-3 and full-pool reevaluation |
| 五role各自winner，固定顺序Final Suite | MUST_IMPLEMENT_NOW | online五输出存在，但Evolution没有新Suite controller/manifest | add minimal suite materializer/controller |
| C untouched直到Suite freeze | MUST_IMPLEMENT_NOW | sealed test路径在同CLI内加载test/reference并运行META | split Official command and require frozen Suite manifest |
| Protected C/Human access boundary | MUST_IMPLEMENT_NOW | CoCo adapter可直接构建，缺少Suite-authorized capability check | Official-only adapter guard + access audit records |
| Official CoCo仅report-only | MUST_VERIFY_BY_TEST | 有official runner，但Evolution仍直接用CoCo | isolation test must prove no import/read/call before authorization |
| 单Qwen TP4 global scheduler | MUST_IMPLEMENT_NOW | clients直接调用同endpoint；五Reviewer仅ThreadPool并发 | add bounded scheduler interface/adapter shared by Reviewer/Evaluator/Reflector/fix/Summarizer |
| 五role/paper DAG有界并行 | MUST_IMPLEMENT_NOW | online有五ReviewerThreadPool；Evolution paper评价仍串行 | add deterministic bounded executor with frozen aggregate order |
| `max-num-seqs=1/2/4` capacity gate | MUST_IMPLEMENT_NOW | 无统一mixed-workload benchmark tool | add preformal benchmark script;不写formal artifacts |
| `max-model-len`、budgets、token caps、window、timeouts实测冻结 | MUST_VERIFY_BY_TEST | 当前大量硬编码3000 chars、8192/16384 token与CLI defaults | materialization/statistics + capacity outputs; formal config consumes result |
| complex Journal recovery/mirror/replacement | DEFER_HARDENING_V2 | 文档目标态复杂，当前不作为Core blocker | fail-stop only |
| formal publication/archive/replica | DEFER_HARDENING_V2 | 不实现 | Core只提交immutable Official result manifest与deterministic report artifact |

## 3. Planned deep modules and interfaces

### `agentreview.review_corpus`

Interface负责：构造/验证Canonical Full Review、生成Primary Projection、只对超长sectionFragment、提交/加载immutable Corpus manifest。调用方不掌握hash、offset、ordering与fragmentation细节。

### `agentreview.review_memory`

Interface负责：加载一个role index、开启fresh paper session、执行deterministic exact-flat `vector_search`、读取projection和hydrate Full Review、执行预算与ledger。Embedding与filesystem是内部seam；测试使用deterministic embedding adapter。

### `agentreview.reviewer_runtime.memory_program_v2`

Interface负责：验证Program的closed `memory_read_mode`，执行deterministic query/retrieval策略，生成唯一Reviewer-visible Memory context。只有summary mode获得post-retrieval completion capability；query/retrieval永远无LLM。

### `memory_evolution.role_evaluator`

Interface负责：Candidate-blind applicability、Candidate scoring、event validation和两层fitness聚合。Human/CoCo adapter不进入该module的依赖闭包。

### `memory_evolution.core_scheduler`

Interface负责：global Qwen sequence semaphore、role round-robin/FIFO、bounded paper DAG与deterministic ordered aggregation。生产adapter包装当前OpenAI-compatible backend，测试adapter记录顺序和并发峰值。

## 4. Code modification plan

### ADD

- `AgentReview/agentreview/review_corpus.py`：Canonical Full Review、Projection、Fragment、manifest与materializer。
- `AgentReview/agentreview/review_memory.py`：immutable role index、exact-flat retrieval、fresh paper session、budget/ledger。
- `AgentReview/agentreview/reviewer_runtime/memory_program_v2.py`：三种read mode与最小capability执行器。
- `AgentReview/memory_evolution/role_evaluator.py`：reference-free applicability/scoring/fitness。
- `AgentReview/memory_evolution/core_scheduler.py`：Qwen与paper DAG有界调度。
- `AgentReview/scripts/build_full_review_corpus.py`：625条Corpus独立build入口。
- `AgentReview/scripts/run_core_capacity_gate.py`：TP4 `max-num-seqs=1/2/4` benchmark入口。
- 对应unit/deterministic/isolation tests。

### MODIFY

- `agentreview/reviewer_runtime/engine.py`：不再构建Candidate-local A memory；Reviewer只消费CanonicalReviewerMemoryContext。
- `agentreview/reviewer_runtime/task_agent.py`：删除Query/KnowledgeItem LLM路径，保留Reviewer response LLM。
- `agentreview/reviewer_runtime/toolkit.py`：通用LLM不再暴露给普通MemoryProgram；接入Review Memory和summary-only capability。
- `memory_evolution/seeds/*.py`：发布三个v2 seed与closed mode。
- `memory_evolution/evaluator.py`：直接读取冻结Corpus，不再A replay/write；调用reference-free evaluator；支持paper DAG。
- `memory_evolution/formal_protocol.py`、`memory_evolution/cli.py`：移除Evolution reference assets/CoCo metric前置，分离Official命令。
- `memory_evolution/search.py`：global 20 attempts、strict dedup、聚合Reflector feedback、global top-3/full-pool winner。
- `memory_evolution/reflector.py`：更新v2 Program contract与summary capability限制。
- `run_single_paper_review_cli.py`：META-OFF默认、Core Memory context、shared scheduler与Final Suite输出。
- formal config/asset manifest：只在实测gate生成具体capacity/budget后更新。

### DELETE/REMOVE

- Evolution中的Human/OpenReview/CoCo reference join和`coco_review` fitness调用。
- `TaskAgent.generate_query()`的LLM structured completion。
- `TaskAgent.extract_knowledge_item()`及Candidate A replay的生成式write。
- seed中的`INSTRUCTION_QUERY`、`INSTRUCTION_KNOWLEDGE_ITEM`生成调用语义。
- LLM Summarizer silent fallback。
- Experience Learner `lesson_learned/fact_to_remember`生成路径。
- online/official默认META调用；保留显式META实验开关。

### TEST

- 625 record cardinality、role/subdimension完整性、Corpus hash稳定性；
- no-memory/no-old-memory/no-external-evidence capability denial；
- projection/fragment byte与offset稳定性；
- exact-flat排序、role isolation、budget/ledger和paper state isolation；
- retrieval/query期间LLM call count严格为0，summary mode恰好允许一次post-retrieval call；
- Human/OpenReview/CoCo import/read/call在Evolution中被拒绝；
- applicability/score vector/fitness Decimal稳定；
- shared-pool softmax RNG、attempt accounting、dedup、top-3和winner；
- Final Suite前C不可读，Suite freeze后Official只report；
- global scheduler并发峰值、FIFO/round-robin与paper完成顺序无关聚合；
- tiny synthetic smoke；
- TP4 capacity 1/2/4 benchmark与token/materialization统计。

## 5. Implementation order

1. Corpus schema/materialization；
2. Review Memory/index；
3. MemoryProgram v2 modes与Reviewer接入；
4. reference-free evaluator；
5. shared-pool search/finalist/winner；
6. Final Suite/Official access isolation；
7. scheduler/paper DAG；
8. tests、tiny smoke、capacity gate。

## 6. 2026-08-30 implementation audit

### MUST_IMPLEMENT_NOW：已形成Core实现

| Core requirement | Implementation mapping | Current status |
|---|---|---|
| 125×5 no-memory Corpus build | `agentreview/review_corpus_builder.py::NoMemoryCorpusBuilder`; `scripts/build_core_v1_review_corpus.py` | 实现完成；strict 2-attempt、complete-set-before-materialization、无Memory/Evidence capability。真实125×5运行待resource gate。 |
| Canonical Full Review / Projection / Fragment / freeze | `agentreview/review_corpus.py::CanonicalFullReviewRecord`, `materialize_corpus`, `CorpusMaterialization.load/write` | 实现并通过schema、cardinality、byte/offset、round-trip deterministic tests。 |
| role-specific exact-flat Review Memory | `agentreview/review_memory.py::ReviewMemoryIndex`, `ReviewMemoryStore`, `ReviewMemorySession` | 实现五role隔离、float32 canonical vectors、binary64 fixed-order dot、atomic budget、full-record hydration与query token cap。 |
| retrieval无LLM、三种mode | `agentreview/reviewer_runtime/memory_program_v2.py::CoreV1ReviewerSession`; `memory_evolution/seeds/*_v2.py` | 实现；Candidate仅返回capability-free plan。Direct/Structured为0次retrieval-side LLM，Summary仅在retrieval后显式调用一次。 |
| legacy/new-formal隔离 | `agentreview/reviewer_runtime/engine.py::ReviewerSession`; legacy `seeds/*.py`; Core `seeds/*_v2.py` | Core seed不能由legacy engine静默加载；旧seed与221项legacy/Core tests保持兼容。 |
| META default off | `agentreview/arguments.py`; `run_single_paper_review_cli.py` | 默认五Specialist直接输出；仅`--enable_meta`显式启用旧META实验。 |
| reference-free role evaluator | `memory_evolution/role_evaluator.py::ReferenceFreeRoleEvaluator` | 两阶段strict evaluator、固定vector、0–4/N_A、applicability consistency与两层Decimal macro fitness实现。API无Human/OpenReview/CoCo输入。 |
| shared-pool M★ / candidate / top-3 / winner | `memory_evolution/core_search.py::SharedPoolMStar` | 三mandatory initial seeds、global binary64 softmax、20 attempt semantics、strict dedup、global top-3与full-pool-only winner实现。 |
| Core formal composition root | `memory_evolution/core_formal.py::CoreRoleEvolutionRunner` | static/rotating/full-pool固定顺序、fresh paper evaluation、聚合Reflector feedback、五role winner到Suite的组合实现；不导入legacy CoCo evaluator。 |
| aggregate-only Core Reflector | `memory_evolution/core_reflector.py::CoreReflector` | 仅接受role/overall/subdimension/failure-code/evaluable-count聚合字段；v2 prompt不再描述或授予KnowledgeItem/Query/write/read-side LLM。 |
| Final Suite / protected C boundary | `agentreview/final_suite.py::FinalReviewSuite`, `ProtectedOfficialAccess` | META-off固定五role顺序；Suite authorize前protected input不可读，Official start后Suite sealed。 |
| global Qwen scheduler / paper DAG | `memory_evolution/core_scheduler.py::GlobalQwenService`, `ScheduledToolkitProxy`, `run_bounded_paper_dags` | 长期全局C、role round-robin/FIFO、所有toolkit completion共享额度；Corpus builder已接入proxy。Reviewer与Applicability并行、Scoring依赖屏障和ordered aggregate实现。 |
| capacity tooling | `memory_evolution/capacity_gate.py`; `scripts/build_core_v1_capacity_workload.py`; `scripts/benchmark_core_v1_qwen_capacity.py`; `scripts/select_core_v1_capacity.py` | 1/2/4测试、stability/OOM/schema/latency/throughput/显存gate及C/2C window选择工具已实现；没有主观写入正式数值。 |

### ALREADY_SATISFIED

- Reviewer generation继续由`TaskAgent.generate_response()`/Reviewer backend完成。
- Evolution mutation与compile-fix继续保留Reflector LLM；Core专用adapter只允许batch aggregate feedback进入mutation。
- 旧formal/run资产与`runs/`未修改；legacy seed仍保持原身份与旧测试语义。

### MUST_VERIFY_BY_TEST

已验证：

- `/data/xyj/envs/env_shengao/bin/python -m unittest discover tests`：`225 tests`全部通过；
- Core deterministic/unit/isolation tests覆盖Corpus、projection/fragment、index round-trip、三mode capability、query/Summary预算、reference-free dependency closure、fitness、RNG/dedup/finalist/winner、scheduler/DAG及C访问边界；
- `tests.test_core_v1_tiny_smoke`完成纯synthetic端到端 smoke：Corpus→Memory→三mode→fitness→shared-pool→Final Suite→protected access。

尚须真实preformal验证：

- 冻结A manifest确为125 papers，随后真实生成625 canonical reviews并materialize 2875 primary projections；
- Qwen3-Embedding-8B真实vector byte/ranking stability与五role index；
- frozen B-local manifests、role eligibility整数阈值、真实Evaluator 5×3 deterministic smoke；
- 完整request token materialization后决定`max-model-len`、Memory/Summary caps、Evidence budgets、timeouts；
- Qwen TP4分别重新启动并实测`max-num-seqs=1,2,4`，再选择capacity C及active-paper window。

2026-08-30只读resource preflight结果：GPU0–3当前旧Qwen服务为`TP4,max-model-len=262144,max-num-seqs=1,gpu-memory-utilization=0.95`，每卡可观察free memory约`540–542 MiB`，低于Core gate的`768 MiB`；GPU4/5仍运行旧CoCo Split/Classify；GPU6/7空闲。因此当前不是exclusive Core capacity环境，本轮未停止服务、未重启模型、未运行或伪造1/2/4 capacity结果，也未启动新formal。

### DEFER_HARDENING_V2

- Q275之后扩展的复杂Audit Hardening、Journal recovery、mirror/replacement、root storage、publication/archive事务不进入首次Core formal的阻塞清单。
- Core v1采用fail-stop、immutable manifest和最小access guard；上述可靠性强化不得反向改变Reviewer input、Memory delivery、search/RNG、fitness/winner或Official语义。
