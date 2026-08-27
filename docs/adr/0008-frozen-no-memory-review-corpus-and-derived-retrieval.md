# 冻结 no-memory Review Corpus，完整 Review 为权威记录，检索视图确定性派生

状态：accepted（2026-08-27）。本 ADR supersede ADR-0002，并部分 supersede ADR-0005 的“共享题目、不共享 AI review”边界及 ADR-0007 的 A 区 sequential self-generated replay 语义。

后续主实验先由统一、冻结的 no-memory Reviewer 在 A 区论文上生成共享的 **Full Review Corpus**，再让所有 MemoryProgram 在相同 Review 输入上进化存储、检索和使用策略。五个 Specialist 的职责和 prompt 不同，因此 `QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL` 分别生成并冻结自己的 role-specific Corpus，不共享一份 general review。真人 review、gold opinion、CoCo taxonomy 与 evaluator 输出仍不得进入 Corpus。该选择把当前研究归因收窄为“固定审稿经验上，Memory 策略及其 Evolution 是否改善 held-out Review”，换取更清晰的单变量归因和可复用的 A 区资产；完整 Harness 自产经验另列后续实验，不与本主线混称。

每篇完整 AI review 作为不可变 **Full Review Record** 保存并按内容哈希锚定。向量 chunk、关键词索引和其他检索单元只是可重建的 **Retrieval Projection**：优先按 Frozen Reviewer rubric subdimension 下的完整 opinion 切分，保留 paper、role、subcategory、原文位置和 Review SHA；解析失败必须保留 whole-review fallback，不得静默丢失原文。不得使用 CoCo/gold 分类结果为 Candidate Memory 打标签。

主实验中每个 A paper × role 只产生一份 canonical Full Review Record。模型 revision、reviewer prompt SHA、temperature、generation budget 与确定性验证规则在 Corpus 生成前冻结。所有 raw attempts 均保存，第一份通过协议验证的输出成为 canonical record；最多允许一次按同一冻结 retry protocol 的 Reviewer LLM 重试。两次均失败时 Corpus build fail-fast，不得跳过该 paper 或用缺失样本继续。多 Corpus replicate 只能作为独立 robustness 实验。

运行期删除生成式 `Memory Query` 与 `KnowledgeItem` LLM。Query 构造和 Review 投影改为 MemoryProgram 内可进化但确定性的代码；Reviewer LLM 继续生成实际 review，Evolution Reflector LLM 继续产生 child MemoryProgram。Embedding 不属于此处禁止的生成模型，可按冻结 revision 批量预计算和缓存；是否同时使用 BM25/FTS 属可逆实现选择，不在本 ADR 固定。

## Considered Options

- **每个 Candidate 从 empty Memory 顺序重放 A 并自产经验**：不再作为下一阶段主实验；它把经验生成与 Memory 策略耦合，重复消耗大量 Reviewer LLM，并扩大归因范围。
- **共享 human/OpenReview review**：拒绝；违反 Human Reference Boundary。
- **共享冻结的 no-memory AI review，确定性构建派生索引**：采纳。

## Consequences

- A 区 Full Review Corpus 成为带 manifest、模型/revision、生成参数、源论文身份与 SHA 的冻结实验资产，不再为每个 Candidate 重复生成。
- 五个 Specialist 共享同一 A paper manifest 和生成条件，但分别持有 role-specific Full Review Corpus。
- Corpus manifest 必须同时锚定 canonical record 和全部 raw attempt；任一 A paper × role 缺失都使该 Corpus 不可用于 formal run。
- Candidate 之间共享 Full Review Record，但可通过各自 MemoryProgram 产生不同 Retrieval Projection、查询、排序与使用方式。
- 旧 run 仍按其记录的 ADR-0002/0007 协议解释；不得把旧 checkpoint 或 Memory snapshot 迁入新协议。
- 新实验必须使用新的 protocol/config/run identity，并将结论限定为固定 Review Corpus 上的 Memory Evolution。
