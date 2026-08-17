# Frozen Reviewer Identity + Evolvable Review Harness，Benchmark 居于最外层

系统边界的核心定义。

**可进化外围（Review Harness）**：Memory、review workflow、context organization、evidence strategy、retrieval strategy、Semantic Scholar 的使用、review generation / critique / repair strategy 及后续合理组件。**最终评价层**：CoCoReviewBench 为第一个正式 Benchmark，经 Benchmark Adapter 接入，系统 benchmark-agnostic；Reviewer 不应知道自己正被哪个 Benchmark 评分。

**固定核心**：Frozen Reviewer Identity（五类职责与子维度）、统一能力接口的语义边界、Benchmark 与 Reviewer 的隔离、外部评价权。

**实验内冻结项**：Base LLM、生成参数及正式比较所需的其他运行条件，在同一次对照实验内保持一致；它们不是 M★ 的永久系统身份。跨独立实验可以把 Base LLM 作为新的受控实验变量更换，用于验证跨 backbone 泛化。

配套边界：

1. **Human Reference Boundary**——真人 review / gold 只用于外部 fitness 与最终评测，不得进入候选可见输入。`pilot_full` 把 `VERIFIED HUMAN REFERENCE OPINIONS` 拼进 episode 属边界违反，修复前其进化产物（含 iter5）不作为 effectiveness 结论的权威 Program，仅作历史分析。
2. **共享题目，不共享答案**——所有 Seed 共享同一批原始历史论文（**论文全文 + candidate-visible metadata + source anchors**；related work 不默认注入，经统一 Semantic Scholar Capability 按需调用，复现所需的 retrieval cache 在系统层维护、仍经 capability 接口取得），各自运行自己的审稿策略、自产经验、自建 Memory；废除「固定 no-memory Reviewer 预生成 AI review 供二次加工」的 Frozen Library Route。episode 中的 metadata 必须二分：candidate-visible（paper_id / title / venue / year / reviewer role 等）可入 prompt，internal provenance（paper_md_path / split / SHA256 / run_id / manifest 等）默认留在系统内部。
3. **Engineering Budget 参数化**——LLM 调用次数/操作、返回字符上限等是工程预算而非方法学冻结；接口参数化，默认值为最小实现服务。永久冻结的是 Reviewer Identity、Benchmark / Candidate 隔离原则和外部评价权；统一能力接口保持一致的能力语义。Base LLM 与具体生成参数属于实验内冻结条件，不属于系统永久身份，跨独立实验可作为受控变量更换。

## Considered Options

- **整个 Reviewer prompt 完全冻结**（ADR-0001 的原始表述）：被修订。它把 workflow、context organization、自检查等本可进化的 Harness 策略也锁死，搜索空间被人为压扁。
- **一切皆可进化（含 Reviewer 职责）**：被否决。职责漂移则实验对象本身改变，效果无从归因。
- **Identity 冻结 + Harness 进化 + Benchmark 最外层**：被采纳。

## Consequences

- ADR-0001 的「冻结 reviewer rubric/prompt」按本 ADR 收窄：冻结的是职责与 rubric 定义，Harness 侧策略开放进化。
- ADR-0002 的「共享语料」按本 ADR 收窄为 Shared Raw Material（原始论文）；语料之上的经验层由各 Program 自产，分叉从经验生成就开始了。
- iter5 降级为历史分析材料；`frozen_memory_library` 路线标记 legacy，不补完剩余 history generation（见 docs/DEPRECATED.md）。
- 待办（见 docs/TODO.md）：episode 构建器剥离真人参照；工程预算参数化；统一 rich paper interface；Benchmark Adapter 抽象；按 Program 各自生成历史经验。
