# Final Suite Sealing and Official CoCo Authorization Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen semantic contract（2026-08-30），Q142、Q145、Q150与Q269、Q274、Q280–Q527已接受。本文冻结suite-level controller、五role winner组装、Final Suite sealing、C/Human-reference访问授权、Official CoCo按授权部署与report-only边界。CORE_V1/HARDENING_V2实施阻塞范围见 `research_validity_core_v1_and_audit_hardening_v2_profile_20260830.md`；任何Profile都不得在授权前访问C或Human/CoCo references。

## 1. Independent Suite-Level Authority

五个role各自在独立authoritative journal中完成Evolution与winner selection，不建立跨role event全序。Final Suite由独立suite-level controller与append-only suite journal管理；它只能读取已经durable commit且通过integrity验证的role outputs，不得写回role journal、重排candidate或发起新的Evolution。

Suite controller必须验证：

- `QUAL / CLAR / SIGN / ORIG / POL`五个role均存在唯一合法 `ROLE_WINNER_COMMITTED`；
- 每个role journal hash chain、run/controller/Evaluator/Program/Candidate identities完整；
- 每个winner来自其已提交global top-3与full-pool records；
- 五个winner按冻结 `QUAL → CLAR → SIGN → ORIG → POL` 顺序组成Suite；
- 不存在B-suite、cross-role score、coordinate search或Global CoCo selection。

## 2. Frozen Suite Events

Role journals正式包含：

- `FINALIST_SET_COMMITTED`；
- `FULL_POOL_EVALUATION_COMMITTED`；
- `ROLE_WINNER_COMMITTED`；
- `ROLE_ARTIFACT_CATALOG_COMMITTED`；
- `ROLE_SEARCH_COMPLETED`。

Suite journal正式包含：

- `SUITE_ARTIFACT_CATALOG_COMMITTED`；
- `AUDIT_SCOPE_QUIESCENCE_COMMITTED`；
- `AUDIT_ASSET_CATALOG_COMMITTED`；
- `AUDIT_READINESS_STARTED`；
- `AUDIT_READINESS_EVIDENCE_COMMITTED`；
- `AUDIT_READINESS_INFRASTRUCTURE_FAILED`；
- `AUDIT_READINESS_ABANDONED`；
- `AUDIT_READINESS_INFRASTRUCTURE_PAUSED`；
- `AUDIT_READINESS_INFRASTRUCTURE_EXHAUSTED`；
- `AUDIT_READINESS_RUN_ABANDONED`；
- `AUDIT_READINESS_COMMITTED`；
- `AUDIT_READINESS_RESULT_REFERENCED`；
- scope-specific `CORPUS_BUILD_AUDIT_NOT_READY / ROLE_RUN_AUDIT_NOT_READY / EMBEDDING_SCHEDULER_AUDIT_NOT_READY / COMPATIBILITY_REPLAY_AUDIT_NOT_READY / COMPATIBILITY_EXPORT_AUDIT_NOT_READY / OFFICIAL_POST_FREEZE_AUDIT_NOT_READY`；
- `CATALOG_VALIDATION_STARTED`；
- `CATALOG_VALIDATION_EVIDENCE_COMMITTED`；
- `CATALOG_VALIDATION_INFRASTRUCTURE_FAILED`；
- `CATALOG_VALIDATION_ABANDONED`；
- `CATALOG_VALIDATION_INFRASTRUCTURE_PAUSED`；
- `CATALOG_VALIDATION_INFRASTRUCTURE_EXHAUSTED`；
- `CATALOG_VALIDATION_COMMITTED`；
- `COMPATIBILITY_PLAN_COMMITTED`；
- `COMPATIBILITY_REPLAY_STARTED`；
- `COMPARISON_ATTEMPT_STARTED`；
- `COMPARISON_EVIDENCE_COMMITTED`；
- `COMPATIBILITY_REPLAY_COMPLETED`；
- `COMPATIBILITY_SEMANTIC_EXPOSURE_VIOLATION`；
- `COMPATIBILITY_ACCESS_INTEGRITY_FAILED`；
- `COMPATIBILITY_REPLAY_ABORTED_INTEGRITY_FAILURE`；
- `COMPATIBILITY_CERTIFICATE_PASSED`或`COMPATIBILITY_CERTIFICATE_FAILED`；
- `COMPATIBILITY_AUDIT_QUIESCENCE_COMMITTED`；
- `COMPATIBILITY_AUDIT_EXPORT_INPUT_COMMITTED`；
- `EXPORT_CANDIDATE_COMMITTED`；
- `EXPORT_CANDIDATE_STORAGE_INTEGRITY_FAILED`；
- `EXPORT_ACCESS_VALIDATION_STARTED`；
- `EXPORT_ACCESS_VALIDATION_EVIDENCE_COMMITTED`；
- `EXPORT_ACCESS_VALIDATION_INFRASTRUCTURE_FAILED`；
- `EXPORT_ACCESS_VALIDATION_ABANDONED`；
- `EXPORT_ACCESS_VALIDATION_INFRASTRUCTURE_PAUSED`；
- `EXPORT_ACCESS_VALIDATION_INFRASTRUCTURE_EXHAUSTED`；
- `EXPORT_ACCESS_VALIDATION_RUN_ABANDONED`；
- `EXPORT_ACCESS_RANGE_VALIDATION_COMMITTED`；
- `EXPORT_PROMOTION_STARTED`；
- `EXPORT_PROMOTION_STORAGE_FAILED`；
- `EXPORT_PROMOTION_ABANDONED`；
- `EXPORT_PROMOTION_COMMITTED`；
- `COMPATIBILITY_AUDIT_EXPORT_STARTED`；
- `COMPATIBILITY_AUDIT_EXPORT_MATERIALIZATION_FAILED`；
- `COMPATIBILITY_AUDIT_EXPORT_RECOVERED`；
- `COMPATIBILITY_AUDIT_EXPORT_FAILED_TERMINAL`；
- `COMPATIBILITY_AUDIT_EXPORT_COMMITTED`；
- `COMPATIBILITY_NOT_REQUIRED_COMMITTED`；
- `COMPATIBILITY_BRANCH_COMMITTED`；
- `AUDIT_DEPENDENCY_CLOSURE_COMMITTED`；
- `AUDIT_DEPENDENCY_TRIGGER_RESOLVED`；
- `AUDIT_DEPENDENCY_CLOSURE_SET_COMMITTED`；
- `FINAL_SUITE_COMMITTED`；
- `OFFICIAL_COCO_AUTHORIZED`；
- `OFFICIAL_EXECUTION_BLOCKED_AUDIT_AUTHORITY_INVALID`；
- `OFFICIAL_SERVICES_STOPPED_AUDIT_BLOCK`；
- `OFFICIAL_REQUEST_ABANDONED_AUDIT_BLOCK`；
- `OFFICIAL_COCO_STARTED`；
- `OFFICIAL_INTEGRITY_IMPACT_CLASSIFIED`；
- `OFFICIAL_POST_FREEZE_AUDIT_AUTHORITY_INVALID`；
- `OFFICIAL_EVALUATION_STOPPED_INTEGRITY_FAILURE`；
- `OFFICIAL_COCO_COMPLETED`。

`AUDIT_READINESS_INFRASTRUCTURE_EPOCH_COMMITTED`与`EXPORT_ACCESS_VALIDATION_INFRASTRUCTURE_EPOCH_COMMITTED`分别属于其独立single-writer Infrastructure Journal。Readiness epoch、run admission、attempt、evidence及run terminal全部由Readiness Infrastructure Journal权威提交；Suite/Scope journals只允许提交引用确切Infrastructure event SHA的幂等workflow projection，不复制或重新授权这些events，也不建立跨scope decision顺序。

全部events使用domain-separated canonical JSON、previous-event SHA、immutable durable commit与可观察recovery规则。Formal期间不得临时扩enum或修改event语义。

Role Catalog在全部role artifacts（含winner）后、`ROLE_SEARCH_COMPLETED`前提交；Suite Catalog在composition与Compatibility predecessor均准备后、`FINAL_SUITE_COMMITTED`前提交。External formal artifact必须在source event前先durable写入并验证SHA/length，再由source event closed ordered `artifact_outputs`登记identity、SHA、length/type、schema/serializer、dependencies及scope；冲突registration是integrity failure。Catalog只能消费这些vectors，不得scan/glob/mtime/enumerate。Identity为`artentry_ / rolecat_ / suitecat_ + full domain-separated SHA256`；Role entries按source Journal位置后artifact ID排序，covered head固定为Catalog commit前直接predecessor，content排除Catalog commit/completion event SHA。

Non-decision raw audits保留occurrences。每个auditscope绑定ordered Journals；auditquiesce绑定各slot pre-quiescence heads及closed transaction/receiver/capability/no-inflight proof。Scope Registry冻结唯一quiescence commit slot；Quiescence event只进入该slot，Catalog range对该slot包含event、其他slots止于各自pre-head。Quiescence Requirement Registry使用稳定`auditquiescereg_` identity机械解释完整closure，并由auditscope/auditquiesce共同绑定；unknown formal transaction使gate失败。Event前已durable audit bytes只能从同一closure state重算expected identities并逐SHA/length验证复用，禁止目录扫描吸收orphan；event已commit则不重跑closure。Compatquiesce显式绑定Replay auditquiesce。

Audit Catalog按slot→journal-local sequence→occurrence排序；ranges为start predecessor exclusive到terminal inclusive，起点显式genesis/null。独立Readiness Infrastructure Journal以auditreadepoch管理连续无空洞epochs，并权威管理run/attempt/evidence/terminal；scope journals仅引用authoritative event。Auditreadrun每epoch最多一run、primary+one，并恰一PASS/FAIL/EXHAUSTED/ABANDONED terminal；只有EXHAUSTED可在保持validator semantic/implementation及verification/storage policy identity完全相同的新epoch successor，semantic FAIL不可转PASS。Infrastructure Journal损坏以首个非法event为cutoff，受影响immutable Catalog在新Journal identity下重验，不改写底层formal result。

Suite Catalog的cross-Journal dependency roots采用固定typed vector：QUAL、CLAR、SIGN、ORIG、POL五个Role Catalog roots，随后`COMPATIBILITY_LIFECYCLE_ROOT`与`COMPATIBILITY_ACCESS_AUDIT_ROOT`。Role root绑定rolecat及commit event SHA；无需Compatibility时必须由`compatna_`与`COMPATIBILITY_NOT_REQUIRED_COMMITTED`机械证明五winner不存在跨implementation preservation需求，后两root再使用显式N/A/null且vector长度不变；否则必须进入Compatibility Plan。需要Compatibility时分别绑定PASSED lifecycle head/certificate event及decision boundary audit head。不同Journal numeric sequences不得混排或flatten；Suite-local external artifacts只按Suite Journal本地position登记。未知/缺失/foreign entry、dependency冲突/缺失/cycle或coverage不一致均禁止commit；Official outputs不得回写。

Catalog-first recovery要求Catalog已commit而completion缺失时只验证原Catalog、coverage/head与scope closure并确定性补completion，不重建、重扫或产生v2。Completion引用不存在、未在连续authoritative chain、SHA/structure/scope非法的Catalog属于Journal integrity failure，不得事后补造。Catalog commit后covered scope永久封闭；之后出现本应入scope的新formal artifact使原completion路径失效，不能扩展或覆盖Catalog。失败且无合法winner/completion的role-run不伪造成功Catalog。

Compatibility lifecycle只在拟混合相同Protocol Semantic Identity下的不同Controller implementation identities时启用。`proposed_suite_composition_id`顶层绑定Protocol Semantic Identity，并以固定五role vector逐项绑定role、winner event、role-run、Candidate、Program及产生winner的Controller implementation；distinct retained source Controller集合从vector派生。Composition不表示Final Suite或Official authority。Plan必须唯一冻结一个已通过gate的`target_controller_implementation_identity`，对每个distinct retained source执行直接`source → target` comparison；禁止A≈B≈T传递、历史其他composition certificate替代或多target graph。Target不要求已产生当前winner，但必须是后续replacement role-runs和该Suite采用的统一execution semantics；无法唯一声明target时五role统一重跑。

Plan前每个input Catalog执行bounded Validation lifecycle。每catvalatt恰一EVIDENCE_COMMITTED/INFRASTRUCTURE_FAILED/ABANDONED；logical PAUSED后至多一个retry，第二infra failure为EXHAUSTED并seal当前validation run，禁止第三attempt/局部新ID，重新执行只能新top-level Suite/Compatibility validation run。EXHAUSTED不是semantic FAIL且禁止Plan。`catvalevid_`绑定attempt、Catalog/context、完整Registry-ordered results/evidence、observed hashes/lengths及validator identities，不绑定catval；Evidence后只补唯一PASS/FAIL terminal。Only PASS可Plan，同一composition仍最多一个Plan。

STARTED后plan不可变。Item terminal闭集为MATCH/SEMANTIC_MISMATCH/ARTIFACT_UNAVAILABLE/DECLARED_HASH_MISMATCH/PAYLOAD_PARSE_INVALID/COMPARISON_EVIDENCE_UNTRUSTWORTHY，均不是early-stop。Fatal仅限Journal chain、Plan/compitem identity、冲突terminal、Catalog/Plan自身integrity或唯一state无法确定。每item固定primary加一次retry；`compatatt_`绑定compitem、attempt kind/index、catalog inputs及tool/gate。事务为ATTEMPT_STARTED→EVIDENCE_COMMITTED→item terminal。Evidence identity为`compev_ + full domain-separated SHA256`，绑定compatatt、compitem、明确source/target pair、observed authoritative hashes/lengths、parse dispositions、tool/gate identity、equality rule实际消费的完整canonical operands及canonical comparison output；恢复不得重开source payload、mutable blob或重做input extraction，仅SHA不足。Preformal冻结最大Evidence event bytes及serialization/strict parse/hash/atomic durability/crash-recovery gate，formal不得外置、压缩或拆分。

正常路径严格为PLAN→STARTED→全部预注册items terminal→COMPLETED→恰一PASSED/FAILED。`COMPATIBILITY_REPLAY_COMPLETED`按Plan顺序绑定全部且仅全部compitem IDs、terminal dispositions与result SHAs；PASSED要求全部MATCH，normal FAILED要求至少一个non-MATCH。Fatal路径严格为PLAN→STARTED→fatal integrity violation→ABORTED_INTEGRITY_FAILURE→FAILED，不提交COMPLETED。COMPLETED与ABORTED、PASSED与FAILED严格互斥；ABORTED后禁止创建新attempt/evidence/item terminal，冲突或错误predecessor均为Suite Journal integrity failure。

Predecision authoritative lifecycle Journal、compev、semantic item records与diff只存于TEB保护目录。Single-writer Controller是唯一授权者/Journal writer；workers只获当前item least-privilege capability。Operator closed projection仅含counts、nonsemantic stage、liveness与infrastructure code。每个semantic logical access使用`semacc_`并在独立append-only Access Audit Journal中提交authorization与唯一terminal；identity绑定accessor/capability/Plan/item-object scope/operation/policy/limits且无semantic payload。Unrecorded success、audit gap/rollback、sequence conflict、scope mismatch或unregistered successful access触发EXPOSURE→ABORTED→FAILED。

每个semantic logical access必须先AUTHORIZATION，read后恰一COMPLETED/FAILED/STATE_UNRESOLVED。Confirmed unauthorized exposure提交`COMPATIBILITY_SEMANTIC_EXPOSURE_VIOLATION`；authorized后状态不明提交`COMPATIBILITY_ACCESS_INTEGRITY_FAILED`，不得把不确定虚报为泄漏。Predecision两者均ABORTED→FAILED但reason分离；post-decision不改certificate，只使Export失败。

PASSED与FAILED后提交唯一compatquiesce并封闭Replay scope。Export Input在semantic read/STARTED前self-contained提交，并绑定Replay auditready terminal及PASS或可信FAIL/AUDIT_NOT_READY。可信FAIL在certificate、quiescence、semantic objects与security boundary完整时仍执行mandatory Export；PAUSED/EXHAUSTED无可信terminal时不得越过gate，fatal exposure/access或source损坏进入terminal failure。

Export Candidate先写入TEB protected immutable storage并由`EXPORT_CANDIDATE_COMMITTED`绑定content facts、locator policy和expected compatexport；不登记、不向operator暴露。Candidate前semantic exec primary+one；commit后不得重读source、重建或产生第二candidate。独立Export Access Infrastructure Journal以exportaccessepoch管理run；每run恰一PASS/FAIL、EXHAUSTED或ABANDONED terminal，PAUSED非terminal，只有EXHAUSTED可在新epoch successor。PASS后promotion通过`exportpromatt_` primary+one只注册同一candidate bytes；attempt绑定candidate/Access PASS/storage policy/implementation并恰一COMMITTED/STORAGE_FAILED/ABANDONED terminal。Formal bytes已durable但event缺失时恢复同attempt且不消耗retry；失败不得返回semantic阶段。

Export identity分transaction、execution、candidate、output与pure-content层；transaction绑定exportinput。Exportout closed outcomes为READY/CANONICAL_VALIDATION_FAILED/STORAGE_IO_FAILED/CANDIDATE_STORAGE_INTEGRITY_FAILED/SEMANTIC_INPUT_MISMATCH/ACCESS_INTEGRITY_FAILED/IMPLEMENTATION_VALIDITY_FAILED/PROTOCOL_VALIDITY_FAILED。Candidate post-commit missing或length/hash/schema mismatch使用`EXPORT_CANDIDATE_STORAGE_INTEGRITY_FAILED`并直接terminal，不得promotion retry或semantic rebuild；暂时读取不可用仍是STORAGE_IO_FAILED。Failure Registry冻结stage、operands、evidence、retry与consequence，raw exception不入identity，unknown不进OTHER。仅storage通常可rebuild，其余确定性/semantic/integrity failure直接terminal。COMMITTED引用唯一READY，FAILED_TERMINAL引用完整chain。

每slot `auditgateroot_`绑定type/applicability及适用的scope/quiesce/Catalog/auditready/events；applicable root还显式绑定产生结果的auditreadrun、auditreadepoch与epoch event SHA并可唯一追溯Evidence。Compatibility N/A绑定compatna authoritative event并保留closed nulls。`suiteauditgates_`是固定九root self-contained vector，Final Suite内嵌并重新验证。每次NOT_READY先生成`auditdepclosure_`并提交`AUDIT_DEPENDENCY_CLOSURE_COMMITTED`，机械保存Registry/manifest graph投影出的preserved、invalidated、must-rebuild、reusable和forbidden vectors；replacement或下游terminal不得先行，operator不得修改，unknown edge为Protocol failure。Official NOT READY只影响report completeness。Operator只读Export。

Q404要求每个scope对权威Readiness terminal提交唯一`auditreadproj_ / AUDIT_READINESS_RESULT_REFERENCED`。PASS/FAIL绑定真实auditready及event；EXHAUSTED/ABANDONED使用closed null。只有当前被选为gate authority的PASS projection可进入Audit Gate Root，历史EXHAUSTED projection永久保留。Q405的Formal Infrastructure Journal Registry是Readiness/Export Access Journal generation的preformal root-of-trust；新generation须由Registry admission/replacement授权，trusted boundary为旧generation最后合法event，Registry自身损坏终止当前formal root而不能递归换Registry混用旧state。Q406要求全部pre-freeze scopes及预注册Official scope共享同一immutable auditquiescereg。

Q407–Q409冻结Export closure。Access run ABANDON必须先停止dispatch并逐attempt终结，再提交run ABANDONED，且仅使用closed reason。单一`EXPORT_PROMOTION_COMMITTED`同时完成attempt success、formal artifact首次authority与artifact_outputs registration；Export lifecycle completion只引用它。`exportcandintegrity_`只在冻结storage recovery后证明已提交candidate missing或identity/length/schema/locator损坏，不包含semantic payload；成立后禁止重读Compatibility sources。

Q410的compatnareg仅允许当前composition在五winner Protocol相同、Controller implementation唯一、Suite target和Official component相同、无foreign winner、无待终结或被绕过Compatibility lifecycle时提交composition-specific compatna。Q412指定suite-level Audit Gate Controller为AUDIT_DEPENDENCY_CLOSURE唯一single writer；Readiness Infrastructure只提供事实，不决定winner保留、role重跑、Suite、Export或Official后果。

Q411的gate-authority loss不原地撤销已提交event。Suite commit前阻止seal；Suite commit后、Official authorization前必须以新suite-run/seal重新绑定新gate；Authorization后、STARTED前追加`OFFICIAL_EXECUTION_BLOCKED_AUDIT_AUTHORITY_INVALID`并禁止旧authorization启动，重验后仍使用新Suite/authorization chain；STARTED后只记录post-freeze audit failure，保留不可反馈的raw Official report，但禁止重封、重选、重跑或返回Evolution。任何阶段不得读取或利用C/Human/CoCo结果决定修复。

Q413要求Audit Gate Controller把全部`AUDIT_READINESS_RESULT_REFERENCED`写入Suite Journal closed namespace；sealed source Journals不重开。Q414的Formal Infrastructure Registry Journal独立承载finfrareg、infrajgen及ADMITTED/REPLACED/SEALED；Registry不是自身managed kind，replacement以旧generation最后合法event而非invalid tail为可信边界。Q415要求每Catalog的Readiness successor chain线性且只有EXHAUSTED可产生唯一successor，Gate Root绑定完整chain及唯一terminal PASS。

Q416规定每个composition在N/A event与唯一Plan lifecycle之间exactly-one且永久互斥。Q417规定AUTHORIZED后STARTED前发生gate invalid时先BLOCK dispatch；Services READY则stop/clear并提交`OFFICIAL_SERVICES_STOPPED_AUDIT_BLOCK`，新authorization不得复用旧READY。Q418使用预注册Official Integrity Impact Registry机械区分AUDIT_AUTHORITY_ONLY与OFFICIAL_EXECUTION_INTEGRITY_AFFECTED；前者可按原协议完成但仅作audit-invalid report，后者立即停止且不得补跑、拼接或恢复。

Q419为每个active NOT_READY保留独立closure，并以`auditdepclosureset_`生成immutable active-set snapshots；只有`AUDIT_DEPENDENCY_TRIGGER_RESOLVED`能将trigger移出active set，合并状态优先级固定为forbidden、must-rebuild、invalidated、reusable、preserved。Q420允许winner provenance完全不变时保持proposed composition而生成新suite_run/final_suite seal；旧Suite不再具execution eligibility。相同composition不得创建第二Plan，只有closure明确允许复用原Compatibility/N/A authority时才可re-seal，否则必须用新role-run/winner provenance形成新composition。

Q421–Q422区分finfrareg规则、finfrajournal root chain与infrajgen。Registry Journal以唯一INITIALIZED event起始且不可自我replacement。Generation损坏时保留合法prefix terminal；合法Evidence已commit但terminal缺失时只从Evidence在新generation确定性terminalize；无Evidence nonterminal由Registry标记invalidated并建立fresh run；cutoff后对象不复用。不得为旧损坏Journal补写terminal或丢弃可信Evidence后重做semantic validation。

Q423要求每composition在selector之后提交唯一`compatbranch_ / COMPATIBILITY_BRANCH_COMMITTED`，Suite不再通过扫描Plan/N-A是否存在推断分支。Q424建立唯一auditdepres，只有完整resolution commit能移出active set。Q425要求latest authoritative closure-set snapshot显式为空且之后无未合并trigger，才能initial seal或re-seal。Q426的auditdeplattice以closed pairwise table和commutative/associative/idempotent gate机器化merge，未知冲突禁止人工消解。

Q427要求每Official run×trigger唯一officialimpact classification，多个trigger任一execution-affected即STOP。Q428把Official completion标记为FORMAL_AUDIT_VALID或AUDIT_INVALID_REPORT_ONLY；execution-affected用互斥STOPPED terminal。完成后才发现authority failure时不改写旧completion，而由追加classification/invalid projection降低有效发布状态。Q429禁止STARTED前dispatch Official evaluation requests；已建identity的pre-start requests必须逐项ABANDON，全部terminal后才能stop services。

Q430使用suiterun表示durably allocated Suite execution lineage，finalsuite表示绑定composition、compatbranch、新gate vector、latest empty closure set及previous Suite的sealed instance。Initial显式双null；re-seal必须有确切前驱。Crash发生在birth allocation后但seal前时恢复同一suiterun/finalsuite，不产生平行ordinal。

Q431–Q433冻结root recovery与generation replacement上限。Storage recovery耗尽但无canonical错误证据时只能是`FORMAL_INFRASTRUCTURE_ROOT_STORAGE_UNAVAILABLE`；只有可证明chain、canonical bytes、replica committed-prefix或genesis/Registry binding损坏时才是`FORMAL_INFRASTRUCTURE_ROOT_INTEGRITY_FAILED`。Verified mirror只提供逐bytes一致副本，不合并或修复authority。Preserved Evidence terminalization使用`EVIDENCE_TERMINALIZATION_RECOVERY`，不重跑validator、不重读input、不消耗attempt；每个managed journal kind在一个top-level root内最多一次replacement，第二次需要replacement即root fail-fast。

Q434–Q436把compatbranch commit设为Replay/Export/Catalog/Suite的唯一authority barrier，并把`AUDIT_GATE_CONTROLLER`纳入finfrareg managed journal/epoch。Resolution采用primary+one infrastructure retry、Evidence-first补terminal；exhaustion保持trigger ACTIVE，只有新controller epoch可另开resolution run。每个active-set delta必须串行产生唯一auditdepclosureset successor，seal引用连续chain当前terminal empty head。

Q437–Q438建立monotonic cumulative `officialimpactset_`和append-only `officialreportstatus_`。Impact aggregate为`CONTINUE_AUDIT_INVALID | STOP_EXECUTION`且STOP吸收；未分类trigger阻止新Official dispatch。Completed run状态可从FORMAL_AUDIT_VALID单调降为AUDIT_INVALID_REPORT_ONLY，进一步或直接降为EXECUTION_INTEGRITY_INVALID_REPORT_ONLY；completion前停止仍使用独立STOPPED terminal。

Q439–Q440要求每个suiterun由`SUITE_RUN_BIRTH_COMMITTED`获得authority并恰一terminal为FINAL或ABANDONED；crash恢复不能消耗新ordinal。固定composition最多INITIAL加一次RESEAL；RESEAL在Official STARTED前再次失去gate/execution authority时关闭当前top-level formal root，禁止在同一root内通过新composition、role rerun或Suite ordinal重置额度。STARTED后RESEAL完全关闭。

Q441–Q443建立与finfrajournal严格分离的`formalroot_ / formalrootctl_`及最小birth/terminal lifecycle。Root Control Journal不由finfrareg管理、不可replacement；其不可继续时外部witness只授权fail-stop。Verified mirror只是immutable prefix witness，不能成为primary。每incident只允许initial primary、一次相同primary recovery和一次mirror verification；无current writable primary为availability failure，有可信canonical conflict才为integrity failure。

Q444–Q447要求replacement前提交完整不可修改的`infrajreplaceplan_`，并以`evidterm_`从preserved Evidence唯一派生new-generation terminal而不重做validation。每active trigger最多在initial与一个recovery Audit Gate Controller epoch执行resolution；第二epoch仍exhausted使root fail-fast。Audit Gate Controller不得自证恢复，新generation必须由Registry授权并通过formalroot/finfrareg预注册的独立Preflight Validator。

Q448禁止v1跨Formal Root保留winner、pool、RNG、Suite或Compatibility authority。Q449使latest Reporting Authority成为report/export gate：completed run使用三态Official Report Status，stopped run使用独立projection；降级生成新artifact并supersede旧artifact。Q450以`suitedispositionreg_`机器化Suite abandonment；RESEAL失败时先terminalize已birth Suite Run再关闭root，formalrootctl不可写时只能记录外部fail-stop witness与audit incompleteness。

Q451–Q455补齐external fail-stop witness、closed Root dispositions、Shutdown Barrier、mirror activation与Root Storage Incident事务。Witness永不替代Root terminal；可写Root先收束全部workflow再terminal。finfrajournal event由formalrootctl中的mirror activation获得downstream authority，避免自引用。Storage incident固定三attempt ordinal并Evidence-first，rootctl不可写时只走external witness。

Q456–Q458要求Replacement Plan通过独立deterministic PASS gate，evidterm位于new managed generation并按per-run causal adjacency产生terminal；Audit Gate Controller preflight同样primary+one retry、Evidence-first，semantic mismatch不可retry，exhaustion进入Root failure。

Q459–Q460分离Completed与Stopped Reporting Authority，并将report artifact绑定到materialization开始和commit时都current的impact/status head。期间状态变化使未授权transaction ABANDON；只有曾经current的artifact才可SUPERSEDED。Q461禁止新Root继承旧A区Corpus、Review Memory及任何formal-derived search/Suite authority，必须从625条no-memory Reviews开始重建。

Q462–Q468物理隔离Root Control与finfrajournal storage failure domains，并为每个authority-changing Registry event建立exactly-once `finfraactivation_` mirror barrier。Publication最多primary+one retry；Root Storage Incident冻结Registry并发。Replacement Plan FAIL关闭Root；new generation只能在replacement event mirror activation后birth。AGC Preflight由Registry Controller承载，只有PASS需mirror activation并授予epoch authority。

Q469–Q472冻结Report Materialization三终态、纯content与authority artifact分层、status降级即时使旧artifact失效、以及独立Current Report projection。未授权materialization ABANDON，只有曾current的artifactSUPERSEDED；新artifact失败时允许无current report，禁止回滚旧authority。

Q473–Q475把Cross-Root input allowlist固定在Root birth，禁止复用旧Corpus raw response；新Root重新发出625 requests。Root终态分为COMPLETED_FORMAL_VALID、COMPLETED_REPORT_ONLY_INVALID及FAILED_OFFICIAL_EXECUTION_INTEGRITY，严格区分完整可信结果、完整但report-only结果与Official中途停止。

Q476–Q479把finfra activation扩为grant/seal/revoke transition Registry，PENDING hold同时冻结新旧authority；Root Control mirror不自我activation，由外部Watchdog三路径验证且storage topology同Root不可改变。Q480–Q483为deterministic、model-free Official Report建立run/attempt/output、artifact storage replacement上限及独立single-writer Reporting Journal。

Q484–Q488以pure pre-birth rootinputmanifest消除Root identity cycle，并严格限制REPORT_ONLY completion只能吸收post-start authority invalidation或post-freeze audit failure。Official STOPPED fail-root不等待diagnostic；Root terminal是所有formal events最终freeze boundary，terminal mirror仅属外部archive verification。

Q489–Q492把C/Human/CoCo建模为Birth时只冻结sealed descriptor、AUTHORIZED后才逐bytes验证的protected inputs；Root Control Event Registry及external mirror identities关闭Root递归，finfra mirror Evidence只存验证operands而不复制prefix。

Q493–Q497为Official Reporting Journal冻结无replacement的三路径恢复、artifact mirror read-only delivery、自包含report output、cross-Journal Reporting Hold及Formal Publication Export事务。只有current FORMAL_AUDIT_VALID report可导出主结果；Report-only/Stopped只能明确diagnostic。

Q498–Q500以closed Shutdown Registry约束incomplete vector，以external rootarchive描述post-terminal归档，并为protected-input preflight failure建立独立Root disposition；该FAIL禁止STARTED、换C/Human或返回Evolution。

Q501–Q503为Protected Official Input建立Evidence-first preflight、least-privilege protected audit domain与唯一Delivery Manifest；普通lifecycle只见hash/count/type/scope，raw C/Human bytes不得进入Evolution、Memory、日志或manifest外recipient。

Q504–Q510以Reporting Event Registry和durable ingress序列关闭Hold竞态，并冻结pending-trigger snapshots、storage replacement、private publication candidate、content/authority分层及HARDENING_V2中COMPLETED Root必须拥有匹配publication artifact的目标条件。CORE_V1完成条件由Q521–Q527与双Profile Amendment另行收窄。

Q511–Q513为post-terminal archive与external delivery设置non-formal bounded audit，并以`protocolroot_ + protocolrootval_ PASS`统一冻结、验证所有Registry/state-machine/capability依赖；不同Protocol Root结果v1不混用。

Q514–Q520归入CORE_V1：Protocol Root必须通过benchmark-independent one-shot pre-birth validation；Protected Actual Reads使用least-privilege Access Journal并对violation/unresolved hard-stop；restart只有semantic contract不变且新instance重新绑定capability时可沿用Delivery Manifest。

Q521–Q525设计语义保留但归入HARDENING_V2，包括Reporting ingress terminal、Journal seal/mirror、formal Publication attempts/promotion/replicas。CORE_V1只要求deterministic immutable Official result与report artifact以及single-writer/fail-stop，不以完整Publication/HA machinery阻塞实验。

Q526–Q527冻结双Profile与性能实施顺序。Core严格覆盖任何research-observable语义；V2只增强durability/availability，若改变request、Memory bytes、RNG、Candidate、fitness、winner、protected inputs或Official evaluation就必须升级Protocol identity。Core性能预算在smoke/microbenchmark实测后预注册，V2性能不阻塞首次Core。

## 3. Final Suite Commit

只有五个role winner均durable commit后，suite controller才能生成Final Suite manifest。Manifest至少绑定：

- suite schema/protocol identity；
- 固定role order；
- 五个role journal head SHAs；
- 五个winner Candidate/Program identities；
- Reviewer、MemoryProgram、META mode与deployment-relevant identities；
- B-local/full-pool selection provenance；
- C/Human/CoCo forbidden-before-authorization policy identity。
- `suiteauditgates_`及self-contained固定九slot `auditgateroot_` vector；Corpus、五role和Scheduler slots均不可为null，Compatibility N/A slots仍生成带冻结reason与closed nulls的稳定root。

若Suite包含不同Controller implementation identities，manifest还必须绑定authoritative composition、Plan、唯一target、每个winner source、direct source→target coverage、PASSED event/certificate及完整provenance。Target冻结behavior-relevant code/assets、dependency lock、protocol/state-machine、serializer/parser、scheduler/controller behavior与capability/API clients，排除PID/endpoint/host/GPU/start time。Suite assembly使用target semantics；Official可new Runtime/process，但runtime manifest implementation component必须精确等于target并证明resource/process变化不改变Formal Observable Semantics。不得PASS后切换第三种implementation。

`FINAL_SUITE_COMMITTED`一经durable commit，Suite内容不可变；不得因后续可用算力、错误分析或benchmark结果重组。

## 4. Official CoCo Authorization Gate

只有 `FINAL_SUITE_COMMITTED`及其全部identity/hash/integrity checks通过后，才能提交 `OFFICIAL_COCO_AUTHORIZED`。在该event之前，formal Evolution、role controller、worker、Evaluator、Reflector与suite builder均不得读取C、Human/OpenReview references、CoCo reference opinions、Split/Classify、taxonomy coverage或score。

Access-control gate必须验证suite journal授权，而不能只依赖prompt或操作者约定。Evolution期间Official CoCo服务不得启动；只有 `OFFICIAL_COCO_AUTHORIZED` durable commit后才允许部署或开放。未授权访问或部署尝试属于formal protocol violation并使实验无效。

Official service resource transition还必须按8×RTX4090 topology contract提交 `OFFICIAL_SERVICES_PREFLIGHTED` 与 `OFFICIAL_SERVICES_READY`。前者只能发生在授权之后，并重新验证Qwen TP4、Embedding、Split/Classify artifact/runtime identities、NUMA topology与exclusive-resource gate；旧进程不得被静默adopt。

Evolution→Official必须强制重启全部model/scheduler/worker services。`EVOLUTION_SERVICES_STOPPED`要求Qwen、Embedding、scheduler/workers退出并释放GPU，KV/prefix/request caches清空；Official阶段使用新进程与新execution provenance，不继承Evolution runtime cache。

## 5. Permanent Seal at Official Evaluation Start

`OFFICIAL_COCO_STARTED`一旦durable commit，当前Final Suite identity永久sealed。此后禁止：

- 修改winner或Program；
- 重选finalist或tie-break；
- 继续mutation、Evolution或调权；
- 根据C/Human/CoCo结果改变prompt、parser、memory、META或系统配置；
- 将Official CoCo结果反馈到当前实验的任何search/selection路径。

Official CoCo只作为post-freeze report-only evaluation。`OFFICIAL_COCO_COMPLETED`保存官方evaluation identity、raw/canonical outputs与report provenance，但不产生任何Evolution-authorized event。

## 6. Required Gates

Formal前至少验证：

- 五role journal并行完成但无cross-role order依赖；
- missing/duplicate/invalid winner rejection；
- fixed role ordering与deterministic Suite identity；
- pre-authorization C/Human/CoCo access denial；
- Final Suite commit后immutability；
- `OFFICIAL_COCO_STARTED`后的permanent seal与feedback-path denial；
- suite journal crash/recovery和完整hash-chain replay。
- formal versus raw audit、`auditscope_ / auditquiesce_` pre-head与唯一commit slot、closed Quiescence Requirement Registry、multi-Journal inclusive ranges、七类Audit Scope、occurrence-preserving identities、single-writer `auditreadepoch_` lifecycle、closed-terminal auditreadrun/attempt/evidence/ready、formal catalogs、typed roots及permanent scope closure；
- fixed-role-vector proposed composition、single frozen target、each-source direct comparison/no chaining、compatplan/ordered-compitem non-cyclic identity、authorized transitive dependency closure/topological order与Final Suite exact-composition binding；
- Catalog Validation per-attempt closed terminal、PAUSED/EXHAUSTED、new-top-level-run-only restart、execution-scoped self-contained `catvalevid_`、complete Registry及unique PASS/FAIL `catval_`、same-composition one Plan与lifecycle exclusivity；
- pre-STARTED authoritative-catalog-only access、payload/rehash/diff denial；
- per-comparison identities、confirmed exposure versus access-integrity、TEB、compatquiesce与Replay auditquiesce、authority-committed exportinput、single exportcap/lease、protected exportcand、single-writer exportaccessepoch、exportaccess run/attempt/evidence/val、nonsemantic promotion、closed Failure Registry、`auditgateroot_ / suiteauditgates_`、Audit Dependency Closure Registry、trusted FAIL versus unknown Readiness及Replay/Export独立stage gates；
- behavior-only target identity、Official new Runtime/process allowance、implementation-component exact equality与runtime Formal Observable Semantics gate；
- deterministic dual-implementation、exact model-request equality/downstream shared-raw-response及Journal/RNG simulation proof闭集；
- mixed-implementation Suite必须引用PASSED event SHA，Protocol-affecting或无法证明等价时五role统一重跑。

任一gate未通过，不得授权Official CoCo。
