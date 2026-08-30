# Evaluator Identity and Capacity Gate Protocol

状态：frozen contract（2026-08-29），Q101–Q105、Q146–Q154与Q156–Q165已接受。本文冻结model independence、semantic/runtime identity、identical retry、shared-Qwen mixed-workload capacity selection、8×RTX4090 runtime与formal model preflight；完整scheduler/DAG见 `shared_qwen_scheduler_and_parallel_pipeline_protocol_20260829.md`，resource topology见 `eight_rtx4090_resource_topology_protocol_20260829.md`。当前concrete manifests、artifact hashes与gate results尚未生成。

## 1. Model Independence Is Not Identity Equality

Qwen3.8-27B 的 FP8、BF16 或其他量化版本不视为相对于基础 Qwen3.8-27B 的独立 evaluator model，不能用来宣称 Reviewer/Evaluator model independence。

但以下任一变化都必须形成新的 Evaluator Identity，实验分数不得混合：

- model source/revision 或 weight artifact；
- dtype/quantization 或 quantization configuration；
- config、tokenizer、tokenizer-config 或 chat template；
- architecture identity；
- 任何会改变模型输入、生成或解析语义的资产。

Model artifact identity 至少冻结 model source、immutable revision、weight-file manifest SHA、config SHA、tokenizer/tokenizer-config SHA、chat-template SHA、dtype/quantization config 与 architecture identity。Served model name 不能替代 artifact identity。

## 2. Two-Layer Evaluator Identity

### EvaluatorSemanticIdentity

至少包含：

- model artifact identity；
- role rubric、prompt、schema 与 registry artifact SHAs；
- tokenizer/chat-template identity；
- 完整 generation request JSON；
- input modality identity；
- Paper Evaluation Context、Source Anchor 与 frozen evidence formatting/order identities；
- canonical serializer、strict parser、validator 与 aggregator identities；
- role-specific aggregation、validity与 consequence rules；
- smoke/registry gate bundle identity。

### EvaluatorRuntimeIdentity

至少包含：

- backend/provider implementation 与 exact vLLM/package versions；
- dtype/quantization runtime configuration；
- tensor-parallel size；
- max model length；
- reasoning parser；
- prefix-caching configuration；
- `max-num-seqs`；
- CUDA、driver 与 GPU architecture；
- 其他可能影响数值执行、batching 或 decoding 的启动字段。

Formal manifest 同时保存并校验 semantic/runtime canonical SHA。Base URL、port、PID、GPU ordinal 与进程启动时间只属于 execution provenance，不进入 semantic identity；它们仍必须记录。

## 3. Frozen Evaluator Request and Identical Retry

Formal Evaluator request JSON 必须完整冻结，至少显式确定：

- `temperature=0`；
- greedy / `do_sample=false`；
- max tokens；
- seed（provider 支持时）；
- stop sequences；
- reasoning configuration；
- response/schema configuration；
- 所有 sampling 与 generation fields，包括 provider defaults 的处理方式。

Q29 的最多两次 evaluator attempts 必须使用完全相同的 request bytes、input identity、model/semantic/runtime identity 与 fresh-session条件。当前旧路径的 `8192 -> retry 16384` adaptive budget 不得用于新 Evaluator。未实际发送的 `reasoning_effort` 必须记录为 `not_sent/unsupported`，不能只记录本地配置值。

非法 JSON/schema/truncation 可以触发一次 identical retry；第二次不得改变 token budget、stop、prompt、schema 或任何 generation field。

## 4. Pre-Formal Capacity Gate

使用benchmark-independent captured mixed workload依次测试 `max-num-seqs=1,2,4`，覆盖Reviewer、Evaluator、Reflector与compile-fix共享同一Qwen服务的实际组合。每个配置必须：

- Evaluator在不同capacity间产生完全一致的canonical score vectors、applicability decisions与event sets；
- temperature=0的Reviewer、Reflector与compile-fix在每个候选capacity内部满足预注册deterministic stability并保持Program/output identity稳定；跨capacity raw-byte完全一致只作额外证据，不是选择必要条件；
- 不出现 OOM、silent truncation 或新增 schema failure；
- 记录 requests/second、tokens/second、p50/p95 latency、peak GPU memory 与错误率；
- 记录 server/runtime manifest SHA 与全部 raw outputs。

每个captured request在每个capacity下重复3次。Evaluator canonical decisions必须完全一致；Reviewer canonical Review payload SHA、Reflector最终Program identity、compile-fix最终Program identity/compile disposition必须在同capacity内一致。Frozen workload必须覆盖near-max Reviewer burst、Reviewer/Applicability/Scoring混合、Evaluator/Reflector/compile-fix混合、五role burst及retry/cancel/replay，并使用相同enqueue workload测试1/2/4。

选择规则：

1. 4 完全通过，则 formal 冻结 `max-num-seqs=4`；
2. 否则若 2 完全通过，则冻结为 2；
3. 否则若 1 完全通过，则冻结为 1；
4. 1 也未通过，则 Evaluator Identity 不得进入 formal。

Formal 开始后不得动态修改 `max-num-seqs`、max model length、TP、dtype/quantization、reasoning parser、prefix caching 或其他 runtime identity。任何修改都形成新 Runtime Identity，旧结果不能继续混用。

Reviewer、Evaluator、Reflector与compile-fix共享冻结的global sequence budget；不得按role或task type分别获得完整 `max-num-seqs`。Scheduler固定role round-robin `QUAL → CLAR → SIGN → ORIG → POL`，role内按committed logical request ID FIFO；policy、implementation/version与capacity进入Runtime Identity。

每个request必须先durable commit `REQUEST_ENQUEUED`，并具有full-hash logical request ID、role-local monotonic sequence与frozen logical-order key；role内实际FIFO按sequence而非hash或arrival time。另以相同workload三次重复测试 `active_paper_window=C/2C`，按median completed logical papers/requests per unit time计算；只有2C安全且throughput至少提升10%才采用，否则采用C。Selected window进入Runtime Identity且formal期间不可变。

新formal默认使用单个Qwen3.8-27B、TP=4，并绑定同一四卡本地PCIe/NUMA island。TP8不进入普通capacity gate；只有TP4无法满足冻结context/KV要求时，才能以新Runtime Identity单独gate，结果不得与TP4混合。

`max-model-len`由冻结tokenizer对全部formal request classes进行完整token materialization后确定：最大input + max output + system/rubric/schema/evidence/framing全部开销向上取整到8192整数倍，并不得超过model artifact/config最大context。Near-max mixed workload必须在capacity C下通过；禁止runtime truncation、adaptive context或formal中修改该值。

TP4使用 `gpu-memory-utilization=0.95`，warmup/prefix-cache初始化后每张TP GPU至少保留768MiB可观察空闲显存，并连续3次通过near-max burst。Prefix caching默认enabled，cold/warm canonical identities必须一致；否则禁用并重新执行完整gate。Exact cpuset/memory-node policy SHA进入Runtime Identity，GPU ordinal只作provenance。

## 5. Formal Model Preflight

`/models` served name 只是 preflight 的一个输入，不足以证明 identity。Formal 前必须冻结并验证：

- local model/weight/tokenizer/chat-template manifests 与 SHAs；
- actual vLLM/backend launch arguments canonical manifest；
- backend/package/CUDA/driver/GPU identity；
- `/models` raw response；
- exact request JSON；
- semantic/runtime identity canonical JSON 与 SHA；
- smoke、registry/parser/validator 与 concurrency-capacity gate results；
- raw request/response bytes、finish reason、token usage、attempt/session provenance。

Served name、immutable revision、artifact SHA、semantic SHA 或 runtime SHA 任一与 formal manifest 不一致，preflight fail-fast。禁止仅因 API 返回 `qwen3.8-27b` 就继续。

## 6. Current Static-Configuration Gap

截至本文创建时，旧 active `resume.cmd` 记录了 `qwen3.8-27b`、model source `/data/xyj/models/Qwen3.8-27B`、revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`、vLLM `0.27.1`、LLM temperature `0.0`、max tokens `8192` 与 retry max tokens `16384`。旧 server command 文档记录 TP=4、max-model-len=262144、max-num-seqs=1、gpu-memory-utilization=0.95、reasoning-parser=qwen3 与 prefix caching。

这些记录不是新 formal Evaluator manifest，也不证明实际 artifact、tokenizer/template、dtype/quantization 或 runtime identity。`docs/LOCAL_QWEN_OPENAI_ALIGNMENT.md` 中存在旧 `qwen3.8-27b-fp8` served-name 示例，与 active command 的 `qwen3.8-27b` 不一致；新 preflight 必须通过 artifact manifests 消除该歧义，不能继承旧名称假设。

## 7. Pending Materialization

Formal-ready 仍需生成：

- concrete model artifact manifest；
- five role-specific Semantic Identity manifests；
- selected Runtime Identity manifest；
- exact request JSON；
- smoke、registry/parser与 capacity gate bundle；
- actual raw-output/provenance storage schema；
- all corresponding canonical SHAs。
