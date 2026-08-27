# AgentReview Review Memory Evolution

AgentReview 通过离线 Evolution 改进专业审稿所使用的 Memory 策略。Reviewer 的职责保持冻结，真人参考与 Benchmark 始终位于 Candidate 不可见的外部边界。

## Language

**Frozen Reviewer Identity**:
`QUAL`、`CLAR`、`SIGN`、`ORIG`、`POL` 五类 Specialist Reviewer 的固定职责及 rubric 子维度。Evolution 不得改变这些职责。
_Avoid_: 完整冻结的 reviewer prompt

**Full Review Corpus**:
由统一的 no-memory Reviewer 在冻结的 A 区论文上预先生成、供所有 MemoryProgram 共享的 AI review 集合。它不包含真人 review、gold opinion 或 evaluator 信息。
_Avoid_: Self-Generated Experience、human review library、初始 memory 数据库

**Full Review Record**:
Full Review Corpus 中一篇完整、不可变且可按内容哈希寻址的 AI review，是所有检索切片的权威原文。
_Avoid_: chunk、KnowledgeItem、向量记录

**Retrieval Projection**:
从 Full Review Record 确定性派生、可随时重建的检索视图。每条 Projection 必须能定位回原 Review，Projection 的丢失或重建不影响原文。
_Avoid_: 原版 review、权威 memory 文本

**Rubric Subdimension**:
Frozen Reviewer Identity 中的细分审稿标签，例如 `QUAL-MET`、`QUAL-EXP`、`QUAL-REP`、`QUAL-CMP`、`QUAL-STA`。它来自 Reviewer 输出协议，不是 CoCo taxonomy 或 gold label。
_Avoid_: CoCo category、evaluator label

**MemoryProgram**:
在共享 Full Review Corpus 上定义确定性存储投影、检索查询、排序和 Memory 使用策略的可执行程序。运行期 Query 构造与 Review 投影不得调用生成式模型。
_Avoid_: review 数据库、LLM 摘要器、prompt 模板

**Evolution Reflector**:
Evolution 阶段根据允许的外部反馈提出 MemoryProgram 子代修改的 LLM。它不参与运行期 Memory Query、Review 投影或数据库构建。
_Avoid_: Reviewer LLM、Memory Query LLM

**Specialist Review Bundle**:
同一论文的五份独立 Specialist Review 组成的规范输出。它在不运行 Meta-Reviewer 时仍是完整有效的系统产物。
_Avoid_: meta-review、拼接字符串

**META-OFF Export**:
将 Specialist Review Bundle 按冻结角色顺序确定性拼接成单个文本的导出形式，不调用 LLM，也不替代 Bundle 作为权威产物。
_Avoid_: meta-review、LLM synthesis

**META-LLM Export**:
显式启用 Meta-Reviewer，对 Specialist Review Bundle 进行 LLM 重组织后得到的实验性导出形式。
_Avoid_: 默认最终输出

**Human Reference Boundary**:
真人 review、gold opinion、Benchmark taxonomy 与 judge 信息只允许进入外部 fitness 或最终评测，不得进入 Full Review Corpus、MemoryProgram 或 Reviewer 输入。

