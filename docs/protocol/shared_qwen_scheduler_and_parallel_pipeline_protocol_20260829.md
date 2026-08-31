# Shared Qwen Scheduler and Parallel Pipeline Protocol

> 本文保存语义合同；当前代码、生产接线与 G2 物化状态以本目录 [README](README.md) 为准。

状态：frozen semantic contract（2026-08-30），Q146–Q155、Q167–Q174与Q230–Q392已接受。本文冻结single-Qwen mixed-workload capacity gate、global sequence budget、role round-robin/FIFO scheduler、logical request/transaction、paper DAG/active window、failure cancellation、Query Embedding occurrence/batch-item raw-response/all-or-none-validation/self-contained batch-results atomic commit/non-authoritative dedup observation/pure-content-vs-independent-commit identity/bounded materialization-attempt state machine/cutoff-recovered order/paused-owner recovery/actual-dispatch accounting/role-local idempotent workflow visibility/deterministic cursor/sealed-role scheduler admission/suite-level one-shot compatibility lifecycle/durable-enqueue/cache-resolution/closed-terminal/cache生命周期/独立scheduler/shared-failure/non-Candidate-padding/strict byte-stability gate与按授权部署CoCo/Embedding的边界；request事务完整规则见 `qwen_request_transaction_and_recovery_protocol_20260829.md`。Concrete mixed-workload assets、selected capacities/windows、scheduler implementation与gate results尚待物化。

## 1. One Shared Qwen Sequence Budget

Reviewer、Evaluator、Reflector、compile-fix与已注册的post-retrieval `LLM Summarizer`共享同一个Qwen服务和一个global in-flight sequence budget。五个role可以逻辑并行提交任务，但global同时运行的Qwen sequences不得超过Q146最终冻结的 `max-num-seqs`；不得按role、task type或Summarizer Program分别复制完整额度。

Capacity候选固定为 `1 → 2 → 4`，不测试或启用5。最高通过capacity被写入Runtime Identity，formal期间不得动态修改；修改即产生新Runtime Identity，旧结果不得混用。

TP4固定 `gpu-memory-utilization=0.95`，每卡warmup后至少保留768MiB可观察空闲显存。Prefix caching默认enabled并进入Runtime Identity；cold/warm输出必须通过相同within-capacity stability gate，否则禁用prefix caching并从头重跑capacity gate。

## 2. Mixed-Workload Capacity Gate

Preformal使用A/B/C与Human/CoCo无关的同一套benchmark-independent captured mixed workload，覆盖Reviewer、Evaluator Applicability/Scoring、Reflector、compile-fix与注册的post-retrieval LLM Summarizer。即使capacity为1或2，也enqueue完全相同workload，只由scheduler限制实际in-flight数。每个captured request在每个capacity内重复3次，并检查：

- OOM、backend error与resource exhaustion；
- truncation、finish reason与token budget；
- schema/protocol validity；
- latency、throughput、peak GPU memory与batch provenance；
- candidate capacity自身在冻结input、seed、generation config与scheduler policy下的重复稳定性。

Evaluator在不同capacity之间及同capacity三次重复中都必须保持canonical applicability、score vector、event set与parsed canonical JSON完全一致。Temperature=0的Reviewer、Reflector、compile-fix与LLM Summarizer不要求 `1/2/4` 三种capacity之间raw bytes完全相同；cross-capacity byte identity只作额外证据，不是选择4/2/1的必要条件。但每个capacity内部三次重复必须满足：Reviewer canonical Review payload SHA一致；Reflector最终 `program_identity_sha256` 一致；compile-fix最终Program identity与compile disposition一致；LLM Summarizer canonical summary SHA、finish reason与protocol disposition一致。Provider raw reasoning或非identity自由文本不参与比较。

Frozen mixed-workload suite至少包含：

1. near-max Reviewer burst；
2. Reviewer + Applicability + Scoring混合；
3. Evaluator + Reflector + compile-fix混合；
4. deterministic retrieval + RetrievalResultManifest + LLM Summarizer混合，覆盖`stop`、合法非空`length`、empty response与transport replay；
5. 五role round-robin burst；
6. Evaluator retry、Candidate cancellation与infrastructure replay。

Payload只使用benchmark-independent synthetic/smoke数据，覆盖short、typical与near-max token长度以及五role rubric/prompt/schema；禁止读取A/B/C、Human或CoCo资产。

选择规则仍为4通过则4，否则2，再否则1；1也失败则共享Qwen Runtime Identity不得进入formal。

## 3. Frozen Global Scheduler

每个Qwen request同时具有：

- domain-separated完整 `logical_request_id`；
- role-local monotonic `logical_request_sequence`；
- 冻结 `logical_order_key`，至少包含stage、attempt、Candidate、paper order、phase与retry index。

Request必须先由role controller durable commit `REQUEST_ENQUEUED`，之后global scheduler才可接收执行。完整request ID用于identity/integrity验证；role内执行顺序使用committed `logical_request_sequence`，不以request SHA字典序或wall-clock arrival竞争决定。

每次model invocation另有request-attempt identity/retry index。Provider response必须先durable commit raw response，之后才可canonicalize；已commit raw response在crash后只重解析，不重发模型。Backend restart复用committed responses，只重发dispatch已commit但response未commit的requests。

Global scheduler采用：

- role轮转顺序固定为 `QUAL → CLAR → SIGN → ORIG → POL`；
- role内按committed `logical_request_sequence` FIFO；
- global in-flight上限等于冻结 `max-num-seqs`；
- execution completion order不得改变role journal、pool ordering、fitness aggregation、softmax或RNG；
- scheduler policy、implementation/version与capacity进入Runtime Identity。

Queue backpressure冻结为 `max_queued_qwen_requests=2W+5`。达到上限后controller阻塞enqueue；queue wait不得丢弃、重排或临时扩W。只有global scheduler持有Qwen endpoint capability，Candidate/MemoryProgram/Evidence API不得直接访问。LLM Summarizer只能通过Program manifest中注册的受限scheduler-mediated capability提交post-retrieval summary request；该capability不得用于query construction、search、filter、top-k、hydration decision或rerank。

必须保存每个request的enqueue/start/finish、batch membership、backend execution、token usage、finish reason与error provenance。Scheduler只控制资源执行，不成为cross-role search-state journal，也不得共享Candidate mutable state。

## 4. Frozen Paper DAG

每个 `Candidate × paper × role` 使用以下DAG：

1. 构建deterministic Paper Evaluation Context与candidate-independent Evidence availability；
2. 在fresh paper-local session中以byte-preserving queries执行Memory retrieval并维护ledger。每个memqocc对应一个logical Embedding request；cache miss提交enqueue后才进入scheduler。Batch raw response先commit再统一验证；VALIDATED后生成全部records，但只有self-contained `QUERY_EMBEDDING_BATCH_RESULTS_COMMITTED`原子提交完整有序record objects/vector bytes/qemb identities后整批才生效。Payload以不自引用的`qembbatch_`标识，外层Journal event独立证明正式commit；Vector JSON使用RFC4648 padded Base64、无compression，real items按tensor position排序且padding排除，并strict验证length/dtype/dimension/SHA/finite。Role-local RECORD event是确定性幂等terminal projection及当前workflow visibility barrier；它commit前不得继续Memory API response/budget/retrieval/delivery。崩溃时从committed validated raw/validation重建完整不可拆分batch event，不promote temporary/orphan；batch event已commit则只补role projection。Retry保持原role FIFO并重新组batch。之后才按mode生成Memory context、可选执行post-retrieval Summarizer及Reviewer；
3. 在fresh evaluator session中执行Candidate-blind Applicability；
4. Reviewer output与canonical Applicability均完成后，执行Scoring；
5. 生成immutable canonical paper evaluation record。

步骤2和3可以并行，因为不得读取彼此状态；步骤4必须等待二者。Applicability仍为每个Candidate×paper×role独立执行，不跨Candidate缓存或共享formal decision。Worker完成顺序不得影响controller按冻结paper order进行的aggregate commit。

除physical Qwen sequence budget外，另冻结global `active_paper_window`，只控制同时保留和推进的paper DAG/context状态。Preformal对 `W=C` 与 `W=2C` 使用相同workload/request identities各重复3次，其中C为已选择的 `max-num-seqs`。Throughput定义为median completed logical papers/requests per unit time。只有 `2C` 无OOM、无protocol failure、满足预注册显存余量，且相对C的median throughput至少提升10%，才选择 `2C`；否则选择C。Formal期间不得动态调整W。

Reviewer request materialization preflight还必须对全部formal `paper × role`计算完整non-Memory tokens、冻结Reviewer max-output与safety reserve，并取最小headroom冻结统一`max_reviewer_memory_context_tokens`。计数包括chat template/special tokens；Memory visible projection的framing/provenance/text只在Memory context侧计数一次。统一cap不足以容纳协议最小技术payload时preformal fail并更换Runtime/Protocol Identity重新gate；不得把该下限解释为必须retrieval或非empty Memory。

`frozen_safety_reserve`只在完整request materialization、冻结tokenizer accounting与benchmark-independent near-max gate后预注册为非负整数。本地完整request token count与正式backend/provider prompt-token accounting必须通过冻结一致性gate；不一致时修复tokenizer/template/materializer/accounting identity并重gate，不得扩大reserve掩盖。Reserve值、依据、test vectors、accounting outputs与manifest SHA进入Runtime/Protocol Identity，formal期间不可修改。

Token accounting gate要求每个冻结test vector的local完整rendered request count与backend/provider `prompt_tokens`严格整数相等，覆盖short/typical/near-max、五role及cold/warm prefix cache，每项重复3次。Usage缺失或任何一次不等均fail；保存rendered bytes、token IDs/count与provider usage。Summarizer-input和Reviewer-memory两个gate分别计数、分别绑定budget identity，禁止交叉借用或把Summarizer queries/raw Memory计入Reviewer-visible context。

## 5. Retry, Cancellation, and Failure Scope

- Evaluator第一次结构非法：仅同一paper执行Q29 fresh-session identical retry，其他无依赖workers可继续；
- 第二次非法、Applicability inconsistency或其他role-level validity failure：停止派发新任务、取消未开始任务并role fail-fast；
- role failure后，已经in-flight的请求可以安全结束并保存raw execution audit，但不得再提交canonical或aggregate decision；
- 普通search-stage child发生Candidate-originated paper/runtime/API failure：终止该Candidate剩余papers、消耗attempt并继续后续global attempt；
- mandatory initial seed failure：initialization fail-fast，不得继续search；
- global top-3 finalist在full-pool reevaluation发生Candidate-originated failure：winner selection fail-fast，不得淘汰、递补或只比较剩余finalists；
- infrastructure failure：暂停受影响工作并恢复同一logical task；identity完全匹配且durable committed的paper records可复用。
- LLM Summarizer空响应或provider/backend故障：infrastructure failure，不回退unsummarized Memory；
- Summarizer越权修改/扩充RetrievalResultManifest、读取未召回Memory或非法调用Qwen：Candidate-originated protocol failure，按当前Candidate所属阶段处理。
- `SUMMARY_INPUT_BUDGET_EXHAUSTED`：Candidate-originated runtime/protocol failure；ordinary child消耗attempt且不admit，mandatory seed initialization fail-fast，finalist winner-selection fail-fast，不转换为fitness `0`或fallback。

Partial/inflight结果不得形成partial fitness、admission或winner decision。

Paper进入terminal流程后先禁止新Query Embedding intent。正常完成必须等待全部相关Embedding transactions合法terminal；Candidate/role failure等先关闭paper时，全部未terminal transactions提交`QUERY_EMBEDDING_ABANDONED`，其late response只作raw audit。Paper terminal event随后作为唯一cache-close authority，并销毁query cache、Memory budget counters、RetrievedMemoryLedger、RetrievalResultManifest、hydrated context、summary与其他paper-local runtime state；qemb/raw Embedding responses/provenance仅保留audit。下一paper从相同immutable Corpus/Projection/Index与fresh session开始，即使query bytes与`memqry_`相同也重新执行Embedding事务。B paper不得写回Review Memory，resume artifact不得跨paper identity复用。

## 6. CoCo and Embedding Deployment Boundary

Evolution期间不得启动Official CoCo服务，权限层必须禁止访问C、Human references与CoCo评价资产。只有suite journal中的 `OFFICIAL_COCO_AUTHORIZED` durable commit后，才允许部署或开放Official CoCo。

Frozen Review Corpus与Frozen External Evidence中可预计算的document embeddings应在preformal生成、冻结identity并形成immutable read-only assets。Formal runtime只计算必要query embeddings；embedding不得调用生成模型。Embedding identity绑定model/revision、tokenizer、pooling、normalization、dtype、projection implementation、source manifest SHA、vector-byte SHA与index SHA。Embedding service使用独立冻结identity并优先使用独立GPU/CPU资源；若与Qwen竞争同一GPU，必须纳入统一resource scheduler并重新通过相应capacity gate。

External Evidence-derived query cache、rerank、summary、runtime embedding或其他Candidate-private派生状态不得跨paper持久化或写回Review Memory，每个Candidate×paper结束后丢弃。Review Memory侧同样只允许冻结Corpus、Projection、Index及其immutable document embeddings跨paper共享；query、ledger、manifest、hydrated context、summary、budget counters和其他paper-local派生状态必须销毁。不存在可由B paper累积的Candidate-private persistent Memory例外。

Full Review Corpus embedding index是formal Vector Search可使用的冻结资产。External Evidence embedding的formal可用性仍受Frozen Evidence API identity约束：v1中预计算index保持dormant，不暴露给Candidate/Evaluator、不参与semantic search/rerank或返回顺序；v1 runtime query embedding只服务合法Review Memory/Vector Search，不查询External Evidence index。未来启用必须发布新pool/API protocol version，并对全部initial seeds提供相同index、query normalization、ranking policy、capability与budget。

GPU6 Qwen3-Embedding-8B preformal按 `batch_size=8 → 4 → 1`测试，使用benchmark-independent short/typical/near-max queries，并覆盖formal scheduler中全部合法real-item occupancy、partial/full batch、padding数量与tensor positions、cold/warm状态及每项三次重复。同一canonical真实query的最终canonical little-endian IEEE-754 float32 vector bytes在所有这些条件下必须逐bytes完全一致；只选择最高满足该要求的batch，所有multiquery batch失败时formal固定1。允许采用冻结且完全可审计的deterministic padding/full-batch策略，但其batch assembly、padding payload、实现/version与行为必须进入Embedding Runtime Identity并通过同一byte-equality gate。Padding output本身不要求跨运行逐bytes一致，只要求每次满足冻结cardinality、tensor-position mapping、dtype、dimension与finite-value约束并确定性丢弃。任何padding数量、位置、occupancy或cache状态引起真实query vector bytes变化都会使该配置失败；`1e-6`最大绝对误差与frozen smoke index top-k/ranking一致性仅作诊断，不得替代byte equality。Selected batch、occupancy/padding policy、dtype、pooling、normalization、tokenizer/model revision与vector serializer进入Embedding Runtime Identity。

Embedding service使用独立queue与scheduler，不计入Qwen queue。Scheduler固定按`QUAL → CLAR → SIGN → ORIG → POL` role round-robin，role内按committed logical sequence FIFO，并依此顺序装配selected batch。未形成full batch时，仅在冻结`max_batch_wait`后允许partial dispatch。禁止按query length/content、cache hit/miss、Candidate identity、fitness或预计runtime重排。

Batch wait从第一个dispatch-eligible request进入当前空assembly buffer时开始，使用冻结monotonic clock implementation计时，至`QUERY_EMBEDDING_BATCH_DISPATCHED` durable commit结束。Clock unit、rounding、implementation及wait值进入Embedding Scheduler Runtime Identity。Scheduler restart时，从journal恢复出的pre-failure waiting requests视为wait已到期；service/runtime preflight通过后按role round-robin和role-local FIFO立即重新assembly/dispatch，不重新等待完整wait。保存enqueue/dispatch、batch membership、tensor position/order、occupancy、padding与Embedding Runtime Identity。实际enqueue/restart时序可改变occupancy，但Q232 byte-invariance必须使其不改变canonical vector bytes。

Batch-level schema/mapping错误、query identity mismatch或当前service epoch内两个transport attempts均失败，会在独立single-writer append-only Embedding Scheduler Journal中提交`EMBEDDING_INFRASTRUCTURE_FAILURE_COMMITTED`，暂停整个shared Embedding scheduler及所有依赖role并立即停止新dispatch。该event是唯一global cutoff：只有cutoff前已完成`QUERY_EMBEDDING_BATCH_RESULTS_COMMITTED`的整批records有效；cutoff前仅有raw/item projection/partial或完整validation/pre-commit materialization，以及cutoff后到达的responses都只作audit，不得继续生效或部分采用。各role journal只引用Scheduler Journal event SHA，不借此建立跨role Candidate/search全序。

`qembbatch_`只绑定pure-content projection：schema/version、Embedding Identity，以及按real tensor position排序且每项仅含position、qemb和完整canonical QueryEmbeddingRecord的items。Batch/request attempts、memqocc、Candidate/paper/role/Program、epoch、validation SHA及Journal/execution字段只进入self-contained event envelope并由event SHA/hash chain覆盖。一个validated batch恰好一个不可拆分event，禁止runtime拆分、外置vectors、compression或分批授权。Preformal物化最大合法event并冻结max bytes、manifest SHA及实测commit-latency threshold，覆盖serialize/parse、双hash、durable write与crash recovery；formal超限protocol/runtime fail。

相同qembbatch不允许省略独立event、复用旧execution chain或提前terminal request；projection引用当前execution确切event SHA。Canonical event/envelope禁止prior-identical lookup；commit后可生成独立non-authoritative `BatchContentDedupObservation`，它不进入chain/identity/recovery/workflow且缺失/失败无影响。Physical dedup不得破坏self-contained读取恢复。

Materialization固定primary加至多一次same-identity rebuild/commit retry，每次有immutable attempt identity与durable disposition。失败提交`EMBEDDING_BATCH_RESULTS_MATERIALIZATION_FAILED`独立global cutoff并停止新dispatch/results/projection；pre-cutoff committed results有效，raw/validated-only pending，post-cutoff response只作audit。第二次相同canonical error是Controller/Protocol validity failure；payload一致但I/O再失败是shared storage unavailable。Crash只认durable attempt events。可恢复I/O重过相同identity storage/materialization preflight并提交`EMBEDDING_BATCH_RESULTS_MATERIALIZATION_RECOVERED`后才恢复；protocol failure不提交recovered。Authoritative chain中已commit event非法属于Journal integrity failure，禁止修补、跳过或继续后续chain。

`matatt_` identity绑定validated batch event、qembbatch、PRIMARY/REBUILD、index 0/1、serializer/parser/controller及storage/durability identities，retry绑primary。Closed dispositions为COMMIT_SUCCEEDED/CANONICAL_VALIDATION_FAILED/STORAGE_IO_FAILED/ABANDONED，每attempt恰好一个；success必须引用已commit results event且不能独立授权qemb。Materializer recovery不改变service epoch；post-cutoff response永久audit-only但actual dispatch历史保留，恢复只能使用剩余transport slots，下一epoch仍消耗request allowance，第三epochowning-role fail。

Results event committed而matatt未terminal时只补匹配success，不重materialize/重写event；失败/abandoned terminal与committed event并存为Journal integrity failure。Failure ordering固定为failed terminal→cutoff→non-formal preflight→RECOVERED→retry；retry成功event→success，retry失败terminal→new cutoff。其他noncausal matatt保持paused并由唯一owner恢复同一identity，不消耗retry；仅role/formal终止才ABANDON。

Embedding Scheduler Journal以`EMBEDDING_SERVICE_EPOCH_COMMITTED`管理global epoch。Initial完整preflight后提交epoch 0/previous null；same-Runtime restart/preflight后才递增。Ordinal只由durable event连续消耗：preflight无event则恢复后重新preflight并复用next ordinal；event已commit而controller/scheduler crash则复用；service实际restart强制new preflight/epoch；temporary/log/PID均无权影响ordinal。任何dispatch引用current committed epoch。Request只在首次dispatch记录initial；下一epoch实际dispatch无论fault attribution均消耗recovery，需要第三epoch时所属role fail-fast。

Recovery重新assembly时，尚未terminal requests保持原role-local sequence和FIFO priority，由scheduler依据冻结role round-robin/FIFO、batch size、`max_batch_wait`及padding policy形成新batch。旧companions、tensor positions、occupancy或padding layout不再约束新执行；新batch attempt必须关联原失败batch provenance，且禁止为了复刻旧batch改变logical queue order。

Cross-role batch的terminal projections独立推进，无all-role barrier。Shared failure pause期间禁止新projection；恢复并提交有效epoch后，各role可分别补交合法pre-cutoff batch result的projection，不调用Embedding、不消耗recovery allowance、不等待其他role。Recovery后逐request检查epoch history；只有需要第三epoch的request所属role fail-fast，其他role继续，失败role以新role-run identity重跑且Final Suite等待五个winner。

Role-local projection canonical object至少绑定role-run、logical request、`qembbatch_`、authoritative results event SHA、实际`qemb_`与controller/schema identity；每个logical request恰好一个，匹配复用、缺失补交、冲突引用role-level integrity fail-fast。失败role须先禁止旧run新request、ABANDON全部未terminal requests、commit `ROLE_FORMAL_FAILED`并seal journal、验证scheduler drained，再提交`ROLE_RUN_SCHEDULER_ADMITTED`。Event绑定新run/role/first sequence/current cursor/Runtime Identity；cursor不reset/rollback，新run从下一次正常role轮转参与，不抢占assembled batch或插入committed ordering。Scheduler key绑定role-run identity+sequence；old/new run不同时active。

Cursor是固定role序上的`next_role_index`。Assembly从committed cursor环扫，empty role仅temporary skip；只有batch dispatch commit更新到最后real item role之后，padding/cache hit/non-enqueued不影响。Restart只恢复last committed cursor，Admission若cursor指向新role则可参与下一正常assembly。

该cursor更新在`QUERY_EMBEDDING_BATCH_DISPATCHED` commit时立即生效，之后的timeout、validation/infrastructure failure、materialization cutoff或service restart均不得回滚；unfinished requests保留role FIFO并从当前cursor重新assembly，只有dispatch未commit时才沿用原cursor。

旧winner混入新run Suite只在独立suite journal的PLAN→STARTED→COMPLETED→PASSED lifecycle完成时允许；FAILED与PASSED恰一且Suite引用PASSED event SHA。STARTED前未读取旧artifact的preflight问题可修复；STARTED后只允许same-plan/input/rules/identity的infra resume。Artifact不可验证、mismatch、tool identity mismatch、非法output或不可信comparison关闭保留路径，后续修复新identity且五role重跑；Compatibility lifecycle不进入role search/RNG/Evolution feedback。

Compatibility Plan在STARTED前只能读取既有authoritative artifact catalog metadata，不得打开payload、rehash、compare、diff或补建catalog；catalog不足即不能保留旧winner。每个comparison item固定primary加至多一次同input/tool identity的read/hash/parse retry。Resume复用durable items并只补缺项；COMPLETED后只补唯一decision，PASSED/FAILED冲突或与COMPLETED不一致为Suite Journal integrity failure。

Materialization cutoff采用singleton paired state machine：最多一个open failure，RECOVERED必须引用该failure及其preflight，open期间不嵌套cutoff。每个Results event唯一绑定一个当时尚未terminal且未消费的originating `matatt_`。Paused matatt以独立execution identity、monotonic resume ordinal及DISPATCHED/OUTPUT_COMMITTED/ABANDONED生命周期维持single owner；restart先终结旧owner再恢复。Post-commit dedup observation使用稳定`dedupobs_`内容identity，但永远audit-only。

Q290–Q298进一步收紧：DedupObservation prefix固定在current Results event；Results event还绑定唯一OUTPUT_COMMITTED execution及output SHA。Execution恰一OUTPUT/ABANDON terminal，cutoff后late output永久audit-only，cutoff前output可在RECOVERED后直接复用。成功role/Suite在seal前提交authoritative artifact catalog；Compatibility以`proposed_suite_composition_id → compatplan_ → ordered compitem_`避免身份循环和hash自引用。Semantic mismatch不得early-stop，完整COMPLETED后才决策；decision前partial semantic results对操作者不可见。证明仅限确定性双实现replay、request-byte equality后共享old raw response的下游replay，以及Journal/RNG simulation。

Q299–Q392进一步冻结decision/output与failure归责。Readiness与Export Access各由独立single-writer infrastructure journal管理连续epoch；Quiescence使用pre-head、唯一commit slot和closed Requirement Registry。Export拆分candidate、Access Validation与promotion预算；Final Suite使用稳定九slot gate identities及机械Audit dependency closure。可信Replay Audit FAIL仍Export，未知Readiness不得越过gate。Target Controller implementation component须精确相等。

Deterministic padding只使用protocol-generated `embedding_padding_item_id`和冻结payload/position/policy，不生成Memory/query/item-attempt/budget/ledger/cache/qemb身份。Padding output只参与cardinality/position/dtype/dimension/finite-value validation并确定性丢弃，不要求跨运行byte identity；真实query bytes则必须对全部合法padding数量/位置、occupancy及cold/warm状态保持完全一致。Payload、数量、插入位置、tensor order与discard rule进入Embedding Runtime Identity。

## 7. Required Gates

Formal前至少验证：

- mixed Reviewer/Evaluator/Reflector/compile-fix/registered LLM Summarizer workload的1/2/4 capacity matrix；
- Evaluator cross-capacity exact decisions与其他temperature=0 tasks的within-capacity stability，包括Summarizer canonical SHA/finish reason/disposition；
- global sequence budget不能按role/task重复分配；
- fixed role round-robin、role FIFO、completion-order permutation与journal independence；
- `REQUEST_ENQUEUED`、logical request identity/sequence/order-key与arrival-order independence；
- paper DAG dependency、Applicability/Reviewer并行与Scoring barrier；
- RetrievalResultManifest先于Summarizer enqueue的durable barrier及resume复用；
- Memory API intent/response/ledger-budget三阶段visibility barrier及crash replay；
- CanonicalReviewerMemoryContext payload/envelope及visible projection先于Reviewer enqueue的commit barrier、双SHA binding与prompt-only-visible gate；
- 完整visible projection token gate、Direct/Structured Candidate overflow、Summary worst-case preformal guarantee及禁止runtime truncation/fallback；
- CanonicalSummarizerInputRecord `suminput_`/SummarizerVisibleInputProjection commit barrier、exact-query eligibility、model-visible field denial与独立Summary-input token gate；
- Embedding observed-query SHA handshake、one-call/one-query cardinality、untrusted framing与Candidate message-boundary denial；
- QueryEmbeddingRecord paper-local cache hit/miss neutrality、cross-scope denial与resume identity validation；
- Query Embedding dispatch/raw-response/item/VALIDATED/self-contained-BATCH_RESULTS/role-terminal commit order、embedded records/vector bytes、orphan non-promotion与workflow visibility；
- RFC4648 padded Base64/no-compression、tensor-position ordering、padding exclusion与strict decode validation；
- `qembbatch_` pure item projection/execution-binding exclusion versus Scheduler Journal commit authority、one-batch/one-unsplittable-event与orphan-content denial；
- same-qembbatch independent execution commits、exact-event projection、no formal dedup及physical-dedup invisibility；
- prior-identical canonical-event denial、audit-only observation neutrality与self-contained recovery independence；
- max event bytes/test manifest、durable/crash/latency gate、bounded materialization attempts、independent cutoff/recovered barrier及I/O/protocol/integrity ownership；
- `matatt_` identity/closed disposition、success-after-event、materializer no-epoch-change及post-cutoff actual-dispatch/slot accounting；
- committed-event missing-success recovery、terminal conflict rejection、cutoff/recovered/retry strict ordering及noncausal paused single-owner recovery；
- Query Embedding logical/transport-attempt identities、committed global epoch/first-dispatch initial epoch、actual-dispatch/no-fault-exemption accounting、每epoch两次attempt、单一request recovery epoch/最多四次invocation与third-epoch role failure；
- batch-attempt ordered item vector、raw-batch-first item projection、timeout fan-out与no response race；
- all-or-none cardinality/mapping/real-item/padding validation、VALIDATED/results双barrier、atomic ordered qemb vector与partial qemb visibility denial；
- one-memqocc/one-request、cache-hit no-attempt resolution与qemb content/execution separation；
- role-local sequence/durable enqueue、cache no-queue、retry original FIFO、recovery reassembly/no old-batch constraint、old/new batch linkage与closed logical terminal set；
- 独立single-writer Embedding Scheduler Journal、shared-service global pause、唯一failure cutoff、role-journal SHA引用与same-runtime bounded recovery；
- `EMBEDDING_SERVICE_EPOCH_COMMITTED` epoch0/restart event、preflight/provenance binding及dispatch-before-epoch denial；
- epoch ordinal durable-only/no-gap crash recovery、cross-role independent projection及owning-role-only third-epoch failure/new-run suite barrier；
- role-local projection deterministic/idempotent identity、conflicting reference rejection、old-run seal/scheduler drain及failed-role current-epoch rejoin preconditions；
- `ROLE_RUN_SCHEDULER_ADMITTED` current-cursor/next-turn/no-preemption semantics及run-keyed FIFO；
- next-role-index round-robin mapping、dispatch-only cursor update、last-real-item/padding/cache rules与restart restoration；
- pre-diff frozen one-shot read-only compatibility replay、no-model/no-C-Human-CoCo、failure-no-adaptation、mixed-implementation Suite provenance与Protocol-affecting five-role rerun；
- suite Compatibility lifecycle journal、PASSED/FAILED exact-one、STARTED boundary、same-plan infrastructure resume与unverifiable/tool-invalid closure；
- byte-length untrusted-data parser/materializer、system/data physical role separation及delimiter/control-character boundary vectors；
- Summarizer exact system/user/generation-prompt layout、forbidden message roles/history/prefill与rendered reconstruction；
- strict local/backend prompt-token integer equality、five-role cold/warm repeated vectors及双gate不混用；
- paper-local Review Memory/Summarizer session销毁、cross-paper state denial与B-paper no-write-back；
- `active_paper_window=C/2C`三次重复、median throughput与10%选择门槛；
- retry/cancel在ordinary child、mandatory seed、finalist与role failure四种scope下的后果；
- CoCo pre-authorization deployment/access denial；
- frozen document embedding与Candidate-private external-evidence state isolation；
- shared-GPU embedding/Qwen联合capacity gate（如适用）。
- Embedding batch 8→4→1、全部合法real-item occupancy/partial-full/padding count-position/cold-warm/三次重复的真实query canonical vector byte equality、padding output structural-only validation，以及`1e-6`/top-k仅诊断。
- failure cutoff前atomic batch-results commit有效条件、validated-but-uncommitted整批无效及recovery-epoch exhaustion role failure。
- Embedding independent scheduler role-round-robin/FIFO、partial-batch max-wait/clock semantics、forbidden reorder与batch/tensor provenance。
- batch-wait first-eligible/dispatch-commit boundaries、frozen monotonic unit/rounding与restart wait-expired immediate assembly。

任一gate未通过，不得启动formal Evolution。
