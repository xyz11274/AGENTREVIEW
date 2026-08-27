# Reviewer / Evolution 解耦（2026-08-24）

> 本文是 [REVIEWER_EVOLUTION_DECOUPLING_20260824.md](REVIEWER_EVOLUTION_DECOUPLING_20260824.md) 的中文翻译（2026-08-25 译）。专有名词、代码标识、命令与 JSON 字段保持英文原文；内容与原文严格一致，原文为权威版本。

## 结果

运行时依赖方向现在是：

```text
MSTAR Evolution
      |
      v
AgentReview Reviewer Core
      |
      v
Candidate Review (candidate_reviews.jsonl)
      |
      v
Official CoCo Evaluation
```

`agentreview.reviewer_runtime` 不 import `memory_evolution`。其中不包含任何 search controller、mutation、reflection、selection、lineage、evolution budget、fitness 或 evolution checkpoint 代码。MSTAR import 这个 Core，并且仍然是全部 optimizer 行为的所有者。

## Reviewer Core

稳定公共值从 `agentreview.reviewer_runtime` 导出：

- `ReviewRequest`：一篇论文、一个 `task_id`、一个显式的 episode 序列。
- `ReviewResult`：评审文本加上 query/context 与稳定身份标识。
- `ReviewerEngine.review(request, memory_program)`：一次性公共入口。
- `ReviewerSession`：有状态的历史回放与 snapshot/restore，供 MSTAR 和批量评审编排使用。
- `MemoryProgramLoader`：接受 seed 文件、任意既有的 `runs/.../best_program.py`、未来 optimizer 的独立 Python 输出，或已晋升（promoted）的 task suite 根目录。
- `KnowledgeItem`、`Query`、`KnowledgeBase`、`write`、`read` 以及全部 `INSTRUCTION_*`/`ALWAYS_ON_KNOWLEDGE` 字段保持其可执行契约不变。

进程 sandbox、sandbox worker、固定的 TaskAgent prompting、capability toolkit、request 构造、episode 格式化以及 MemoryProgram loader 现在都位于 `agentreview/reviewer_runtime/` 之下。候选代码（candidate code）仍与宿主隔离，并且只能收到既有的 capability RPC 面。

Core 刻意不感知 A/B/C/D。它只理解普通的 request 与既有的 `self_generated_after_review` episode 协议。Zone 选择属于 CLI/evolution adapter 的职责。

## MSTAR 集成与不变的语义

`memory_evolution.evaluator.evaluate_program` 现在请求 `ReviewerSession` 生成每份评审。MSTAR 仍按相同顺序执行：

1. 按时间顺序的历史回放与写回（writeback）；
2. static/rotating validation 选择；
3. Reviewer Core 生成；
4. MSTAR fitness 计算；
5. reflection、mutation、selection、lineage 记账与晋升（promotion）。

这次重构不改变 softmax selection、temperature、lineage 公平性、attempt/completed 预算、static/rotating 分配、reflector prompt、contextual V4A、embedding、compile repair、fitness 公式或 checkpoint 身份。`candidate_reviews.jsonl` 只是观测产物（observation artifact）。它不属于 controller 状态、checkpoint/resume、reflection 输入、selection、fitness 或 champion 判定的一部分。

既有 import 通过兼容模块（compatibility module）继续有效：

```text
memory_evolution.program_protocol
memory_evolution.sandbox
memory_evolution.task_agent
memory_evolution.toolkit
memory_evolution.integration
memory_evolution.checkpoint
```

既有的 `python -m memory_evolution.cli evolve-task ...`、seed、已晋升的 program、run 产物以及 legacy 的 Reviewer JSON 文件仍然可读。

## Reviewer CLI

单任务：

```bash
python -m agentreview.cli review \
  --task-id QUAL \
  --memory-program memory_evolution/seeds/vector_search.py \
  --paper-id paper-1 \
  --question-file request.txt \
  --run-identity reviewer-run-1 \
  --output candidate_reviews.jsonl
```

Temporal C/D 批量：

```bash
python -m agentreview.cli review \
  --task-id QUAL \
  --memory-program runs/.../best_program.py \
  --temporal-manifest configs/coco_temporal_split_v2/manifest.json \
  --zone effectiveness_holdout \
  --coco-root /data/xyj/shengao/CoCoReviewBench \
  --episodes-jsonl frozen_A_episodes.jsonl \
  --run-identity effectiveness-run-1 \
  --output candidate_reviews.jsonl
```

temporal Reviewer adapter 只接受 `effectiveness_holdout`（C）与 `held_out_benchmark_test`（D）。temporal run 要求显式提供冻结的历史文件。fresh memory 只能通过显式的 `--allow-fresh-memory` 调试 override 获得。manifest 绝不会被用来猜测 A 区历史。

META 仍是单个 task，但其 question 必须包含已冻结的 specialist review。temporal META 批量使用 `--questions-jsonl`；Core 不会悄悄运行或拼接五个 specialist。

legacy 的六角色 CLI 仍作为上层编排层，并且现在在 `reviews.json`、`memory.json`、`review_history.json` 与 `timing.json` 之外，还按论文输出 `candidate_reviews.jsonl`。

## Candidate review 契约

在选定的 run 内，每行 JSONL 唯一对应一条 `(paper_id, task_id)` 评审：

```json
{
  "schema_version": "agentreview-candidate-review-v1",
  "paper_id": "paper-1",
  "task_id": "META",
  "review_text": "...",
  "reviewer_identity": {
    "name": "Final Meta-Reviewer",
    "task_id": "META",
    "rubric_sha256": "..."
  },
  "memory_program_identity": {
    "logical_identity": "META/best_program",
    "source_kind": "promoted_best_program"
  },
  "memory_program_sha256": "...",
  "model": "qwen3.8-27b-fp8",
  "revision": "...",
  "run_identity": "final-run-1",
  "provenance": {}
}
```

`provenance` 是可选的，并且是 optimizer 专属字段（如 candidate、parent、iteration、lineage、replicate、score）的唯一存放位置。Reviewer Core 和 CoCo 都不要求它。

## 独立的官方 CoCo 评测

入口：

```bash
python -m agentreview.coco_eval \
  --input candidate_reviews.jsonl \
  --task-id META \
  --coco-root /data/xyj/shengao/CoCoReviewBench \
  --output-dir coco_evaluation
```

`--task-id` 为必填。`--run-identity` 可以从观测产物中选定一个 run。过滤之后，重复的 paper ID 会被拒绝；不会隐式拼接 specialist review。

该 adapter 只写出官方的 `{id, text}` candidate 输入，然后按顺序运行官方程序：

```text
src/evaluation/_2_split.py
  -> src/evaluation/_3_classify.py
  -> src/evaluation/_4_evaluation.py
```

它不使用 MSTAR 的 run 目录、lineage、selection、champion 或 checkpoint。它不调用 `CoCoReviewFitness`——后者的调和平均公式仍属于 MSTAR evolution fitness，而非官方 benchmark 协议。

默认不提供任何 evaluation flag，因此 `_4_evaluation.py` 保留其官方默认集合（category level、旧版全文指标、以及 paper-level judge）。官方 flag 原样转发，不做重新解释。judge 默认为同一个 `qwen3.8-27b-fp8` backend 完成整场评测；没有 fallback，也没有混合 judge 报告。

该 adapter 钉住（pin）以下内容的 SHA-256：官方 split、classify、evaluation、taxonomy/prompt registry、judge prompt、parser/utilities、内置的 left-out selection、完整的人类 classify 参考，以及所有受支持的 benchmark JSONL 文件。它记录实际的 CoCo Git HEAD，但不会拒绝非协议性改动（如 onboarding 或服务启动脚本）。

该 checkout 不附带 `benchmark_reviewer_selection.json`。adapter 使用 CoCo 自带的 `_0_human_review.load_selection_from_left_out_file` 在输出目录中创建等价的 selection sidecar，再让官方 evaluator 指向该 sidecar。Human-reference 的构造与聚合仍留在官方 `_4_evaluation.py` 内部。

## 四区边界

- A（`historical_experience`）是显式的 Reviewer 历史。
- B（`evolution_validation`）仍由 MSTAR 的 fitness、selection 与 reflection 拥有。
- C（`effectiveness_holdout`）只能通过独立的 Reviewer adapter 访问。
- D（`held_out_benchmark_test`）只能通过独立的 Reviewer/final-evaluation adapter 访问，永不进入 evolution。

Core 中不包含任何 zone 名称，也没有 temporal manifest 解析器。

## 确定性验证

测试覆盖：

- 只用 Reviewer 执行一个 seed；
- 只用 Reviewer 执行既有的 `best_program.py`；
- import Core 时不加载任何 `memory_evolution` 模块；
- legacy evolution 生成入口与新 Core 之间的 mocked 语义一致性（parity）；
- 无 provenance 的独立 candidate JSONL 到 CoCo 的映射；
- CoCo 重复论文拒绝，以及对 lineage/checkpoint 零依赖；
- 未来 optimizer 的输出被当作普通 MemoryProgram 加载；
- temporal C/D 要求显式历史，除非选择调试 override；
- mocked 的官方 split/classify/evaluation 命令编排。

本次验证不包含新的 evolution、真实 LLM 冒烟测试或长 CoCo benchmark。
