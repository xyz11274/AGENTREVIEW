# ADR-0008：冻结 Full Review Corpus 并确定性派生 Review Memory

- 状态：Accepted
- 日期：2026-08-27
- 最近核验：2026-08-31

## 背景

各 Memory Program 自行生成历史经验，会同时改变历史内容与读取策略，使候选间比较失去单变量解释。

## 决策

1. 固定 Reviewer 在 A 区以 no-memory 模式生成完整 Full Review。
2. Canonical Full Review Record 是历史评审内容的权威记录。
3. Role projection、fragment 和 exact-flat embedding index 从完整记录确定性派生。
4. 五个角色使用彼此隔离的 Review Memory 索引，并共享同一冻结 Corpus 身份。
5. Candidate 只声明 retrieval plan；Host 执行检索、hydrate、token 计数、预算扣减和 ledger 记录。
6. 每个 Candidate × paper × role 使用 fresh session。
7. 三种 read mode 共享同一冻结 Corpus，并通过能力集合表达各自差异。

## 结果

实验把可进化变量集中到读取策略。完整评审支持回溯，派生视图支持角色检索，Host 执行保证预算与隔离具有统一语义。

## 替代方案

候选本地历史库会混合内容生成与检索策略。只保留向量片段会丢失完整记录权威。当前设计同时提供可比性与可追溯性。
