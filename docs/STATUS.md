# AgentReview 当前系统状态

核验日期：2026-08-31

核验代码：0406eaedec513570a7f7842114301a573c1f8610

工作目录：/data0/xyj/shengao/AgentReview_0827

本文只记录会变化的事实。稳定设计见 [ARCHITECTURE.md](ARCHITECTURE.md)，目标合同见 [protocol/README.md](protocol/README.md)。

## 1. 执行结论

当前仓库达到：

> Core v1 核心算法与领域模块主体已实现，独立 CLI 与生产组合根完成全合成 G1 闭环。

当前正式状态是：

> G2 真实输入与 preflight 工件待形成，G3 正式五角色、Suite 和 Official 运行待执行。

因此，仓库已有可执行研究架构，也已有完整控制流 smoke；当前工件支持工程接线结论，尚未形成正式实验结果。

## 2. 三层门禁

| 门禁 | 当前状态 | 现有证据 | 完成条件 |
|---|---|---|---|
| G1 Production Wiring | PASS（synthetic） | artifacts/core_v1_g1_production_wiring_20260831/integration_smoke_report.json 与 core_v1_closure.json | 保持五角色、Suite、受保护读取顺序和 Core CLI 回归通过 |
| G2 Preformal Artifacts | PENDING | closure 中全部 G2 条目为 false；Corpus r7 checkpoint 保存 185/625 | 生成并验证 Corpus、五索引、B-local、Evidence、Evaluator smoke、capacity、runtime profile、final preflight |
| G3 Formal Closure | PENDING | closure 中全部 G3 条目为 false | 生成五 winner、Final Suite、authorization、Official result 与 immutable final report |

现有 G1 smoke 使用：

- 5 条 synthetic A review；
- 2 篇 synthetic B paper；
- hash embedding；
- Fake Qwen；
- 空 Evidence items；
- synthetic protected inputs；
- REPORT_ONLY Official lambda；
- 每角色 1 个 attempt；
- require_bubblewrap=false。

它完成五角色遍历、181 次 Qwen execution 记录、Suite 冻结，以及 Suite 前 0 次、Official 后 1 次 protected loader 调用。它表达 composition correctness。

现有 core_v1_closure.json 记录旧 AgentReview_0811 绝对路径。门禁状态仍可读，路径字段属于旧运行位置；下一次 closure audit 应从当前目录生成。

closure-audit 的退出码只表达 G1：G1 PASS 时返回 0，G2/G3 PENDING 仍需要从 JSON 状态与 missing 列表判断。

## 3. 测试状态

从 AgentReview/ 执行：

~~~bash
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m unittest discover -s tests -v
~~~

结果：

~~~text
Ran 243 tests
FAILED (errors=5)
~~~

五个 error 的根因：

| 数量 | 根因 | 影响 |
|---|---|---|
| 4 | AgentReview/tests 缺少 __init__.py；环境把 tests 包解析到 site-packages/tests | test_core_v1_tiny_smoke、test_corpus_freeze_audit、test_memory_program_v2、test_review_memory 的跨测试 import 失败 |
| 1 | configs/coco_temporal_split_v1/manifest.json 缺失 | test_temporal_protocol 的真实 frozen split audit 失败 |

pytest -q 在 collection 阶段同样遇到四个 tests 包解析错误。

Evolution Viewer 独立测试：

~~~bash
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m unittest discover -s evolution_viewer/tests -v
~~~

结果为 Ran 10 tests / OK。

测试目录静态定义的 test 方法数量大于 unittest 实际执行数量。当前状态只引用真实命令输出。

## 4. Temporal 与资产配置

### 4.1 实体数据

configs/coco_temporal_split_v2 的现存 JSONL 行数：

| 数据 | 行数 |
|---|---:|
| historical_experience | 2391 |
| evolution_validation | 299 |
| effectiveness_holdout | 300 |
| held_out_benchmark_test | 899 |
| paper_families | 3889 |

Frozen S2 v2 的本体与 companion manifest SHA 和 asset_manifest_v2.json 中的值一致。REVIEW_Request.md 的 SHA 也一致。

### 4.2 Manifest 缺口与根因

当前缺少：

- configs/coco_temporal_split_v1/manifest.json
- configs/coco_temporal_split_v2/manifest.json

AgentReview/.gitignore 先忽略所有 *.json，只放行 configs/*.json。该规则覆盖一层配置文件，嵌套 configs/coco_temporal_split_v*/manifest.json 继续处于忽略范围。这是两个 temporal manifest 缺失的共同版本控制根因。

asset_manifest_v2.json 仍引用缺失的 v2 manifest，并同时声明 formal_ready=true、formal_blockers=[]。该状态字段与当前 checkout 的实体闭包冲突。该 manifest 还绑定 legacy v1 seeds，因此它表达 legacy formal 资产身份，无法代替 Core runtime config。

formal_temporal_v3_qwen3e8b.json 同样引用缺失 manifest，并把 selection assets 标记为 regeneration_required。

Frozen S2 companion manifest 的 asset/path 字段保留 AgentReview_0811 旧绝对路径。内容 SHA 可复核，路径 provenance 需要在下一次物化时刷新。

## 5. Core 正式运行阻塞项

### 5.1 Composition 输入

仓库中当前缺少 schema research-validity-core-v1-runtime-config-v1 的实例。

现有 embedding Adapter 与 Core loader 的接口形状不同：

- Core 需要 factory(dict) 返回 callable；
- callable 同时提供 token_count(text)；
- create_qwen3_embedding 返回 Qwen3Embedding 对象，公开 embed(list[str])。

现有 Official Adapter 与 Core application 的接口形状不同：

- Core 需要 official_runner(FinalReviewSuite, protected) → Mapping；
- agentreview.coco_eval 接受 argparse/file workflow；
- legacy CoCoMetricBenchmarkAdapter 接受单个 submission；
- 仓内唯一同形 callable 是 synthetic smoke lambda。

完成条件：提供 Core-compatible embedding query Adapter、Official Adapter 和严格 runtime config，或提供等价 external application factory。

### 5.2 G2 资产

core_closure.py 期望的正式根目录目前缺少：

- canonical Corpus manifest、625 条 Full Review、projection、fragment；
- QUAL/CLAR/SIGN/ORIG/POL 五个 Review Memory index；
- B-local manifest；
- Frozen Evidence pool；
- Evaluator production smoke；
- capacity results 与 selection；
- main formal runtime profile；
- final preflight。

Review Memory index 的生成器与 closure 使用两套文件名：build_core_v1_review_memory_index.py 生成 qual.review_index.json 等小写名称，closure 要求 review_memory/QUAL.json 等大写名称。正式提交前需要统一 canonical 命名、内容校验和 manifest 引用；当前仓库尚无该提交 Adapter。

Corpus r7 运行证据显示：

- supervisor status 仍写 WAITING_FOR_CORPUS_BUILD；
- 记录的 builder PID 2207970 与 Qwen PID 2201571 已结束；
- builder log 最后记录 completed=185、total=625；
- checkpoint/items 保留已提交记录；
- status 与输出路径仍指向旧 AgentReview_0811。

该 checkpoint 可以由相同 identity 的 builder 继续使用；当前状态文件本身已经过时。

### 5.3 Evaluator 与 Evidence seam

五份 scoring-anchor 协议当前是 frozen semantic spec，正式 Core config 尚未把这些文件或 REVIEW_Request SHA 绑定为 role rubric identity。Application 当前使用 core-v1-evaluator:<role> 与 core-v1-role-rubric:<role> 占位 identity。

事件所有权还存在协议差距：协议要求 DUPLICATE_PADDING 由 deterministic normalizer 派生，当前 parser 允许 LLM 直接输出该事件；协议要求的 SYSTEMATIC_HALLUCINATION parser/aggregator 派生逻辑也尚未进入代码。正式 Evaluator gate 需要把两类事件实现为 deterministic derivation，并生成 registry、测试向量和 durable record。

Frozen Evidence record、pool、loader 与 evaluator rendering 已实现。Core application 当前把完整 Evidence context传给 Evaluator。

协议定义的 Candidate-facing Evidence API、访问预算和调用 audit 尚未接入 CoreV1ReviewerSession。完成条件是把受控 API 接入 Host capability seam，并产出 G2 access evidence。

### 5.4 调度与 capacity

GlobalQwenService 与 ScheduledToolkitProxy 已进入 Core application。当前共享范围是单 application 进程；run-role 多进程分别建立 service。Role runner 逐 paper 串行。

GlobalQwenScheduler 和 run_bounded_paper_dags 由测试覆盖，生产组合根尚未调用。五角色全局公平调度、paper DAG 与 active-paper window 仍是待接线项。

现有 capacity workload 缺少 post-retrieval summarizer workload。Benchmark 当前把 oom_count 与 protocol_failure_count 固定写为 0，只把空响应计入 schema failure；因此现有 harness 尚不能证明 OOM/protocol gate。它也尚未覆盖 cold/warm、真实 schema/compiler 和 active-paper window。capacity selector 的三次重复、稳定性、OOM/schema/free-memory 校验逻辑已经实现。

### 5.5 Journal、身份与 Official durability

Core role search 当前使用内存 pool、attempt 和 RNG。角色进程中断后从三 seed 重跑；durable winner 只在角色完成后写入。

当前 role_run_identity 绑定 schema、role、run_seed、B-local SHA 和 Evidence SHA。正式 provenance 还需要绑定 Corpus/index、rubric、Evaluator/Qwen、runtime limits 与 seed bytes。

CoreReflector 当前固定 max_mutation_attempts=1、max_compile_fix_attempts=0；这两个行为参数尚未进入 runtime config 与 role_run_identity。

Final Suite 当前保存 winner identity/hash，未保存 Program bytes 或 locator。Official Adapter 需要可靠解析 winner Program。

ProtectedOfficialAccess 的 once 状态位于进程内。新 CLI 进程可以再次授权并覆盖 official_result.json；core_application 当前不写 official/authorization.json。最小 decision journal、durable authorization 与 immutable report 是 Core 正式闭包项。

role winner、Suite 和 Official result 都通过 os.replace 写入固定路径，因此重复使用 output_dir 会覆盖已有工件。正式执行需要全新 artifact root 与不可变提交门禁。

Core application 与 closure 的 G3 目录合同还需要外层组合：application 把 role_winners、final_suite.json、official_result.json 写入同一个 output_dir；closure 期望 winner/Suite 位于 formal/，Official result、authorization 与 final report 位于 official/。当前仓库尚无负责 Official 目录路由和最终提交的 controller。

复杂 journal mirror、watchdog、generation replacement、external archive 和 publication promotion 属 Audit Hardening v2。

## 6. Effectiveness screening

effectiveness_screening/ 作为暂未启用的 legacy 实验接口保留。当前有四个共同阻塞：

1. effectiveness_screening_v2.json 指向缺失且已被 legacy 标记取代的 v1 temporal manifest。
2. Generation 命令使用 direct-review 默认 enable_meta=false；Judge 强制读取 Final Meta-Reviewer。
3. 三个 lineage 把三个逻辑 arm 展开为七个实际 label；分类和评分仍按 no_memory/seed/evolved 聚合，lineage 输出路径发生覆盖。
4. Blind pair 与 summarize 同时使用 seed、seed_experience 和 expanded labels，目录身份无法统一。

完成条件：统一 temporal v2 manifest、META topology、arm identity、文件布局和 summary grouping，然后重新 freeze design。

## 7. Legacy 与保留接口状态

| 接口 | 状态 | 启用条件 |
|---|---|---|
| agentreview.cli standalone Reviewer | 可达 | 使用 legacy Program |
| memory_evolution.cli legacy M★ | 可达 | 使用旧数据、fitness 与 artifact contract |
| run_single_paper_review_cli.py | 可达 | 五 specialist 默认输出；META 通过显式参数开启 |
| Old Arena/UI | 休眠接口 | 由 library caller 构造 Arena/Environment |
| Optional providers | 保留 Adapter | 安装对应依赖并提供 provider 配置 |
| Database/Supabase | 保留 Adapter | 提供环境配置并由 Arena caller 调用 |
| OpenScholar bridge | 未接线 helper + 历史快照 | 先完成凭据治理、选择可执行 helper 并建立调用链测试 |
| Result Viewer | 可达 | 当前完整度判断期待 META |
| Evolution Viewer | 可达 | 输入采用 legacy lineage/scores layout |

OpenScholar 的 use_search_apis.py 包含一个 fallback credential literal。安全启用条件是轮换该凭据、移除源码字面量，并只从受控环境注入。本文不记录凭据内容。

OpenScholar 中 use_search_apis.py、src/utils.py 与 utility/s2_retrieval.py 含可执行 helper；config.py、openscholar_api.py、src/open_scholar.py、src/instructions.py 等主体整体位于三引号字符串中，运行时只形成模块文档字符串。

cache/ 下存在缺少主数据库的 SQLite -shm/-wal 临时伴随文件。它们不是正式 embedding cache，也不构成 G2 资产。

## 8. 发布判断

当前可以发布的结论：

- Core v1 的核心算法与主要领域模型已进入代码；
- 独立 Core CLI 与生产 composition root 已存在；
- synthetic G1 smoke 已通过；
- 主要领域模块和接口拥有单元/集成测试；
- legacy 与 optional 接口的身份已经区分。

正式实验发布需要：

1. 修复测试发现与 temporal manifest 的版本控制根因；
2. 提供 Core-compatible adapters 和 runtime config；
3. 完成 G2 全部真实工件与内容预检；
4. 补齐最小 journal、provenance、winner Program locator 和 durable Official authorization；
5. 运行五角色并冻结 Final Suite；
6. 执行 Official，生成 immutable final report；
7. 统一 index、formal 与 official 的生成路径和 closure 路径合同；
8. 以 closure JSON 证明 G1、G2、G3 同时通过。
