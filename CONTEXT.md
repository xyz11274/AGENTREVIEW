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
可选的无状态汇总者，只读取 Frozen Paper 与 Five-Review Bundle，按固定生成契约产出结构化观点与 recommendation。它作为共享候选评价链路中的固定组件，生成供 CoCo 评价的 Final Review。
_Avoid_: 第六个 Specialist、META Program

**Meta Synthesis Request**:
独立版本化的 Meta 生成契约，定义从论文与 Five-Review Bundle 生成观点、来源、证据引用、建议和 recommendation 的结构。每个观点和建议具有独立边界，结构校验验证字段与引用身份。
_Avoid_: 第六个 Reviewer Role、META Program prompt

**Final Review**:
交给 CoCo 评测器的最终一级数据。启用 Meta 时它是结构化观点及建议逐项确定性渲染的完整评审；禁用 Meta 时它是带固定角色标题的 Five-Review Bundle 确定性拼接。
_Avoid_: Specialist Review、CoCo input chunk、Evolution reward

## 记忆与检索

**Memory Seed**:
自进化搜索的初始记忆组织和使用方案，包括 Vector Search、LLM Summarizer 与 Experience Learner。每个方案供五角色共同使用，本轮讨论中的 seed 均指这些初始方案。
_Avoid_: 未注明含义的 seed、random seed

**Search Random Seed**:
控制一次自进化搜索中随机选择的数值种子。不同 Search Random Seed 对应重复搜索，用于观察结果对随机选择的敏感性。
_Avoid_: Memory Seed、初始记忆程序

**Reviewer Memory**:
一个 Specialist Reviewer 独占的历史审稿知识集合。不同角色之间不共享 Memory 内容或写入状态。
_Avoid_: Global Memory、Shared Memory

**No-Memory Control**:
与对应记忆组保留相同五角色、审稿策略、汇总流程及其他实验条件，仅关闭历史记忆检索和注入的 AgentReview 对照组。
_Avoid_: CoCo Native Baseline、同时关闭 S2 的对照

**Memory Strategy**:
五角色共同采用的记忆组织、构建、检索和使用规则，涵盖记忆数据格式、读写逻辑与记忆交互指令。它描述如何处理记忆，各角色的 Memory 内容和运行状态保持独立。
_Avoid_: Reviewer Role、CoCo strategy

**Derived Memory**:
共享 Program Version 为某一角色从获准的开发来源生成的冻结记忆资产。它可以在离线构建时由模型归纳，运行时归属于该角色。
_Avoid_: Canonical Review、online memory

**Evolution Feedback**:
Program Version 在开发论文上产生的完整 Review 与标准 CoCo 开发评分反馈。它用于改进后续 Reflection 和 Evolution，并保留开发数据来源身份。
_Avoid_: Memory writeback、Canonical Review

**Memory Item Envelope**:
所有 Program 可变 Memory payload 外层的最小溯源结构，固定包含 item、role、source review/evaluation 和 derivation identity；payload schema 由 Program Version 自由定义。
_Avoid_: Fixed experience ontology、paper copy

**KnowledgeItem**:
MemoryProgram 写入 KnowledgeBase 的结构化 Review Experience。它可以在离线 Evolution 或 Experience Learner build 中由生成模型产生或修改，但不能在 Review Run 中生成。
_Avoid_: Paper chunk、online writeback

**Query**:
Review Run 根据冻结论文和 Reviewer Role 构造的 Memory 查询对象，其格式和含义由 MemoryProgram 的查询结构与记忆交互指令定义。构造方式与可用工具遵循冻结的实验协议。
_Avoid_: 检索结果、外部论文查询

**KnowledgeBase**:
MemoryProgram 拥有的 role-local 状态容器。`write` 只在离线 Memory Build 中可用；snapshot 冻结后，Review Run 只能调用 `read`。
_Avoid_: Global memory、mutable review-time store

**Memory Snapshot**:
某一角色在共享 Program Version 下构建并冻结的 KnowledgeBase 状态。每份 snapshot 同时具有角色、策略版本与来源语料身份。
_Avoid_: Parent runtime state、online checkpoint

**Reflector Feedback Packet**:
某个父代的源码、开发评分和允许用于反思的开发案例组成的离线变异输入。所有案例与反馈均可追溯到开发分区。
_Avoid_: Global evaluator trace、benchmark feedback

**Evolution Attempt**:
一次父代选择、当前轮开发反馈、记忆程序变异、有限修复及候选结果记录。成功与失败均消费预先记录的尝试预算，恢复沿用已消费记录。
_Avoid_: Successful child count、hidden retry

**Candidate Sandbox**:
在独立受限进程中运行 M★ MemoryProgram 的透明安全层。它保持 M★ Program/Toolkit 接口，不赋予网络、CoCo 路径或任意宿主文件访问能力。
_Avoid_: Research protocol、in-process exec

**Run Record**:
一个 stage 的轻量运行记录，只绑定配置、输入/输出 artifact IDs、模型、seed 与状态。
_Avoid_: Global readiness manifest、audit registry

**Frozen System Bundle**:
正式 Review 所需的共享 Program Version、五份角色 Memory、固定 Reviewer 职责、模型、Retrieval/S2 和汇总配置的冻结集合。
_Avoid_: Evolution run、readiness gate

**Tiny E2E**:
用极小开发论文集和 Evolution 预算验证 Review、Memory、Evolution、Meta/concat 与评测交接的能力实验。它属于开发验证，与封存最终测试具有独立身份。
_Avoid_: Formal experiment、schema-only smoke

**Paper-Local Failure**:
单篇论文在 Review 或评分阶段耗尽该阶段重试预算后仍失败的结果。失败论文保留在原定样本集合中，并具有可追溯的阶段与失败原因。
_Avoid_: Global run failure、silent omission

**Candidate-Local Failure**:
某 Program Version 经可复核检查确认违反预先冻结的执行或输出契约。有限修复耗尽后结束对应 attempt，记录违规证据，其他合格候选继续。
_Avoid_: Infrastructure failure、paper error

**Infrastructure Failure**:
模型服务、磁盘或 checkpoint 等共享依赖不可用。当前 Evolution Run 暂停，修复验证后从已提交产物及持久化预算恢复。
_Avoid_: Candidate rejection、low score

**Evaluation Unavailable**:
已冻结输出因共享数据、评价管道或归因未定的问题而无法获得有效评分的状态。对应论文保留在原定集合中，以失败状态和空分数表示。
_Avoid_: 候选低分、失败惩罚分

**Evaluation Epoch**:
共享故障修复后，为受影响比较统一建立的评价版本。该版本的全部候选与对照使用相同样本和评分身份，完整重评后恢复比较。
_Avoid_: 搜索轮次、单篇重试次数

**Evaluation Receipt**:
候选评价的完整凭据，绑定原定论文清单、冻结输出、程序与 Memory 身份、评分协议、逐篇结果和聚合成绩。候选准入与发布依据同一 Evaluation Epoch 下可验证且可比的凭据。
_Avoid_: 子集均分、未溯源分数

**Repair Evidence**:
恢复共享故障前保存的原因、修复内容和验证结果。它绑定恢复操作及新的 Evaluation Epoch，保留此前的失败与预算记录。
_Avoid_: 空白重试理由、重置预算

**Champion**:
在既有完整可比评价下选出的冻结最优候选。整轮修复或失败预算耗尽时保留其原有身份，本轮结果标记为未完成。
_Avoid_: 未完成比较的新 winner、成功子集最佳候选

**Immutable Artifact**:
通过临时文件和原子替换提交后不再原地修改的一级数据文件。索引、JSONL、Chunk 和 embedding 可以由它重建。
_Avoid_: Mutable result row、derived index

**Retrieval**:
根据查询从 Frozen S2 Evidence 或 Reviewer Memory 中选择并记录证据来源的阶段。查询构造、命中选择与生成式记忆加工具有各自身份，允许的操作及成本遵循冻结实验协议。
_Avoid_: Reflection、当前论文的来源证据

**LLM Summarizer Seed**:
三个初始 Memory Seed 之一。它先完成确定性 Retrieval 并冻结命中 ID，再将这些固定经验摘要为供审稿使用的记忆上下文。
_Avoid_: Generative Retrieval、silent summarization

**Frozen S2 Evidence**:
在数据准备阶段从 Semantic Scholar 获取并冻结的本地文献与元数据。正式 Review 和 Evolution 只读取其冻结版本。
_Avoid_: Live S2、runtime S2

**Frozen S2 Pool**:
以规范论文标题查询、排除目标论文及其 family/version 副本后冻结的 S2 evidence 集合。开发与最终测试使用各自具有分区身份和来源记录的 Pool。
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
0831 历史协议中，对已保存 Specialist Review 执行的整体质量内评。它与 0915 的标准 CoCo 开发评分是不同的评价协议。
_Avoid_: CoCo score、Official Evaluation

**Internal Evaluator**:
执行历史 Internal Evaluation 的模型角色。
_Avoid_: CoCo Judge、online evaluator

**Review Quality**:
历史 Internal Evaluator 给一份 Specialist Review 的单一 `1–5` 整体质量分。它记录旧实验的候选排序依据。
_Avoid_: Weighted reward、CoCo score

**Evolution Run**:
遵循 M★ 反思式记忆程序进化，在共同候选池中演化共享记忆策略的运行。它以开发 Review、记忆运行轨迹与标准 CoCo 开发评分支持变异和评价；候选供五角色共同使用，学习与候选选择材料具有明确的开发分区身份。
_Avoid_: Review Run、online writeback

**Evolution Fitness**:
共享候选的最终完整评审在固定开发评价集上，依据标准 CoCo Table-1 `Paper.` 聚合得到的选优成绩。五角色单独评分作为诊断材料，与此选优成绩分开记录。
_Avoid_: 历史 Review Quality、五个角色分别选优

**Consolidated Results Report**:
从已保存的原始评审、评分和运行记录独立生成的派生报告。它集中展示各 Memory Seed、候选与对照的逐篇明细和汇总成绩，保留数据分区、评价协议及失败状态。
_Avoid_: 原始评审、评分器、候选池

**Program Version**:
五个 Reviewer Role 共用的一份可执行记忆策略版本，构成进化池中的一个候选。它拥有统一谱系，每个角色在该版本下持有自己的记忆资产。
_Avoid_: 每角色独立候选、META Program

**Review Strategy**:
位于固定 Reviewer Role 职责之外的审稿方法指令，描述检查论文和使用证据的方法。
_Avoid_: Reviewer Role、REVIEW_Request

**Reflection**:
从已保存的开发 Review 与允许读取的开发评分反馈中归纳失败模式和改进方向的生成阶段。
_Avoid_: 测试结果调参、retrieval retry

## CoCo 评测与数据分区

**CoCo Native Baseline**:
使用同一本地基础模型，通过 CoCo 自带的审稿提示与输出提取流程生成评审的对照系统。它与保留 AgentReview 审稿流程但关闭记忆的对照系统具有不同身份。
_Avoid_: AgentReview no-memory、无条件基础能力

**CoCo Opinion Split**:
把完整评审拆成可独立评价的观点的阶段。
_Avoid_: tokenizer 分词、类别分类

**CoCo Opinion Classification**:
为拆分后的观点分配 CoCo 类别标签的阶段。
_Avoid_: tokenizer 分词、观点拆分

**External Development Corpus**:
供 Memory 构建和系统开发使用、与最终测试按研究工作及其版本隔离的论文和参考资产集合。用于标准 CoCo 开发评分的样本具有相应的协议参考资产。
_Avoid_: 最终测试集、固定旧 OpenReview split

**Development CoCo Evaluation**:
在开发分区的已保存评审上执行标准 CoCo 协议的评分过程。其评分反馈可用于进化，全部输入与输出保留开发分区身份。
_Avoid_: Internal Evaluation、最终测试评分

**Research Work Family**:
同一研究工作的论文及其修订、预印本和发表版本组成的集合。跨开发与最终测试划分时，它们具有同一分区归属。
_Avoid_: 单一 paper ID、仅标题完全相同

**Exposed Historical Evaluation**:
过去执行过或查看过结果的评测集合。历史执行本身不等于用于开发；是否参与记忆构建、反思、选优或系统修改，须由实际用途记录区分。
_Avoid_: 自动判定泄漏、未经核对的开发集

**Development Use Evidence**:
将具体论文或研究版本与记忆构建、反思、候选选优、指导系统修改等实际用途关联的可核验记录。预先保留的开发样本具有明确开发归属；单纯历史评测或查看分数不自动产生开发用途。
_Avoid_: 运行过即泄漏、准备了数据即已消费

**Existing-Benchmark Paired Evaluation**:
在既有基准的同一论文集合和同一评价条件下，重新评价全部冻结的待比较系统，比较逐篇效果差值。主评价排除具有开发用途及相同研究版本关联的样本，保留历史用途不明的说明，不宣称严格未见盲测。
_Avoid_: 旧环境分数直接作对照、严格未见测试

**Sealed CoCo Test**:
与开发资产按研究工作及其版本隔离、在全部待比较系统和实验方案冻结后才运行最终生成与评价的论文集合。参考评审只供评分，测试结果不反馈开发。封存约束本轮用途，不自动表示该基准历史上从未运行过。
_Avoid_: Development split、fitness set

**CoCo-1300 Protocol-Compatible Resample**:
0831 历史实验从发布的 3,900 篇 CoCo 论文中按 13 个 venue-year 各抽取 100 篇得到的协议兼容重采样集合。原清单保持不变，可作为既有基准配对评价的基础；本轮主评价另行记录有依据的排除清单。
_Avoid_: CoCo training set、official 1300 IDs

**CoCo Evaluation**:
对已保存评审使用标准 CoCo 协议执行的评价过程。Development CoCo Evaluation 与最终测试复用协议，分别持有自己的样本、参考资产和结果。
_Avoid_: Internal Evaluation、Reviewer component

**CoCo Firewall**:
隔离最终 CoCo 测试资产及其衍生数据与开发活动的系统边界。开发评分反馈具有独立来源，可供 Evolution 使用。
_Avoid_: 全部 CoCo 信息禁用、prompt-level hiding

**Primary Meta System**:
在打开最终 CoCo 测试前冻结的主系统，使用五份 Specialist Review 和无状态 Meta Reviewer 生成 Final Review。
_Avoid_: Post-CoCo winner

**Concat Ablation**:
与 Primary Meta System 共用同一 Five-Review Bundle、按固定顺序直接拼接的预注册消融系统。
_Avoid_: Fallback Meta
