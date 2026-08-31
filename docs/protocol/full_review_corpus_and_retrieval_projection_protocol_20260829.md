# Full Review Corpus and Retrieval Projection Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen through Q392（2026-08-30）。本文冻结A区625条no-memory Full Review Records的独立formal build stage、strict record schema、2,875个primary subdimension projections、超长section的确定性fragmentation、三级内容identity、五个role-specific deterministic exact-flat vector indices、统一只读Review Memory API及atomic预算、transactional RetrievedMemoryLedger、immutable RetrievalResultManifest、strict CanonicalReviewerMemoryContext，以及“retrieval无LLM、显式Summarizer仅作post-retrieval condensation”的边界。Query Embedding另冻结occurrence-level logical request、batch/item raw-response-first transport transaction、all-or-none validation、self-contained batch-level atomic qemb生效、non-authoritative dedup observation、pure-content batch/independent execution commit、bounded materialization-attempt state machine/cutoff-recovery ordering/paused-owner recovery、actual-dispatch accounting、role-local idempotent workflow visibility、content-level `qemb_`、durable enqueue、独立Scheduler Journal、committed global service epoch/request-relative bounded recovery、deterministic cursor/recovery reassembly、sealed-role scheduler admission、suite-level one-shot compatibility lifecycle、role-local third-epoch failure scope、non-Candidate padding、closed terminal disposition、paper-terminal cache关闭和canonical vector byte-stability；三个v2 seed与read-mode完整contract见`formal_memory_seed_and_read_mode_protocol_20260829.md`。

## 1. Corpus Cardinality and Scope

Frozen A manifest包含125篇papers。每篇分别生成 `QUAL / CLAR / SIGN / ORIG / POL` 五个role-specific no-memory reviews，共625条canonical Full Review Records。

五个role各自形成独立Full Review Corpus；不得把五份review合并为一个record，也不得用某role review填充另一role corpus。Corpus build是Evolution之前的独立formal stage，625条全部完成前不得启动Retrieval Projection、embedding index或Evolution。

## 2. Strict No-Memory and Evidence Boundary

每条Corpus Review的输入仅允许：

- target paper；
- deterministic STRUCTURAL context；
- frozen role-specific Reviewer prompt/config；
- frozen Reviewer/Qwen semantic/runtime identities。

Corpus build严格禁止：

- 读取任何已有Review Memory或Full Review Corpus；
- Frozen External Evidence API或其派生state；
- Human/OpenReview/CoCo assets；
- Evaluator、Reflector、KnowledgeItem LLM或Memory Query LLM；
- 其他paper的Review或Candidate state。

禁止External Evidence是因为Corpus Review随后成为persistent Review Memory；将external-evidence-derived content写入该Memory会违反paper-level evidence state isolation。

## 3. Parallel Build Scheduler

Corpus build复用冻结的单Qwen3.8-27B TP4、capacity C、global scheduler与request transaction protocol：

- role round-robin固定为 `QUAL → CLAR → SIGN → ORIG → POL`；
- 每role内部paper order由A manifest冻结；
- 五role逻辑并行，physical Qwen in-flight不超过C；
- worker completion order不得改变record identity、role/paper order或canonicalization；
- Corpus build使用独立journal/run identity，不与Evolution role journals混合。

## 4. Canonical Record and Failure Rule

每个 `A paper × role`最终必须有且只有一个canonical Full Review Record。保存所有raw request attempts；第一个合法response成为canonical。最多一次完全相同semantic/generation identity的fresh-session schema/protocol retry；两次均失败则整个Corpus build fail-fast。

禁止跳过paper、缩小Corpus、用其他paper/role替换、结果后重写prompt或生成多个corpus版本择优。多corpus replicate只能作为另行注册的robustness实验。

### 4.1 Strict Canonical Full Review Payload

Canonical parsed payload采用strict closed schema，只允许：

```json
{
  "role": "QUAL | CLAR | SIGN | ORIG | POL",
  "paper_id": "<frozen A-manifest paper ID>",
  "subdimension_sections": [
    {
      "subdimension_id": "<current-role closed-registry ID>",
      "review_text": "<complete section review>",
      "paper_anchor_ids": ["<sorted unique paper anchor ID>"]
    }
  ],
  "overall_assessment": "<optional role-level assessment>"
}
```

`overall_assessment`是唯一可选字段；存在时必须满足冻结schema的string约束。`subdimension_sections`必须完整覆盖当前role的固定subdimension vector，并严格按照role registry canonical order出现：

- `QUAL`：5 sections；
- `CLAR`：3 sections；
- `SIGN`：4 sections；
- `ORIG`：6 sections；
- `POL`：5 sections。

每个section都必须存在，即使Reviewer认为该paper在该subdimension中没有明显问题、内容很短或正式评价中可能为`N_A`。Corpus section不是formal applicability decision。Payload不得包含Candidate自评分、Evaluator score、formal applicability、fitness或Evaluator reasoning。

缺失section、重复section、额外section、unknown subdimension、顺序错误、重复/未排序anchor ID或任何额外字段均为非法输出。Parser不得repair、补项、重排或coerce；只允许上述一次identical retry，仍非法则Corpus build fail-fast。

### 4.2 Authoritative Record Envelope

Canonical Full Review Record的审计envelope必须同时绑定并保存：

- raw provider response bytes及其SHA256；
- canonical parsed payload及其SHA256；
- target paper identity与paper content SHA；
- deterministic STRUCTURAL context identity/SHA；
- role-specific Reviewer/model artifact与runtime identities；
- role prompt/config/schema/parser identities及SHAs；
- logical request、request-attempt与generation identities；
- 全部raw attempts、finish reason、token usage和transaction provenance。

Raw bytes是provider响应的审计事实，canonical payload是Projection materialization的唯一文本来源。二者不得相互替代。

### 4.3 Three-Level Content Identity

Full Review、Primary Projection与Projection Fragment分别使用domain-separated canonical JSON identity与完整小写SHA256：

```text
full_review_record_id = "fullrev_" + SHA256(full_review_identity_object)
primary_projection_id = "proj_" + SHA256(primary_projection_identity_object)
projection_fragment_id = "frag_" + SHA256(projection_fragment_identity_object)
```

Identity object都必须显式包含`identity_type`与schema version，并至少绑定：

- `fullrev_`：Corpus/materialization identity、role、paper identity、canonical Full Review payload SHA与record schema identity；
- `proj_`：Full Review Record ID、role、subdimension ID、canonical section object/text SHA、Projection materialization identity；
- `frag_`：Primary Projection ID、fragment ordinal、精确字符/token offsets、fragment text SHA、splitter/tokenizer/materialization identity。

Runtime地址、base URL、port、PID、hostname、GPU ordinal、mtime和timestamp只作execution provenance，不进入内容identity。相同冻结资产重建必须得到完全相同的三级ID；collision、缺失、prefix/schema不匹配或非法重复均使materialization fail-fast。

## 5. Corpus Commit Barrier

只有以下条件全部满足，才允许durable commit Corpus complete：

- 625条canonical records齐全；
- A paper/role coverage与frozen manifest完全一致；
- raw-response-first request transactions完整；
- Reviewer/prompt/input/runtime identities一致；
- canonical payload与record SHAs完整；
- 无Memory、External Evidence、Evaluator、Reflector、CoCo/Human访问；
- role/paper order与journal hash chain完整。

`FULL_REVIEW_CORPUS_COMMITTED`之后才能执行无LLM deterministic Retrieval Projection、chunk representation与embedding index materialization。

## 6. Retrieval Projection and Fragmentation

Full Review Record是权威原文，永不被chunk替代。每个canonical `subdimension_section`确定性产生一个primary subdimension projection，因此625条Full Review Records固定产生：

```text
125 papers × (5 QUAL + 3 CLAR + 4 SIGN + 6 ORIG + 5 POL) = 2,875 primary projections
```

任何primary projection不得因“无明显问题”、内容短或被判断为低价值而删除。`overall_assessment`不生成projection或vector entry；Full Review Record整体也不另行embedding，避免同一Review通过full-text、overall与section重复表示而获得额外retrieval probability mass。

### 6.1 Secondary Fragmentation

每个primary projection先用冻结的Qwen3-Embedding tokenizer计算token数：

- `<= 2048` tokens：保持完整，生成一个fragment；
- `> 2048` tokens：按Markdown paragraph boundary顺序packing；
- 单个paragraph仍超限：按冻结sentence boundary规则顺序切分；
- 单个sentence仍超限：最后使用冻结token-window规则切分；
- overlap固定为`0`。

Projection pipeline不得使用LLM、摘要、改写、semantic segmentation、semantic dedup、rerank或结果依赖删除。Paragraph/sentence splitter实现、版本、Unicode/whitespace normalization、tokenizer identity与token-window规则全部进入Projection Identity。

每个fragment必须保存：

- role与subdimension ID；
- fragment ordinal；
- parent primary projection identity；
- canonical Full Review Record ID/SHA；
- 相对于section `review_text`的精确字符起止offset；
- 相对于section `review_text`的精确token起止offset；
- fragment text及SHA；
- projection/splitter/tokenizer identities。

Primary Projection保存完整、排序去重的`section_paper_anchor_ids`。Fragment仅以`inherited_section_anchor_ids`继承所属section的整体evidence scope；该字段不得解释为fragment-level grounding。只有anchor ID按照冻结语法在fragment文本中显式出现时，确定性parser才生成排序去重的`explicit_fragment_anchor_ids`，且必须是`section_paper_anchor_ids`的子集。禁止使用LLM、embedding、semantic matching或parametric knowledge推断fragment-local grounding。

2,875表示primary projection数量；超长section切分后，实际vector entries数量可以大于2,875。每个生成的fragment在其role index中只允许出现一次。

## 7. Five Role-Specific Vector Indices

建立且只建立五个role-specific indices：`QUAL / CLAR / SIGN / ORIG / POL`。每个index包含当前role所有primary projections派生的fragments；不得建立23个独立index，也不得把Full Review Record或`overall_assessment`作为额外vector entry。

`subdimension_id`只作为fragment metadata和optional search filter。Reviewer/Candidate只能访问当前role index；cross-role retrieval是Candidate-originated protocol failure。Optional filter必须是当前role closed registry中的合法subdimension ID，所有query、filter与result均进入audit。

V1 search固定使用normalized float32 document/query embeddings上的deterministic exact flat inner-product：

- 不使用ANN、rerank、多路召回或semantic post-filter；
- 冻结CPU search implementation/version、index engine/version、维度累加顺序与score serializer；
- 排序固定为 `similarity descending, projection_fragment_id ascending`；
- 保存query vector canonical bytes/SHA、每个candidate fragment的canonical similarity表示、完整pre-top-k排序及最终top-k；
- 相似度精确同分只使用完整`projection_fragment_id ascending`决胜。

Canonical machine path固定为：Embedding输出先canonicalize为little-endian IEEE-754 float32 bytes；norm与dot-product按照冻结维度顺序使用binary64 reference accumulation；normalized vector再次canonicalize为little-endian float32 bytes；ranking直接使用该冻结binary64 similarity。每个score同时保存`float.hex()`与canonical serializer bytes，不做Decimal量化或容差合并。

Reference accumulation实现必须单线程、顺序固定且完全冻结，不得调用可能因thread count、BLAS、SIMD reduction、hardware dispatch或adaptive kernel改变累加顺序的实现。CPU/reference implementation、language/runtime、version与test-vector SHA进入Review Memory Index Identity。只有binary64 similarity bit-exact相等时才进入fragment ID tie-break。

Index materialization必须绑定Full Review Corpus manifest、三级Projection Identity schema、Embedding Semantic/Runtime Identity、ordered fragment manifest、canonical normalized vector bytes/SHA、exact-flat implementation identity与index SHA。相同输入重建必须产生相同ordered fragment manifest、vector identities与search results。

## 8. Unified Read-Only Review Memory API

三个mandatory initial seeds及其所有descendants获得完全相同的只读API capability与冻结budget：

```text
vector_search(role, query, optional_subdimension_filter, top_k)
get_projection(projection_id)
get_full_review_record(full_review_record_id)
```

`vector_search`结果至少返回projection/fragment text、similarity、subdimension ID、Full Review Record ID与offset。Candidate可在预算内只使用返回的section fragment，也可通过record ID hydrate权威完整Full Review Record。Projection delivery与full-record hydration均按冻结规则计入call/record/token budget；不得通过重复读取规避计费。

每个`Candidate × paper × role`统一冻结以下预算维度：

- `max_memory_api_calls`；
- `max_delivered_fragments`；
- `max_full_record_hydrations`；
- `max_delivered_tokens`；
- `max_top_k`。
- `max_memory_query_tokens_per_call`。

具体整数必须在Corpus/Projection materialization与冻结tokenizer statistics完成后、查看Candidate结果前预注册。所有initial seeds及descendants使用完全相同的API capability、计数规则与budget。重复读取同一fragment或record重复计费。Vector query仅接受合法非空UTF-8并保持bytes不变，不执行任何Unicode/line-ending/whitespace/case/semantic normalization；raw与canonical query bytes合法时必须相同。API执行前以Embedding tokenizer验证`max_memory_query_tokens_per_call`，超限或非法UTF-8/empty均Candidate invalid request且不执行Embedding/index/delivery。同一canonical bytes另以Summarizer tokenizer计数供累计Summary gate；audit保存raw/canonical bytes/SHA、两套token IDs/count与identities。Embedding request及eligible Summary query必须使用同一exact bytes。

Canonical bytes是逻辑query string的strict UTF-8 encoding，不是JSON wire bytes；escaping/order/transport只作provenance。Embedding service对decoded query重新strict encode并返回/记录`observed_query_sha256`，必须与Memory API committed query SHA相等，否则infrastructure/protocol failure且不得使用vector result、提交budget transition或delivery。Summarizer只复用committed canonical bytes，不从transport logs重建。

Query content identity使用`memqry_ + full domain-separated SHA256`绑定schema、canonical bytes与UTF-8 byte length；调用级`memqocc_ + full domain-separated SHA256`另绑定memqry、Memory API call、Candidate/paper/role/Program及call ordinal。相同bytes共享memqry但独立calls保持不同memqocc。Authoritative records保存两者，visible projection只显示ordinal与bytes。

Canonical query vector保存为`qemb_ + full domain-separated SHA256` QueryEmbeddingRecord，绑定memqry、Embedding Semantic/Runtime Identity、tokenizer/pooling/normalization/dtype/dimension、little-endian float32 vector bytes/SHA、observed query SHA及schema/serializer。`qemb_`是content identity，不绑定paper、Candidate、role或Program；不同paper独立执行可以产生相同qemb，但必须各自具有合法paper-local execution chain，相同ID不得解释为cross-paper cache reuse。每个memqocc通过其一一对应logical request引用实际qemb。Immutable cache只在同一Candidate×paper×role×Program及完全相同Embedding identities内复用，resume亦同；禁止跨paper/Candidate/role。Cache hit不改变call/budget/ledger/result/visible semantics，仅作不可见execution provenance且不影响scheduler或fitness。

一次实际Embedding HTTP/model invocation使用独立`query_embedding_batch_attempt_id`，绑定有序item `query_embedding_request_attempt_id` vector、tensor positions、occupancy、padding与Embedding Runtime Identity。`QUERY_EMBEDDING_BATCH_DISPATCHED`后，service完整raw response、provider provenance和batch metadata必须先以batch级record durable commit；随后按冻结schema/tensor positions确定性拆分全部item-level records。统一验证batch与全部items/padding；`QUERY_EMBEDDING_BATCH_VALIDATED`后才可确定性生成全部real-item QueryEmbeddingRecords与qemb identities，但仍不可正式使用。Embedding Scheduler Journal随后一次性提交self-contained `QUERY_EMBEDDING_BATCH_RESULTS_COMMITTED`，canonical payload直接包含完整有序request-attempt IDs、QueryEmbeddingRecord objects、canonical vector bytes与qemb identities，不引用外部未授权record；该event使整批结果同时生效。Role-local `QUERY_EMBEDDING_RECORD_COMMITTED`仅是引用该event的可重建terminal projection，也是当前workflow使用qemb前的必要visibility barrier。任一validation非法或batch-results event未commit都使整批无正式qemb。VALIDATED与BATCH_RESULTS之间的temporary/per-item materialization只作audit；崩溃后从committed raw response及validation record重新构造完整payload，不promote orphan。仅dispatch无raw response时关闭旧attempt并按recovery scheduler重组执行；late response仅作raw audit。

Canonical vector bytes使用RFC 4648 standard padded Base64：保留`=`，禁止whitespace、URL-safe alphabet或其他variant；v1不压缩。每个record保存byte length与SHA256；real items按tensor position升序，padding不进入payload。Parser重新验证Base64、length、little-endian float32、dimension、SHA及finite values，任一失败使整批results非法。

Batch Results纯内容身份使用`query_embedding_batch_results_id = "qembbatch_" + full domain-separated SHA256(canonical content projection)`。Projection绑定schema/version、Embedding Identity，并按real tensor position排序；每个item仅含`real_tensor_position`、`qemb_`与完整canonical QueryEmbeddingRecord。Batch/request attempts、memqocc、Candidate/paper/role/Program、service epoch、validation SHA及全部Journal/execution字段均排除。Self-contained results event envelope仍保存这些execution bindings并由event SHA/hash chain覆盖；相同内容可以共享qembbatch值，但每次执行仍须独立合法commit chain，孤立artifact无formal authority。

相同qembbatch不触发formal dedup：每次独立execution必须提交自己的完整self-contained results event并由本次logical requests引用确切event SHA。Canonical event/envelope禁止prior-identical结果；commit后可生成独立audit-only `BatchContentDedupObservation`，它不进入chain/identity/workflow/recovery且缺失/失败无影响。Physical dedup不可见，self-contained event读取恢复不得依赖observation或外部metadata。

一个validated batch恰好对应一个不可拆分的self-contained results event；formal禁止拆event、外置vector、compression或分批授权。Preformal使用selected batch size、dimension、schema、Base64 expansion和serializer物化最大合法event，重复执行serialize/parse、content/event hash、durable atomic write与crash recovery，冻结最大canonical event bytes、测试manifest SHA及实测后预注册的commit-latency threshold。Formal超限为protocol/runtime failure，不切换representation。

Batch-level schema/mapping错误、query identity mismatch或epoch内两个attempts均失败暂停整个共享Embedding scheduler及全部依赖role，并停止新dispatch。独立Scheduler Journal记录enqueue、batch、self-contained results、failure/restart/preflight与`EMBEDDING_SERVICE_EPOCH_COMMITTED`。Initial preflight后提交epoch 0/previous null；same-Runtime restart/preflight后才递增。Ordinal只由durable event连续消耗：preflight无event时恢复后重新preflight并复用next ordinal；committed event后的controller crash直接复用；service实际restart强制新preflight/new epoch；temporary/log/PID均无权影响ordinal。任何dispatch必须引用current committed epoch。只有cutoff前BATCH_RESULTS有效；其他中间状态只作audit。

Materialization固定一次primary加至多一次same-identity deterministic rebuild/commit retry，每次有独立immutable identity和durable disposition。任一失败提交`EMBEDDING_BATCH_RESULTS_MATERIALIZATION_FAILED`作为独立global cutoff，停止新Embedding dispatch、results commit及role-local projection；cutoff前已commit results有效，raw/validated-only batch保持pending，cutoff后response只作audit。相同canonical validation错误在第二次重现则Controller/Protocol validity fail；payload一致但storage/I/O再次失败则shared storage unavailable，不误判serializer。Crash只依durable attempt events，不从temporary/log推测或追加retry。

Materialization attempt ID使用`matatt_ + full domain-separated SHA256`，绑定validated batch event、qembbatch、PRIMARY/REBUILD kind、index 0/1、serializer/parser/controller与storage/durability identities，retry另绑primary。Closed dispositions为COMMIT_SUCCEEDED/CANONICAL_VALIDATION_FAILED/STORAGE_IO_FAILED/ABANDONED；每attempt恰好一个。Success必须引用已经进入authoritative chain的results event，不能先于commit或独立授权qemb。

Results event committed但matatt未terminal时，recovery验证其validated batch/qembbatch/implementation bindings后只补success，不重materialize或重写event；既有失败/abandoned terminal与committed event并存即Journal integrity failure。

顺序固定为primary failure disposition→failure cutoff→非正式preflight→RECOVERED→retry；retry成功results event先于success，失败disposition先于新cutoff。RECOVERED只恢复commit权限。其他非故障matatt在cutoff保持non-terminal paused，RECOVERED后由唯一owner从durable input恢复同一attempt，不消耗retry；只有role/formal先终止才ABANDON。Protocol failure不提交recovered；authoritative invalid event仍为Journal integrity failure。

Materializer/storage/controller恢复不改变Embedding service epoch；只有model/service process restart才新preflight/new epoch。Cutoff后responses永久audit-only，但其actual dispatch/epoch/index保留；恢复后只能使用当前epoch剩余transport slot，不增设隐藏attempt。需下一epoch仍消耗唯一request recovery allowance，第三epoch继续owning-role fail-fast。

Deterministic padding使用protocol-generated `embedding_padding_item_id`，绑定冻结payload、tensor position、padding policy及schema/version。Padding不属于memqocc、logical/item attempt、Memory API call、Candidate budget、ledger、cache或qemb。其response只参与batch cardinality/position validation，随后确定性丢弃；payload/count/placement/order/discard policy进入Embedding Runtime Identity。

每个`memqocc_`必须且只能产生一个独立`query_embedding_request_id`；role controller分配role-local sequence。Cache hit显式terminal且不enqueue；cache miss须提交`QUERY_EMBEDDING_ENQUEUED`。Item attempt绑定committed global epoch与epoch-local index `0/1`。Request仅在首次实际dispatch固定`initial_embedding_service_epoch`；仅排队不消耗，并审计全部实际dispatch epochs与recovery allowance。Request若因自身、同batch其他item或共享service故障在下一epoch重新dispatch，一律消耗唯一recovery allowance，不提供fault-attribution豁免；最多两个epochs和四次invocations，需要第三epoch时所属role fail-fast。

Recovery后不保持原失败batch组成。尚未terminal requests保留原role-local sequence/FIFO priority，由scheduler按冻结round-robin/FIFO、batch size、`max_batch_wait`及padding policy重新assembly；新执行生成新的batch-attempt identity，并关联旧失败batch与新recovery batch provenance。旧companions、tensor positions、occupancy及padding layout不构成约束，且不得为了复刻旧batch改变queue order。

Logical request closed terminal set固定为`QUERY_EMBEDDING_RECORD_COMMITTED / QUERY_EMBEDDING_CACHE_HIT_RESOLVED / QUERY_EMBEDDING_ABANDONED`，每个request必须且只能拥有一个。Attempt timeout/failure与infrastructure pause均非logical terminal；相同identity恢复后继续原request，只有paper/role最终终止才转为ABANDONED。正常paper必须等待全部logical requests terminal。

Batch-results commit后但role-local RECORD projection前，Memory API不得继续response、budget、retrieval或delivery。Projection canonical object至少绑定role-run identity、logical request ID、`qembbatch_`、authoritative Scheduler Journal event SHA、实际`qemb_`与role-controller/projection schema identity。同一logical request恰好一个正式projection：完全匹配则复用，缺失则确定性补交，相同内容不得二次terminal；引用不同batch event、qemb或identity时role-level protocol integrity fail-fast。Projection不进入qemb/qembbatch内容身份或跨role顺序。若paper/role已关闭则提交ABANDONED，既有qemb只作audit/content用途。

Cross-role batch中的各role projection独立提交，不设置all-role barrier。Shared failure pause期间禁止新projection；same-Runtime恢复并提交有效epoch后，各role分别补交合法pre-cutoff batch result的projection，不调用Embedding、不消耗recovery allowance、不等待其他role，且不形成跨role search全序。

Shared failure先全局pause/recover，再按request actual-dispatch epochs判定。需要第三epoch的request仅使其所属role fail-fast并关闭该role pending work；其他未超限role继续，多个超限role分别失败。未失败role的durable state/winner有效；失败role以新role-run identity从初始化重跑，Final Suite等待五个合法winner。

失败role的新role-run只有在旧run禁止新request、全部未terminal requests提交ABANDONED、`ROLE_FORMAL_FAILED` durable commit且journal sealed、scheduler确认无旧run pending/dispatchable work后，才能提交`ROLE_RUN_SCHEDULER_ADMITTED`。Admission绑定新run/role/first sequence/current cursor/Runtime Identity；cursor不reset/rollback，新run从event后的下一次正常role轮转参与，不抢占assembled batch或插入committed ordering。新run重建全部role state，sequence可从0开始但scheduler key绑定role-run identity与sequence；old/new run不同时active。

Cursor是冻结role顺序上的`next_role_index`。Assembly从committed cursor环扫，空role仅temporary skip；只有batch-dispatch durable commit更新到最后real item role之后，padding/cache hit/non-enqueued不影响。Restart只用last committed cursor，Admission若指向新role则其可参与下一正常assembly。

Compatibility使用独立suite-level journal：PLAN_COMMITTED→REPLAY_STARTED→REPLAY_COMPLETED→恰一PASSED/FAILED。PLAN先于旧artifact comparison，STARTED后immutable，Suite引用PASSED event SHA，FAILED永久关闭路径且不进入Evolution反馈。STARTED前未读取旧artifact的preflight部署问题可修复；STARTED后临时infra可同plan恢复同一replay，但artifact缺失/损坏不可验证、mismatch、tool identity mismatch、非法output或不可信comparison均关闭保留路径。任何修复形成新identity并五role重跑；resume不得改plan/input/algorithm/equality/expected outputs。

Paper terminal event是query-embedding cache关闭的唯一authoritative boundary。进入terminal流程后先禁止新Embedding intent；正常paper必须等待全部相关Embedding transactions terminal后才可提交terminal event。Candidate/role failure等先关闭paper时，对全部未terminal transactions提交`QUERY_EMBEDDING_ABANDONED`；其late responses只能保存raw audit，不得生成qemb、重开cache、修改ledger/budget或影响结果。既有records保留audit-only，不能供下一paper、Candidate或role执行复用；新paper对相同`memqry_`仍须重新执行Embedding事务。

Formal Embedding gate要求同一canonical真实query在全部合法real-item occupancy、padding数量/positions、partial/full batch、cold/warm状态及三次重复中得到逐bytes相同的canonical little-endian float32 vector。Padding output自身只要求cardinality/mapping/dtype/dimension/finite合法，不要求byte-identical，并始终丢弃。任一padding/occupancy/cache状态改变真实query bytes，即使ranking不变也fail。按8→4→1选择最高通过batch；独立scheduler按五role固定round-robin、role FIFO装配，partial batch只可在冻结wait后dispatch。Wait从首个eligible request进入空buffer开始，以冻结monotonic clock计时至batch dispatch commit；restart恢复的waiting requests视为到期并在preflight后立即按冻结顺序assembly。完整审计batch、padding与clock semantics；`1e-6`和ranking仅诊断。

每次调用采用atomic request semantics：执行前确定性计算完整call/fragment/hydration/token成本。任一非call budget不足时，整次调用返回deterministic `REVIEW_MEMORY_BUDGET_EXHAUSTED`，不得partial delivery、缩小top-k或runtime truncation；失败检查不消耗fragment/hydration/token delivery额度，但正常消耗一次Memory API call。达到call budget后，后续调用直接返回相同exhausted状态，不产生delivery或扩增其他budget。Budget exhaustion不直接处罚Candidate，Candidate可以使用已合法取得的Memory继续Review。非法role、cross-role filter、unknown/foreign ID或其他越权请求属于Candidate-originated protocol failure，不替换结果或转记fitness=0。

Review Memory retrieval本身必须完全model-free、deterministic且可重放：

- Memory query construction不得调用LLM；
- vector search/retrieval不得调用LLM；
- retrieval rerank或semantic filtering不得调用LLM；
- subdimension filter、`top_k`选择与hydration decision不得由隐藏LLM服务完成；
- API不得包含隐藏query rewriting、summary或semantic post-processing；
- 禁止隐藏Memory Query LLM、KnowledgeItem LLM或retrieval-side auxiliary LLM。

MemoryProgram可以进化确定性的query文本、filter、`top_k`和hydration策略，但不得把生成模型置于这些retrieval decisions之中。Candidate不得持有Qwen endpoint credential，也不得调用未声明外部模型。

### 8.1 Append-Only Retrieved Memory Ledger

每个paper-local Review Memory session维护append-only `RetrievedMemoryLedger`。每次成功delivery按实际API call/occurrence order追加immutable entry，绑定call identity、projection/full-record identity、canonical delivered bytes/SHA、delivery token cost与budget pre/post state。重复读取保留为不同occurrences并重复计费；不得hidden dedup、合并或重排。

每次调用先durable commit immutable call intent及pre-budget state，再执行API并durable commit完整response/disposition，随后提交绑定该response的`MEMORY_BUDGET_TRANSITION_COMMITTED`；成功delivery再提交ledger occurrence并绑定同一transition。Success、budget exhaustion与Candidate invalid request的budget transition均必须在向Candidate暴露结果前完成；无内容delivery不得跳过预算事务。Response已commit但transition未commit时只补完transition，不重新调用API；success response/transition已commit但ledger未commit时只补完ledger。仅intent已commit才从相同pre-budget state确定性重放。Logs、process memory及partial/orphan files均非正式delivery。

Budget truth table固定为：success使`api_calls +1`并按canonical response实际成本增加各delivery计数；非call维度不足仅使`api_calls +1`；Candidate invalid request在call未饱和时仅使`api_calls +1`后终止Candidate；call已饱和时提交`pre == post == max`的no-op transition；infrastructure failure不提交正式transition并保持pre-state。每个正式response/disposition必须且只能绑定唯一budget transition，禁止重复提交或重复计费。

### 8.2 Immutable Retrieval Result Manifest

`POST_RETRIEVAL_LLM_SUMMARY`每个`Candidate × paper × role`最多调用一次。调用前，controller必须根据当前ledger的全部成功delivery occurrences按原始call/occurrence order生成并durable commit immutable `RetrievalResultManifest`，之后才能enqueue Summarizer request。Manifest至少绑定：

- Candidate、paper、role、Program与Review Memory API identities；
- 实际query文本、optional subdimension filter与`top_k`；
- 完整有序retrieved `projection_fragment_id` vector；
- 与每个fragment对应的canonical similarity表示；
- hydrated Full Review Record IDs及其delivery order；
- 实际delivered canonical bytes及其逐项SHA；
- budget pre/post state与完整call/delivery provenance；
- exact-flat search、index、Embedding与tokenizer identities。

Manifest通过domain-separated canonical JSON计算`retrieval_result_sha256`，并保留全部重复delivery occurrences，不执行hidden dedup。已提交Manifest不可修改、补项、重排或替换；resume必须复用该manifest，不重新检索。任何Summary input必须可验证地投影自该已提交manifest。没有Summary调用时不生成虚假Summary manifest，也不占用Summarizer Qwen budget；普通ledger与Reviewer-visible Memory audit继续保存。

### 8.3 Registered Post-Retrieval LLM Summarizer Exception

唯一冻结例外是明确注册的 `LLM Summarizer` 方法行为。它只能在deterministic retrieval完成并冻结返回集合、similarity、ordering与`top_k`之后，对已经合法返回的Memory文本执行query-focused summary/condensation。Summary调用不是retrieval的一部分，不得改变或补充召回集合、similarity、ranking、filter或`top_k`结果。

每次Summarizer调用必须：

- 经过global Qwen scheduler，不直接访问model endpoint；
- 使用冻结prompt、generation config与统一budget；
- 绑定Program capability/identity以及logical request/request-attempt identity；
- 完整保存input、raw output、canonical output、finish reason与token audit；
- 输入由已提交`RetrievalResultManifest`确定性装配，只允许加入冻结role framing和该manifest中已经用于retrieval的query；
- 不得加入paper正文、Frozen External Evidence、未召回Memory或额外retrieval结果；
- 不得额外读取或检索未返回Memory；
- 不得把summary写回Corpus、Projection、Index或任何跨paper persistent Memory。

Summary input/output token budget在Corpus materialization statistics和mixed-capacity gate完成后预注册。Dispatch前必须使用冻结Qwen tokenizer与完整framing确定性计算manifest-derived input cost；若超出input budget，返回冻结`SUMMARY_INPUT_BUDGET_EXHAUSTED` disposition且不dispatch Qwen。不得runtime truncation、删除retrieved items、改变manifest、扩大budget、发起额外Summary调用或fallback原始Memory。

`SUMMARY_INPUT_BUDGET_EXHAUSTED`是Candidate-originated runtime/protocol failure：ordinary child终止剩余paper evaluation、消耗attempt且不admit；mandatory initial seed initialization fail-fast；global top-3 finalist winner-selection fail-fast且不得递补。不得转记fitness `0`。若ledger无任何成功delivery，则为合法empty-ledger：不调用Summarizer、不生成虚假Summary manifest、不占Qwen budget，并生成显式empty Reviewer Memory context正常继续。

Provider返回的非空UTF-8 content bytes直接形成canonical summary payload与SHA，不要求模型生成JSON，也不执行schema/semantic retry。Raw response仍须先durable commit，再canonicalize。Within-capacity同一captured input重复3次时，canonical summary SHA、finish reason与protocol disposition必须完全一致；跨capacity不要求bytes一致。

`finish_reason=stop`与`finish_reason=length`都可形成合法canonical summary，前提是content非空。`length`表示合法但截断的summary：按实际provider bytes原样canonicalize并继续，不增加output budget、不执行额外summary retry，也不回退为原始Memory。两种finish reason均进入canonical audit和within-capacity stability比较。

Summary不设置semantic/schema retry；transport retry只遵循统一request transaction protocol。空content、provider/backend故障按infrastructure failure暂停并恢复，禁止silent fallback为unsummarized Memory。Program越权访问、修改已提交Manifest、读取未召回Memory或非法调用Qwen属于Candidate-originated protocol failure。

除这一已注册Summarizer capability外，其他Program不得为query construction、retrieval、rerank或semantic filtering调用LLM。未来扩展必须发布新的protocol/capability version并重新执行Program、scheduler与capacity gates。

### 8.4 Paper Isolation

每个`Candidate × paper × role`从相同immutable Corpus/Projection/Index和fresh Review Memory/Summarizer session开始。Query cache、budget counters、RetrievedMemoryLedger、RetrievalResultManifest、hydrated context、summary与其他paper-local runtime state在paper结束后丢弃，不得进入下一篇B paper。唯一跨paper存在的是冻结Corpus/Projection/Index/embedding assets及Program code/identity；B paper不得写回Review Memory。

### 8.5 Canonical Reviewer Memory Context

Reviewer Memory输入拆分为strict authoritative `CanonicalReviewerMemoryContext`与其唯一Reviewer-visible projection。Authoritative closed payload不包含自身SHA；对domain-separated canonical payload计算`canonical_reviewer_memory_context_sha256`后，由envelope保存payload及SHA。Visible projection独立计算`reviewer_visible_projection_sha256`，authoritative payload绑定该SHA。Reviewer request metadata同时引用并验证两个SHA；实际prompt只注入冻结framing、必要非语义source provenance与最终context text的visible projection bytes。Authoritative payload绑定mode、Program/Candidate/paper/role、ledger与适用manifest SHA、ordered source occurrences、final budget state、canonical context/summary、formatter/framing及token count。Candidate ID、内部SHA、budget、runtime或audit字段不得暴露给Reviewer。Program不得自由拼接Memory prompt、delimiter、source ordering或二次选择delivery。

每个visible source occurrence只允许展示occurrence ordinal、source A-paper manifest ID、role、content kind、subdimension ID或固定`ROLE_OVERALL_ASSESSMENT`，以及排序去重后的paper anchor IDs。Content kind闭集为`PROJECTION_FRAGMENT / PRIMARY_PROJECTION / FULL_REVIEW_RECORD / FULL_REVIEW_SECTION / FULL_REVIEW_OVERALL_ASSESSMENT`。`FULL_REVIEW_RECORD`只用于Direct/Summary hydration occurrence provenance；section/overall kinds只用于Structured view projection。Fragment只展示`explicit_fragment_anchor_ids`，inherited section anchors仅供authoritative audit；Projection/section展示完整`section_paper_anchor_ids`；overall展示空anchor集合。重复occurrence逐项保留。Similarity、query、`top_k`、budget、ledger/call/request ID、Candidate ID及Full Review/Projection/Fragment/Context SHA或其他audit identity均禁止进入Reviewer prompt。

- Direct mode：包含ledger全部成功occurrences，保持call order与重复项；
- Structured Experience mode：消费全部occurrences，依次按role canonical subdimension order、Full Review Record ledger首次出现顺序、record内ledger occurrence顺序组织；重复occurrence保留。Anchor只作排序去重后的非语义provenance，不得驱动复制、筛选、权重或semantic dedup；原文bytes不变且不得总结或选择性删除；
- Summary mode：Reviewer只读取canonical summary及非语义source provenance，不同时读取原始retrieved Memory；
- Empty ledger：生成显式empty canonical context，正常进入Reviewer。

Hydrated Full Review Record在ledger中始终只形成一个hydration occurrence和一次delivery/accounting。Structured formatter对单个occurrence执行section projection：每个既有subdimension section的原始`review_text` bytes在对应group恰好展示一次；可选`overall_assessment`在所有groups后的冻结role-level block恰好展示一次。该projection只改变外层framing/placement，不创建新retrieval、delivery、ledger occurrence、Projection/Fragment identity或计费。不同calls重复hydrate同一record时仍逐occurrence展示，不跨occurrence去重。

每个应展示内容生成一个audit-only `viewitem_ + full domain-separated SHA256` StructuredViewItem，绑定source ledger occurrence、closed content kind、subdimension/role-level表示、原始byte range/SHA及display ordinal。Fragment/projection occurrence各生成一个对应view item；Full Review occurrence为每个固定section各生成一个section item，并在overall存在时额外生成恰好一个overall item。它不属于Corpus/Projection/Fragment/retrieval result/delivery或新Memory内容，不产生计费，也不得把identity暴露给Reviewer。Unknown kind、cardinality mismatch、遗漏、重复、错误映射或bytes变化均protocol fail-fast。

Summary Reviewer-visible projection按原始ledger顺序为每个成功delivery occurrence保留最小provenance并保留重复项；fragment/projection/hydrated record分别使用对应fragment/projection/record kind，不展开StructuredViewItems。该provenance仅表示Summarizer输入集合，不声明Summary逐项覆盖、评价或保留了每个source的全部内容。

Summary request使用strict `CanonicalSummarizerInputRecord`与唯一`SummarizerVisibleInputProjection`。Record使用`suminput_ + full domain-separated SHA256`，authoritative payload/envelope绑定RetrievalResultManifest、Program/Candidate/paper/role、ledger/budget及完整audit identity；visible projection独立计算SHA。Request同时绑定record identity与visible SHA，prompt只注入visible bytes。Model-visible闭集只含冻结role/Summary framing、按committed API call顺序且产生至少一个ledger delivery的成功vector query exact bytes、按ledger顺序的全部成功delivery Memory bytes，以及最小content-kind/subdimension/anchor provenance；重复eligible query/delivery保留。Query可包含deterministically paper-derived文本，但必须与实际index request逐bytes相同，禁止额外paper/STRUCTURAL通道、扩展/改写query、External Evidence、similarity、rank/top-k、budget、Candidate ID、内部SHA或未检索信息。Failed/exhausted/invalid/zero-delivery query不进入visible projection；get/hydrate不生成query。旧`CanonicalSummarizerInputProjection`只作migration alias，不得成为新formal machine type。

每个eligible成功vector-search call恰好产生一个visible query occurrence，不因multi-fragment results或后续get/hydrate复制；相同bytes的两个独立calls仍产生两个ordered occurrences并分别计入累计budget。Authority绑定call identity，visible只显示ordinal和bytes。Query与retrieved Memory均以冻结delimiter/length/framing作为untrusted quoted data；Candidate不能控制system role、message boundary或framing。System instructions禁止data覆盖任务或请求额外信息/capability，但不对query做semantic stripping或改写；其对Summary行为的影响属于显式Program行为。

Framing使用冻结ASCII header和UTF-8 byte length，parser按length读取raw payload而非搜索delimiter；query/Memory bytes不变，system与untrusted data保持物理message-role隔离。Header spaces/newlines、ordinal/length格式、content-kind encoding及parser/materializer identity全部冻结。Delimiter-like、多语言、CRLF和允许control-character vectors必须唯一解析；length mismatch、byte变化或role-boundary变化均protocol failure。

Summarizer messages严格为一个immutable system message、一个只含SummarizerVisibleInputProjection的user message，以及冻结chat template追加的generation prompt。禁止developer/tool、assistant history/prefill、额外user或Candidate控制system/boundary。Messages JSON、system prompt、chat template、rendered prompt及generation config全部进入Summarizer Semantic Identity并可确定性重建。

另行预注册跨role/paper/Candidate/mode统一的`max_reviewer_memory_context_tokens`，由所有formal `paper × role`的最小`max_model_len - complete_non_memory_input_tokens - reviewer_max_output_tokens - frozen_safety_reserve`得到。完整非Memory输入计数包括chat template、special tokens、全部paper/STRUCTURAL/prompt/schema及Memory projection外Reviewer framing；Memory projection自身framing/provenance/text完整计入cap且不双重计数。Direct/Structured因Candidate retrieval导致超限时产生`REVIEWER_MEMORY_CONTEXT_BUDGET_EXHAUSTED`并按ordinary child/mandatory seed/finalist的冻结Candidate-failure范围处理，禁止裁剪、fallback或切换mode。Summary mode必须在preformal证明max-output加最坏合法provenance/framing始终可容纳，否则protocol/runtime gate failure。Tokenizer/formatter/accounting错误属于infrastructure/protocol failure；empty ledger合法。Minimum Memory requirement只指技术可执行边界，不强制Candidate retrieval或非空Memory；统一cap不足则以新Runtime/Protocol Identity重新gate，formal中禁止动态调整。`frozen_safety_reserve`在完整materialization/accounting/near-max gate后预注册为带依据与测试manifest的非负整数；它不是count mismatch容差。本地与backend prompt-token accounting不一致必须修复identity后重gate，禁止用reserve掩盖，formal中不得动态修改。

Summarizer-input与Reviewer-memory使用独立token gate。前者根据Memory API calls/delivery/hydration预算、query/provenance字段最大长度及实际Memory token budget构造最坏合法input；eligible成功query occurrences使用Summarizer tokenizer累计并满足统一`max_summarizer_query_tokens_total`，完整framing、queries、Memory bytes与provenance再满足`max_summarizer_input_tokens`和Qwen context。任一超限按`SUMMARY_INPUT_BUDGET_EXHAUSTED`且不dispatch，并按Q195 stage ownership处理；不得删早期query、仅保留末项、去重、截断、换mode或fallback。Direct/Structured不执行Summary，免于累计query limit。后者只使用Summary `max_output_tokens`、Reviewer-visible provenance与Memory framing验证统一Reviewer cap，不计Summarizer queries或raw retrieved Memory。所有Programs共享声明的per-call/total limits。所有visible provenance字段冻结格式/最大长度并用最大occurrence boundary vectors验证；任一gate失败只可preformal调整统一budget并产生新Protocol/Runtime Identity，formal不得截断、去重、降output或扩容。

所有相关token-accounting test vector要求local完整rendered request count与backend/provider `prompt_tokens`严格整数相等，覆盖short/typical/near-max、五role、cold/warm cache且每项重复3次。Usage缺失或任一不等即preformal failure，不允许容差、平均或reserve抵消；完整保存request bytes、token IDs/count、provider usage与accounting identity。

每次调用必须记录最终实际送入Embedding/vector index的query text、role、filter、`top_k`、logical request/call identity、返回projection IDs及顺序、similarities、hydration、delivery token usage、budget pre/post state与错误/disposition。API capability相同不要求三个initial seeds采用相同实际retrieval strategy。

Q282–Q289进一步冻结共享Embedding结果事务：`BatchContentDedupObservation`使用稳定`dedupobs_`身份但保持audit-only；每个Results event唯一绑定commit时合法且尚未terminal的originating `matatt_`；materialization failure/recovered形成不允许嵌套的singleton paired cutoff；paused matatt通过独立execution identity、monotonic resume ordinal及DISPATCHED/OUTPUT_COMMITTED/ABANDONED维持single owner。Batch dispatch commit后cursor不因任何后续失败回滚。Compatibility replay只能在STARTED前由既有authoritative catalog metadata冻结inputs；逐项最多primary加一次相同identity retry，resume只补缺失durable item或最终decision。

Q290–Q298进一步规定：DedupObservation prefix以current Results event位置固定；Results event绑定唯一originating OUTPUT_COMMITTED execution/output SHA。Execution恰一OUTPUT或ABANDONED terminal，cutoff后late output不能formal commit，cutoff前output在RECOVERED后直接复用。成功role/Suite在seal前提交authoritative artifact catalog。Compatibility使用composition、plan与item三层非循环identity，必须完成全部items后才决策且终结前隐藏semantic results；任何不能无模型、无外部API、无禁用benchmark资产证明的变化统一重跑。

Q299–Q392补充decision/output、failure、audit与Compatibility recovery。Readiness与Export Access各由独立single-writer infrastructure journal管理连续epoch；Quiescence使用pre-head、唯一commit slot和closed Requirement Registry。Export拆分candidate、Access Validation与promotion预算；Final Suite使用稳定九slot gate identities及机械Audit dependency closure。可信Replay Audit FAIL仍Export，未知Readiness不得越过gate。Target Controller只绑定behavior implementation，Official Runtime可重建但implementation component必须精确相同。

## 9. Required Gates

Formal前至少验证：

- 125×5 coverage与role/paper ordering；
- strict no-memory/no-external/no-CoCo capability denial；
- 五个role的strict section coverage/order、closed schema与forbidden scoring/applicability字段；
- 625条records确定性生成2,875个primary projections；
- `2048` token boundary、paragraph/sentence/token fallback与zero-overlap expected vectors；
- 无overall/full-record重复vector entry，且每个fragment仅进入当前role index一次；
- `fullrev_/proj_/frag_`三级identity重建、collision/missing/duplicate rejection；
- inherited versus explicit fragment anchors及subset validation；
- normalized float32 exact-flat search、CPU accumulation、score serialization、完整pre-top-k与ID tie-break；
- cross-role/filter registry denial、API budget计费与full-record hydration；
- 五个Memory budget维度、相同seed capability、重复计费、budget exhaustion与invalid-request matrix；
- Memory call intent/response/budget-transition/ledger transaction、无内容delivery预算提交、visibility barrier与crash recovery/no-double-charge；
- Budget truth table、response/transition一对一、saturated no-op与infrastructure no-transition；
- query construction、search、filter、`top_k`、hydration与rerank全程无LLM且可确定性重放；
- RetrievalResultManifest durable barrier、input projection、resume reuse与mutation/extra-input rejection；
- paper-local ledger/session/budget/summary isolation及B-paper write-back denial；
- CanonicalReviewerMemoryContext authoritative schema/visible projection isolation、Reviewer request SHA reference、all-occurrence coverage与三mode content boundary；
- authoritative payload/envelope non-self-hash、visible projection独立SHA及prompt injection boundary；
- visible provenance closed fields、forbidden similarity/query/budget/internal identity与重复occurrence保留；
- content-kind closed enum/cardinality mapping及explicit-fragment/section/overall anchor rules；
- Full Review record Direct/Summary-only provenance、Structured-only section/overall及mode-kind validation；
- Structured Experience三级确定性顺序、重复保留、anchor provenance-only与no-rewrite/no-selection；
- hydrated Full Review单occurrence/单计费、section与optional overall恰好一次的projection；
- StructuredViewItem audit identity、exact coverage、byte equality、non-asset/non-delivery/non-billable boundary；
- full visible-context token count、Direct/Structured overflow failure scope、Summary worst-case preflight与empty validity；
- all-paper-role minimum-headroom global cap、chat/special-token coverage、technical-minimum-only与no dynamic adjustment；
- safety-reserve measurement manifest、local/backend count consistency、mismatch fail-fast与no masking；
- Summary occurrence-order provenance、duplicate preservation与input-set-only/no-coverage-claim semantics；
- CanonicalSummarizerInputRecord `suminput_`/SummarizerVisibleInputProjection双identity、model-visible closed inputs与independent-paper/external/internal-field denial；
- exact index-query equality、successful-delivery eligibility、failed/zero-delivery exclusion、query per-call/total budgets及legacy type rejection；
- byte-preserving UTF-8/nonempty validation、raw/canonical equality、Embedding/Summarizer双tokenizer audit与no transformation；
- logical-query versus wire provenance、observed-query SHA equality与mismatch infrastructure failure；
- memqry content/memqocc occurrence identities、same-query duplicate-call audit与visible-ID denial；
- qemb vector identity、memqocc reference、paper-local/resume cache与cross-scope denial/cache neutrality；
- Query Embedding dispatch/raw/item/VALIDATED/self-contained-BATCH_RESULTS/role-terminal链、committed-response replay、orphan non-promotion、invalid-response no-qemb及no-budget boundary；
- batch-attempt/item-attempt split、ordered tensor binding、batch-raw-first item projection、timeout fan-out与late-response denial；
- all-or-none batch validation、VALIDATED/self-contained-results双barrier、embedded full records/vector bytes、ordered batch qemb vector、one-invalid-invalidates-all与role workflow visibility denial；
- RFC4648 padded Base64/no-compression、tensor-position ordering、padding exclusion与strict vector decode validation；
- `qembbatch_` pure item projection、execution-binding exclusion、content-vs-Journal commit identity、continuous-chain authority与orphan-content denial；
- same-content independent self-contained events、exact-event workflow projection、no formal/visible dedup与physical-dedup-only allowance；
- prior-identical canonical-field denial、audit-only DedupObservation missing-neutrality与self-contained recovery no-dependency；
- one-batch/one-unsplittable-event、max event bytes/manifest、durability/crash recovery及measured commit-latency gate；
- bounded materialization attempts/durable dispositions、independent failure/recovered cutoff、post-cutoff denial、transient-I/O versus repeated-canonical-error ownership与committed-event integrity failure；
- `matatt_` identity/parent/closed disposition、success-after-results-commit、no epoch reset及post-cutoff actual-dispatch accounting；
- event-committed/success-missing recovery、failure-terminal conflict rejection、cutoff/recovered/retry strict order与paused noncausal single-owner attempts；
- Query Embedding logical/transport-attempt identities、epoch-local two-attempt timeout/late-response state machine及canonical winner arbitration；
- `EMBEDDING_SERVICE_EPOCH_COMMITTED` epoch0/restart lifecycle、first-dispatch request initial epoch、actual-dispatch/no-fault-exemption accounting、single recovery epoch/max-four calls与third-epoch role failure；
- epoch ordinal durable-only/no-gap crash recovery、cross-role projection independence及owning-role-only third-epoch failure/new-run suite barrier；
- role-local projection deterministic identity/idempotency/conflict rejection、old-run seal/scheduler drain及current-epoch rejoin preconditions；
- `ROLE_RUN_SCHEDULER_ADMITTED` cursor/next-turn/no-preemption semantics及role-run-keyed FIFO；
- next-role-index scan/dispatch-only mutation/padding-cache exclusion与restart cursor recovery；
- pre-diff frozen one-shot read-only Compatibility Certificate、no-model/no-C-Human-CoCo replay、failure-no-adaptation、Suite provenance及Protocol-affecting five-role rerun；
- Compatibility lifecycle journal、PASSED/FAILED exclusivity、pre/post-STARTED infrastructure boundary、unverifiable artifact/tool-invalid closure及Suite event reference；
- one-memqocc/one-logical-request、cache-hit no-attempt terminal resolution与call/budget/audit preservation；
- role-local sequence/durable enqueue、cache-hit no-queue、retry FIFO preservation与restart position recovery；
- closed RECORD/CACHE_HIT/ABANDONED terminal set、exactly-one invariant与infrastructure nonterminal pause；
- qemb content identity/excluded execution scope、same-qemb independent execution proof与cross-paper reuse denial；
- paper-terminal/role-failure cache closure、audit-only retention与same-memqry new-paper forced transaction；
- normal terminal drain、failure-path abandonment与late-response no-qemb/no-ledger mutation；
- recovery reassembly/no old-batch constraint、old/new batch linkage与queue-order preservation；
- batch 8→4→1在合法occupancy/cold-warm/三次重复中的canonical vector byte equality，deterministic padding identity及容差/ranking仅诊断；
- Embedding role-round-robin/FIFO scheduler、partial-batch max-wait clock、forbidden reorder与batch/tensor provenance；
- shared-service failure global pause、pre-cutoff atomic batch-results validity、validated-but-uncommitted whole-batch invalidity与same-runtime preflight recovery；
- independent Scheduler Journal global cutoff、role-journal SHA references与no cross-role Candidate/search order；
- padding-item identity、frozen payload/position/discard与Memory/Candidate/budget/qemb isolation；
- real-query exact-byte gate over occupancy/padding/cold-warm matrix与padding-output structural-only validation；
- first-eligible wait start、batch-dispatch commit stop、frozen clock/unit/rounding与restart immediate-dispatch semantics；
- per-call Candidate invalid versus cumulative Summary exhaustion、Direct/Structured exemption与no query fallback；
- one-call/one-query occurrence、multi-fragment/get/hydrate no-copy、repeated-call preservation与untrusted framing/capability denial；
- byte-length framing、system/data role isolation、delimiter/control-character boundary vectors与parser mismatch rejection；
- Summarizer exact message layout、forbidden roles/history/prefill与Semantic Identity reconstruction；
- Summarizer-input versus Reviewer-memory dual gates、separate counts/budgets、worst-case schema bounds与no cross-budget reuse；
- exact local/backend prompt-token equality、five-role cold/warm 3-repeat vectors及complete request/token audit；
- 已注册LLM Summarizer只能消费冻结retrieval结果，不能改变或扩充retrieval，并且只能经global scheduler；
- zero/one Summary call、ledger全部occurrences、input-budget preflight/no-dispatch、input scope、prompt/config、request/token audit、within-capacity exact stability、`stop/length`接受、empty/infrastructure/Candidate failure matrix与禁止silent fallback/write-back gates；
- C-way scheduling permutation不改变canonical identity；
- one-retry/fail-fast及raw-response-first recovery；
- Corpus commit barrier拒绝624条、重复条与unknown role/paper；
- Projection materialization在Corpus commit前不可启动。
