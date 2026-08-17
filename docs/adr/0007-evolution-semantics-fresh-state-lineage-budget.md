# 进化语义：fresh-state replay、时序协议、code lineage、统一预算上限

Evolution 的运行语义固定为：

1. **Empty-state 起始**：每个 Seed / Candidate / Program 从 empty Memory State 开始，在完全相同的 Historical / Experience Set 上按冻结顺序运行。
2. **时序协议**：对 `Paper_i`：`读取 Paper_i → 可读 Paper_1..Paper_{i-1} 的已有 Memory → 当前 Program 自己审稿 → 审稿完成后才把 Paper_i 的经验写入 Memory`。当前论文不能先写 Memory 再审自己，不能读取自己刚产生的经验。
3. **Memory 不跨 Program**：Seed A / B / C 之间不共享 Program-specific Memory；Candidate 不继承父 Candidate 的运行时 Memory State。**继承和 mutation 的是 Program / code lineage，不是已构建的 Memory State。**
4. **Fresh-state replay**：每次 Candidate fitness evaluation 都是：

`fresh empty state → replay Historical / Experience Set → Candidate 自己产生 review / trajectory / evidence → 构建该 Candidate 自己的 Program-specific Memory → Evolution Validation → external fitness`

Memory 不是在 Evolution 之前统一预构建的资产，而是 Candidate evaluation 的运行产物。

Historical Set 中产生的经验可以写入 Memory；Evolution Validation 产生的 review 用于 fitness / feedback，不进入该 Candidate 的 Historical Memory。

1. **三条 Seed lineage 独立**：`seed_vector_search` / `seed_llm_summarizer` / `seed_experience_learner` 及各自 `evolved_from_*` 后代；实验记录携带 parent seed、lineage、SHA256、fitness、promotion provenance；global champion 评选保留全链。
2. **工程资源边界与用量记录**：同一次正式实验内所有 Candidate 使用相同 Base LLM 和相同系统级资源能力边界。Context organization 与实际 context 使用量属于 Review Harness 的策略自由，不要求不同 Program 实际消费相同长度的 context。

系统仅保留 Base LLM context window、API / runtime 限制和防止失控所需的工程安全边界，不以固定实际 context 长度限制不同 Program 的策略差异。

正式实验必须记录实际资源使用，包括：

- LLM calls
- prompt / completion tokens
- context size
- memory context size
- S2 retrieval usage
- 方便时记录 latency / cost

这些数据用于后续进行 performance–cost 分析，不作为强迫所有 Program 使用完全相同资源量的要求。

1. **反馈通道收窄**：reflector / mutation 只能获得 scalar fitness、不泄漏答案的 structured metrics、不含 gold 内容的错误类型；judge rationale 若复述 gold 不得回灌（ADR-0005 的 Human Reference Boundary 旁路规则）。

## Considered Options

- **Candidate warm-start 父代的 Memory State**：被否决。fitness 归因混淆（收益来自 code 还是继承的记忆？），且祖先经验沿 lineage 泄漏，等价于隐式共享答案。
- **三个 Seed 合并为单一起点池**：被否决。三种初始假设混血统后无法回答「哪类假设更适合进化」。
- **预算不设统一上限、Program 自由使用算力**：被否决。无法区分策略收益与算力收益；上限内自由、上限统一是公平比较的前提。

## Consequences

- 实现上，fitness 评估的运行结构必须支持 fresh replay（状态完全可重置、顺序确定）。
- Evaluator feedback 通道需审计：现有 reflector 的 `EvalFeedback`（failed cases / traces / judge rationale）是否含复述 gold 的内容——列入 docs/TODO.md。
- 预算参数化但同实验一致，用量日志成为实验记录的必备字段。
