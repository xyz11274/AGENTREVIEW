# ADR-0011: Research Validity Core v1 与 Audit Hardening v2 实施分层

状态：accepted（2026-08-30）。本ADR记录Q526–Q527的实施层级决策；它不删除Q230–Q525的目标语义，而是决定哪些能力阻塞首次formal experiment。

## Context

协议设计已经同时覆盖科研有效性、可重现decision lineage、故障恢复、高可用、report publication与post-terminal archive。若把全部多Journal mirror、generation replacement、cross-Journal recovery和publication HA都作为首次运行前置，工程量会重新成为实验吞吐与启动时间的主要瓶颈，违背最初缩短全流程、提升并发利用率的目标。

另一方面，任何会改变数据可见性、Reviewer或Memory输入、Candidate生成、fitness、M★ search trajectory、winner、Final Suite、C/Human protected access或Official evaluation的简化都会直接损害科研有效性，不能以工程减负为理由推迟。

## Decision

正式采用两个显式Implementation Profiles：

### Research Validity Core v1

首次formal experiment的hard-blocking profile。至少包含：

- A/B/C与family/time split冻结，C untouched；
- Human/OpenReview/CoCo在Evolution全程不可见；
- reference-free role-specific evaluator、五角色rubric/applicability/fitness；
- A区625条no-memory Canonical Full Review Corpus及deterministic Projection/Embedding/Review Memory；
- 三种Memory seed、retrieval无隐藏LLM、post-retrieval Summarizer边界；
- shared-pool M★、20 attempts、binary64 parent selection、Candidate/Program identity；
- static/full-pool winner选择与Final Suite冻结；
- raw-response-first必要provenance、Candidate independence和Candidate-visible input determinism；
- Official前禁止C/Human，Protected Input preflight、Delivery Manifest与Actual Read Access Journal；
- Official request/evaluation identity及结果只report、不反馈Evolution；
- Protocol Root与benchmark-independent pre-birth validation；
- 足以重现research decisions的最小append-only/single-writer Journal、normal-path Evidence与fail-stop。

Core统一故障原则：

> Normal path must be strict; complex infrastructure failure may fail-stop the current Formal Root instead of supporting in-place recovery.

Core选择fail-stop不授权放松任何research/data-isolation semantics。

### Audit Hardening v2

Core科研闭环成功后实施。包括verified mirrors、activation barriers、Infrastructure generation replacement、Evidence terminalization、Root watchdog/mirror、复杂in-place recovery、availability-driven dependency closure/re-seal、Reporting ingress/Hold HA、Reporting Journal recovery、report replacement、formal publication promotion/replicas、post-terminal archive与external delivery audit。

V2不得改变model requests、Memory delivered bytes、RNG、Candidate births、fitness、parent selection、winner、protected Official inputs或Official evaluation。若发生这些变化，必须形成新的Research Protocol Semantic Identity，而不能继续称为纯hardening。

Q475/Q510的完整Formal Publication transaction、replica与archive硬要求仅适用于HARDENING_V2。CORE_V1成功至少要求immutable Official result manifest与deterministic report/result artifact合法commit。

## Performance Gate

CORE_V1在formal前通过smoke/microbenchmark实测并冻结total wall-clock、storage、critical service capacity及timeout/read/write budgets。保留no-audit/minimal-instrumentation baseline作工程比较，但它不是formal result。Core超预算时只优化I/O batching、serializer、Journal、cache、scheduler和并发实现，不关闭Core semantics。

Audit Hardening v2使用独立性能Profile，其未完成或性能未达标不阻塞首次CORE_V1 formal experiment。

实施顺序固定为：Core Protocol Root → deterministic/unit/synthetic gates → Core smoke → Core性能预算冻结 → Core formal experiment → Audit Hardening v2。

## Consequences

- 已接受的Hardening条款保留并显式标记，不被静默遗忘。
- CORE_V1实现范围必须由Profile Registry/manifest机器化，而不是靠文档阅读临时判断。
- 同一Formal Root不得在运行中切换Profile或启用未在Protocol Root中冻结的部分Hardening能力。
- V2实现不得读取CORE_V1、C/Human或Official结果来修改research semantics。
