# Research Validity Core v1 全流程重新验证报告

> **2026-08-31 实施后复核（取代本文原 production-wiring 结论）：** 本文以下正文保留为修复前审计证据。当前已新增独立 `CoreV1FormalApplication`、Core CLI、B-local manifest、Frozen External Evidence Pool、production-path integration smoke 与三阶段 closure audit。完整仓库测试为 `254 tests / OK`；五角色 production smoke 实际通过 `CoreRoleEvolutionRunner → CoreV1ReviewerSession → ReferenceFreeRoleEvaluator → CoreReflector → child admission → finalist/winner → FinalReviewSuite → ProtectedOfficialAccess → Official stub`；Suite freeze 前 protected loader 调用数为0，Official后为1。权威机器结果见 [`core_v1_closure.json`](../../artifacts/core_v1_g1_production_wiring_20260831/core_v1_closure.json)：`G1_PRODUCTION_WIRING=PASS`，`G2_PREFORMAL_ARTIFACTS=PENDING`，`G3_FORMAL_CLOSURE=PENDING`。G1通过后已在全新r9/r6路径启动真实625 Corpus；本文不把运行中状态误写为G2 PASS，capacity与formal仍未启动。

日期：2026-08-31  
验证范围：测试、静态调用闭包、真实 A 区请求物化、既有产物和运行状态  
变更边界：本轮只修改 Markdown；未修改 Python、配置、prompt、schema、运行产物或服务状态

## 1. 最终结论

**当前 Research Validity Core v1 的组件测试通过，但生产全流程没有打通。**

需要严格区分以下三个结论：

| 验证层级 | 结果 | 能证明什么 |
|---|---|---|
| 仓库 unit/deterministic/isolation tests | PASS，249/249 | 已实现模块在测试输入下满足各自合同 |
| synthetic tiny smoke | PASS，1/1 | 若手工提供 Corpus、embedding、evaluator JSON、fitness 和 protected loader，各组件可以在一个测试函数中组合 |
| production full-flow closure | **FAIL** | 没有生产 composition root/CLI 将真实 Corpus、Memory、Evidence、Evaluator、Evolution、Final Suite 和 Official 串成可启动流程 |

因此，不能从“249 个测试全部通过”推导“可以启动 formal”。当前正确状态是：

```text
component-correct
  != production-wired
  != artifact-ready
  != formal-ready
```

## 2. 本轮实际执行的验证

### 2.1 完整测试套件

执行：

```bash
source /data/xyj/envs/env_shengao/bin/activate
cd /data/xyj/shengao/AgentReview_0811/AgentReview
PYTHONPATH=. python -m unittest discover -s tests -v
```

结果：

```text
Ran 249 tests in 17.351s
OK
```

### 2.2 显式 tiny smoke

执行：

```bash
PYTHONPATH=. python -m unittest tests.test_core_v1_tiny_smoke -v
```

结果：

```text
Ran 1 test in 0.013s
OK
```

### 2.3 真实 A 区 Corpus request materialization

使用当前 A selection、真实 125 篇 paper、五角色 prompt/schema、Qwen tokenizer/model identity，在临时目录重新物化 v5 bootstrap：

```text
bootstrap_gate=PASS
profile_version=v5
paper_count=125
request_count=625
structural_context_count=125
required_context_tokens=31402
corpus_build_max_model_len=32768
builder_manifest_version=corpus-builder-semantic-implementation-v2
semantic_audit_present=false
```

该结果证明：当前真实 A manifest 可以机械地产生 125×5 个 no-memory Corpus 请求，且 `corpus_build_max_model_len` 可以由真实请求派生。它**不**证明 625 次 Qwen generation、Corpus freeze、index 或后续 formal 已完成。

### 2.4 生产闭包 gate

闭包检查连续执行两次，结果完全一致：

```json
{
  "artifact_counts": {
    "b_local_manifest": 0,
    "canonical_full_reviews": 0,
    "capacity_selection": 0,
    "evidence_set_manifest": 0,
    "final_suite": 0,
    "frozen_corpus_success": 0,
    "main_formal_runtime": 0,
    "review_memory_index": 0,
    "role_winner": 0
  },
  "full_flow_gate": "FAIL",
  "production_callers": {
    "CoreRoleEvolutionRunner": [],
    "CoreV1ReviewerSession": [],
    "ProtectedOfficialAccess": [],
    "ReferenceFreeRoleEvaluator": []
  },
  "required_core_cli_present": false,
  "tiny_smoke_uses_attempts_zero": true,
  "tiny_smoke_uses_core_role_runner": false,
  "tiny_smoke_uses_evaluator_facade": false,
  "tiny_smoke_uses_reviewer_session": false
}
```

这是本轮全流程验证的决定性失败证据。

## 3. 为什么 tiny smoke 不是端到端验证

`AgentReview/tests/test_core_v1_tiny_smoke.py` 的行为是：

1. 第 23–30 行只构造五条 synthetic Full Review，每个 role 一条，不是 625 条真实 Corpus；
2. 第 26、37、44–45 行使用字符 tokenizer 和测试 embedding，不使用冻结 Qwen tokenizer/Embedding service；
3. 第 49–68 行直接调用 `materialize_reviewer_memory_context()`，没有经过生产 `CoreV1ReviewerSession`；
4. 第 77–119 行把预先写好的 JSON 直接送入 parser，没有调用 `ReferenceFreeRoleEvaluator` 的真实 applicability/scoring 会话；
5. 第 122–135 行将 `SharedPoolMStar(attempts=0)`，没有执行 20 次 global evolution、parent selection、Reflector mutation 或 child admission；
6. 第 148–153 行使用 lambda stub 返回 protected 内容，不是 Official CoCo adapter；
7. 测试没有实例化 `CoreRoleEvolutionRunner`，也没有验证实际 production CLI。

这个测试应保留，因为它能快速验证模块合同；但应更名或在说明中明确为 **component-contract smoke**，不得作为 production end-to-end gate。

## 4. 全流程的真实断点

### 4.1 P0：缺少生产 composition root 和 Core v1 CLI

生产源码中没有调用者实例化：

- `CoreRoleEvolutionRunner`；
- `CoreV1ReviewerSession`；
- `ReferenceFreeRoleEvaluator`；
- `ProtectedOfficialAccess`。

`memory_evolution/core_formal.py` 提供的是依赖注入式语义组件。它要求调用方提供 `review_candidate`、`mutate`、`validate_program`、paper sets 和 evaluator 等依赖，但目前没有应用层把这些依赖绑定到真实 Qwen scheduler、Review Memory、Evaluator、Reflector、B-local 和 Final Suite。

当前 `python -m memory_evolution.cli --help` 只暴露：

```text
prepare-specialists
prepare-test
prepare-meta
cache-meta-inputs
evolve-task
evolve-specialists
evolve-meta
evaluate
final-evaluate-suite
```

这些是 legacy/META/CoCo 流程，不是 Research Validity Core v1 formal 入口。

### 4.2 P0：缺少冻结 B-local 的生产资产和装载器

`CoreRoleEvolutionRunner` 可以接收 `static_papers`、`rotating_schedule` 和 `full_pool_papers`，但仓库中没有已冻结的：

- B-local total paper/family manifest；
- role-specific static-25 membership；
- rotating schedule；
- Candidate-blind eligibility audit 结果；
- `min_static_evaluable[role]` / `min_full_pool_evaluable[role]`。

现有 B-local manifest 产物数为 0。没有这些资产，search fitness、Reflector batch 和 finalist full-pool 的真实输入都无法确定。

### 4.3 P0：Frozen External Evidence 仍只是字符串接口

当前生产代码只在以下位置接受 `frozen_external_evidence: str`：

- `memory_evolution/core_formal.py`；
- `memory_evolution/role_evaluator.py`。

仓库中没有 production `EvidenceSetManifest`、materializer、cutoff validator 或只读 Evidence API；现有 evidence set manifest 产物数为 0。仅传入一个字符串不能证明 Candidate/Evaluator 获得同源、同 cutoff、可引用 item-level evidence，也不能实现 evidence ID 验证。

### 4.4 P0：缺少真实 adapter 绑定

至少缺少一层应用装配，将以下模块绑定为同一条运行路径：

```text
frozen Corpus/index
  → ReviewMemoryStore / paper-local session
  → v2 Memory Program
  → CanonicalReviewerMemoryContext
  → scheduled Reviewer Qwen request
  → Candidate-blind Applicability
  → reference-free Scoring
  → canonical two-level fitness
  → Core Reflector mutation
  → SharedPoolMStar
  → CoreRoleEvolutionRunner
```

模块存在不等于这些回调已经以正确的 identity、budget、failure semantics 和访问边界接好。

### 4.5 P0：缺少五角色到 Official 的生产入口

目前没有生产命令完成：

```text
QUAL / CLAR / SIGN / ORIG / POL role runs
  → five durable role winners
  → fixed-order Final Suite freeze
  → protected-access authorization
  → untouched C + Human/reference
  → Official CoCo report-only evaluation
```

因此 C/Human 隔离虽然有组件测试，尚未被真实应用入口强制执行。

### 4.6 运行前置产物尚未生成

当前真实产物计数：

| 产物 | 数量 |
|---|---:|
| Canonical Full Reviews | 0 |
| Corpus success manifest | 0 |
| Review Memory index manifest | 0 |
| Evidence set manifest | 0 |
| B-local manifest | 0 |
| Capacity result | 0 |
| Capacity selection | 0 |
| MAIN_FORMAL_RUNTIME_PROFILE | 0 |
| Role winner | 0 |
| Final Suite | 0 |

这是“尚未执行到”的运行前置条件，不应与代码断点混为同一个 bug。但在生产入口完成之前直接生成这些昂贵产物，仍无法证明最终 formal 可以运行。

## 5. 当前运行状态

- 无 tmux server；
- 无 Qwen/vLLM、Embedding、CoCo、Corpus 或 formal 进程；
- GPU 0–7 均约 `15 MiB`、利用率 `0%`；
- IDE 中打开的 `corpus_supervisor_review_request_r2/status.json` 是历史失败产物：`state=CORPUS_BUILD_FAILED`、`capacity_or_formal_started=false`，不是活跃运行状态。

## 6. 建议的最小根因修复

本节是待用户确认后才执行的程序修改计划；本轮没有实施。

### P0-1：新增一个深而窄的 Core v1 应用入口

新增单一 `CoreV1FormalApplication`（名称可调整），负责加载冻结 manifest 并装配既有深模块。它不重新实现 evaluator、search、Memory 或 scheduler，只拥有生命周期和依赖闭包。

建议提供明确阶段：

```text
validate-inputs
component-smoke
run-role
freeze-suite
run-official
```

不要把 legacy `memory_evolution.cli` 继续补丁式改造成双语义入口；Core v1 使用独立命令，降低 Human/CoCo 或 META 路径误入 Evolution 的风险。

### P0-2：物化并冻结两类缺失输入

1. B-local manifest/eligibility audit；
2. Frozen External Evidence Pool v1 manifest/API。

这两类是 evaluator/search 的真实研究输入，不是防御性 Journal 扩展。实现应只满足 Core v1 已冻结的最小语义，不继续增加 recovery/mirror/identity 状态机。

### P0-3：补齐 production adapters

新增或实现：

- Qwen scheduler → Reviewer/Evaluator/Reflector/Summarizer 的统一调用 adapter；
- `ReviewMemoryStore` → `CoreV1ReviewerSession` → Reviewer request 的 adapter；
- paper/context/evidence → `ReferenceFreeRoleEvaluator` 的 adapter；
- `CoreReflector` → `SharedPoolMStar.mutate` 的 adapter；
- B-local manifests → `CoreRoleEvolutionRunner` 的 deterministic loader；
- role winners → Final Suite → Official access 的应用层 adapter。

### P0-4：新增真正能失败的 production-path integration smoke

使用 deterministic fake transports，但必须调用与 formal 相同的 production composition root，并至少验证：

- 五个 role 都经过 `CoreRoleEvolutionRunner`；
- Reviewer 经过 `CoreV1ReviewerSession`；
- Evaluator 经过 `ReferenceFreeRoleEvaluator` facade；
- search 至少执行一次真实 child attempt，而不是 `attempts=0`；
- Final Suite freeze 前 protected loader 调用数为 0；
- freeze/authorize 后才允许 Official stub 读取；
- production CLI 的 dry/smoke phase 返回 PASS。

这个 smoke 通过后，才值得启动 625 条真实 Corpus generation 和后续 capacity gate。

## 7. 修复后的验证顺序

用户确认程序修改后，按以下顺序执行，不改变研究语义：

1. unit tests；
2. deterministic/schema/isolation tests；
3. production-path integration smoke；
4. 真实 A 区 v5 Corpus build（625 条）和 freeze；
5. Projection/Fragment/Embedding/Index；
6. B-local/Evidence materialization 与真实 request/token statistics；
7. evaluator production smoke；
8. Qwen TP4 `max-num-seqs=1/2/4` capacity gate；
9. mechanical selection 和 MAIN runtime freeze；
10. final preflight；
11. five-role formal Evolution；
12. Final Suite freeze；
13. protected Official CoCo report-only evaluation。

## 8. 明确禁止的错误推进方式

- 不把 249 个单测通过解释为 formal-ready；
- 不把 component-contract tiny smoke 改名包装成真实 end-to-end；
- 不使用 legacy `evolve-meta` / `final-evaluate-suite` 启动 Core v1；
- 不在缺少 B-local/Evidence 真实 manifest 时用 synthetic fixture 冒充 formal；
- 不先运行昂贵的 625 Corpus/capacity，再期待不存在的 production入口自动出现；
- 不为解决装配缺口继续扩展 Audit Hardening v2 状态机；
- 不在用户确认前修改程序。

## 9. 等待确认的程序修改范围

建议确认以下最小范围：

1. 新增独立 Core v1 production composition root/CLI；
2. 新增最小 B-local manifest/eligibility materializer；
3. 新增最小 Frozen External Evidence Pool v1 materializer/API；
4. 接入现有 Memory、Reviewer、Evaluator、Reflector、search、suite 和 protected access 模块；
5. 新增基于 production path 的 integration smoke；
6. integration smoke 通过后，才恢复真实 Corpus → capacity → formal 流程。
