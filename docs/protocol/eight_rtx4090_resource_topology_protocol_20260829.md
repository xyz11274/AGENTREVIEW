# Eight RTX 4090 Resource Topology Protocol

状态：frozen semantic contract（2026-08-29），Q156–Q165已接受。本文冻结新formal在8×24GB RTX4090上的TP、GPU/NUMA assignment、context materialization、memory reserve、prefix cache、exclusive watchdog、phase restart与resource transition journal；具体 `max-model-len` 数值、capacity/window结果、process allowlist/cpuset与service lifecycle scripts尚待物化。本文不授权停止、迁移或复用当前运行服务。

## 1. Verified Hardware Topology

2026-08-29只读审计确认：

- 8张 `NVIDIA GeForce RTX 4090`，每张总显存24564 MiB；
- GPU0–3彼此为 `PIX`，属于NUMA node 0；
- GPU4–7彼此为 `PIX`，属于NUMA node 1；
- 两组之间为 `SYS`，需跨CPU/NUMA interconnect；
- `nvidia-smi topo -m`未显示NVLink。

因此单个Qwen默认应使用完整四卡本地岛，而不是未经gate直接跨两个NUMA岛使用TP=8。GPU ordinal只作execution provenance；正式resource manifest绑定GPU architecture、topology class、TP与runtime identity。

## 2. Verified Current Process Snapshot

只读审计时的现状是：

- GPU0–3：`Qwen3.8-27B` vLLM，TP=4、`max-model-len=262144`、`max-num-seqs=1`、gpu-memory-utilization=0.95；
- GPU4：旧CoCo Split model；
- GPU5：旧CoCo Classify model；
- GPU6–7：无compute process。

该快照只说明当前机器占用，不构成新formal Runtime Identity或授权状态。按新Suite协议，Evolution阶段CoCo不得部署；当前旧服务不能被静默视为新formal合规服务，也不得在本protocol设计阶段自动停止。

## 3. Frozen Preformal/Materialization Phase

- GPU0–3：单个Qwen3.8-27B TP=4 candidate runtime，执行mixed-workload `max-num-seqs=1/2/4` gate；
- GPU4–7：可用于Frozen Review Corpus/document embedding预计算分片与embedding一致性审计；
- 完成materialization后只保留immutable index，不保留Candidate-private runtime state；
- formal Evolution前必须关闭全部额外materialization workers并重新执行exclusive-resource preflight；
- Official CoCo Split/Classify在此阶段不得启动；
- Qwen TP=8不作为默认candidate，只有TP4无法满足已冻结context/capacity gate时才允许按新Runtime Identity单独测试。

## 4. Frozen Formal Evolution Phase

- GPU0–3（NUMA0 island）：唯一Qwen3.8-27B服务，TP=4；Reviewer、Evaluator、Reflector、compile-fix共享冻结global sequence budget；
- GPU6（NUMA1 island）：独立冻结的Qwen3-Embedding-8B query service；
- GPU4、5：不得启动Official CoCo Split/Classify或其他compute process；
- GPU7：spare/backpressure reserve，不运行未注册compute workload；
- Qwen scheduler使用冻结capacity C与active-paper window W；
- embedding与Qwen处于不同PCIe/NUMA岛，不争用同一GPU显存；
- CPU worker/process affinity优先与其GPU岛一致，并进入execution/resource provenance。

闲置GPU不是protocol failure。单Qwen约束优先于追求所有GPU表面利用率；不得通过未注册第二Qwen replica改变Reviewer/Evaluator execution identity。

## 5. Frozen Official CoCo Phase

只有 `OFFICIAL_COCO_AUTHORIZED` 后才转换为：

- GPU0–3：冻结Qwen TP=4，为sealed Final Suite在untouched C生成reviews；
- GPU4：Official CoCo Split；
- GPU5：Official CoCo Classify；
- GPU6：冻结embedding query service，为C上的合法Review Memory/Vector Search提供query embedding；
- GPU7：spare/backpressure reserve，不形成新的model identity或selection path。

CoCo service artifacts、requests与runtime identity必须重新preflight；不得因为旧进程已经存在就跳过授权或identity gate。

## 6. TP=4 and Context/KV Boundary

TP=4是formal默认，且必须使用单个四卡本地PIX/NUMA island。TP=8不进入普通 `max-num-seqs=1/2/4` gate，不与TP4结果混合。TP=8只有在以下条件全部满足时才可成为新的formal candidate：

- TP4无法通过预注册context/capacity gate，而不是仅因GPU利用率不满；
- TP8使用独立Runtime Identity与完整mixed-workload gate；
- 审计跨NUMA通信、throughput、p95、OOM与canonical decision stability；
- 明确解决embedding与Official CoCo阶段资源重排；
- 不与TP4结果混合。

`max-model-len`不得沿用未经materialization的262144默认值。Formal前必须使用冻结Qwen tokenizer，对Reviewer、Evaluator Applicability/Scoring、Reflector、compile-fix及其他formal request classes计算：完整input tokens + 冻结max output tokens + system/rubric/schema/evidence/framing全部开销。取最大required length，向上取整到8192整数倍，并验证不超过model artifact/config声明的最大context。随后用near-max mixed workload验证capacity C个并发请求；任何超限均fail-fast，禁止runtime truncation或动态扩容。最终值进入Runtime Identity。

TP4冻结 `gpu-memory-utilization=0.95`。Model load、warmup及prefix-cache初始化后，每张TP GPU的可观察空闲显存不得低于768MiB；capacity C的near-max mixed burst必须连续3次无OOM、无protocol failure。任一GPU低于余量或出现OOM，则当前capacity/config gate失败，并按预注册顺序降capacity或发布修改后的preformal Runtime Identity重新gate。Formal期间不得修改capacity、context、utilization或其他KV配置。

## 7. Exclusive Resource and NUMA Contract

Capacity gate与formal期间整台8-GPU机器是exclusive experiment resource：

- 禁止未注册GPU compute process；
- Qwen及相关CPU workers绑定本地NUMA island；
- Embedding以及授权后的CoCo绑定另一NUMA island；
- Qwen vLLM driver/workers及global scheduler绑定NUMA0；Evolution role controllers、paper/context workers及GPU6 Embedding绑定NUMA1；Official Split/Classify/Embedding继续绑定NUMA1；
- cpuset或numactl必须同时冻结CPU affinity与memory-node policy，并生成 `cpu_affinity_manifest_sha256`；
- exact CPU set、topology class与NUMA affinity policy进入Runtime Identity；
- GPU ordinal、PID、port与hostname只作execution provenance；
- preflight发现旧CoCo或其他未授权compute process时fail-fast，不自动kill。

## 8. Resource Transition Journal

Resource/suite journal正式增加：

- `RESOURCE_TOPOLOGY_COMMITTED`；
- `EVOLUTION_SERVICES_PREFLIGHTED`；
- `EVOLUTION_SERVICES_READY`；
- `EVOLUTION_SERVICES_STOPPED`；
- `OFFICIAL_SERVICES_PREFLIGHTED`；
- `OFFICIAL_SERVICES_READY`。

`EVOLUTION_SERVICES_READY`前必须验证Qwen/Embedding identities、GPU topology/affinity、exclusive-resource gate及Official CoCo未部署/未开放。`OFFICIAL_SERVICES_PREFLIGHTED`只能发生在 `OFFICIAL_COCO_AUTHORIZED` durable commit之后，并重新验证Qwen、Embedding、Split/Classify artifacts/runtime identities与资源拓扑。旧进程存在不能替代preflight，也不得继承为新formal committed runtime state。

`EVOLUTION_SERVICES_STOPPED`只能在五个role winner、Final Suite及相关formal state冻结后提交，并要求Evolution Qwen、Embedding、scheduler和workers全部退出、GPU资源释放，KV/prefix/request caches不跨阶段保留。授权后必须启动全新的Official Qwen、Embedding、Split、Classify进程，重新preflight并生成新execution provenance；model/Semantic Identity保持冻结，但不得adopt Evolution阶段进程。

## 9. Prefix Caching

Qwen保留 `--enable-prefix-caching`，其enabled状态、implementation/version与runtime config进入Runtime Identity。Mixed capacity gate同时覆盖cold-cache与warm-cache；Reviewer payload、Evaluator decisions、Reflector/compile-fix Program identity等全部behavior-relevant canonical outputs必须满足冻结稳定性。

Cache hit/miss只进入execution audit，不得作为Candidate输入、fitness、parent selection或scheduler priority；Candidate不得读取或控制cache。Evolution→Official强制重启必须清空cache。若cold/warm导致正式canonical identity不一致，则该Runtime Identity失败，禁用prefix caching并重新执行完整capacity gate。

## 10. Exclusive Resource Watchdog

只读watchdog检查GPU compute allowlist、model endpoints与resource manifest：

- `EVOLUTION_SERVICES_READY`前；
- 每个global attempt开始前；
- finalist/full-pool阶段开始前；
- formal期间后台每60秒。

普通只读监控工具不属于compute violation。检测到违反allowlist/topology的未注册compute process时，提交 `RESOURCE_EXCLUSIVITY_VIOLATION`，停止派发且不自动kill。Active attempt按role-level resource/infrastructure failure终结，formal role run fail-fast；外部进程随后消失也不得直接继续或混用违规前后结果，只能按冻结恢复/重新运行规则处理。

Observable contamination window默认从最后一次已提交clean watchdog observation到violation detection；若有可信、更早的违规process start provenance，则起点前移。Window前已提交 `ROLE_SEARCH_COMPLETED`的role可保持有效；其余共享受影响窗口的未完成role以新role-run identity从初始化重跑。完整scope规则见 `qwen_request_transaction_and_recovery_protocol_20260829.md`。

## 11. Pending Materialization

仍需物化并冻结：

- concrete `max-model-len`与model-context validation report；
- concrete memory-reserve/capacity C/cold-warm prefix-cache结果；
- exact NUMA/cpuset policy与process allowlist；
- Evolution-to-Official service restart/adoption规则；
- prefix-caching与other vLLM launch fields；
- watchdog allowlist、violation scope/recovery rule；
- 全部resource transition event schemas、preflight outputs与crash/recovery vectors。
