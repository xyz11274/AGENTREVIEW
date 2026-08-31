# Machine Registry and Formal Gate Protocol

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen contract（2026-08-28），Q96–Q100 已接受。本文冻结 registry namespace、允许的构建信息、fallback policy、entity merge、versioning 与 formal gate；具体 registry/schema/parser artifacts 尚待生成。

## 1. Fully Qualified Identity

所有正式 registry key 使用：

`<role>:<registry_version>:<key>`

例如 `POL:1:DATA_CONSENT`、`QUAL:1:METHOD_CORRECTNESS`。不同 role 中同名短 key 不共享 identity，也不允许隐式继承。

每个 registry artifact 至少冻结：

- registry name 与 version；
- role namespace；
- 全部合法 fully-qualified keys；
- 每个 key 的规范定义与 primary subdimension；
- admissible-support policy；
- 允许的 judgment object/evidence classes；
- canonical ordering；
- serializer/normalization identity；
- artifact canonical SHA256。

Formal manifest 必须引用 registry artifact SHA，而不是只记录人类可读 version 字符串。

## 2. Construction Information Boundary

`object_class`、`requirement_key` 与 controlled-fact registries 只能依据：

- 已冻结的 23-subdimension rubrics；
- master reference-free evaluator protocol；
- benchmark-independent synthetic/smoke cases。

禁止读取 B-local Candidate Review/fitness、C、Human/OpenReview references、CoCo taxonomy/Split/Classify/score 来扩展或调整 registry。允许对 frozen manifests 做 Candidate-blind 结构兼容性检查，但不得依据具体 paper 中发现的 expected weakness、ideal criticism、评价答案或结果增加 key。

任何 registry change 都必须发生在 formal 前，并形成新的 registry/protocol version、artifact SHA 与完整 gate 结果；新旧 identity 的分数不得混合。

## 3. No Open-Ended Fallback

禁止 `OTHER`、`MISC`、free-text key 或未版本化 extension field。所有正式 judgment 必须映射到 closed registry。

Evaluator 输出未知 key 属于 schema/enum failure，可按 Q29 在相同 frozen generation identity 下 fresh-session retry 一次。两次仍无法产生合法 key，该 role formal run fail-fast。Formal 运行中不得：

- 临时扩展 enum；
- 静默丢弃未知 judgment；
- 把未知 judgment 降级到自由文本；
- 选择“最接近”但语义不同的合法 key；
- 根据已产生的 formal result 修改 registry。

若 formal 前发现合法 rubric judgment 无法表达，必须发布新 registry/protocol version，并重新执行全部 smoke/registry/parser/validator gates。

## 4. Conservative Deterministic Entity Merge

只有满足以下任一可机械证明条件时，才能合并 entity mentions：

- 相同 frozen manifest ID；
- 相同 Source Anchor object；
- entity type 相同且 explicit identifier/name exact-normalized 后相同；
- 冻结 deterministic structural rule 证明为同一对象。

禁止使用 embedding、LLM semantic similarity 或 parametric knowledge 合并。无法确定时保持不同 entity IDs；宁可 under-merge，不得错误合并后把两个独立 judgments 压成同一个 `judgment_id`。

Entity normalizer 必须冻结 Unicode/case/whitespace/punctuation/URL/identifier 处理规则、version、test vectors 与 SHA identity。

## 5. Required Formal Gate

Formal 前至少执行并保存：

- 每个合法 key 的 positive schema test；
- 每个非法/未知 key 的 rejection test；
- role namespace collision test；
- canonical ordering 与 hash test；
- entity normalization、exact merge 与 conservative non-merge test；
- POL ownership mapping completeness/uniqueness test；
- 每种 admissible-support policy 的 consequence test；
- `CORE/SUPPORTING × invalid-ID × remaining-support` matrix；
- Q30 systematic-hallucination 的三个触发路径；
- fabricated claim 无法 canonicalize 时禁止 shared-evidence merge；
- registry/schema/parser/validator version 与 artifact-SHA binding test。

所有 test vectors、canonical expected outputs、expected SHA、实际结果与 registry/parser/validator versions 必须冻结并进入 Evaluator Identity。任一 gate 未通过，不得启动 formal run；禁止 formal 结果出现后放宽 expected output 或 acceptance criterion。

## 6. Pending Materialization

本文不声称 artifacts 已经存在。Formal-ready 仍要求生成并冻结：

- 五个 role 的 object/requirement/controlled-fact registries；
- entity registry/normalizer；
- POL ownership map；
- support-policy map；
- evaluator atomic、N/A、evidence-limitation、validator-derived 与 runtime registries；
- JSON Schemas、canonical serializer、strict parser、validator、aggregator 与 test-vector bundle。
