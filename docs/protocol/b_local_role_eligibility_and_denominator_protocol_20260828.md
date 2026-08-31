# B-local Role Eligibility and Denominator Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen（2026-08-28），Q71–Q75 已接受。本文冻结 eligibility、role-specific membership、threshold enforcement、applicability consistency 与 denominator audit 规则；五个 role 的具体整数、selection seed/hash 和生成后的 manifests 仍须在 formal run 前预注册。

## 1. Shared Full Pool and Role-Specific Membership

五个 role 共享同一个冻结的 B-local total paper/family pool。Full-pool lineage-champion reevaluation 使用该统一 full pool，不得按 role 删除不利 paper；`paper_fitness=N/A` 的一致排除规则处理真实不适用样本。

每个 role 分别冻结自己的：

- static-25 membership 与 paper order；
- frozen rotating family pool；
- rotating-5 schedule 与每轮 paper order。

Membership 必须由 Candidate-blind eligibility audit 和预注册、确定性的 selection rule 生成。Formal manifest 至少冻结 role、pool manifest SHA、eligibility-audit version、selection algorithm/version、seed、hash algorithm、tie-break rule、family IDs、paper IDs 与 order。允许的实现模式是对 eligible families 计算包含 protocol version、role、frozen seed 与 family ID 的 canonical hash，按 hash 与 canonical family ID 排序后确定性选择；禁止人工 cherry-picking 或查看 Candidate/evaluator score 后改 membership。

同一 role 内 no-memory control、三个 mandatory initial seeds、全部 Evolved Candidate 和全部 global finalists 使用完全相同的对应 set/schedule。不同 role 的 set 可以重叠，因为 role fitness 不横向比较。

## 2. Candidate-Blind Eligibility Audit

在冻结 B-local full-pool manifest 后、查看任何 Candidate output 前，执行 Human/CoCo-free 的 role eligibility audit。Audit 输入只允许：

- frozen paper input；
- deterministic structural metadata；
- 当前 frozen role rubric/protocol；
- 与 role-level evaluability 直接相关的 frozen evidence availability metadata。

Audit 只允许输出 paper/family 对某 role 是否进入 selection eligibility frame，以及预注册的 eligibility reason code。禁止输出或保存 expected weakness、expected criticism、ideal answer、importance judgment、subdimension score、Candidate-specific information 或任何可作为 Reflector 教学信号的内容。

该 audit 不是正式 Applicability phase：其输出不得注入 Reviewer/Evaluator prompt，不得缓存为正式 subdimension applicability map，也不得替代每个 Candidate fresh session 中的 Candidate-blind Applicability phase。

## 3. Role-Specific Threshold Manifest

不使用跨 role 的统一比例、统一常数或固定“20/25”。每个 role 在 formal run 前分别预注册：

- `min_static_evaluable[role]`；
- `min_full_pool_evaluable[role]`；
- 两个明确整数的制定依据；
- B-local full-pool manifest SHA；
- role-specific static/rotating manifest SHA；
- rubric/protocol version；
- eligibility-audit version 与产物 SHA。

阈值必须在任何 Candidate output、formal evaluator score 或 Human/CoCo information 可见前冻结，formal run 后不得降低或改写。

## 4. Stage Enforcement

### Static-25 Search

同一 role 的 canonical applicability map 建立后，若 static-25 实际 evaluable-paper 数量低于 `min_static_evaluable[role]`，该 role search 立即 fail-fast。不得把 `N/A` 转为 `0`/满分，不得换 paper 或降低阈值。

### Rotating-5 Feedback

Rotating-5 不参与 Candidate ranking，因此不设置 fitness-validity 最低数量。若当前 batch 没有 evaluable paper：

- 输出 `NO_EVALUABLE_ROTATING_PAPERS`；
- `batch_fitness=N/A`；
- 不生成虚假 aggregate score；
- 不临时换 paper、不重抽样；
- 不处罚 Candidate；
- Reflector 只能看到该聚合状态，不得看到 paper identities 或逐 paper applicability。

### Full-Pool Winner Selection

全部预注册规则产生的global finalists在同一个frozen B-local full pool、paper order、Evaluator Identity与fresh sessions下重评。若实际evaluable-paper数量低于 `min_full_pool_evaluable[role]`，该role winner selection fail-fast；不得按结果减少/替换finalists、缩小pool或修改denominator。

## 5. Applicability and Denominator Consistency

Applicability phase 不读取 Candidate。因此，同一 `paper × role` 在不同 Candidate independent sessions 中必须产生完全相同的 canonical subdimension applicability map。

任一不一致触发 `APPLICABILITY_INCONSISTENCY`，并使整个 role formal run fail-fast。禁止：

- 只淘汰不一致 Candidate；
- 多数投票；
- 取 union/intersection；
- 修改 denominator 或 exclusion set 后继续；
- 将 semantic inconsistency 当作基础设施 retry。

只有 Q29 已定义的非法 JSON、schema failure、截断等基础设施错误，才允许在完全相同 frozen generation identity 下 fresh-session retry 一次。

某 paper 的全部 subdimensions 为 `N/A` 时，`paper_fitness=N/A`。所有 Candidate 必须使用完全一致的 paper exclusion set 和 denominator；任何不一致均 fail-fast。

## 6. Required Formal Audit Artifacts

正式审计至少保存：

- 每个 role/stage/paper/subdimension 的 canonical applicability map；
- 所有 `paper_fitness=N/A` 的 paper IDs；
- 每个 role/stage 的实际 evaluable-paper 数量与对应预注册阈值；
- subdimension-level 与 paper-level N/A reason codes；
- external-evidence insufficiency 导致的 N/A 数量；
- 每个 rotating batch 的 evaluable 数量、`batch_fitness` 与 empty-batch 状态；
- 每个 Candidate 的 paper exclusion set SHA 与 denominator；
- applicability consistency comparison 结果；
- eligibility audit、selection rule、threshold 与全部相关 manifest identities。

这些 artifacts 只用于审计与 fail-fast，不得向 Reflector泄露逐 paper 信息，也不得用于 formal 结果出现后修改 protocol。
