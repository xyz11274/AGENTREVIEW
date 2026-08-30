# AgentReview Review Memory Evolution

AgentReview 通过离线 Evolution 改进专业审稿所使用的 Memory 策略。Reviewer 的职责保持冻结；Specialist Evolution 完全 reference-free，真人参考与 Benchmark 只在 Final Review Suite 完全冻结后的 Official CoCo Evaluation 中出现。

## Language

**Frozen Reviewer Identity**:
`QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL` 五类 Specialist Reviewer 的固定职责及 rubric 子维度。Evolution 不得改变这些职责。
_Avoid_: 完整冻结的 reviewer prompt

**Full Review Corpus**:
某一Specialist Reviewer在冻结A区125篇论文上预先生成的no-memory AI review集合。`QUAL / CLAR / SIGN / ORIG / POL`各有独立Corpus，总计625条canonical Full Review Records；同一角色的所有MemoryProgram共享同一份冻结Corpus。Build输入仅含paper、STRUCTURAL context与冻结role Reviewer配置，禁止读取已有Memory、Frozen External Evidence、Human/CoCo、Evaluator或Reflector。625条全部commit后才允许无LLM生成Retrieval Projection与embedding index。
_Avoid_: Self-Generated Experience、human review library、初始 memory 数据库

**Full Review Record**:
Full Review Corpus 中某一 A paper × role 的完整、不可变 AI review，是所有检索切片的权威原文。它始终包含该role全部固定subdimension sections；这些sections不是formal applicability判断。
_Avoid_: chunk、KnowledgeItem、向量记录

**Retrieval Projection**:
从 Full Review Record 的固定subdimension section确定性派生、可随时重建的检索视图。625条Records固定产生2,875个primary projections；仅超长section可再确定性分片，且Full Review、Primary Projection与Fragment分别具有稳定内容身份。
_Avoid_: 原版 review、权威 memory 文本

**Rubric Subdimension**:
Frozen Reviewer Identity 中的细分审稿标签，例如 `QUAL-MET`、`QUAL-EXP`、`QUAL-REP`、`QUAL-CMP`、`QUAL-STA`。它来自 Reviewer 输出协议，不是 CoCo taxonomy 或 gold label。
_Avoid_: CoCo category、evaluator label

**MemoryProgram**:
在共享 Full Review Corpus、Projection与Review Memory API上定义query构造、检索、hydration和Memory使用策略的可执行程序。每个Program声明唯一read mode；mode可以随child改变，但query、search、filter、top-k、rerank与hydration decision始终model-free。
_Avoid_: review 数据库、隐藏Memory Query服务、prompt 模板

**Memory Read Mode**:
Program对已召回Review Memory的冻结处理类型，闭集为direct retrieval、post-retrieval LLM summary与structured experience recall。它属于Program identity而非lineage；只有summary mode获得最小化的显式Summarizer capability。
_Avoid_: lineage capability、runtime mode switch、generic LLM toolkit

**Review Memory API**:
所有MemoryProgram共享的统一、model-free、deterministic只读Corpus检索边界，提供role-local vector search、Projection读取与Full Review Record hydration。API capability与budget对三个initial seeds相同，但它们可采用不同的可审计确定性retrieval策略。
_Avoid_: Candidate-private index、cross-role retrieval、hidden reranker

**Canonical Memory Query**:
MemoryProgram提交并通过non-empty UTF-8 validation的byte-preserving query，是Embedding检索与合法Summary query occurrence共享的唯一内容。它不经过Unicode、whitespace、line-ending或semantic rewriting；Embedding与Summarizer可用各自冻结tokenizer对同一bytes独立计数。
_Avoid_: normalized query、summary-expanded query、Memory Query LLM output

**Memory Query Occurrence**:
某一Candidate、paper与role中的一次已提交vector-search调用对Canonical Memory Query的调用级身份。相同query bytes共享内容身份，但每次独立调用保留独立occurrence以维护顺序、预算与恢复审计。
_Avoid_: deduplicated query、query content hash、visible call ID

**Query Embedding Record**:
把Canonical Memory Query、冻结Embedding Identity与canonical vector bytes绑定起来的内容寻址记录；只有所属Query Embedding Batch Result原子提交后才作为共享内容正式存在。当前paper workflow还必须提交role-local terminal projection才能使用它；相同内容身份不授予跨paper cache复用权限。
_Avoid_: paper-local execution record、persistent query cache、document embedding

**Query Embedding Request**:
与一个Memory Query Occurrence一一对应、在冻结Embedding Identity下取得query vector的paper-local逻辑请求。它可由合法paper-local cache直接解决，或在每个Embedding Service Epoch内通过最多两个相同transport attempts完成；其逻辑身份跨一次受限service recovery保持不变。
_Avoid_: HTTP request、embedding call attempt、query cache entry

**Query Embedding Request Attempt**:
Query Embedding Request在某个Embedding Service Epoch内的一次实际网络调用；身份显式绑定service epoch与epoch-local transport index，timeout durable commit后永久关闭，late response只能进入raw audit。
_Avoid_: retry result、logical embedding request、canonical qemb

**Embedding Service Epoch**:
由`EMBEDDING_SERVICE_EPOCH_COMMITTED`全局单调编号、绑定同一冻结Runtime Identity与完整preflight的一段共享service execution区间。Request只在首次实际dispatch固定initial epoch，任何后续epoch dispatch都会消耗其唯一recovery allowance；仅排队不消耗。
_Avoid_: unlimited service retry、new logical request、runtime identity change

**Embedding Scheduler Journal**:
共享Embedding scheduler/service的single-writer append-only权威历史，记录enqueue、batch、response、validation、atomic batch results、failure与global service epoch，并提供各role journal引用的唯一global failure cutoff。它不为五个role的Candidate/search state建立全局顺序。
_Avoid_: role search journal、scheduler log、cross-role candidate order

**Query Embedding Batch Attempt**:
一次Embedding transport invocation及其有序item request-attempt集合；batch raw response是各item vector projection的共同审计来源，但不合并item身份。
_Avoid_: Query Embedding Request、vector batch result、shared qemb

**Query Embedding Batch Result**:
一个validated batch全部真实item Query Embedding Records及vector bytes的self-contained有序原子内容；其纯内容身份排除request、paper、role、epoch与Journal execution bindings。只有独立commit event进入连续权威hash chain才授权整批qemb正式存在；内容artifact本身不证明commit或允许workflow使用。
_Avoid_: partial qemb commit、role-local qemb authority、validated-only result

**Batch Content Dedup Observation**:
Batch Result正式提交后生成的稳定内容寻址、非权威审计记录，只描述current Results event之前具有相同内容身份的events；其prefix cutoff固定在current event位置，不随Journal后续推进而变化。它不进入event chain、formal identity、recovery或Candidate-visible行为，重复生成合法，缺失、失败或审计冲突均不改变正式状态。
_Avoid_: Batch Results event field、dedup authority、workflow cache

**Query Embedding Terminal Projection**:
把一个role-local逻辑Query Embedding Request确定性绑定到已正式提交的Batch Result及其qemb的幂等终结记录。它授权当前workflow使用共享内容，但不创造qemb内容身份，也不建立跨role顺序。
_Avoid_: qemb content identity、batch commit authority、cross-role barrier

**Batch Results Materialization Attempt**:
把一个validated Query Embedding Batch Result确定性构造并提交到权威Scheduler Journal的一次有界尝试。每个attempt最多有initial与一次infrastructure-resume execution；Results event唯一绑定一个未terminal attempt及其已提交output，且只有该Results commit能够授予qemb正式生效。
_Avoid_: Embedding inference retry、qemb authority、unbounded storage retry

**Materialization Execution**:
Materialization Attempt的一次具体worker执行，具有稳定identity、单调resume ordinal及恰一committed-output或abandoned终态。Committed output是可恢复复用的权威执行结果；晚于global cutoff的未提交output只能保留作audit。
_Avoid_: worker process、materialization retry、temporary output

**Materialization Execution Output**:
Materialization Execution提交的raw-noise-independent、execution-scoped canonical decision结果，闭集为完整Batch Results candidate payload、canonical validation failure evidence或storage failure evidence。它绑定具体execution但不包含non-decision raw-audit identity，且只有后续合法Results event才能授予qemb正式生效。
_Avoid_: worker-local output、external payload pointer、qemb commit

**Materialization Decision Projection**:
先于Materialization Execution Output构造、排除execution bindings的唯一semantic-level decision内容身份，用于等价比较、Compatibility与审计。Execution Output必须内嵌同一projection并重新验证其identity；该projection不授予OUTPUT或Results commit authority，也不能终结任何workflow。
_Avoid_: Materialization Execution Output、qemb authority、resume result

**Materializer Global Pause**:
共享Materializer、storage或control-plane健康性无法继续证明时，在权威Scheduler Journal中开启的唯一全局暂停区间。它以commit位置定义cutoff，保留此前合法outputs/results，并由绑定同一pause和通过preflight的recovered event关闭。
_Avoid_: process-local pause flag、single-batch materialization failure、worker timeout

**Batch Results Materialization Cutoff**:
共享results pipeline因materialization/storage故障进入暂停状态的唯一open failure event。它必须与一个明确引用该failure及preflight的recovered event成对关闭；open期间不得嵌套新cutoff或提交results/workflow projection。
_Avoid_: Embedding service epoch、nested failure stack、process-local pause flag

**Role Run Scheduler Admission**:
旧role-run完全终结并从共享scheduler排空后，新role-run获得该role调度资格的权威边界。它不重置global round-robin cursor、Embedding Service Epoch或其他role的queue state。
_Avoid_: role restart timestamp、first enqueue、scheduler reset

**Formal Observable Semantics**:
Formal系统中可能影响Candidate、搜索轨迹、评价或最终Suite的全部canonical bytes、decisions、ordering、state transitions与failure dispositions。仅内部实现不同而这些正式可观察结果完全等价，才属于execution-only变化。
_Avoid_: unit-test output、selected happy-path hashes、internal implementation detail

**Compatibility Certificate**:
在Protocol Semantic Identity不变时，通过具有权威suite-level lifecycle的一次性预注册、完整执行且终结前隐藏语义结果的只读重放，证明两个Controller implementation identities对其完整受影响Formal Observable Semantics完全等价的冻结审计资产。它不能授权Protocol变化、启动后换工具、失败后适配或无法覆盖的差异进入同一Final Suite。
_Avoid_: informal migration note、partial regression test、protocol-version bridge

**Authoritative Artifact Catalog**:
Role或Suite在成功completion/seal前提交并由终结event反向绑定的不可变、依赖闭包完整的artifact metadata集合，使Compatibility Plan无需打开payload即可唯一冻结输入。它绑定covered Journal range/head，提交后scope永久封闭，不能事后扫描、补造、替换或吸收Official CoCo产物；缺失时旧winner不具备Compatibility保留资格。
_Avoid_: filesystem inventory、post-hoc payload scan、audit file list

**Artifact Registration**:
非self-contained formal artifact在其source authoritative event中获得的唯一权威登记，证明其canonical bytes已先行durable并实际验证content identity、长度、类型及依赖。Catalog只能从这些登记确定性派生，不能通过文件系统扫描补充。
_Avoid_: future artifact declaration、directory inventory、seal-time discovery

**Audit Asset Catalog**:
按冻结scope type、唯一scope identity与authoritative source-Journal覆盖范围，保存events通过`audit_outputs`引用但不参与formal decision或Compatibility的external raw audit occurrences及其登记。Catalog提交后covered range永久封闭；Mandatory资产缺失会阻止audit readiness，但不追溯改变已经合法提交的decision、fitness或winner。
_Avoid_: Authoritative Artifact Catalog、Compatibility dependency closure、formal decision input

**Formal Audit Scope**:
由closed registry预先定义、绑定owner与有序authoritative source-Journal slots、拥有唯一identity且最终独立封闭的raw-audit归属边界。Compatibility Replay与Compatibility Export是不同scope；scope关闭后的operational audit不能回填其中。
_Avoid_: log directory、dynamic audit category、Audit Asset Catalog

**Audit Readiness**:
基于已提交的self-contained verification evidence，对一个封闭Audit Asset Catalog的mandatory与optional资产完整性作出的唯一PASS或FAIL结论。它只决定该scope能否进入Final Suite readiness，不重新解释或修改既有formal decision。
_Avoid_: fitness validity、Catalog validation、raw-audit repair

**Audit Readiness Validation Run**:
在一个冻结Readiness infrastructure epoch中，对同一immutable Audit Asset Catalog执行的一次有界验证生命周期。基础设施耗尽只封闭当前run；新epoch可以重验相同Catalog，但semantic FAIL不可重验为PASS。
_Avoid_: Audit Readiness result、local retry ID、formal scope rerun

**Audit Readiness Infrastructure Epoch**:
共享Readiness validator与storage在完整preflight后获得的权威运行世代。只有基础设施实际恢复并再次通过preflight才产生后继epoch，不改变被验证的Catalog或实验结果。
_Avoid_: validator process、retry counter、Audit Scope identity

**Audit Readiness Infrastructure Journal**:
跨Corpus、Role、Scheduler、Compatibility与Official scopes共享的Readiness validator/storage执行权威历史。它为epoch、validation run、attempt、evidence与run terminal建立唯一全序；scope Journal只能保存引用该权威结果的幂等workflow projection，不为各scope的formal decisions建立跨scope顺序。
_Avoid_: role journal、Audit Catalog、validator log

**Readiness Workflow Projection**:
Scope Journal对某个权威Readiness run terminal的唯一幂等本地引用。它不重新授权或改写Readiness；PASS/FAIL可以引用真实Readiness result，Exhausted/Abandoned必须使用受控null而不得伪造result。
_Avoid_: second readiness decision、scope-local validator result、checkpoint copy

**Formal Infrastructure Journal Registry**:
在formal前冻结、为Readiness与Export Access Infrastructure Journal generations授予authority的规则内容。其独立Registry Journal是该层root-of-trust，只管理generation admission、replacement与seal；自身integrity失效会终止当前formal root，不能递归创建临时Registry继续混用旧状态。
_Avoid_: infrastructure run journal、operator recovery note、self-authorized replacement

**Formal Infrastructure Registry Journal**:
承载Infrastructure Journal generation admission、replacement与seal事实的独立root authoritative hash-chain。它不属于自身管理对象，不能用generation replacement机制替换自己。
_Avoid_: Formal Infrastructure Journal Registry rules、managed Infrastructure Journal、self-replacing root

**Top-level Formal Root**:
一次完整formal experiment的最外层冻结authority identity，绑定Protocol、experiment manifest、resource/protocol roots及必要seed/config。Root failure后的新实验必须获得新Root identity，v1不跨Root继承winner或search state。
_Avoid_: Formal Infrastructure Registry Journal、Suite Run、process execution

**Formal Root Control Journal**:
为Top-level Formal Root提供唯一birth、关键root disposition与terminal的最小append-only authority chain。它与finfrajournal隔离、不由finfrareg管理且不能在同一Root内replacement。
_Avoid_: managed Infrastructure Journal、workflow journal、root recovery log

**External Root Failure Witness**:
当Formal Root Control Journal本身无法可信继续时，对“当前Root必须fail-stop”作出的外部不可变证明。它只引用最后可信状态，不能替代Root terminal，也不能授权repair、replacement或恢复dispatch。
_Avoid_: Formal Root terminal、recovery authority、replacement journal

**Formal Root Shutdown Barrier**:
Root仍可写时，在Root terminal前冻结的全局收束边界，证明新dispatch已停止、capability已撤销、open workflows已terminal或被如实列入incomplete vector，并记录全部可信Journal heads。
_Avoid_: process shutdown log、Root terminal、best-effort cleanup

**Verified Mirror Snapshot**:
对Formal Infrastructure Registry Journal某个immutable canonical prefix的逐bytes只读证明。它不能声明current primary head、接受新event、被提升为primary或与其他snapshot合并。
_Avoid_: writable failover journal、backup restore authority、replica voting member

**Formal Infrastructure Authority Activation**:
Root Control Journal对某个已提交Registry authority event及其verified mirror snapshot作出的exactly-once采用记录。Registry event在activation前只有历史存在性，不能向managed workflow授予新执行authority。
_Avoid_: Registry event、mirror snapshot、repeatable notification

**Infrastructure Authority Transition Registry**:
冻结哪些Registry event会授予、替换、封存或撤销managed infrastructure authority，以及其PENDING hold、mirror activation和downstream capability变化的closed registry。
_Avoid_: event log、capability manifest、activation record

**Root Storage Availability Failure**:
Formal Infrastructure Registry Journal的authoritative bytes在冻结primary与verified mirror recovery路径耗尽后仍不可获得，但没有证据证明canonical chain错误的root fail-stop。它不能被报告为Journal corruption。
_Avoid_: root integrity failure、hash-chain corruption、temporary retry

**Root Canonical Integrity Failure**:
由可信证据证明Formal Infrastructure Registry Journal的hash chain、同sequence canonical bytes、committed replica prefix或genesis/Registry binding已经损坏的root terminal。它与单纯storage不可用严格区分。
_Avoid_: storage timeout、mirror unavailable、bounded recovery exhaustion

**Infrastructure Journal Generation**:
由Formal Infrastructure Journal Registry授权的一代Readiness或Export Access权威Journal。其可信前驱是旧generation最后一个验证合法的event，而不是被观察到的首个非法event。
_Avoid_: process restart、validator retry、unregistered journal directory

**Infrastructure Journal Replacement Plan**:
Managed Journal replacement前由Registry authority一次性冻结的完整恢复计划，覆盖旧generation全部可识别runs、trusted cutoff、preserved Evidence、Q422分类、fresh/terminalization路径与禁止复用集合。
_Avoid_: operator recovery checklist、partial run list、post-replacement classification

**Root Storage Incident**:
由Formal Root Control Journal承载、诊断finfrajournal availability或integrity异常的三路径Evidence-first事务。它以唯一RECOVERED或TERMINAL结束，不在被诊断Journal内记录自身authority。
_Avoid_: filesystem retry loop、Registry generation replacement、operator incident ticket

**Evidence Terminalization Decision**:
在generation replacement中仅依据旧可信prefix内preserved Evidence与原冻结decision rule确定性派生PASS或FAIL的记录。它不重新读取input、不执行validator，也不是新的semantic validation Evidence。
_Avoid_: validation retry、reconstructed evidence、best-effort terminal

**Audit Scope Quiescence**:
通过关闭新dispatch、终结既有request并撤销formal response receiver capability，证明某个Audit scope不能再产生新formal audit asset的权威边界。边界后到达的物理bytes只能进入预注册的post-closure scope，不能回填旧Catalog。
_Avoid_: wall-clock wait、process idle、Audit Catalog commit

**Audit Quiescence Requirement Registry**:
按Audit Scope类型冻结必须收束的formal transactions、terminal规则、ABANDON条件、source-Journal slot/cardinality/order、receiver/capability边界及no-inflight条件的有稳定身份closed registry。它使Quiescence completeness不依赖日志扫描或人工判断。
_Avoid_: request inventory、shutdown checklist、runtime heuristic

**Pre-Freeze Audit Gate Root**:
Final Suite九slot vector中的稳定内容根，绑定一个pre-freeze Audit Scope的closure、Catalog、Readiness资格以及产生该资格的唯一Readiness run和infrastructure epoch。Compatibility不适用slot也保留受控N/A root，不能通过删slot改变Suite identity。
_Avoid_: raw audit path、optional report check、Official post-freeze audit

**Audit Not Ready**:
某个已完成formal decision的scope未满足下游Audit Readiness gate的状态。它取消该scope进入后续阶段的资格，但不追溯修改既有content、fitness、winner、certificate或Official result。
_Avoid_: formal decision failure、winner deletion、fitness invalidation

**Audit Dependency Closure Registry**:
冻结Audit Not Ready对upstream authority与downstream eligibility影响范围的Candidate-independent registry。它决定必须新建、允许复用与禁止复用的对象，防止运行后人工缩小重跑范围。
_Avoid_: operator rerun choice、best-effort recovery、result-aware exception

**Audit Dependency Closure Projection**:
某次具体Audit Not Ready按照冻结Dependency Closure Registry投影得到的不可变affected-object记录。它完整区分保留、失效、必须重建、允许复用与禁止复用的对象，并在replacement或下游终结前成为唯一恢复依据。
_Avoid_: operator recovery plan、manual impact list、partial rerun choice

**Active Audit Dependency Trigger**:
已经提交NOT_READY及Dependency Closure、但其全部replacement/revalidation要求尚未由权威Resolution证明满足的未解决审计触发器。历史NOT_READY只有在显式Resolved后才退出active set，不能因时间经过或局部修复自动消失。
_Avoid_: all historical failures、operator-open ticket、temporary warning

**Audit Dependency Closure Set**:
Suite-level Audit Gate Controller对全部Active Audit Dependency Triggers作出的不可变合并快照。它按冻结状态格计算当前整体保留、失效、重建与禁用范围，历史快照不被后续resolution覆盖。
_Avoid_: mutable recovery checklist、latest error list、manual union

**Audit Dependency Trigger Resolution**:
机械证明某个Active Audit Dependency Trigger要求的全部replacement、revalidation与禁止复用约束已经满足的唯一权威记录。只有它能把trigger移出active set；基础设施失败或局部完成不能隐式解决trigger。
_Avoid_: manual closure、partial repair、absence from latest error list

**Audit Gate Controller Infrastructure Epoch**:
由Formal Infrastructure Journal Registry授权、为Audit Dependency Resolution与Closure Set single-writer事务提供执行authority的一代Controller基础设施生命周期。只有完整恢复和preflight后的新epoch才能在resolution infrastructure exhaustion后开启新run。
_Avoid_: process restart、local retry counter、Suite Run

**Official Integrity Impact Set**:
Official开始后对全部已分类integrity triggers及其impact decisions形成的单调累积快照。`STOP_EXECUTION`是吸收态，已分类trigger不会因后续修复被移除。
_Avoid_: active error list、mutable incident status、Official score summary

**Official Report Status**:
对已完成Official run的append-only有效报告资格投影，可从formal-audit valid单调降级为audit-invalid或execution-integrity-invalid report-only，但不改写原completion event。
_Avoid_: Official terminal event、result score、mutable publication flag

**Stopped Official Reporting Authority**:
对因execution integrity failure停止、没有OFFICIAL_COCO_COMPLETED的Official run提供的唯一incomplete report-only资格。它不能加入Completed status chain、升级为valid或进入正式主结果。
_Avoid_: Completed Report Status、Official result、retry authorization

**Official Report Artifact**:
绑定确切Official terminal、current Reporting Authority、Impact Set、source manifest和materializer的不可变报告产物。只有commit时状态仍current才获得authority；从未获得authority的物理bytes是orphan，不是superseded artifact。
_Avoid_: mutable report file、raw result manifest、publication status flag

**Official Reporting Journal**:
由独立single-writer Reporting Controller维护，承载Reporting Authority、materialization、artifact、storage integrity、supersession与Current Report projection的append-only历史。Evaluation、materializer与exporter workers均不能直接授予report authority。
_Avoid_: Official Evaluation Journal、report directory、publication exporter log

**Official Report Content**:
只由canonical report bytes及其内容解释格式定义的纯内容身份。它不包含Official run、source result、Reporting Authority、Impact Set或execution provenance；相同content不代表相同report authority。
_Avoid_: Official Report Artifact、materialization transaction、current report

**Current Official Report Projection**:
某个Reporting Authority state对唯一current report artifact的不可变引用，不复制report bytes。若新状态尚无合法artifact，系统可以没有current report，但不能继续引用旧状态artifact。
_Avoid_: report content、mutable latest pointer、UI display state

**Cross-Root Input Registry**:
在新Formal Root birth时冻结、限定哪些root-independent immutable input classes可从历史存储重新采用的closed allowlist。未知class默认禁止，formal-derived Corpus、Memory和search资产不在其中。
_Avoid_: cache allowlist、Cross-Root winner preservation、runtime reuse heuristic

**Sealed Protected Official Input**:
在Formal Root birth时仅以sealed manifest、locator/capability及forbidden-before-authorization承诺冻结的C、Human/OpenReview或CoCo资产。其raw bytes只能在Official授权后、STARTED前验证。
_Avoid_: pre-freeze visible input、verified protected bytes、live Official input

**Protected Official Input Delivery Manifest**:
Protected preflight PASS后、Official STARTED前冻结的唯一允许交付集合，规定每个C/Human/CoCo asset的content facts、recipient component、capability、framing及用途。它授权可能的读取，但不替代实际protected access audit。
_Avoid_: protected preflight evidence、runtime read log、Official result input cache

**Official Reporting Hold**:
Reporting Controller在新的Official integrity delta被分类前提交的独占阻断状态。Hold期间不能commit report artifact、Current Report或publication export；全部pending deltas被Impact/Status消费后才能释放。
_Avoid_: scheduler pause、mutable lock、Impact Set event

**Formal Publication Export**:
对一个current report artifact进行正式主结果或明确diagnostic交付的status-bound事务。Commit前必须重验Reporting Authority、Impact Set、Hold与artifact bytes，Root terminal后不能新建formal export。
_Avoid_: file copy、Current Report projection、external post-terminal delivery

**Protocol Root Manifest**:
定义一次Formal Protocol全部Registry、schema、serializer、identity domain、state machine、dependency graph和capability policy的纯semantic内容根。独立Protocol Root Validation authority证明这些规则跨Registry一致；Formal Root必须同时绑定content与PASS。
_Avoid_: Formal Root、runtime manifest、cross-registry test result

**Research Validity Core v1**:
首次formal experiment的hard-blocking实施Profile，只包含会改变数据边界、Reviewer/Memory/Candidate、fitness/search/winner、Final Suite、protected Official inputs、Official evaluation语义的规则，以及最小可重现decision lineage、normal-path Evidence与fail-stop。
_Avoid_: minimal-quality experiment、no-audit baseline、full availability recovery

**Audit Hardening v2**:
在Research Validity Core闭环后实现的durability/availability Profile，包括verified mirrors、generation salvage、复杂in-place recovery、Reporting/Publication HA与post-terminal archive。它不得改变任何research-observable semantics。
_Avoid_: optional research rule、Core semantic downgrade、cross-Protocol result preservation

**Suite Disposition Reason Registry**:
冻结Suite Run可ABANDON原因、适用predicate、所需证据、root/dependency后果和primary-reason优先级的closed registry。它禁止自由文本或operator选择更方便的失败原因。
_Avoid_: error message、incident note、dynamic failure enum

**Suite Run**:
由authoritative birth event取得唯一ordinal的Final Suite sealing执行生命周期。每个run最终只能提交一个Final Suite或以受控原因ABANDON；controller crash只恢复同一run，不产生新birth。
_Avoid_: Final Suite content identity、process invocation、reseal retry counter

**Proposed Suite Composition**:
在Final Suite正式提交前，以固定role顺序的五个winner、role-run、Protocol Semantic Identity及相关Controller implementations定义的拟组装内容身份。它只解除Compatibility Plan与Final Suite commit的循环依赖，不表示Suite已经提交或获得Official CoCo授权。
_Avoid_: Final Suite Seal、Compatibility Certificate、deployment manifest

**Compatibility Not Required Decision**:
由冻结eligibility rule机械证明一个Proposed Suite Composition不存在跨Controller implementation preservation需求的权威N/A决定。它不是operator绕过Compatibility Plan的选项。
_Avoid_: no-op certificate、manual waiver、empty Compatibility Plan

**Compatibility Branch**:
一个Proposed Suite Composition必须且只能通过显式adoption event选择的权威one-of路径：Compatibility Not Required或唯一Compatibility Plan lifecycle。任一分支提交后都不能回退或切换到另一分支。
_Avoid_: optional compatibility、fallback branch、post-failure N/A

**Compatibility Plan**:
针对一个Proposed Suite Composition预先冻结的唯一target Controller implementation、有序comparison specifications、完整transitive dependency closure、Formal Observable Semantics与equality rules。其content identity先于逐项comparison identity生成；同一composition终身最多一个authoritatively committed Plan，且每个retained source都必须直接与该target比较。
_Avoid_: replay output、post-diff test plan、certificate decision

**Catalog Validation Record**:
Compatibility Plan提交前，对一个输入Catalog的identity、coverage、scope、registration与dependency closure完成closed验证并得到PASS或FAIL的唯一semantic terminal记录。只有PASS可进入Plan；同一Catalog、composition context与validator semantics不能从多个结果中择优。
_Avoid_: seal-time scan、post-Plan repair、informal integrity check

**Compatibility Comparison Item**:
Compatibility Plan中针对一个Formal Observable Semantics对象的不可变比较单元。它具有closed terminal disposition和自足的committed comparison evidence；普通mismatch、缺失或不可解析必须完成为item结果，不能伪装成fatal replay failure。
_Avoid_: partial diff、early-stop signal、ad-hoc regression check

**Compatibility Comparison Evidence**:
一次Compatibility comparison attempt提交的self-contained、decision-sufficient canonical证据，绑定明确的source/target implementation pair、完整comparison operands与canonical output。它在提交后允许恢复时无需重读source payload或重新提取输入，并在最终decision前只对冻结Trusted Execution Boundary可见。
_Avoid_: diff path、hash-only pointer、operator-visible comparison log

**Compatibility Access Audit**:
记录Compatibility semantic capability授权及实际使用结果的独立、非语义、append-only权威历史。每次logical access先授权、后读取并以完成、失败或无法确定暴露状态终结；已证实越权暴露与授权后状态不明是两个不同的integrity事实。
_Avoid_: semantic result log、operator debug trace、payload export

**Target Controller Implementation**:
Compatibility Plan冻结并由所有retained source直接证明等价的唯一behavior-relevant Controller实现。它绑定代码、依赖和协议行为，但不绑定PID、endpoint、GPU ordinal或某次进程生命周期。
_Avoid_: Controller process、Official Runtime Identity、deployment endpoint

**Compatibility Audit Export**:
Compatibility PASSED或FAILED及Audit Quiescence正式提交后，由唯一Export capability从受保护权威状态生成的single-file、self-contained不可变语义审计artifact。它具有有界materialization lifecycle，允许操作者查看完整终局结果，但不参与Compatibility decision或修改certificate。
_Avoid_: authoritative Compatibility Journal、predecision status、certificate input

**Export Semantic Input Manifest**:
在任何Export semantic read之前冻结的完整source-object集合及其canonical content identities。它约束Export最终使用的semantic set，但允许crash recovery在受审计lease中重复物理读取同一immutable object。
_Avoid_: Export output、runtime read log、mutable file list

**Export Candidate**:
Export execution根据冻结Semantic Input生成并durable保存、但尚未通过Access Range Validation授权的canonical artifact bytes。它可以具有预期Export内容身份，却不能进入Suite Catalog、向operator展示或代表authoritative Export。
_Avoid_: Compatibility Audit Export、Export READY output、temporary worker file

**Export Candidate Storage Integrity Evidence**:
在冻结storage recovery完成后，依据expected与observed locator、hash、length及schema证明一个已提交Export Candidate确实缺失或身份损坏的self-contained非语义证据。暂时不可读或backend故障不属于该证据。
_Avoid_: Compatibility payload、storage timeout、semantic candidate rebuild

**Export Promotion**:
Access Range PASS后，把同一份durable Export Candidate bytes登记为正式Compatibility Audit Export的非语义事务。它不能读取Compatibility semantic sources、修改candidate或产生新的Export内容身份。
_Avoid_: Export materialization、semantic replay、candidate rebuild

**Export Access Validation Run**:
在一个冻结Access Validation infrastructure epoch中，对同一Export transaction、terminal access range与lease history执行的一次有界验证生命周期。它必须唯一终结为PASS、FAIL、Infrastructure Exhausted或Abandoned；只有可信Evidence可以产生PASS或FAIL。
_Avoid_: Export execution、semantic retry、access log scan

**Export Access Validation Infrastructure Journal**:
Export Access validator与Access Audit backend epochs的single-writer权威历史。它约束run能在哪个基础设施世代执行，但不改变certificate或Export semantic input。
_Avoid_: Compatibility lifecycle journal、Access Audit Journal、validator process log

**Export Access Range Validation**:
对Export期间全部semantic access是否只来自唯一Export Capability、唯一active lease及冻结object scope作出的唯一PASS或FAIL结论。它要求完整可信range evidence，不能由Journal连续性或基础设施成功单独替代。
_Avoid_: connectivity check、Access Audit head、Export schema validation

**Compatibility Audit Quiescence**:
Compatibility certificate终结后关闭comparison/replay工作、收束其全部既有事务与late audit的唯一权威边界。该边界之后只允许一个scope-limited、read-only的Export capability，禁止产生新的Compatibility semantic decision。
_Avoid_: process idle、certificate terminal、Export completion

**Export Capability**:
Compatibility Audit Quiescence之后，单个Plan唯一允许读取冻结semantic objects以生成Audit Export的logical least-privilege capability。任一时刻只能存在一个authoritative active lease，且Export access range中不得混入任何其他semantic capability。
_Avoid_: operator filesystem access、comparison capability、runtime credential

**Embedding Padding Item**:
为保持冻结batch形状而由协议生成的非Candidate runtime item；它没有Memory query、budget、ledger、cache或qemb身份，其输出只用于cardinality、position、dtype、dimension与finite-value结构验证，不要求跨运行byte identity。
_Avoid_: dummy Memory query、synthetic Candidate request、padding qemb

**Post-Retrieval LLM Summarization**:
注册的LLM Summarizer在immutable Retrieval Result冻结后，对当前paper已经返回的Memory执行的显式query-focused condensation。它不是retrieval，不得改变召回集合、排序或top-k，也不得写回任何persistent Memory资产或silent fallback。
_Avoid_: Memory Query LLM、retrieval reranker、hidden summary service

**Retrieval Result Manifest**:
当前paper全部成功Memory delivery occurrences的不可变、有序快照，是post-retrieval Summarization唯一合法的Memory输入边界。它冻结实际query、结果、重复交付、顺序、内容与budget状态，Summary不得读取manifest之外的Memory。
_Avoid_: mutable retrieval cache、Summarizer-selected context、search trace only

**Canonical Summarizer Input Record**:
绑定Retrieval Result Manifest、Program与完整审计状态的Summarizer权威输入记录；其内容身份与唯一model-visible projection分离。它本身不得注入模型。
_Avoid_: CanonicalSummarizerInputProjection、manifest-as-prompt、free-form summary input

**Summarizer Visible Input Projection**:
从Canonical Summarizer Input Record确定性生成的唯一Summarizer可见输入，只呈现冻结Summary framing、实际成功检索queries及全部已交付Memory。它不得加入独立paper通道、External Evidence或未检索信息。
_Avoid_: paper-aware second reviewer、expanded query、audit envelope

**Canonical Reviewer Memory Context**:
Reviewer Memory输入的authoritative closed-schema record及其唯一Reviewer-visible projection。权威记录绑定Program/Candidate、ledger、budget、source、framing与内容身份；Reviewer只看到冻结framing、必要的非语义来源provenance和最终context text，Program不能自由拼接、删除或隐藏Memory。
_Avoid_: ad-hoc memory prompt、Program-selected final context、uncommitted delivery

**Structured Experience Recall**:
`Experience Learner v2`对全部合法交付的原始review内容按role subdimension、source Full Review Record与ledger occurrence进行的确定性组织。它保留重复交付，不生成lesson/fact、不改写或选择性删除review文本，也不产生跨paper learned state。
_Avoid_: KnowledgeItem LLM、generated lesson、semantic rewriting

**Structured View Item**:
Structured Experience Recall中证明某一已交付fragment、projection、Full Review section或overall assessment在确定性展示中恰好出现一次的audit-only视图身份。它不是Corpus、Projection、Fragment、retrieval result、delivery或新的Memory内容，也不产生任何检索或交付计费。
_Avoid_: retrieval item、new projection、billable Memory record

**Evolution Reflector**:
Evolution 阶段根据允许的外部反馈提出 MemoryProgram 子代修改的 LLM。每个 iteration 的 `rotating 5` 只生成当前 batch 聚合后的 role-specific subdimension scores、overall fitness、预注册 failure codes 和 compile/runtime/protocol error；不得进入 Candidate 排名，也不得与不同 rotating subset 的结果跨 Candidate 比较。Reflector 不接收逐 paper 信号、自由文本 rationale 或详细 evidence answer，也不参与运行期 Memory Query、Review 投影或数据库构建。
_Avoid_: Reviewer LLM、Memory Query LLM

**Specialist Review Bundle**:
同一论文的五份独立 Specialist Review 组成的规范输出。它在不运行 Meta-Reviewer 时仍是完整有效的系统产物。
_Avoid_: meta-review、拼接字符串

**Specialist-Local Fitness**:
只评价一个 Specialist Reviewer 及其 MemoryProgram 的角色内、reference-free、paper-grounded fitness。单篇 paper fitness 是该 role 所有 applicable subdimensions 的等权 macro-average，`N/A` 从分母排除；Candidate score 再对被评 B-local papers 等权平均，不设置人工 role-specific 权重。同一 role 的 no-memory、三种 Memory method、所有 Seed 和 Evolved Candidate 共享完全相同的 Evaluator Identity。它不得读取 Human/OpenReview review、CoCo reference opinion、CoCo taxonomy、CoCo Split/Classify 或 CoCo score。
_Avoid_: final CoCo score、Suite fitness

**Reference-Free Paper-Grounded Evaluator**:
Specialist Evolution 中使用的 role-specific evaluator。输入仅限当前 paper、合法 source anchors、冻结的 role rubric、Candidate Review 和与 benchmark 无关的冻结 evaluator configuration。`relevant/N/A`、correctness、importance、grounding、coverage 等判断只根据论文本身完成；Candidate 自报标签和分数不构成评价证据。23 项可执行契约维护在 `docs/protocol/role_specific_reference_free_evaluator_protocol_20260828.md`，其状态 frozen 前不得启动 formal run。
_Avoid_: Human-reference evaluator、CoCo proxy、统一跨角色 rubric

**Candidate Independence**:
每个 Candidate 从完全相同的 frozen paper input、source anchors、role rubric、paper order、A-state baseline 和 Evaluator Identity 开始，在独立 evaluator session 与独立 runtime state 中评价。一个 Candidate 的 Review、score、evaluator reasoning、Memory mutation、cache 和中间状态不得被另一个 Candidate 读取或继承。
_Avoid_: warm evaluator session、cross-candidate cache、shared mutable state

**Formal Controller Journal**:
每个role formal search的append-only权威事务历史。Attempt、parent selection、Candidate birth、dedup、static evaluation、admission、failure与completion均以domain-separated、hash-chained immutable events durable commit；checkpoint只是可由journal重建的projection/cache。Resume必须先验证journal，再恢复pool与RNG；不得根据日志、pool残留、临时文件或checkpoint猜测正式状态。具体contract见 `docs/protocol/formal_controller_journal_and_recovery_protocol_20260829.md`。
_Avoid_: checkpoint-as-authority、filesystem-state inference、parent reselection on resume

**Final Suite Seal**:
五个role winner均在各自journal中durable commit后，由独立suite-level controller按 `QUAL → CLAR → SIGN → ORIG → POL` 固定顺序提交Final Suite。只有Suite integrity通过后才允许授权Official CoCo；`OFFICIAL_COCO_STARTED`一经提交，Suite永久sealed，CoCo只作report-only评价且不得形成任何Evolution反馈。具体contract见 `docs/protocol/final_suite_sealing_and_official_coco_authorization_protocol_20260829.md`。
_Avoid_: pre-freeze C access、CoCo-guided reselection、post-CoCo evolution

**Eight-4090 Resource Topology**:
新formal绑定8×24GB RTX4090的双NUMA/PCIe-island拓扑。Evolution固定GPU0–3上的单Qwen TP4、GPU6 Embedding、GPU4/5禁用CoCo、GPU7 reserve；Official授权后GPU4/5才部署Split/Classify。整机在capacity gate/formal期间exclusive，发现未注册compute process只fail-fast不自动kill。`max-model-len`按全部formal request token materialization向上取整到8192倍数后冻结；TP4使用0.95显存利用率、每卡至少768MiB余量，prefix cache必须通过cold/warm稳定性gate。Evolution→Official强制重启全部服务并清空runtime cache。具体contract见 `docs/protocol/eight_rtx4090_resource_topology_protocol_20260829.md`。
_Avoid_: default TP8 across NUMA、live CoCo during Evolution、unregistered GPU process

**Canonical Evaluator Score**:
某一 `Candidate × paper × role` 唯一合法的正式 score。保存全部 raw attempts；基础设施错误、截断或非法 JSON/schema 最多在完全相同 generation identity 下用 fresh session retry 一次，第一个合法输出成为 canonical。两次均非法时 formal run fail-fast，非法输出不得记 `0`。
_Avoid_: judge voting、best-of-retries、invalid-as-zero

**Two-Phase Applicability Protocol**:
每个 Candidate 的独立 evaluator session 先在看不到 Candidate Review 的 Applicability phase 中，仅依据 paper、anchors 和 role rubric 输出各 subdimension 的 `applicable/N/A`；Scoring phase 随后才读取 Candidate Review，并只评分 applicable subdimensions。Applicability 不跨 Candidate 缓存；相同 paper×role 在不同 Candidate session 中不一致时触发 `APPLICABILITY_INCONSISTENCY` 并作为 protocol failure，禁止用不同分母继续比较。
_Avoid_: candidate-conditioned applicability、shared semantic applicability cache

**Paper Evaluation Context**:
为效率可通过无 LLM 的确定性流程预生成并在 Candidate 间共享的 candidate-independent、客观 paper-derived context，只允许 section/offset、source-anchor map、table/figure/equation 位置与 bibliography/appendix/artifact/checklist presence。只有可由明确结构事实确定的 applicable/N/A 才能共享；语义 applicability 必须由每个独立 evaluator session 判断。它不得包含 expected weakness、expected criticism、importance、ideal answer、潜在问题、evaluator judgment 或任何带答案的 paper audit。
_Avoid_: Paper Rubric Audit、synthetic reference answer、expected issue list

**Frozen External Evidence Pool**:
独立于 Candidate Memory、按 paper/source/cutoff/provenance/hash 冻结的只读外部证据池，v1 包含 `PRIOR_WORK`、`POLICY`、`ARTIFACT_LICENSE`。`PRIOR_WORK` 使用 frozen S2 temporal v2；`POLICY` 保存对应 venue/year/cutoff 的官方规则；`ARTIFACT_LICENSE` 保存论文明确引用的数据、代码、license、IRB/consent 外部页面。当前无 `PLAGIARISM_OVERLAP`。三条 mandatory lineage 获得完全相同的只读 API、调用预算与返回规则；每个 Candidate 可针对当前 paper 自主决定是否、何时及在预算内调用多少次，这一策略允许被 Evolution 优化。所有 query、调用次数与返回 evidence IDs 必须记录。读到的 canonical records 不得进行 lineage-specific 改写、摘要、重切片、重嵌入、重排序或派生存储，只允许用于当前 paper Review，结束后不保留派生状态。Evaluator 读取目标 paper 的全部 frozen records。只有证据超过预注册 context/token budget 时，才允许所有 lineage 与 Evaluator 共用同一 deterministic truncation/retrieval policy。

每个 target paper 具有唯一 `EvidenceSetManifest`，每个外部 source item 具有独立 `evidence_id`；S2 query/result-list 只作为 set/query identity，正式事实引用必须落到 item ID。每个 item 同时冻结审计权威 raw blob/hash 与无 LLM deterministic canonical text projection/hash。v1 API 只提供 availability、metadata listing、specific-item read 与 partition sequential read，不提供 semantic search/rerank。全部 target papers 的完整 delivered set 必须在 formal 前按冻结 Evaluator tokenizer 通过 token-budget gate；runtime 不得 adaptive truncation。

每个 Candidate×paper 使用相同 `max_api_calls/max_delivered_records/max_delivered_tokens`、fresh evidence session与独立audit log；重复delivery重复计费。Budget耗尽只停止新delivery，不直接处罚。Candidate-originated invalid request默认只使当前Candidate evaluation按冻结runtime/protocol failure规则失败；pool/integrity故障是infrastructure/role-level问题。Paper结束后只持久保存canonical Review与API audit，不保存evidence派生Memory/cache。Evaluator读取完整delivered set但看不到Candidate调用日志，因而只评价最终Review质量，不奖励工具调用。
_Avoid_: Candidate Memory、Evaluator-only prior-work oracle、live web search

**Evidence ID**:
对规范化 canonical JSON identity 计算 SHA256 得到的冻结证据身份。Identity 至少包含 `pool_version`、`partition`、`target_paper_id`、`canonical_source_identity`、`cutoff` 与 `content_sha256`。Manifest 同时保存 target family、source URL/ID、venue/year、retrieval/snapshot time、MIME/type、provenance 和 availability status。Evaluator 引用的 ID 必须属于当前 target paper 的 pool。
_Avoid_: raw string concatenation、unscoped citation ID

**Admissible External Evidence**:
模型的 parametric knowledge 可能影响内部推理，但不构成可采纳的正式外部证据。任何会影响 `QUAL-CMP`、`SIGN-SOT`、`ORIG-*`、`POL-PLG` 等正式 score 的论文外事实，都必须绑定 Frozen External Evidence Pool 中的具体 evidence ID；没有可引用 frozen evidence 时，不得仅凭模型记忆奖励或处罚 Candidate，相应外部判断为 `N/A` 或不计分。`POLICY` 只能证明规则内容，不能脱离 paper evidence 单独证明违规。
_Avoid_: unverifiable model-memory citation、policy-implies-violation

**Role-Local Winner Selection**:
Specialist Evolution 停止 mutation 后执行的同角色两阶段 winner protocol。Vector Search、LLM Summarizer、Experience Learner 是三个 mandatory initial search seeds：全部合法且Program identity互不重复后进入同一个 shared candidate pool，由 global softmax parent selection 共享每 role总计20个evolution attempts，不设置per-lineage attempt quota或lineage champion。Child在static evaluation前做strict Program dedup；duplicate mutation消耗attempt但不新增pool mass。Fitness严格先做paper内applicable-subdimension平均，再跨evaluable papers平均。结束后按strict Program identity防御性去重，再按canonical Decimal static-25 fitness降序、canonical candidate ID升序选择固定global top-3；finalists可全部来自同一seed lineage，第3/4名Decimal同分也不扩容。三名finalists在相同frozen B-local full pool、paper order、Evaluator Identity和独立session下重评，按canonical Decimal full-pool fitness与candidate ID产生唯一winner；static分数不参与最终tie-break。Parent softmax独立保留冻结的M★ binary64语义。Finalist failure不递补。
_Avoid_: B-suite、cross-role selection、per-lineage champion/quota、result-dependent finalist rule

**Subdimension Score**:
`0–4 + N/A` 的冻结角色内量表。每个 role/subdimension 分别定义评分锚点；`4` 表示 Candidate Review 对该 subdimension 中 material/important 内容的评价准确、具体且充分 grounded，不奖励穷举更多 criticism。相同数值不得跨 role 解释或比较。
_Avoid_: universal quality score、criticism count

**Validity and Penalty Rules**:
不构成统一 quality dimensions 的预注册规则：非法 schema/parse/protocol failure 进入冻结 retry/fail-fast；单个局部 fabricated material claim 或 invalid anchor 使受影响 subdimension 记 `0`；存在至少两个彼此独立的 material fabricated claims、fabrication 跨至少两个 subdimensions，或一个虚构核心证据被用于支撑多个判断时，触发 systematic hallucination 并使整篇 paper fitness 记 `0`。`material` 指足以影响 review judgment 的事实或证据错误。局部 role leakage 忽略，主体严重越界时整篇记 `0`；duplicate padding 先确定性去重再评分。Evaluator 必须输出固定 event code，全部阈值与 failure code 在 formal run 前冻结。
_Avoid_: shared weighted penalty dimension、post-hoc threshold

**Specialist Evolution**:
某一 Specialist Reviewer 的独立 MemoryProgram Evolution。五个角色保留独立single-writer controller、authoritative journal、lineage、RNG、candidate pool和Specialist-Local Fitness。五个role可以工程并行，paper workers也可按冻结capacity并行，但worker只生成immutable artifacts，controller按冻结逻辑顺序聚合提交；完成先后不得改变fitness、pool或RNG。
_Avoid_: five-review joint evolution、multi-role Candidate

**Review Suite**:
由五个 Specialist 在各自冻结 B-local evaluator 下独立选出的唯一 winner，按 `QUAL → CLAR → SIGN → ORIG → POL` 固定顺序直接组成的系统级部署单元。不存在跨角色二次选择或 coordinate search。
_Avoid_: 单个 Evolved Program、Meta-Reviewer

**Suite Selection**:
已拒绝的旧提案：在 Specialist Evolution 后跨角色挑选 Program 组合。Final Review Suite 由五个独立 winner 确定性组成，不设置二次 selection、coordinate search、Global CoCo selection 或 Human-reference selection。
_Avoid_: Specialist winner selection、deterministic Suite formation、META synthesis

**Evaluator Identity**:
唯一标识 evaluator 语义的冻结身份。当前不要求 Evaluator 与 Reviewer 使用不同模型，允许二者同为 `qwen3.8-27b`，formal gate 不得实现 same-model rejection；系统必须保留可替换的 evaluator provider/model 接口。五个 role 可以共享同一个 evaluator model/version，但各自冻结 role-specific rubric、prompt、schema、parser 和 aggregation。Formal evaluation 固定为 `temperature=0`、greedy（若 provider 支持）、固定 seed（若 provider 支持）和 fresh session。Identity 至少包含 provider、model/version、temperature、generation configuration、prompt SHA、schema/parser/aggregation version、source-anchor 规则与数据 manifest，并保存全部 raw outputs。未来改用独立 Evaluator 时必须产生新的 Identity，分数不得与旧实验混合。

Evaluator identity 分为 `EvaluatorSemanticIdentity` 与 `EvaluatorRuntimeIdentity`。前者冻结 model artifact、role rubric/prompt/schema/registry、tokenizer/chat template、完整 request JSON、input/context/evidence formatting 与 parser/validator/aggregator；后者冻结 backend/vLLM、dtype/quantization、TP、max model length、reasoning parser、prefix caching、max-num-seqs、CUDA/driver/GPU architecture。Base URL/port/PID/GPU ordinal 仅作 execution provenance。Qwen3.8-27B 的 FP8/BF16/其他量化不构成相对基础模型的独立 evaluator model，但任何 artifact/dtype/quantization/tokenizer/template 变化仍产生新 Evaluator Identity，分数不得混合。

**Evaluator Input Modality Identity**:
Evaluator 实际可观察输入的冻结身份。当前 formal identity 是 text-only Markdown，不包含 PDF page render 或 figure image pixels；即使底层模型支持多模态，也不得评分未提供的视觉属性。未来加入 PDF/image input 必须创建新的 protocol/input identity，分数不得与 text-only 实验混合。
_Avoid_: model capability、assumed visual access

**Evaluator Determinism Gate**:
每个 role 使用 5 个 A/B/C 之外的 benchmark-independent smoke cases，每个 case 重复 3 次。Raw 文本与非计分措辞可以不同，但 canonical parsed JSON、applicability map、`0–4` score vector 与 event-code set 必须完全一致；不设置数值容差。任一正式决策不一致时，该 Evaluator Identity 不得进入 formal run。
_Avoid_: score tolerance、post-hoc relaxation、benchmark-tuned smoke test

**Evaluator Capacity Gate**:
在benchmark-independent captured mixed workload上依次测试vLLM `max-num-seqs=1,2,4`，覆盖Reviewer、Evaluator、Reflector与compile-fix共享的单Qwen服务。Evaluator跨capacity的canonical decisions必须完全一致；其他temperature=0任务要求每个capacity内部达到预注册deterministic stability，跨capacity raw-byte identity只作额外证据。4通过则formal冻结为4，否则依次降为2、1。五role可逻辑并行，但共享一个global sequence budget；scheduler固定 `QUAL → CLAR → SIGN → ORIG → POL` round-robin和role内FIFO。Formal开始后修改capacity/scheduler/runtime即产生新Runtime Identity，不能混合结果。
_Avoid_: mid-run concurrency change、8192-to-16384 evaluator retry

**Canonical Evaluator Records**:
每个正式 `Candidate × paper × role` 分别保存 Candidate-blind `CanonicalApplicabilityRecord`、显式引用前者 canonical SHA 的 `CanonicalScoringRecord`，以及绑定两者并记录最终 score vector、paper fitness 与 validity result 的 `CanonicalPaperEvaluationRecord`。每个 record 通过冻结 serializer canonicalize 后计算 SHA256，并绑定 schema/parser/protocol/Evaluator Identity、input/evidence manifests 与全部 raw attempt provenance。严格 closed-schema parser 不修复、不补字段、不 coercion；第一个完全合法 raw object 成为 canonical，最多一次 Q29 fresh-session retry。
_Avoid_: final-score-only artifact、best-effort parser、Candidate-visible applicability

**Canonical Judgment Object**:
Candidate-independent underlying issue identity。`judgment_id` 是受控 canonical object（role、closed-registry object class/requirement key、deterministic target entity IDs、governing obligation IDs）的 SHA256；不得包含自由文本、subdimension、event code、score、Candidate/review/evaluation identity。Supporting paper anchors/external evidence 不进入 `judgment_id`，但必须 scope-validation、排序、去重后进入 event identity。具体 Candidate evaluation 的 `event_id` 另行绑定 Candidate、review、evaluation identity、event code 与 supporting evidence。POL requirement key 由机器化 ownership resolver 映射到唯一 primary subdimension。
_Avoid_: free-text judgment ID、event hash as issue identity、Evaluator-selected POL ownership

**Applicability Decision Identity**:
不同 fresh session 的完整 ApplicabilityRecord SHA 可以不同；跨 Candidate 比较使用独立 `applicability_decision_sha256`，只覆盖 role/paper/protocol、固定顺序 applicability vector、N/A primary/detail reason codes 与允许的 applicability anchor/entity IDs。它排除 session/attempt/time/Candidate/review/runtime metadata，也排除 scoring-level evidence limitations。任何同一 paper×role decision SHA 不一致均触发 `APPLICABILITY_INCONSISTENCY`。
_Avoid_: record-SHA equality、evidence limitation in applicability hash

**Evidence Limitation**:
当 frozen external evidence 只阻断 subdimension 中的一部分论文外判断而 PAPER 部分仍可评分时，subdimension 保持 applicable；被排除的 global SOTA、external completeness、global novelty 等 judgment 在 ScoringRecord 中单独记录。Evidence limitation 不是 N/A、Candidate event 或 penalty，不进入 applicability identity。
_Avoid_: partial evidence gap as N/A、evidence absence penalty

**Evidence Support Identity**:
Candidate-independent `evidence_availability_sha256` 冻结 target paper/cutoff/pool/role/requirement 下客观 evidence 状态；Candidate-specific `evidence_limitation_sha256` 只记录该 Candidate 实际 external judgments 中被排除的部分。每个 `role + requirement_key` 由 closed registry 绑定 `PAPER_REQUIRED / EXTERNAL_REQUIRED / PAPER_AND_EXTERNAL_REQUIRED / PAPER_OR_EXTERNAL`。非法 ID 被移出 admissible support set但不自动证明 fabrication；仅当 CORE judgment 的 support policy 因此不满足时确定性归零，SUPPORTING judgment 不新增独立 penalty。
_Avoid_: evidence limitation in applicability hash、invalid ID as automatic fabrication

**Canonical Evidence Object**:
用于识别同一 fabricated core evidence 被多个 judgments 复用的 Candidate-independent identity，只由 role、closed evidence-object class、controlled fact key 与 deterministic target entity IDs 构成。无法唯一规范化时不得猜测共享 identity；仍可通过多个独立 fabrication 或跨 subdimension fabrication 触发 systematic hallucination。
_Avoid_: free-text evidence identity、forced semantic merge

**Machine Registry Identity**:
所有正式 registry key 使用 `<role>:<registry_version>:<key>`，不同 role 不共享短 key identity，也不隐式继承。Registry 只能依据冻结 rubrics/master protocol 与 benchmark-independent synthetic/smoke cases构建；无 `OTHER/MISC` 或自由文本 fallback。Entity 只在 manifest/anchor identity、同 type exact normalized identifier 或确定性结构规则证明相同时合并，禁止 embedding/LLM/parametric merge。Registry artifact 与完整 positive/rejection/hash/ownership/support/systematic-hallucination test vectors 一并冻结并进入 Evaluator Identity。
_Avoid_: short-key collision、runtime enum expansion、semantic entity merge

**B-local**:
B zone 中专用于 Specialist-Local Evolution 的冻结 paper/family pool。五个 role 共享同一个 frozen full paper/family pool，但按 Candidate-blind eligibility audit 与预注册 seed/hash selection rule 分别冻结 role-specific static-25 membership 和 rotating schedule；禁止人工 cherry-picking。同一 role 的全部 lineage、method、seed 和 Candidate 使用完全相同的集合与顺序。`static 25` aggregate fitness 是唯一 search fitness，用于 parent/promotion；每 iteration 的 `rotating 5` 仅提供 batch-aggregated Reflector feedback，不进入 Candidate 排名。若 rotating batch 无 evaluable paper，记录 `NO_EVALUABLE_ROTATING_PAPERS`，不生成 fitness、不换 paper、不处罚 Candidate。最终 role finalists 都在统一 frozen full pool 上重评。
_Avoid_: static 25、单次 evaluation batch

**Role-Specific Evaluable-Paper Threshold**:
在查看任何 Candidate 结果前，依据 frozen B-local manifest 与 Candidate-blind、Human/CoCo-free eligibility audit，为每个 role 分别预注册 `min_static_evaluable` 与 `min_full_pool_evaluable` 的明确整数、依据、manifest SHA 和 rubric/protocol version。Audit 只判断 role-level evaluability，不产生 expected weakness、ideal criticism、评分答案或 Candidate-specific information，也不缓存为正式 applicability map。某 paper 全部 subdimensions 为 `N/A` 时，其 `paper_fitness=N/A`，并对所有 Candidate 一致地从 paper-level 平均分母排除；static 或 full-pool 实际数量低于对应 role 阈值时 fail-fast。不得使用跨 role 通用比例或固定“20/25”。
_Avoid_: universal threshold、N/A-as-zero、post-result threshold

**B-suite**:
已取消的 B zone 实验阶段。原分配给 B-suite 的 papers/families 保持 reserved，不进入 B-local、C 或当前主实验的任何 fitness、selection 与评价。
_Avoid_: C Holdout、Official CoCo set、扩大 B-local

**Official CoCo Evaluation**:
Final Review Suite、模型、prompt、权重和全部系统配置完全冻结后，在 untouched C 上运行的独立外部评价。只有该阶段可以读取 Human Review/reference opinions。结果只用于最终报告，不得反馈到 fitness、selection、mutation、early stopping、调权、winner selection 或系统修改。
_Avoid_: evolution fitness、CoCo proxy、B-suite selection、Global CoCo objective

**Official Execution Block**:
Official CoCo已获不可撤销的历史授权、但在正式开始前发现Audit Gate authority失效时追加的执行禁止状态。它不改写旧授权，只阻止该授权继续启动Official evaluation。
_Avoid_: authorization deletion、temporary service pause、winner reselection

**Official Integrity Impact Classification**:
Official开始后，由预注册且result-independent的Registry机械判断某项完整性故障仅破坏audit authority，还是同时破坏Official execution semantics的closed分类。它决定原run能否继续为audit-invalid report，不能依据CoCo结果优劣选择。
_Avoid_: operator severity judgment、result-quality triage、retry decision

**Official Completion Status**:
Official run完成时对其当前formal-audit资格的closed标签：Formal Audit Valid或Audit Invalid Report Only。Execution-integrity受损的run不形成completion，而以停止失败终结；完成后新发现的authority failure只能通过追加projection降低有效发布状态，不能改写历史completion。
_Avoid_: mutable completed result、unlabelled Official output、post-hoc score deletion

**Re-sealed Final Suite**:
Winner composition不变但Audit Gate/seal authority更新后形成的新Suite run与Final Suite identity。它引用旧Suite和新gate chain；旧Suite保持历史sealed且永久失去execution eligibility。
_Avoid_: reopened Suite、modified Final Suite、new winner composition

**Suite Run**:
Suite sealing controller分配的不可变execution-lineage birth identity。Initial run显式无前驱；Re-seal run必须引用前一Suite Run与触发原因，crash recovery继续同一birth ordinal而不创建平行run。
_Avoid_: proposed composition、Final Suite content、filesystem run directory

**META-OFF Export**:
将 Specialist Review Bundle 按冻结角色顺序确定性拼接成单个文本的导出形式，不调用 LLM，也不替代 Bundle 作为权威产物。
_Avoid_: meta-review、LLM synthesis

**META-LLM Export**:
显式启用 Meta-Reviewer，对 Specialist Review Bundle 进行 LLM 重组织后得到的实验性导出形式。
_Avoid_: 默认最终输出

**Human Reference Boundary**:
真人 review、OpenReview reference opinion、gold opinion、Benchmark taxonomy 与 judge 信息不得进入 Specialist Evolution 的任何环节，包括 evaluator-only fitness、selection、mutation feedback、early stopping、调权和 winner selection。它们只允许在 Final Review Suite 完全冻结后，由 untouched C 上的 Official CoCo Evaluation 读取；查看结果后不得继续修改、重选或进化当前系统。

## Evolution Information-Flow Invariant

> Candidate 之间独立；Evaluator 条件固定；Reflector 只接收聚合反馈；Human/CoCo 信息在 Evolution 全程不可见。
