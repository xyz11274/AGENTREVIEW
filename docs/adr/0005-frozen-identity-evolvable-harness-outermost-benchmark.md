# ADR-0005：Frozen Reviewer Identity、可演化 Memory Harness 与外置 Benchmark

- 状态：Accepted
- 适用范围：Reviewer、MemoryProgram、候选可见信息与外部评价

## Context

系统需要稳定的审稿任务身份，同时允许搜索记忆、检索、上下文组织和经验抽象策略。效果归因要求 Reviewer 职责保持稳定，搜索变量保持清晰，外部 benchmark 保持独立。

## Decision

系统采用三层边界。

### 1. Frozen Reviewer Identity

固定内容包括：

- QUAL、CLAR、SIGN、ORIG、POL 与 META 的职责；
- 六个 system prompt、rubric 维度和输出语义；
- Specialist request v1 与 META synthesis request v2；
- Candidate 与 evaluator 的信息边界；
- Benchmark 的外部评价权。

`REVIEW_Request.md` 是六个有序章节的身份内容源。`agentreview/reviewer_identity.py` 是唯一渲染 Implementation，`configs/frozen_reviewer_identity_v2.json` 记录 source SHA、六个 task prompt SHA、Specialist/META request template SHA 与 composite suite SHA。部署编排、Reviewer Core、Evolution、META cache、Effectiveness design lock 与 formal gate 读取同一套身份。

META v2 请求依次组合 paper ID、同一论文的 Canonical Paper Context（正文与 Source Anchors）、`QUAL/CLAR/SIGN/ORIG/POL` 五份 Specialist Review 和最终输出指令。

### 2. Evolvable Review Harness

MemoryProgram 可以改变：

- memory schema；
- write-time abstraction；
- index 与 retrieval strategy；
- query construction 与 context organization；
- read-time compression；
- Frozen S2、embedding、vector store 和 LLM capability 的使用策略；
- review workflow 中由 MemoryProgram 协议承载的逻辑。

固定 TaskAgent 提供 extract、query 与 response Adapter。当前 Implementation 使用 LLM 按严格 JSON schema 构造 KnowledgeItem、Query，并用冻结 task system prompt 生成最终 Review。

### 3. Outermost Benchmark

Reviewer Core 保持 benchmark-agnostic。评测调用在 Reviewer 输出之后进入 Benchmark Adapter：Evolution 与 Sealed D 使用进程内 `ReviewSubmission` 逐条计分，Official CoCo 使用已持久化的 `candidate_reviews.jsonl` 启动独立三阶段运行。

## Shared raw material and program-specific experience

同一次比较的所有 Program 使用同一批冻结原始历史论文、candidate-visible metadata 与 source anchors。每个 Program 从空状态顺序 replay 该执行面的冻结历史子集，并按 `self_generated_after_review` 协议生成自己的 Review、KnowledgeItem 与 Memory。Evolution 默认从 A 区选择 125 个 Episode；Effectiveness 固定选择 20 个 A 区目标；Reviewer Core 使用调用方提供的 Episode 序列。

Program 之间共享题目身份和能力边界。每个 Program 拥有独立运行时 Memory。

## Human Reference Boundary

Candidate-visible：

- 论文正文；
- paper ID、title、venue、year 与 reviewer task；
- source anchors；
- 当前 Program 自身 Memory；
- 当前 paper scope 的 Frozen S2 capability。

Evaluator-only：

- human review 与 gold；
- benchmark taxonomy 和 score；
- judge rationale 与完整 evaluator trace；
- internal provenance 中的受限评价字段。

Reflector 接收 scalar fitness、安全数值指标、预定义错误类别与资源统计。

## Experimental controls

同一次正式比较冻结 Base LLM、generation parameters、模型 revision、embedding identity、data manifest、capability contract 与 evaluator revision。跨独立实验可以把 backbone 作为显式受控变量。

## Consequences

- Evolution 的主要可变对象是完整 MemoryProgram 源码。
- 三个 Seed 共享 A/B paper identity，各自生成经验并建立 Memory。
- Candidate evaluation 使用 fresh-state replay，收益归因于当前 Program。
- Reviewer Core 提供稳定协议；部署编排把 MemoryProgram 作为 context provider；Reviewer Core CLI 执行完整协议。
- Benchmark 扩展集中在 Adapter seam，Reviewer Core Interface 保持稳定。
- 所有正式产物携带 Program、data、model、capability 与 evaluator provenance。
