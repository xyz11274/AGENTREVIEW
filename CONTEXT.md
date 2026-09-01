# AgentReview Research Core v2 领域术语

本文定义 Research Core v2 的规范语言。它描述目标领域，而不是当前 legacy 代码的实现细节；系统设计见 `docs/RESEARCH_CORE_V2_DESIGN.md`。

## 审稿

**Specialist Reviewer**:
五个独立审稿主体之一。每个 Specialist Reviewer 只承担一个固定角色：`QUAL`、`CLAR`、`SIGN`、`ORIG` 或 `POL`。
_Avoid_: Agent、维度 Agent、CoCo Reviewer

**Reviewer Role**:
Specialist Reviewer 的冻结职责。五个角色的 identity、职责和输出要求由只含五个 Specialist 定义的 `REVIEW_Request.md` 提供，不引用 CoCo 子类或评测提示。
_Avoid_: CoCo 类别、Reviewer 类型

**Specialist Review**:
一个 Specialist Reviewer 对一篇论文生成的完整、未切割审稿结果。它是永久保存的一级数据。
_Avoid_: Chunk、Memory item、摘要

**Five-Review Bundle**:
同一论文的五份 Specialist Review 按 `QUAL / CLAR / SIGN / ORIG / POL` 固定顺序组成的集合。
_Avoid_: Meta Review、平均分

**Meta Reviewer**:
可选的无状态汇总者，只读取 Frozen Paper 与 Five-Review Bundle 并生成 Final Review；它没有 Memory，也不参与 Evolution。它输出 recommendation，但不产生自定义最终分数或独立 confidence 字段。
_Avoid_: 第六个 Specialist、META Program

**Meta Synthesis Request**:
独立版本化的 `META_SYNTHESIS_Request_v1.md`。它只定义 Meta Reviewer 如何读取论文与 Five-Review Bundle 并产生 Final Review，不属于 `REVIEW_Request.md`、Reviewer Role 或 Specialist Evolution。
_Avoid_: 第六个 Reviewer Role、META Program prompt

**Final Review**:
交给外部评测器的最终一级数据。启用 Meta 时它是 Meta Reviewer 的完整输出；禁用 Meta 时它是带固定角色标题的 Five-Review Bundle 确定性拼接。
_Avoid_: Specialist Review、CoCo input chunk、Evolution reward

## 记忆与检索

**Reviewer Memory**:
一个 Specialist Reviewer 独占的历史审稿知识集合。不同角色之间不共享 Memory 内容或写入状态。
_Avoid_: Global Memory、Shared Memory

**Memory Strategy**:
决定如何从已保存的完整 Review 派生检索单元、建立索引并确定性召回证据的版本化规则。
_Avoid_: Reviewer Role、CoCo strategy

**Derived Memory**:
Evolution Run 从可追溯的完整 Review 与 Internal Evaluation 中为一个 Program Version 生成的冻结记忆资产。它可以在 Evolution 时由模型归纳，但在 Review Run 中只接受确定性检索。
_Avoid_: Canonical Review、online memory

**Evolution Feedback**:
新 Program Version 在 development papers 上产生的完整 Review 与 Internal Evaluation。它只修正后续 Reflection、Evolution 方法和方向，永不写入冻结 Reviewer Memory。
_Avoid_: Memory writeback、Canonical Review

**Memory Item Envelope**:
所有 Program 可变 Memory payload 外层的最小溯源结构，固定包含 item、role、source review/evaluation 和 derivation identity；payload schema 由 Program Version 自由定义。
_Avoid_: Fixed experience ontology、paper copy

**KnowledgeItem**:
MemoryProgram 写入 KnowledgeBase 的结构化 Review Experience。它可以在离线 Evolution 或 Experience Learner build 中由生成模型产生或修改，但不能在 Review Run 中生成。
_Avoid_: Paper chunk、online writeback

**Query**:
Review Run 根据冻结论文 JSON 和 Reviewer Role 确定性构造的 Memory 查询对象。它不能由生成模型改写、分解、修复或重试。
_Avoid_: LLM query、search prompt

**KnowledgeBase**:
MemoryProgram 拥有的 role-local 状态容器。`write` 只在离线 Memory Build 中可用；snapshot 冻结后，Review Run 只能调用 `read`。
_Avoid_: Global memory、mutable review-time store

**Memory Snapshot**:
一个 Program Version 从共享 Canonical Review Corpus 独立构建并冻结的 KnowledgeBase 状态。子代继承 Program/lineage，不继承父代物化 snapshot。
_Avoid_: Parent runtime state、online checkpoint

**Reflector Feedback Packet**:
某个父代的源码、静态总分和五个 rotating failure cases 组成的离线变异输入。它不包含 human review、CoCo、其他角色或 evaluator hidden reasoning。
_Avoid_: Global evaluator trace、benchmark feedback

**Evolution Attempt**:
一次父代选择、Reflector patch、可选一次 repair、编译/smoke 和 candidate outcome 提交。无论成功或失败都消费一次固定预算。
_Avoid_: Successful child count、hidden retry

**Candidate Sandbox**:
在独立受限进程中运行 M★ MemoryProgram 的透明安全层。它保持 M★ Program/Toolkit 接口，不赋予网络、CoCo 路径或任意宿主文件访问能力。
_Avoid_: Research protocol、in-process exec

**Run Record**:
一个 stage 的轻量运行记录，只绑定配置、输入/输出 artifact IDs、模型、seed 与状态。
_Avoid_: Global readiness manifest、audit registry

**Frozen System Bundle**:
正式 Review runtime 所需的五个 Program/Memory、冻结 Reviewer identity、模型、Retrieval/S2 和 Meta/concat 配置集合。Evaluator 与 Reflector 只作为来源链接，不进入 runtime。
_Avoid_: Evolution run、readiness gate

**Tiny E2E**:
用极小论文集和 Evolution 预算验证 Review、Memory、Evolution、Meta/concat 及 artifact 边界的能力实验。它不产生正式 winner，也不运行 CoCo。
_Avoid_: Formal experiment、schema-only smoke

**Paper-Local Failure**:
单篇论文的 Review 或 Internal Evaluation 在一次相同请求重试后仍失败。它保留失败产物并让其他论文继续；static evaluation 中按最低质量分计入。
_Avoid_: Global run failure、silent omission

**Candidate-Local Failure**:
某 Program Version 的 build、snapshot 或通用执行失败。对应 attempt 失败且 candidate 不入 pool，但其他 attempts 继续。
_Avoid_: Infrastructure failure、paper error

**Infrastructure Failure**:
模型服务、磁盘或 checkpoint 等共享依赖不可用。当前 run 停止，并从最后已提交 artifact 恢复。
_Avoid_: Candidate rejection、low score

**Immutable Artifact**:
通过临时文件和原子替换提交后不再原地修改的一级数据文件。索引、JSONL、Chunk 和 embedding 可以由它重建。
_Avoid_: Mutable result row、derived index

**Retrieval**:
从 Frozen S2 Evidence 或 Reviewer Memory 中确定性选择并冻结证据 ID 的阶段。它可以使用 embedding、BM25、cosine similarity、metadata filtering 和非生成式 reranking；只有 LLM Summarizer Seed 可以在 Retrieval 结束后对已冻结的命中集合做生成式摘要。
_Avoid_: LLM query rewrite、LLM filtering、LLM relevance judgment

**LLM Summarizer Seed**:
三个初始 Memory Seed 中唯一允许在 Review 前增加生成调用的 Program。它先完成确定性 Retrieval 并冻结命中 ID，再摘要这些固定证据；摘要不能改变召回集合或触发 retry。
_Avoid_: Generative Retrieval、silent summarization

**Frozen S2 Evidence**:
在数据准备阶段从 Semantic Scholar 获取并冻结的本地文献与元数据。正式 Review 和 Evolution 只读取其冻结版本。
_Avoid_: Live S2、runtime S2

**Frozen S2 Pool**:
以规范论文标题在数据准备阶段查询、排除目标论文及其 family/version 副本后，按稳定 `paper_id` 冻结的 S2 evidence 集合。External Development Corpus 与 CoCo-1300 使用不同 Pool，Review Run 只按 ID 本地读取。
_Avoid_: Shared live database、title search at runtime

**S2 Capability**:
Program Version 对当前论文的 Frozen S2 Evidence 进行受限查询、选择和使用的能力。Capability 可用不表示 Program 实际查询或注入了证据。
_Avoid_: Always-on S2、固定 S2 输入

**Autonomous S2 Policy**:
Program Version 针对每篇论文与 Reviewer Role 自主决定是否查询 S2、选择哪些 evidence，以及如何约束其用途的策略。
_Avoid_: Dataset-level S2 toggle、Primary S2 开关

**S2 Disposition**:
一次 Review 对 S2 Capability 的结果分类：未查询、查询后不用，或选择并注入证据。它区分 Capability availability、consultation 与实际 injection。
_Avoid_: S2 enabled、S2 hit

**Diagnostic S2 Intervention**:
为归因诊断预注册的强制查询或屏蔽 S2 操作。它拥有独立实验身份，不属于 Primary，也不参与 winner 选择。
_Avoid_: Primary policy、默认 S2 配置

**S2 Usage Summary**:
基于 Review provenance 汇总 Program 自主查询、注入、查询后不用和空结果行为的派生报告。强制诊断样本不进入自主策略使用率分母。
_Avoid_: S2 quality attribution、CoCo score

**Factorial Attribution Diagnostic**:
在冻结 development 数据上组合 baseline/evolved Memory 与 baseline/evolved Autonomous S2 Policy 的四格诊断。它估计两个主效应及交互，不改变 Primary winner。
_Avoid_: Primary comparison、forced-consult/off comparison

**Review Experience**:
从完整 Specialist Review 派生的可复用审稿知识，例如审查启发、证据模式、失败模式与方法学检查点。它可以引用来源论文身份，但不复制来源论文正文充当 Memory。
_Avoid_: Paper chunk、S2 paper、raw manuscript copy

**S2 Self Match**:
S2 exact-title search 返回的目标论文本身或同 family/version 副本。它必须在数据准备阶段通过稳定 ID、外部 ID、family title alias 等确定性规则排除。
_Avoid_: Related literature、valid external evidence

**Memory Chunk**:
从 Specialist Review 派生的二级检索单元。每个 Chunk 必须能追溯到唯一原始 Review、派生规则及其在原文中的位置。
_Avoid_: Review、source of truth

**Retrieval Provenance**:
一次 Retrieval 的可复核来源记录，包括 Memory/S2 资产版本、查询构造版本、命中项、分数、顺序和过滤规则。
_Avoid_: CoCo trace、judge rationale

## 运行与演化

**Review Run**:
只执行“论文与冻结证据 → 五份 Specialist Review → Final Review”的运行。它不执行 Reflection、Evolution 或 CoCo Evaluation。
_Avoid_: Evolution Run、benchmark run

**Internal Evaluation**:
Evolution Run 对已保存 Specialist Review 执行的批量模型评审。它为 Reflection 提供质性反馈，不属于在线 Review 流程，也不使用 CoCo 信息。
_Avoid_: CoCo score、Official Evaluation

**Internal Evaluator**:
执行 Internal Evaluation 的模型角色。它默认可以与 Reviewer 共用同一基础模型以研究 self-critique，也可以通过同一接口替换成独立 evaluator model。
_Avoid_: CoCo Judge、online evaluator

**Review Quality**:
Internal Evaluator 给一份 Specialist Review 的单一 `1–5` 整体质量分。固定 batch 的平均值仅用于候选排序与父代选择，质性反馈才是 Reflection 的主要输入。
_Avoid_: Weighted reward、CoCo score

**Evolution Run**:
读取已保存的 Review，依次执行批量 Internal Evaluation、Reflection 和 candidate 生成，产生新的 Program Version。它不在内部发起 Review 请求。
_Avoid_: Review Run、online writeback

**Program Version**:
某一 Reviewer Role 的可执行 Memory、Retrieval、Workflow 与 Review Strategy 版本。它不能修改仅含五个 Specialist 定义的冻结 `REVIEW_Request.md` 或 Reviewer Role，并且不与其他角色或 Meta Reviewer 共享 lineage。
_Avoid_: Global candidate、META Program

**Review Strategy**:
Program Version 提供的、位于冻结 Review Request 之外的审稿方法指令。它可以改变证据使用与检查顺序，但第一版不能增加额外模型调用或改写角色职责。
_Avoid_: Reviewer Role、REVIEW_Request

**Reflection**:
从已保存 Review 与 Internal Evaluation 中归纳失败模式和改进方向的生成阶段。
_Avoid_: CoCo analysis、retrieval retry

## 外部评测

**External Development Corpus**:
从 `OpenReview/train_clean` 选择、与 CoCo benchmark 分开，并经过 exact ID、canonical title、paperhash 和内容 hash 最小直接去重的论文集合。
_Avoid_: CoCo benchmark papers、family audit graph

**Sealed CoCo Test**:
从 CoCo benchmark 预先冻结、在 Program、Memory、Meta 配置全部冻结后才打开的外部测试集合。
_Avoid_: Development split、fitness set

**CoCo-1300 Protocol-Compatible Resample**:
从发布的 3,900 篇 CoCo 论文中按 13 个 venue-year 各冻结抽取 100 篇、保持分数分布的最终测试集合。作者原始 1,300 IDs 未发布，因此本集合是可复现重采样，不称为 official split。
_Avoid_: CoCo training set、official 1300 IDs

**CoCo Evaluation**:
在 Final Review 已永久保存后独立运行的外部测试。CoCo 的 taxonomy、reference、Split/Classify、judge 和 score 只存在于该边界内。
_Avoid_: Internal Evaluation、Reviewer component

**CoCo Firewall**:
隔离 CoCo 资产与结果的系统边界。Reviewer、Memory、Retrieval、Reflection 和 Evolution 均不能读取边界内数据。
_Avoid_: prompt-level hiding

**Primary Meta System**:
在打开 CoCo 前冻结的主系统，固定使用五份 Specialist Review 和无状态 Meta Reviewer 生成 Final Review。
_Avoid_: Post-CoCo winner

**Concat Ablation**:
与 Primary Meta System 共用同一 Five-Review Bundle、按固定顺序直接拼接的预注册消融系统。
_Avoid_: Fallback Meta
