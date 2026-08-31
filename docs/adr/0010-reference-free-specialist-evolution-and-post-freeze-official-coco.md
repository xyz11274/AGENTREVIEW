# ADR-0010：Reference-Free Specialist Evolution 与冻结后 Official

- 状态：Accepted
- 日期：2026-08-28
- 最近核验：2026-08-31

## 背景

Human/OpenReview/CoCo 参照进入进化 fitness 会让搜索直接优化 Official 目标，并削弱冻结后评价的独立性。

## 决策

1. QUAL、CLAR、SIGN、ORIG、POL 五个 specialist 分别进化。
2. B 区 Evaluator 只读取论文、结构上下文、Frozen Evidence、角色 rubric 和 Candidate review。
3. Applicability 阶段保持 candidate-blind，Scoring 阶段读取 Candidate review。
4. 三枚 seed 进入同一角色的 shared candidate pool；固定 attempt budget、softmax parent selection、Program 去重和失败占槽共同决定搜索。
5. 所有 Candidate 先在 static-25 评价；rotating set 只提供变异反馈；全局 top-3 在 B-local full pool 上决定唯一 winner。
6. 五个 Role Winner 按固定顺序冻结为 Final Review Suite，Core v1 关闭 META。
7. Protected Official Access 在 Suite 冻结后授权 C 区与 Human/CoCo 参照。

## 结果

B 区 fitness 表达角色 rubric 下的 reference-free 质量，C 区 Official 表达冻结 Suite 的独立外部效果。两种指标分别报告。

## 替代方案

B 区 Human-reference fitness、B-suite CoCo 选择和进化阶段 META 都会扩大受保护信号的影响面。当前设计把受保护参照集中到冻结后的单一 seam。
