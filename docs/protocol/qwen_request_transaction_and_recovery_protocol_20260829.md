# Qwen Request Transaction and Recovery Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen semantic contract（2026-08-29），Q166–Q173及Q186–Q187已接受。本文冻结request-level events、raw-response-first commit、late-response arbitration、orthogonal semantic/transport retry identity、backend restart、request-class timeout、Qwen queue backpressure、endpoint isolation及LLM Summarizer request/result语义；concrete schemas、timeout table与gate vectors尚待物化。

## 1. Request-Level Lifecycle

每个Qwen logical request使用以下immutable events：

- `REQUEST_ENQUEUED`；
- `REQUEST_DISPATCHED`；
- `REQUEST_RESPONSE_COMMITTED`；
- `REQUEST_CANONICAL_RESULT_COMMITTED`；
- `REQUEST_FAILED`。

Events属于authoritative journal体系并使用domain-separated canonical JSON、previous-event SHA、durable commit与严格state transition。普通stdout、HTTP client日志、temporary或orphan files不构成formal request state。

## 2. Raw-Response-First Commit

Provider/model response返回后，worker必须先durable commit immutable raw response及其request-attempt/provenance，再允许parser/controller产生canonical result。恢复规则固定为：

- `REQUEST_RESPONSE_COMMITTED`存在而canonical result未提交：复用该raw response重新解析，不重新调用模型；
- 只有 `REQUEST_DISPATCHED`、没有committed response：以相同logical request/generation identity重新发送；
- 只有日志、partial或orphan response：仅保留审计，不复用，按未提交response处理；
- 已提交canonical result不得因后续raw output、retry或restart重新选择。

## 3. Logical Request and Request-Attempt Identity

Q29 identical retry继续属于同一个 `logical_request_id`与generation identity。每次实际model invocation必须具有独立immutable request-attempt identity。Semantic retry与transport retry正交表示：

- `semantic_attempt_kind ∈ {PRIMARY, IDENTICAL_SCHEMA_RETRY}`；
- `semantic_retry_index`；
- `transport_attempt_index = 0,1,...`。

因此一次schema retry自身发生transport retry时可表达为 `IDENTICAL_SCHEMA_RETRY + transport_attempt_index=1`，不与primary transport retry混淆。

```text
request_attempt_id = "reqatt_" + SHA256(canonical identity object)
```

Identity至少绑定 `identity_type`、schema version、logical request ID、semantic attempt kind/index、transport attempt index与Runtime Identity SHA。Provider request ID、PID、port、timestamps只作execution provenance。

Retry不得改变Candidate input、request bytes、prompt/schema、temperature、seed、max tokens、stop、reasoning config、scheduler policy或其他semantic request fields。Request-attempt provenance不改变logical generation identity。

注册Summarizer request必须同时绑定已提交`suminput_` CanonicalSummarizerInputRecord identity与`summarizer_visible_projection_sha256`；provider prompt只能由verified SummarizerVisibleInputProjection bytes构造，不得注入authoritative envelope/audit字段。旧`CanonicalSummarizerInputProjection` machine type不得用于新formal。所有使用context/token budget进行正式决策的冻结request vectors，local完整rendered request token count必须与provider/backend `prompt_tokens`严格整数相等；usage缺失或任一差异均preformal gate failure，禁止容差、平均或safety-reserve抵消。

Summarizer request messages固定为一个immutable system message、一个只包含length-prefixed SummarizerVisibleInputProjection的user message及chat template generation prompt。Developer/tool、assistant history/prefill、额外user或Candidate-controlled system/message boundary均schema failure，不得dispatch。Request identity绑定完整messages JSON、system prompt SHA、chat-template SHA、rendered prompt bytes和generation config。

## 4. Attempt Closure and Late Responses

Transport timeout一旦durable commit `REQUEST_ATTEMPT_TIMED_OUT`，对应request-attempt永久关闭。Timeout commit前已经durable commit的response仍可使用；commit后才到达的late response只保存raw execution audit，不得canonicalize。下一transport attempt或Q29 retry dispatch后，旧attempt不得与新attempt竞争canonical result。

Evaluator结构非法必须先durable commit `REQUEST_ATTEMPT_INVALID`，才允许启动identical schema retry。Canonical result来自事务状态机中第一个合法durable commit且仍处于有效状态的response，不以wall-clock网络包到达顺序决定。

## 5. Backend Infrastructure Restart

Evolution阶段允许同Runtime Identity的backend restart：

1. 暂停全部受影响role；
2. 提交 `SERVICE_FAILURE_DETECTED`；
3. 终止故障服务并清理KV/prefix/request runtime cache；
4. 按完全相同的frozen Runtime manifest重启；
5. 重新执行model/resource/exclusive preflight；
6. 提交 `SERVICE_RESTARTED`；
7. 复用committed responses/canonical records，只重发dispatch已commit而response未commit的requests。

该流程不消耗global evolution attempt。若model artifact、launch args、capacity、context、scheduler或任何Runtime Identity字段变化，则禁止恢复旧formal state；相关role按冻结规则fail-fast或使用新role-run identity重启。

## 6. Request-Class Timeouts

每个request class依据frozen mixed-capacity gate计算：

```text
timeout_seconds = max(600, ceil_to_60_seconds(2 * max_observed_gate_latency))
```

上限为3600秒；推导值超过3600说明当前capacity/runtime gate失败。另冻结：

- connect timeout：30秒；
- queue/pool acquire timeout：30秒；
- 同一transport request最多2次infrastructure transport attempts；
- 两次均timeout：暂停并归为infrastructure failure，不消耗Evolution attempt；
- HTTP/provider/transport timeout不得归因于Candidate；
- Candidate sandbox自身read/write/runtime timeout仍是Candidate-originated failure；
- `finish_reason=length`按对应Reviewer/Evaluator/Reflector协议处理，不伪装为transport timeout。
- 对注册LLM Summarizer，非空`finish_reason=stop`或`length`均为合法canonical result；`length`不触发budget扩容、semantic retry或fallback。空content/provider/backend故障归为infrastructure failure。

Concrete request-class timeout table、gate latency evidence与manifest SHA必须在formal前冻结。

Scheduler backpressure与transport timeout严格分离：controller等待queue capacity、request在 `REQUEST_ENQUEUED` 后等待dispatch，都不启动30秒timeout。30秒connection/pool-acquire timeout只在 `REQUEST_DISPATCHED` 后HTTP client实际获取连接/发送请求时开始。Enqueue-to-dispatch latency仅用于audit；可产生non-terminal liveness warning，但不得处罚、重排、扩容或改变formal semantics。

## 7. Queue Backpressure

Qwen scheduler冻结：

```text
max_queued_qwen_requests = 2W + 5
```

其中W是冻结active-paper window。达到上限后role controller阻塞新enqueue，不丢弃、不重排、不扩大W。Retry只在原request释放execution slot后按冻结logical order重新进入scheduler，不得突破queue上限。

## 8. Endpoint and Capability Isolation

只有global Qwen scheduler持有Qwen endpoint credentials/capability。Candidate sandbox、MemoryProgram与Frozen Evidence API不得直接访问Qwen endpoint。Reviewer、Evaluator、Reflector、compile-fix与注册的post-retrieval LLM Summarizer请求全部必须经scheduler提交；Summarizer只能通过manifest声明的受限capability消费当前paper已冻结的retrieval结果，不得把该通道用于query construction、search、rerank或额外Memory读取。

Embedding service与Frozen Evidence API使用独立capability和queue，不计入Qwen request queue。Network/process gate必须验证Candidate runtime无法绕过scheduler访问localhost、service port或其他生成式模型endpoint。

## 9. LLM Summarizer Transaction Rules

Summarizer request只能在对应immutable `RetrievalResultManifest` durable commit后enqueue；每个`Candidate × paper × role`最多一个Summary logical request。Logical request identity必须绑定`retrieval_result_sha256`、Program/Summarizer capability、frozen prompt/config与request input/output budget。Request input只能由该manifest、冻结role framing及manifest中已经使用的query确定性装配。

Dispatch前使用冻结Qwen tokenizer对完整manifest-derived request framing计算input tokens。超过冻结input budget时提交`SUMMARY_INPUT_BUDGET_EXHAUSTED` disposition，不创建model dispatch/request-attempt；不得删减manifest、runtime truncation、扩大budget、再次调用Summary或fallback原始Memory。

Summarizer没有semantic/schema retry；每个logical request只使用`semantic_attempt_kind=PRIMARY`，transport attempts仍按本协议计数和恢复。Provider返回后先commit raw response；非空UTF-8 content bytes直接成为canonical summary payload/SHA，不要求JSON。`finish_reason=stop`和非空`length`均合法，且不得触发额外summary invocation。

Empty content、provider/backend failure或两次transport attempts均失败按infrastructure failure暂停同一logical task；禁止退回unsummarized Memory。越权输入、manifest mutation、未召回Memory访问或非法Qwen调用属于Candidate-originated protocol failure。

## 10. Resource Contamination Window

`RESOURCE_EXCLUSIVITY_VIOLATION`的observable contamination window默认从最后一次已提交clean watchdog observation开始，到violation detection为止；若有可信、更早的违规process start provenance，则window起点前移。

- 在window开始前已经durable commit `ROLE_SEARCH_COMPLETED`的role保持有效；
- 其余尚未完成且共享受影响资源窗口的role全部fail-fast；
- 无法进一步确定process start时，按last-known-clean边界保守处理全部未完成role；
- 受影响role不得从旧journal继续，必须以新role-run identity从初始化重跑；
- 已证明在window前完成的role winner可与重跑winner组成Final Suite，但suite manifest必须记录每个role-run identity与resource provenance；
- 上述组合只允许在C/Human/Official CoCo尚未访问时进行。

## 11. Required Gates

Formal前至少验证：

- request event state machine与invalid transition rejection；
- raw-response-first crash points、parse-only recovery与orphan rejection；
- logical request versus request-attempt/retry identities；
- timeout/invalid commit前后late-response race与canonical winner vectors；
- scheduler queue wait versus post-dispatch connection/pool timeout；
- service failure/restart、cache clearing与Runtime Identity mismatch rejection；
- timeout derivation、transport/sandbox/length ownership matrix；
- `2W+5` backpressure、retry ordering与no-drop/no-reorder；
- Candidate/sandbox direct-endpoint denial；
- Summarizer manifest-before-enqueue、raw-response-first、primary-only semantic attempt、`stop/length` canonicalization、empty/infrastructure handling与no-fallback vectors；
- zero/one Summary logical request、input-token preflight、no-dispatch exhaustion与no-second-call vectors；
- contamination-window role preservation/invalidation与new-run restart vectors。

任一gate未通过，不得启动formal Evolution。
