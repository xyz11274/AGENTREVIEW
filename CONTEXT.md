# AgentReview 领域术语

本文定义仓库使用的规范术语。完整模块边界与执行流见 `docs/ARCHITECTURE.md`。

## 审稿领域

**AgentReview**

以五位独立 Specialist Reviewer 和一位 Final Meta-Reviewer 完成论文审稿的系统。

**Frozen Reviewer Identity**

六个 Reviewer Task 的职责、完整 system prompt、请求协议与输出语义组成的冻结身份。`REVIEW_Request.md` 是内容源，`reviewer_identity.py` 负责唯一渲染，`frozen_reviewer_identity_v2.json` 记录 source、task prompt、Specialist/META request template 与 composite suite SHA256。

**Specialist Task**

五个相互独立的审稿任务：`QUAL` 评价研究质量，`CLAR` 评价表达清晰度，`SIGN` 评价研究意义，`ORIG` 评价原创性，`POL` 评价写作规范与政策风险。

**META Task**

按 `meta-synthesis-request-v2` 读取同一论文的 Canonical Paper Context（正文与 Source Anchors）以及按固定顺序排列的五份 Specialist Review，完成跨维度综合与最终 Meta Review 的任务。

**Rubric Subdimension**

Frozen Reviewer Identity 内部的细分审稿标签，例如方法质量、实验质量、可复现性与比较完整性。

**Specialist Review Bundle**

同一论文对应的五份 Specialist Review 的结构化集合。META 以该集合为唯一 Specialist 输入。

**Canonical Paper Context**

由论文 Markdown 构造的规范论文上下文，包含固定字段、内容版本和来源信息。

**Source Anchor**

把 Reviewer 引用的证据定位回论文段落或标题区域的稳定标识。

**Review Request**

Reviewer Core 接收的目标审稿请求，携带论文身份、任务身份、规范上下文和溯源信息。

**Review Result**

一次审稿调用产生的正文、任务身份、程序身份和运行溯源记录。

## 记忆领域

**Episode**

一篇历史论文及其 Reviewer 自生成 Review 构成的顺序经验单元。

**Historical Experience Protocol**

`self_generated_after_review` 协议：Program 先对历史论文生成 Review，再把该 Review 写入自身 Memory；A 区 Episode 按冻结顺序 replay。

**MemoryProgram**

实现固定协议的可执行记忆程序。Program 定义指令、`KnowledgeItem`、`Query` 和 `KnowledgeBase(toolkit)`；写接口为 `write(item, raw_text)`，读接口为 `read(query) -> str`。

**KnowledgeItem**

TaskAgent 根据历史论文与自生成 Review 构造的结构化写入对象。

**Query**

TaskAgent 根据目标论文与 Reviewer Task 构造的结构化检索对象。

**KnowledgeBase**

MemoryProgram 拥有的状态容器，负责写入历史经验、读取目标相关经验以及生成可恢复快照。

**TaskAgent**

固定的 LLM 驱动 Adapter，负责生成 `KnowledgeItem`、`Query` 和最终 Review。Memory Evolution 改变 MemoryProgram，TaskAgent 保持稳定。

**Reviewer Core**

拥有 MemoryProgram 加载、隔离执行、TaskAgent、session 状态、snapshot/restore、严格产物和 capability 边界的稳定内核。

**Reviewer Session**

一个 Program、一个 Reviewer Task 和一组能力组成的有状态评审会话。

**Memory Snapshot**

KnowledgeBase 在完成历史 replay 后的可序列化状态。同一执行面内的每个目标从该执行面冻结的同一历史基准快照开始。

**Memory Toolkit**

宿主向 MemoryProgram 提供 LLM、embedding、冻结 S2、向量存储和日志能力的 Adapter。

**Capability**

Sandbox 通过窄接口访问的宿主操作。调用计数、输入范围和资源预算属于 capability 合约。

**Sandbox Wire Protocol**

Reviewer Core 宿主与候选 worker 之间的逐行 JSON command/capability 协议。宿主发送 `command`，worker 返回 `result` 或发起 `tool_call`，宿主以 `tool_result` 应答。

**Legacy Memory Library**

生产编排入口在 `existing` 模式下使用的 JSONL 经验库与词法检索接口。该接口服务历史分析和兼容运行。

## 演化领域

**Seed Program**

搜索池的三个起始 MemoryProgram：`vector_search`、`llm_summarizer`、`experience_learner`。

**Evolution Attempt**

一次完整的父代选择、Reflector 变异、质量检查、编译检查和评估机会。当前正式预算为 20 次，所有结果均消费一次预算。

**Quality-Passing Pool**

通过协议、源码、编译和质量门禁并完成 Static Evaluation 的全局候选集合。父代以分数 softmax 从该集合选择。

**Lineage**

候选从 Seed Program 到子代的来源链。Lineage 用于溯源、champion 统计和可视化。

**Static Selection**

从 B 区冻结的固定评估子集，用于跨候选可比评分。

**Historical Episode Selection**

从 A 区冻结的 Program replay 子集。Evolution 默认预算为 Static Selection 的五倍，即 25 个 B target 对应 125 个 A Episode；Reviewer Core、部署与 Effectiveness 分别冻结自身调用范围。

**Rotating Selection**

从 B 区按 iteration 确定性选择的反馈子集，用于生成新的变异信号。

**Reflector**

读取允许反馈并生成完整 MemoryProgram 子代源码的 LLM 组件。

**Candidate Review Record**

候选在某个目标上的严格 JSONL 评审产物，携带 schema 版本、身份哈希与 provenance。

**Promoted Program**

经选择后写入 `best_program.py` 并由 promotion manifest 绑定源码哈希的 MemoryProgram。manifest 以自身目录解析 `program` 与 `source_run`，并从 `<source_run>/config.json` 校验 embedding provenance。

**Outer Seed**

整次 Evolution 搜索的实验随机种子。Outer Seed 与池内三个 Seed Program 属于两个独立概念。

## 数据与评测领域

**Paper Family**

由规范标题与 paperhash 的传递闭包归并得到的论文提交族。正式 split 每个 family 选择一个代表目标。

**Zone A — Historical Experience**

用于顺序 replay 和构建 Program Memory 的历史论文区。

**Zone B — Evolution Validation**

用于 Static Selection、Rotating Selection 和 META validation 分配的演化验证区。

**Zone C — Effectiveness Holdout**

用于 seed/evolved/no-memory 效果筛选的隔离留出区。

**Zone D — Held-out Benchmark Test**

用于冻结套件最终评测的一次性测试区。

**Frozen S2 Pool**

按目标论文、规范查询标题和时间 cutoff 绑定的 Semantic Scholar 检索资产。

**Asset Manifest**

用相对路径、版本和 SHA256 绑定 Frozen Reviewer Identity、split、S2、Seed Program 与 embedding 身份的正式资产索引。

**Formal Protocol Gate**

在正式演化与最终评测前统一校验资产、模型端点、程序身份、数据区和运行 provenance 的入口。

**Proxy Screening**

Effectiveness Screening 使用的 CoCo-style 代理指标流程，用于 C 区效果筛选。

**Sealed D Suite Evaluation**

`final-evaluate-suite` 在 formal gate 后运行的六任务 D 区测量。它在 Review 生成后通过冻结 ReviewSplit/ReviewClassify、`CoCoMetricBenchmarkAdapter` 与 `CoCoReviewFitness` 内联计分，并持久化紧凑套件报告；结果身份为 `sealed_d_coco_style`。

**Specialists-Only Sealed D Evaluation**

`final-evaluate-suite --specialists-only` 在同一 formal gate 后，只运行 `suite_manifest.promoted` 绑定的五个 overall Specialist champion，不要求或执行 META。结果身份为 `sealed_d_specialists_coco_style`，不得与六任务套件结果混称。

为端到端时延试验，可在 `--specialists-only` 下使用
`--max-specialist-test-items N --specialist-test-subset-seed S`。它仍执行完整的历史
memory replay、D 生成和 CoCo Adapter 评分，但按任务、seed 与 EvalItem ID 的稳定
SHA-256 排序只取 D 子集；结果身份为 `d_subset_specialists_coco_style`，且
`final_test=false`、`non_reportable=true`，不得作为 sealed-D 正式结果。

**Official CoCo Evaluation**

`agentreview.coco_eval` 读取已持久化的 `candidate_reviews.jsonl`，调用 CoCoReviewBench 自带 split、classify 和 evaluation 程序，并持久化各阶段产物形成的外部评测结果。

## 系统边界

**Production Orchestrator**

执行 Markdown 抽取、五 Specialist 并发审稿、META 汇总、checkpoint 和在线 memory writeback 的部署入口。

**Reviewer Core CLI**

直接以 MemoryProgram 驱动完整 review/write/query/read/response 协议的稳定候选审稿入口。

**Result Viewer**

读取部署审稿 JSON 产物的只读 UI。

**Evolution Viewer**

读取 M★ `pool`、`lineage.jsonl`、`scores.jsonl` 和 trace 的独立只读 UI。

**Dormant Interface**

仓库保留的暂未启用接缝。当前集合包括 Arena/Supabase 快照、Bard 与 LangChain Provider Interface、BGE-M3 Adapter、OpenScholar core/API 字符串快照和 OpenReview 数据导入；Anthropic、Cohere、Transformers、Human 与 Dummy 属于已注册的可选 Provider Adapter。
