# AgentReview 领域术语

本文是领域语言的唯一入口。每个术语表达稳定含义；实现路径、命令和当前进度分别进入架构、运行手册和状态文档。

## 研究对象

### Research Validity Core v1

首次正式实验采用的最小研究闭包。它固定研究语义、隔离受保护参照，并以可核验工件连接数据准备、角色进化、Suite 冻结和 Official 评价。

### Audit Hardening v2

Core v1 之后的运行强化层。它覆盖高可用 journal、复杂恢复、外部归档、发布治理和多副本审计。

### Frozen Reviewer Identity

评审者的角色目标、rubric 和固定推理职责。实验通过固定 Reviewer Identity，把可进化变量集中到 Memory Program。

### Reviewer Role

五个相互独立的评审维度之一：QUAL、CLAR、SIGN、ORIG、POL。每个角色拥有独立 rubric、Review Memory 索引、候选池、适用性判定和 winner。

### Rubric

角色的评分定义与子维度集合。Rubric 约束 Reviewer 输出和 Evaluator 判定，并作为正式实验身份的一部分冻结。

## 数据与记忆

### Four-Zone Split

按论文族和时间规则冻结的四区数据协议。A 用于历史 Corpus，B 用于 reference-free 进化与选择，C 用于冻结后的 Official 评价，D 保留给旧 benchmark 路径。

### Full Review Corpus

由固定 Reviewer 在 A 区生成的完整 no-memory 评审集合。完整记录是 Review Memory 所有派生视图的权威来源。

### Retrieval Projection

从 Full Review 按角色和子维度确定性派生的检索视图。Projection 保留来源身份，并服务于角色隔离的索引。

### Retrieval Fragment

Projection 的有界片段。Fragment 支持精确回溯到完整记录和来源位置。

### Review Memory

基于冻结 Corpus 和角色索引构成的只读记忆。每次 Candidate × paper 会话使用独立预算与访问 ledger。

### Memory Read Mode

Memory Program 选择的封闭读取方式：向量检索、检索后一次摘要、结构化经验读取。Read Mode 决定 Host 提供的能力集合。

### Frozen Evidence Pool

在进化前物化的只读外部证据集合。它以目标论文和来源身份组织，并通过受控读取接口进入评价上下文。

## 进化

### Memory Program

决定 Review Memory 读取计划的候选程序。Core v1 Candidate 输出声明式 retrieval plan，Host 执行检索、预算和能力控制。

### Program Identity

由规范化程序内容及其行为声明导出的稳定身份。相同 Program Identity 在共享候选池中只代表一个程序。

### Candidate

某个 Program 在特定角色、控制器和出生事件下的实例。Candidate Identity 同时表达程序身份与进化来源。

### Seed

角色进化的初始 Program。Core v1 固定使用向量检索、LLM 摘要和经验学习三枚 v2 seed。

### Attempt

一次有编号的 parent 选择、反馈、变异、验证和准入事务。角色的 attempt 数量由冻结的 B-local schedule 决定。

### Shared Candidate Pool

同一角色内三枚 seed 及其后代共享的候选集合。Parent 选择、去重、finalist 选择和 winner 选择都在该集合中发生。

### Static Set

同一角色为所有 Candidate 固定的 25 篇评价集合。合法性校验与 Program Identity 去重决定 Candidate 准入；Static fitness 驱动 parent 选择和全局 top-3 排序。

### Rotating Feedback Set

按 attempt 预注册的小批 B-local 论文。它只生成聚合反馈，供 Reflector 产生下一份 Program。

### B-Local Full Pool

角色在 B 区的完整可评价集合。全局 top-3 finalist 在该集合上重新评价，并产生唯一 role winner。

### Reflector

把聚合评分、子维度统计、失败码和可评价数量转换为 Program 变异的固定角色。它接收结构化反馈，并保持受保护参照隔离。

## 评价与冻结

### Applicability

Evaluator 依据论文、结构上下文、冻结证据和 rubric 判断子维度是否适用。该判定对同一 paper × role 的全部 Candidate 保持一致。

### Reference-Free Evaluation

进化阶段使用论文内容、冻结证据和 rubric 对 Candidate review 评分。Human/OpenReview/CoCo 参照由 Official 阶段管理。

### Role Fitness

先在论文内聚合适用子维度，再在论文间聚合得到的角色分数。Canonical Decimal 表示用于持久化与排序。

### Role Winner

某一角色的 top-3 finalist 在 B-local full pool 上评价后选出的唯一 Candidate。Winner 绑定角色运行身份、Candidate Identity、Program Identity 和 full-pool fitness。

### Final Review Suite

按 QUAL → CLAR → SIGN → ORIG → POL 固定顺序封装五个 Role Winner 的冻结对象。Core v1 的 Suite 关闭 META。

### Protected Official Access

Final Suite 冻结后开放 C 区和 Human/CoCo 参照的授权 seam。授权、读取和完成事件共同表达 Official 访问生命周期。

### Official Evaluation

对冻结 Final Review Suite 进行的独立外部评价。Official 结果用于报告研究效果，并保持进化过程的参照隔离。

## 证据与完成度

### Formal Artifact

带 schema、身份、校验值和来源关系的持久化记录。正式状态由工件内容与门禁共同证明。

### G1 — Production Wiring

证明独立 Core CLI、生产组合根、五角色、Suite 和受保护 Official seam 已形成可执行控制流。

### G2 — Preformal Artifacts

证明正式 Corpus、索引、B-local、Evidence、Evaluator、capacity 和 runtime profile 已完成物化与预检。

### G3 — Formal Closure

证明五个正式 Role Winner、Final Suite、Official 授权、Official 结果和最终报告已经形成闭包。

### Legacy M★

早期的 Memory Program 演化、META 和 CoCo 评价路径。它保留复现实验和兼容接口身份，与 Core v1 分开运行。
