# AgentReview 全流程首要阻塞原因复核审计

日期：2026-08-31  
审计类型：代码、文档、运行产物只读审计；仅允许修改 Markdown 结论  
代码基线：分支 `20260827`，提交 `669629c4f68d38eae3e4757001cb5af2b2f521d0`  
结论置信度：高

## 0. 2026-08-31 根因修复实施结果

本报告第5节确认的首个运行阻塞已经按“删除错误机制，而不是扩充白名单”的方式修复：

- 删除 `agentreview/corpus_semantic_audit.py` 及其专项测试；
- `NoMemoryCorpusBuilder` 不再用关键词/正则裁决开放式 Review 文本语义；
- Corpus service preflight 改为 role contract smoke，只验证 token accounting、`finish_reason`、strict Canonical Full Review schema、paper/role/section/anchor/request identity；
- freeze audit 只保留完整625条、五角色基数、严格 schema/identity、anchor referential integrity、request binding、first-legal-attempt 和 no-memory/capability isolation 等可机械证明的条件；
- Corpus build、bootstrap/profile、freeze audit、success manifest 与 preflight schema 均升级身份，旧失败产物不会与新 Corpus 混用；
- 保留非法 JSON、空输出、truncation、缺失/重复 section、非法 anchor、identity mismatch 的一次 identical retry 与 fail-fast；没有增加 regex 例外、额外 retry 或 fallback。

真实失败闭环已离线回放：`corpus_build_review_request_r5` 中此前被误判的 `B16Jem9xe × POL` raw response，在未改写任何 Review 文本的前提下通过 strict parser，得到5个 POL sections。新增回归测试要求该类谨慎措辞第一次即成为合法 canonical output。

验证结果：Corpus 相关24项测试通过；仓库完整 `unittest` 249项通过。当前没有启动 Qwen、Corpus、capacity 或 formal，也没有修改任何既有 `runs/`/失败 artifact。

本修复解除的是第1个已观测阻塞。第8节所述 production composition root 和 Frozen External Evidence 真实资产缺口仍然存在；在这些输入协议已有真实资产之前，不把 synthetic fixture 包装成虚假的 formal CLI。

## 1. 执行结论

目前“全流程无法打通”不是单一 GPU、Qwen 或 capacity 参数问题，而是两个连续层级的问题：

1. **当前最早、已经真实触发的阻塞，是 Corpus bootstrap 的脆弱验收机制。** Corpus Reviewer 的概率性自然语言输出必须同时通过 strict JSON、固定 section/anchor 约束和基于英文关键词的 semantic boundary audit。任一 A paper × role 连续两次未通过，整个 625 条 Corpus 立即写入不可恢复的 `CORPUS_BUILD_FAILED.json` 并封存。最新一次失败中，模型已经返回结构合法、语义谨慎的 POL review，但审计器因不认识一种等价的谨慎措辞而误判。
2. **即使绕过当前 Corpus 失败，Core v1 仍没有可运行的生产端到端入口。** Corpus、Review Memory、三种 Memory mode、reference-free evaluator、shared-pool search、Final Suite 和 protected Official access 都存在模块或测试实现，但没有 production composition root/CLI 把它们接成一条真实链路。`memory_evolution.cli` 和 README 仍指向 legacy seed、META、Human/CoCo fitness 与 legacy final suite。

因此，首要原因应分两种口径表述：

- **首个观测到的运行阻塞：Corpus semantic audit + 两次重试 + 全局 fail-fast。**
- **系统级根本阻塞：Core v1 只有组件级实现，没有生产级端到端装配和入口。**

只修最新的 `POL-ANO` 关键词不会打通全流程；它只会让系统继续运行到下一个措辞、schema 或装配缺口。

## 2. 审计边界与未执行事项

本次执行了：

- 静态代码调用关系审计；
- 现有 Markdown/ADR/README 一致性审计；
- Corpus checkpoint、failure record、supervisor status、runtime profile、Qwen log 的只读核验；
- 使用既有 raw response 的离线 parser/semantic-audit 重放；
- 当前进程、tmux 和 8 张 GPU 的只读状态检查。

本次没有：

- 修改任何 Python、配置、schema、prompt 或 artifact；
- 启动/停止 Qwen、Embedding、CoCo 或实验进程；
- 重跑模型请求、Corpus、capacity gate 或 formal；
- 读取 C、Human/OpenReview reference 或 Official CoCo 评价内容。

## 3. 当前真实进度

### 3.1 运行状态

审计时：

- 无 tmux server；
- 无 Qwen/vLLM、CoCo、Corpus builder、supervisor 或 formal 进程；
- 8 张 RTX 4090 均约使用 `15 MiB`，GPU utilization 为 `0%`；
- 当前不是“正在运行但卡住”，而是“Corpus 失败后已经停止”。

### 3.2 产物门阶段

| 阶段 | 已有事实 | 状态 |
|---|---|---|
| A manifest / 625 request materialization | 多个 bootstrap 目录均有 625 条 request materialization | 已完成多次 |
| Corpus service preflight | 最新相关 preflight 为 `PASS` | 已通过 |
| 625 Canonical Full Reviews | 10 个 sealed failure；没有完整 Corpus | **失败** |
| Corpus materialization / Projection / Fragment | 没有成功 Corpus可供物化 | 未开始 |
| 五 role Review Memory index | `index_manifest.json` 数量为 0 | 未开始 |
| real Memory/Evidence/formal request statistics | 依赖完整 Corpus/index | 未开始 |
| capacity 1/2/4 result / selection | result 与 selection 均为 0 | 未开始 |
| MAIN_FORMAL_RUNTIME_PROFILE | 数量为 0 | 未冻结 |
| 五 role formal evolution / winner | winner 数量为 0 | 未开始 |
| Final Suite / Official | 数量为 0 | 未开始 |

现有所有 checkpoint 目录合计保存了 216 个成功的 paper × role item，但它们分散在不同 semantic/runtime identity 的失败尝试中，不能拼成一份合法的 625 Corpus。

## 4. 最小可重复失败闭环

最新失败记录：

- supervisor：`artifacts/core_v1_preformal_20260830/corpus_supervisor_review_request_r5/status.json`
- failure：`artifacts/core_v1_preformal_20260830/corpus_build_review_request_r5/checkpoint/CORPUS_BUILD_FAILED.json`
- stage：`CORPUS_BUILD`
- completed before failure：`14 / 625`
- paper：`B16Jem9xe`
- role：`POL`
- attempts：2
- 两次 `finish_reason`：均为 `stop`
- 两次 parser disposition：均因 `POL-ANO:ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY` 失败
- `capacity_or_formal_started=false`

这个闭环无需模型、GPU 或网络即可重复检查。对现有状态与失败 record 做断言，稳定得到：

```text
REPRO=RED
stage=CORPUS_BUILD
completed_before_failure=14/625
paper=B16Jem9xe
role=POL
reason=POL-ANO:ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY
capacity_or_formal_started=false
```

## 5. 首要运行阻塞：语义审计误判

### 5.1 最新 raw output 实际表达了什么

最新失败 output 的 `POL-ANO` 段落明确说明：论文文本中可见作者、机构和邮箱，但没有 frozen venue/year anonymity policy，因此**无法仅凭现有证据认定 double-blind violation 或 compliance clearance**。

这与 Corpus prompt 中“可描述可见身份信息，但不得认定违规或合规”的意图一致。

### 5.2 为什么仍被判失败

`agentreview/corpus_semantic_audit.py` 的判断方式不是语义证明，而是：

1. 正则查找 `violation / breach / compliance` 与 `anonymous / anonymity / double-blind` 是否同句出现；
2. 再检查整段是否包含 `_limited()` 的有限英文短语白名单；
3. 未命中白名单便生成 `ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY`。

`_limited()` 接受如 `cannot determine`、`insufficient evidence`、`without a frozen policy` 和 `no frozen venue`，但最新 output 使用的是语义等价的：

```text
it is not possible to declare ... based solely on the supplied evidence
```

同时它说的是：

```text
does not provide a frozen venue or year-specific anonymity policy
```

两种措辞均没有精确命中 `_limited()` 白名单，于是“不能认定”被误当成“作出了认定”。

### 5.3 离线反事实验证

本次把同一份既有 raw output 通过严格 JSON parser 成功解析，然后只在内存中做不改变含义的措辞替换：

```text
original_semantic_violations=
  [('POL-ANO', 'ANONYMITY_DECISION_WITHOUT_FROZEN_POLICY')]

meaning_preserving_wording_change_violations=[]
```

替换没有改变 paper、role、section、anchor、外部证据边界或结论，只把 `not possible to declare` 改写为 `evidence is insufficient to determine`。审计结果由失败变为通过，证明该 gate 对表述形式敏感，而不是可靠地识别研究语义越界。

### 5.4 代码中的放大机制

- `agentreview/review_corpus_builder.py:644-706`：每项固定最多两次；semantic violation 与非法 JSON 一样使 attempt 无效。
- `agentreview/corpus_checkpoint.py:144-149`：一旦 failure marker 存在，禁止 resume。
- `agentreview/corpus_checkpoint.py:168-184`：任一项两次失败即写入全局 `CORPUS_BUILD_FAIL_FAST`。
- `agentreview/corpus_checkpoint.py:198-200`：只有完整 625 条才能形成合法结果。

最终形成：

```text
自然语言生成
  → strict schema/anchor/semantic lexical gate
  → 同一措辞模式重试两次
  → 整个 Corpus sealed fail-fast
  → 无 Projection/Index
  → 无真实 budget/materialization
  → 无 capacity gate
  → 无 formal evolution
```

## 6. 历史失败证明这不是单点 bug

现存 10 个 sealed Corpus build failure：

| Run | 已提交 item | 失败位置 | 两次失败原因 |
|---|---:|---|---|
| `corpus_build_v1` | 3 | paper 0 / ORIG | anchor IDs 未预先排序去重 |
| `corpus_build_v2` | 3 | paper 0 / ORIG | anchor IDs 未预先排序去重 |
| `corpus_build_v3` | 151 | paper 30 / CLAR | strict JSON object failure |
| `corpus_build_v4_r2` | 9 | paper 1 / POL | missing documentation policy limitation |
| `corpus_build_v4_r3` | 4 | paper 0 / POL | plagiarism limitation + data clearance |
| `corpus_build_review_request_r1` | 1 | paper 0 / CLAR | unseen visual property judgment |
| `corpus_build_review_request_r2` | 11 | paper 2 / CLAR | strict JSON object failure |
| `corpus_build_review_request_r3` | 6 | paper 1 / CLAR | unseen visual property judgment |
| `corpus_build_review_request_r4` | 9 | paper 1 / POL | anonymity decision without policy |
| `corpus_build_review_request_r5` | 14 | paper 2 / POL | anonymity decision without policy |

这说明目前采用的是“看到一种输出失败 → 增补 prompt/regex/test → 重建 identity → 从头再跑 → 遇到下一种输出”的补丁循环。它无法证明 625 条输入空间已经被覆盖，也无法给出合理的完成概率。

现有 unit tests 只覆盖预先写定的安全/不安全句型。例如测试接受 `Without a frozen venue/year anonymity policy...` 和 `cannot be assessed`，但没有覆盖最新模型使用的 `it is not possible to declare...`。这类有限样例不能为开放式自然语言建立完备的 closed semantic validator。

## 7. 被否定的首要原因假设

### 7.1 Qwen/GPU/KV/OOM 不是最新失败的首因

最新 Corpus runtime profile 与日志显示：

- Qwen3.8-27B，TP=4；
- `max-num-seqs=1`；
- `max-model-len=32768`；
- materialized required context 为 `31402`；
- model declared context limit 为 `262144`；
- `gpu-memory-utilization=0.939`；
- temperature 0、seed 0、output cap 8192；
- service preflight 为 `PASS`；
- 请求持续正常运行，KV cache usage 约 `1.2%–8.5%`；
- 日志未发现 OOM 或 traceback；
- 最新失败 output 为 `finish_reason=stop` 且 strict schema parse 通过。

所以 latest red gate 出现在模型返回之后。调大显存、context、timeout 或 `max-num-seqs` 不会修复该误判。

### 7.2 capacity 尚未运行，不可能是 formal 未启动的直接失败结果

capacity benchmark 依赖完整 Corpus、Projection、Index 和真实 request materialization。当前 Corpus 尚未完成，因此 capacity result/selection 数量均为 0。不能把“尚未到达 capacity gate”解释为“capacity 配置失败”。

## 8. 系统级根本阻塞：没有生产 Core v1 全流程入口

### 8.1 Core 模块存在，但调用者只在 tests

静态调用关系显示：

- `CoreRoleEvolutionRunner(...)` 仅在 `tests/test_core_formal.py` 实例化；
- `CoreV1ReviewerSession(...)` 仅在 `tests/test_memory_program_v2.py` 实例化；
- `ProtectedOfficialAccess(...)` 仅在 tests 实例化；
- `freeze_suite_from_role_runs(...)` 仅在 tests 调用。

`memory_evolution/core_formal.py::CoreRoleEvolutionRunner` 接受 `review_candidate`、`mutate`、`validate_program` 和 `behavior_manifest` 等 injected callbacks。这证明它是一个可测试的语义组件，不是已经接入实际 Qwen、Review Memory、Evidence、Evaluator、checkpoint 和五 role 运行的生产 composition root。

### 8.2 正式 CLI 仍然是 legacy 流程

`memory_evolution/cli.py`：

- 不导入 `core_formal`、`CoreRoleEvolutionRunner`、`CoreV1ReviewerSession` 或 `ReferenceFreeRoleEvaluator`；
- `_seed_paths()` 仍返回旧 `vector_search.py / llm_summarizer.py / experience_learner.py`，不是 v2 seeds；
- 仍暴露 `evolve-meta`、`final-evaluate-suite`；
- 仍选择 `coco_review` 并处理 `META_TASK`、Human/reference 数据路径。

因此当前真实 CLI 无法启动已经冻结的 Core v1 研究语义。

### 8.3 Frozen External Evidence Pool 仍是协议字段，不是生产资产

生产源码和 scripts 中没有 `EvidenceSetManifest`、Frozen External Evidence Pool materializer 或只读 API 的实现/入口。`CorePaperInput.frozen_external_evidence` 只是调用方可注入的字符串字段。这不足以证明 cutoff、item-level evidence ID、完整 delivered set 或 Candidate/Evaluator 同源证据边界。

### 8.4 即使 Corpus 明天通过，下一步仍会停在哪里

若只放宽最新 POL gate，预期流程最多可以继续到 Corpus、Projection/Embedding/Index 和部分 statistics/capacity workload。随后仍缺少一条生产命令完成：

```text
load frozen Corpus/index
  → instantiate v2 Memory session
  → run Reviewer
  → run Candidate-blind applicability + scoring
  → five independent shared-pool M★ role runs
  → role winners
  → freeze Final Suite
  → authorize protected C/Human
  → Official CoCo report-only evaluation
```

## 9. 文档与代码状态漂移

### 9.1 implementation matrix 把“模块实现”写成了“Core 实现完成”

`RESEARCH_VALIDITY_CORE_V1_IMPLEMENTATION_MATRIX_20260830.md` 第 6 节标题为 `MUST_IMPLEMENT_NOW：已形成Core实现`，并把 `CoreRoleEvolutionRunner` 称为 composition root。更准确的状态应是：多数核心语义存在模块级实现，synthetic/unit tests 证明局部 contract；但真实 625 Corpus 未完成、production composition root 未接线，capacity/main formal/final suite/official 均未运行。

“存在类与 tests”不能等价为“production path 可运行”。

### 9.2 README 描述的是 legacy 系统

`AgentReview/README.md` 当前仍宣称 five specialists 后接 META、Human category coverage 进入 specialist fitness、运行 `evolve-meta`，以及 `final-evaluate-suite → CoCoMetricBenchmarkAdapter` 是正式入口。这些均不是当前 Research Validity Core v1 的目标语义。照 README 执行会进入旧系统，而不是 reference-free evolution + META-off + frozen Final Suite + post-freeze Official CoCo。

### 9.3 CONTEXT 与 Core baseline 的比例失衡

`CONTEXT.md` 包含大量 Audit Hardening v2 的 journal、mirror、recovery、root-control 和 materialization identity 术语。ADR-0011 已明确这些不应阻塞首次 Core experiment。继续把这些条目作为操作入口，容易再次把“科研闭环”与“高可用审计系统”混在一起。

### 9.4 Corpus prompt 实际冻结了 REVIEW_Request 之外的大量行为

用户最新目标强调：五 reviewer 的强制不变部分是 `REVIEW_Request.md`，其余工程形式应有弹性。但当前 `corpus_role_prompts_v4.py` 又加入 `COMMON_CORPUS_INSTRUCTIONS`、五份长 `ROLE_SPECIFIC_INSTRUCTIONS`、strict JSON only、固定 evidence-boundary 措辞要求及 lexical semantic audit。

这些内容均进入 generation semantic identity，事实上成为了第二套冻结 Reviewer 行为，并且已经直接阻塞 Corpus。它把本应由 reference-free Evaluator 判断的部分语义，前移成了 Corpus generation 的强制 accept/reject gate。

## 10. 根因排序

| 排名 | 假设 | 结论 | 证据强度 |
|---:|---|---|---|
| 1 | 开放式 LLM 输出 + lexical semantic audit + whole-Corpus fail-fast 导致 bootstrap 几乎不可完成 | **确认，当前首个阻塞** | 高 |
| 2 | Core v1 只有组件/tests，没有 production end-to-end composition root | **确认，系统级根本阻塞** | 高 |
| 3 | 文档把模块完成度误写为系统 readiness，实际入口仍是 legacy | **确认，放大错误决策** | 高 |
| 4 | Frozen External Evidence 生产实现缺失 | **确认，后续 formal blocker** | 高 |
| 5 | Qwen/GPU/context/capacity 是 latest failure 主因 | **否定** | 高 |

## 11. 建议的修复顺序（本次未改代码）

### P0：先恢复最小研究主线，不再逐条补 regex

在代码修改前明确两个会改变 research semantics 的选择：

1. Corpus acceptance 是否只负责可机器验证的结构合法性、身份、anchor referential integrity，而把自然语言质量/越界交给后续 reference-free audit/evaluator；
2. Corpus 是否必须由 Reviewer 原生输出 23 个 JSON sections，还是保存完整 role review，再由确定性结构化步骤产生 projection。

无论选哪种，都不应继续用有限关键词白名单声称可以完备判定开放式自然语言语义。

### P1：建立唯一生产 Core command，再做长 Corpus/Capacity 实验

先实现一个最小 production composition root，能用 synthetic/few-paper real inputs 串起：

```text
Corpus → Index → three v2 modes → Reviewer → reference-free Evaluator
→ shared-pool role evolution → five winners → Final Suite → protected Official stub
```

它必须明确拒绝 legacy seeds、META、Human/CoCo evolution evaluator。没有这条链路，不应把 625 Corpus 成功当作“即将 formal”。

### P2：把 Corpus fail-fast 从“自然语言措辞完美”中解耦

应保留 hard failure 的是：空响应、truncation、非法 schema/identity、缺失或重复 role sections、非法 anchor ID、Memory/Human/CoCo/C capability 越权。

需要重新设计的是：依赖关键词解释语义的 hard reject、要求模型自己预排序无序集合、因单项自然语言输出使整个 625 Corpus 永久 sealed，以及每修一个 prompt/regex 就必须从头重建的循环。

### P3：统一文档入口

- 把 implementation matrix 区分为 module-complete、production-wired、real-gate-passed；
- README 顶部明确 legacy 与 Core v1 两套入口，Core production command 不存在时不得给出虚假命令；
- 将 ADR-0011 作为首次实验的范围权威；
- CONTEXT 中 Audit Hardening 仅作 deferred reference，不再驱动 Core 实施。

### P4：然后再恢复真实实验顺序

```text
small production-path smoke
→ authoritative 625 Corpus
→ Projection/Embedding/Index
→ real budgets/materialization
→ evaluator production smoke
→ fresh TP4 capacity 1/2/4
→ main formal preflight
→ five role Evolution
→ Final Suite freeze
→ protected Official CoCo
```

## 12. 最终判断

现有系统**有修复空间，不需要把所有已有模块推倒重写**：Corpus schema、projection、Review Memory、v2 seeds、reference-free evaluator、shared-pool search 和 Final Suite 的局部实现可以复用。

但不能继续按当前方式“修一个失败短语，再从头跑一次”。需要重新设计的是两条连接层：

1. **Corpus 的接受边界**：从脆弱自然语言执法器退回到可验证结构与 capability 边界；
2. **生产 composition root**：真正把已有 Core 模块接成唯一可运行主线，并隔离 legacy CLI。

换言之，建议是“保留有效组件，重做最小主干”，不是“在现有 protocol machinery 上继续加规则”，也不是“完全从 0811 零开始重写一切”。
