# Shared-Pool M★ Attempt and Failure Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen contract（2026-08-29），Q116–Q140 已接受。本文取代此前per-lineage equal budget与lineage champion条款；global top-3 finalist完整规则见 `global_top3_finalist_selection_protocol_20260829.md`，canonical fitness/Program/Candidate identity见 `canonical_fitness_program_and_candidate_identity_protocol_20260829.md`，authoritative journal/recovery见 `formal_controller_journal_and_recovery_protocol_20260829.md`。

## 1. Shared Initial Pool and Global Search

每个role的正式Evolution包含三个mandatory initial search seeds：

- Vector Search；
- LLM Summarizer；
- Experience Learner。

它们不是三条分别分配相同mutation budget的独立实验分支。三个seeds全部通过初始化gates后共同进入一个candidate pool；每个role随后执行总计20个global evolution attempt slots。每轮从整个累计pool按冻结static-25 fitness与softmax policy选择parent。Parent softmax保留当前M★ binary64语义与固定 `tau=0.15`；它不使用finalist ranking的Decimal arithmetic。允许不同seed/descendant lineage得到不同数量的后续attempts，这属于global search的exploitation/exploration结果，不是protocol violation。

Lineage identity仍必须记录，用于provenance和分析，但不产生quota、保留席位或champion权利。

## 2. Attempt Ownership and Failure Charging

以下Candidate-originated failure消耗当前global attempt slot，不产生child，也不补attempt：

- compile/runtime failure在冻结fix budget后仍未解决；
- `INVALID_EVIDENCE_API_REQUEST`或其他Candidate-originated protocol failure；
- Reflector mutation在冻结mutation/fix budget后仍失败；
- Candidate未能完成合法static evaluation且原因可归于Candidate code/behavior。

Infrastructure failure不消耗attempt。Pool/service/hash/manifest/orchestrator/backend故障应停止，并由authoritative Controller Journal恢复原slot；checkpoint只作为可重建projection。无法可靠归因时按infrastructure failure保守停止，不得武断计为Candidate failure。

Failure ownership、证据、attempt ID、parent/lineage、checkpoint与最终disposition必须写入controller audit。

Evaluator两次均非法、Applicability inconsistency或Evaluator Identity mismatch是role-level validity failure，必须立即fail-fast，不得记为Candidate failure后继续。合法static canonical evaluation完成即必须admission，包括fitness为 `0.000000000000` 的Candidate。

## 3. Attempt-Internal Sub-Attempts

以下属于当前evolution attempt内部，不增加global 20-attempt budget：

- 冻结上限内的compile-fix；
- Q29 identical Evaluator retry；
- 冻结transport/infrastructure retry；
- 同一attempt的checkpoint replay。

所有sub-attempt保存独立provenance，但Candidate code/input不得因Evaluator retry改变。Retry/fix exhaustion后按上一节确定failure owner与attempt consumption。

## 4. Mandatory Seed Initialization

三个initial seeds的初始化不计入20 attempts。每个seed必须先通过：

- compile/runtime protocol gate；
- static-25 canonical evaluation；
- Frozen Evidence API capability/budget gate；
- semantic/runtime/schema/registry/audit identity gate。

三个seed全部合法后才开始global search。任一mandatory initial seed非法，该role formal run fail-fast；不得临时替换seed、复制另一seed或缩减initial pool。

三个mandatory initial seeds的完整 `program_identity_sha256` 必须互不相同；严格Program identity重复同样使initialization gate fail-fast。

No-memory是独立control condition，不是第四个Evolution seed，不参与parent selection、mutation或finalist竞争。

## 5. Global Eligible Pool and Finalists

20个global attempts结束后，eligible pool包含：

- 三个合法initial seeds；
- 所有成功完成static-25 canonical evaluation的children。

合法child无需优于parent即可进入pool。Child通过compile/runtime gate后、static-25 evaluation前执行admission-time strict Program dedup。若Program identity已在shared pool中，记录 `DUPLICATE_PROGRAM_MUTATION`：该attempt消耗预算，但不执行static evaluation、不新增pool entry、不增加softmax probability mass，并保留duplicate-target provenance。不存在per-lineage champion或每seed保留一个代表的要求。Global finalists按冻结static-25 canonical Decimal fitness与deterministic canonical candidate-ID tie-break从整个shared pool产生；多个或全部finalists可以来自同一seed lineage。

全部global finalists在相同frozen B-local full pool、paper order、Evaluator Identity与fresh sessions下统一重评，不向Reflector提供反馈。最高full-pool canonical Decimal fitness成为该role唯一winner，Decimal同分按冻结canonical candidate ID决定。

## 6. Global-Finalist Rule

每个role从shared eligible pool选择固定global top-3 unique Programs进入full-pool reevaluation。不设置seed/lineage保留席位，不按tie扩容，不允许failure后递补。Program identity、canonical Decimal fitness与candidate-ID的机器定义仍须物化并通过gate。

## 7. Parent-Selection Commit and Resume

Shared-pool softmax的eligible vector保持pool插入顺序：三个initial seeds固定为 `Vector Search → LLM Summarizer → Experience Learner`，成功admission的children按global attempt admission顺序追加，duplicate/failed attempts排除；不得改按Candidate ID排序。Canonical Decimal固定字符串由冻结Python `float()` 转为binary64，再执行 `fitness / 0.15 → subtract max → math.exp → random.Random.choices`。必须冻结Python/math/runtime、PRNG algorithm、run seed与每轮RNG state，并保存candidate vector、Decimal、`float.hex()`、logits、raw weights、normalized probabilities、chosen parent与selection commit SHA。

每个global slot先durable commit domain-separated `att_` full-hash attempt identity，再以独立immutable journal event原子提交parent selection及selection前后RNG state，之后才进入rotating evaluation和mutation。Parent selection一旦提交，resume不得重新计算；未完成durable commit的selection只能从冻结pre-selection RNG state重放。

`canonical_candidate_id` 绑定immutable birth event。Initial seeds使用冻结seed key/birth slot，children使用global attempt index；Candidate ID还绑定role、formal run seed、parent ID、Program identity与controller protocol identity。Compile/fix成功并生成最终Program manifest后提交Candidate birth，随后才执行dedup和static evaluation。Compile前失败没有Candidate birth；duplicate child有birth record但不进入pool。ID首次提交后不得因score、success、admission或ranking改变。
