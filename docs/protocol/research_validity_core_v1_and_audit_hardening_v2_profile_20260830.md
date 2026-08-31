# Research Validity Core v1 and Audit Hardening v2 Profile Amendment

> 本文保存实施分层合同；当前代码与工件状态以本目录 [README](README.md) 为准。

状态：frozen amendment（2026-08-30），Q514–Q527已接受。本文件决定实施阻塞范围；不删除或改写其他协议中的目标语义。发生冲突时，research-observable语义仍以原协议为准，而“是否阻塞首次formal run”以本Profile Amendment为准。

## 1. Profile Registry

每个protocol feature必须标记且只能标记为：

- `CORE_V1_BLOCKING`
- `HARDENING_V2`

CORE_V1_BLOCKING的判定标准：缺失或改变该feature会影响数据/信息边界、Reviewer或Evaluator request、Memory delivered bytes、Candidate行为、fitness、RNG/search trajectory、winner、Final Suite、protected Official inputs或Official evaluation/report-only隔离。

HARDENING_V2只允许增强durability、availability、in-place recovery、formal publication或archive，而不改变上述research-observable语义。

## 2. CORE_V1_BLOCKING Scope

Core至少包含ADR-0011列出的科研语义，以及：

- `protocolroot_ / protocolbootstrap_ / protocolrootval_ PASS`；
- benchmark-independent Protocol validation run/attempt/Evidence；
- Protected Input preflight、Delivery Manifest、Actual Read Access Journal；
- normal-path raw-response-first records及足以重建decision lineage的authoritative events；
- deterministic report/result artifact；
- infrastructure anomaly的closed fail-stop，不要求原Root salvage。

Core Journal corruption、Controller implementation变化、跨generation recovery或复杂cross-Journal authority损坏可以直接fail-stop当前Root。未来实验必须创建新Root；不得静默salvage。

## 3. HARDENING_V2 Scope

Hardening包括：

- verified Journal mirrors与mirror activation；
- Root Watchdog、Root archive和external delivery audit；
- Infrastructure Journal replacement、Replacement Plan与Evidence terminalization；
- complex recovery epochs、availability-driven dependency closure/re-seal；
- Reporting ingress terminal sequence、Hold race hardening和Reporting Journal mirror/recovery；
- report artifact storage replacement；
- formal publication candidate/promotion、attempt recovery与replicas。

这些feature的目标语义继续冻结，但其machine artifacts未实现不阻塞首次CORE_V1。

## 4. Core Reporting Completion

CORE_V1不要求Q510的完整Formal Publication transaction、mirror或archive。成功Root至少要求：

- immutable Official result manifest；
- deterministic, model-free report/result artifact；
- 与最终Official terminal和report-only状态一致的标签；
- no feedback path to Evolution；
- minimal single-writer commit或fail-stop。

HARDENING_V2才要求formal publication artifact、candidate promotion、verified replica、Reporting Journal seal/mirror与post-terminal archive。

## 5. Profile Identity and Gates

Protocol Root必须绑定Profile Registry、每个feature的profile assignment及dependency graph。Cross-registry gate必须证明：

- 所有research-observable dependencies都落在CORE_V1；
- HARDENING_V2 feature被关闭时存在closed Core fail-stop替代路径；
- Core不会调用或部分启用未物化的V2 authority；
- 同一Formal Root不能动态切换Profile；
- V2实现若改变research-observable bytes/decisions会产生新Protocol Root。

## 6. Performance Gate

CORE_V1 preformal先以synthetic/smoke workload测量，再预注册：

- total wall-clock upper bound；
- peak/total storage upper bound；
- Qwen/Embedding/Reviewer/Evaluator/Official capacity；
- request/read/write/timeout budgets；
- Corpus、Projection/Embedding、paper evaluation、Evolution与Official throughput。

No-audit/minimal-instrumentation baseline仅用于工程比较，不产生formal result。Core超预算只能优化execution implementation，不能弱化Core semantics。

V2使用独立性能Profile，不能阻塞首次CORE_V1 formal experiment。
