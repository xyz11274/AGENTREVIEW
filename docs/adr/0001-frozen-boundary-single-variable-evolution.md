# 冻结边界：单变量原则下的 MemoryProgram Evolution

> **状态：部分被 ADR-0005 supersede（2026-08-17）**。本 ADR 的「MemoryProgram-only evolution」仅描述**当前已实现阶段 / 特定单变量 effectiveness 实验**的范围，**不是系统级永久限定**——Review Harness Evolution 不被禁止，系统级 Frozen/Evolvable Boundary 以 ADR-0005 为准。「冻结 reviewer prompt」收窄为冻结 Frozen Reviewer Identity（职责与 rubric 定义）；Engineering Budget 参数化（ADR-0007）。仍然成立的：单变量归因原则（同一实验只让一个变量族变化）、冻结部分的运行时强制（SHA256 校验）。


本 ADR 记录的是当前已实现阶段中针对 MemoryProgram 的单变量 effectiveness 实验，而不是 M★ 的系统级永久边界。

在该特定实验中，为了回答「其他条件相同，仅改变 MemoryProgram 是否能够提高审稿质量」这一局部研究问题，只允许 MemoryProgram 这一变量族发生变化，其余实验条件在同一次正式对照中保持一致。

M★ 的系统级目标架构不受 MemoryProgram-only 限制。Review Harness 的 Frozen / Evolvable Boundary 以 ADR-0005 为唯一权威定义：Frozen Reviewer Identity 永久固定，Memory、workflow、context、evidence、retrieval、critique / repair 等 Harness 组件允许在后续实验中逐步进入进化空间。

## Considered Options

- **当前实验采用 MemoryProgram-only 单变量比较**：被采纳，仅适用于本阶段 effectiveness 实验。
- **在当前 MemoryProgram effectiveness 实验中同时改变多个 Harness 组件**：本实验不采用。原因是会破坏当前实验的单变量归因。该结论不代表系统级永久否决 Review Harness Evolution。

## Consequences

- 单变量控制是具体实验的方法学要求，而不是 M★ 系统永久只能进化 MemoryProgram 的架构限制。若后续将新的 Harness 组件纳入进化，应作为新的变量族和实验阶段明确记录。
- 代码中所有 SHA-256 校验和检查（rubric、classifier、embedding adapter、seed programs、data manifest）是这一决策的执行机制，不得为了"跑通"而绕过。
- 若 MemoryProgram Evolution 的有效性得到验证，后续工作可以逐层解冻（retrieval → context strategy → 更完整的 harness evolution），每次解冻应作为新的实验变量单独归因，并考虑另立 ADR。
- 在线/continual evolution 同样被明确排除在当前主张之外（见 CONTEXT.md 的 Offline Evolution）。
