# TODO

工程待办，按实现依赖排序。完成一项划掉一项并注明日期。原则：系统设计不为某个 benchmark 服务，CoCo 官方 pipeline 准备是独立轨道（第 6 节）。

## 1. 边界正确性

- [ ] **修复 Human Reference Boundary**：`agentreview_data.py` 的 episode 构建器（`_training_episode` / `_paper_question`）把 `VERIFIED HUMAN REFERENCE OPINIONS` 拼进了 candidate-visible 文本——真人参照只许进 fitness / evaluator，不许进 Program 可见输入。
- [ ] **Canonical Rich Paper Interface**：Evolution、Historical Experience Generation、Deployment 共用同一 paper-content builder——candidate-visible 输入统一为「论文全文 + Candidate-Visible Metadata + source anchors」，clipping / truncation / context budget / preprocessing 同一实现（禁止 evolution 60K vs deployment 35K 式分裂）；取代 `build_reviewer_memory_episode_text` 的预消化模式；移除 `FROZEN RELATED-WORK CACHE` 默认拼接。
- [ ] **Metadata visibility 二分落地**：candidate-visible（paper_id / title / venue / year / reviewer role）vs internal provenance（paper_md_path / split / SHA256 / source path / asset version / run_id / manifest），后者不得拼进 prompt。
- [ ] **Frozen S2 Retrieval Pool + Optional Usage**：

  - 对实验涉及的每篇论文按稳定 paper identity / paper ID 统一运行 Semantic Scholar retrieval；
  - 将结果冻结、版本化、缓存，形成 paper-specific S2 Retrieval Pool；
  - 同一论文的所有 Seed / Candidate 使用同一份冻结结果；
  - S2 结果不默认注入 Reviewer context；
  - Review Harness 自主决定是否读取以及如何利用；
  - 当前正式实验不允许不同 Candidate 通过不同实时 query 获得不同外部知识；
  - S2 retrieval asset 的版本 / SHA256 / paper mapping 写入 Asset Manifest。

## 2. 经验闭环

- [ ] **Program-specific Self-Generated Experience**：每个 Program 从 empty Memory State 开始，按冻结顺序在 Historical Set 上自产经验（审稿完成后才写入当前论文经验）——替代固定 no-memory history generation。
- [ ] **Fresh-state Candidate replay**：每次 fitness evaluation 的运行结构支持状态完全重置 + 确定顺序重放。
- [ ] **Seed lineage tracking**：三条 lineage（`seed_vector_search` / `seed_llm_summarizer` / `seed_experience_learner` 及 `evolved_from_*`）的记录字段：parent seed、lineage、candidate/program SHA256、fitness、promotion provenance。

## 3. Evolution 实验基础

- [ ] **Engineering Budget / Resource Logging**：

  - 同实验统一 Base LLM、runtime / API capability boundary；
  - 不强制不同 Program 实际使用相同 context/token/tool 数量；
  - Context organization 与实际资源使用属于 Harness 策略；
  - 保留模型/API物理限制和安全上限；
  - 记录 LLM calls、prompt/completion tokens、actual context size、memory context size、S2 usage，以及可获得的 latency/cost。
- [ ] **Asset Manifest 首个版本**：历史论文集、seed programs、proxy holdout、数据根配置；含 legacy path resolver——历史 run artifact（如 iter5 内嵌 `_0722` 路径）不原地修改，记录 legacy recorded path → 当前真实 asset + SHA256 + provenance relation。**正式权威 evolved asset = 边界修复后重新进化的产物**，iter5 程序仅历史分析。
- [ ] **建立 Benchmark-Reserved Venue-Year Manifest**：

  - 从 CoCoReviewBench metadata / manifest 提取其覆盖的全部 venue-year cells；
  - 标记为 `benchmark_reserved`；
  - 从 OpenReview Source Pool 中排除所有 benchmark_reserved venue-year，形成 Development Candidate Pool；
  - A / B / C 只能从该 pool 构建；
  - 建立后再执行 paper ID / normalized title / paper family 零重叠审计。

- [ ] **建立独立 Evolution Validation Set**（当前缺口：正式集合不存在；不得用 proxy 20 顶替）：QUAL 最小实现约 20–50 篇（数量为工程配置）；与其他三区 paper ID + normalized title + paper family 零重叠；独立 manifest + SHA256；采样来源与分层规则需在实现前定一个小决策。B 区只提供 fitness / evolution feedback，不作为 Historical Memory writeback 数据源。
- [ ] **Evaluator feedback 旁路审计**：reflector 的 `EvalFeedback`（failed cases / traces / judge rationale）不得含引用、摘要或复述 gold 的内容；只允许 scalar fitness、不泄漏答案的 structured metrics、无 gold 的错误类型。
- [ ] **Promotion manifest 全链路自动化**（`verify_promoted_program()` 流程）。

Evolution Validation 从 OpenReview 非 benchmark corpus 中独立构建，默认目标规模 30 篇（允许通过配置调整至约 20–50 篇）。

采样优先满足：

1. 与 Historical / Proxy / Official Benchmark 的 paper ID、normalized title、paper family 零重叠；
2. 覆盖多个 venue/year；
3. 尽量覆盖不同 review quality / decision strata，避免单一难度；
4. 三条 Seed lineage 使用完全相同的 Validation Set；
5. selection 前冻结 manifest，Evolution 过程中不得换题。

具体 strata 根据 OpenReview 当前可用字段确定，不在设计层强制依赖某一个字段（例如 decision），以避免数据字段缺失时修改研究定义。

## 4. 评价解耦

- [ ] **Benchmark Adapter 抽象**：评测逻辑与 Reviewer 核心解耦；CoCoReviewBench 作为第一个 adapter 接入；Reviewer 不知晓自己被哪个 benchmark 评分。

## 5. 实验

- [ ] **QUAL re-evolution**：边界修复后按完整预算重跑（三条 lineage）。
- [ ] **Effectiveness Holdout / proxy 20**：frozen program 的独立 measurement——2 replicates、Blind Judging、两臂同模型同参数同预算；结果只称 development / effectiveness measurement。2 replicates 采用全链路独立重复：每个 replicate 从 fresh empty Memory State 开始，独立 replay Historical Set、自产 Experience、重建 Program-specific Memory，再运行 Effectiveness Holdout。不得跨 replicate 共享已构建的 Memory State。
- [ ] **Official CoCo 终验**：Program fully frozen 后经 Benchmark Adapter 跑官方 pipeline。

## 6. CoCo 官方 pipeline 工程准备（独立轨道，不阻塞系统设计）

- [ ] 重新生成 `benchmark_reviewer_selection.json`：运行 `/data0/xyj/shengao/CoCoReviewBench` 的 `_0_human_review.py`。
- [ ] 验证 `ReviewClassify` checkpoint 完整性：`model/classify_model/` 存在 filepart 残留，核对 safetensors 清单，不完整则重下。
- [ ] 修复现存**活跃**配置中内嵌 `AgentReview_0722` 的绝对路径（如 `configs/effectiveness_screening_v1.json`）；历史 run artifact 不改（ADR-0003）。
