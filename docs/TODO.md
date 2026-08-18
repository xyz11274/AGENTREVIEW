# TODO

工程待办，按实现依赖排序。完成一项划掉一项并注明日期。原则：系统设计不为某个 benchmark 服务，CoCo 官方 pipeline 准备是独立轨道（第 6 节）。

## 1. 边界正确性

- [x] ~~**修复 Human Reference Boundary**：candidate-visible episode 剥离 human reference；reference 仅保留在 evaluator 侧。~~（2026-08-17）
- [x] ~~**Canonical Rich Paper Interface**：Evolution、Historical Experience Generation、Deployment 共用 `canonical-rich-paper-v1` builder、统一 60K 可配置预算；related work 不再默认拼接。~~（2026-08-17）
- [x] ~~**Metadata visibility 二分落地**：candidate-visible metadata 与 internal provenance 分离，canonical builder 拒绝 provenance 字段。~~（2026-08-17）
- [ ] **Frozen S2 Retrieval Pool + Optional Usage**（冻结器与 fail-closed 校验已完成；实际 3900-target 资产仍等待可用 S2 API 凭据）：

  - 对实验涉及的每篇论文按稳定 paper identity / paper ID 统一运行 Semantic Scholar retrieval；
  - 将结果冻结、版本化、缓存，形成 paper-specific S2 Retrieval Pool；
  - 同一论文的所有 Seed / Candidate 使用同一份冻结结果；
  - S2 结果不默认注入 Reviewer context；
  - Review Harness 自主决定是否读取以及如何利用；
  - 当前正式实验不允许不同 Candidate 通过不同实时 query 获得不同外部知识；
  - S2 retrieval asset 的版本 / SHA256 / paper mapping 写入 Asset Manifest。

## 2. 经验闭环

- [x] ~~**Program-specific Self-Generated Experience**：实现 `read prior Memory → self-review → write current experience`，screening matrix 按 Program × replicate 独立生成历史经验。~~（2026-08-17）
- [x] ~~**Fresh-state Candidate replay**：每次 fitness evaluation / replicate 均新建 sandbox 与 empty state，按 selection manifest 顺序 replay。~~（2026-08-17）
- [x] ~~**Seed lineage tracking**：三条 lineage 独立选 parent / reference，记录 parent seed、lineage、program SHA256、fitness 与 promotion provenance，并产出 lineage champions。~~（2026-08-17）

## 3. Evolution 实验基础

- [x] ~~**Engineering Budget / Resource Logging**：统一能力边界并记录 LLM calls、prompt/completion tokens（provider 提供时）、context/memory context、S2 usage、latency。~~（2026-08-17）

  - 同实验统一 Base LLM、runtime / API capability boundary；
  - 不强制不同 Program 实际使用相同 context/token/tool 数量；
  - Context organization 与实际资源使用属于 Harness 策略；
  - 保留模型/API物理限制和安全上限；
  - 记录 LLM calls、prompt/completion tokens、actual context size、memory context size、S2 usage，以及可获得的 latency/cost。
- [x] ~~**Asset Manifest v2 + paper-level temporal split**：冻结 `configs/coco_temporal_split_v1/`，当前 A/B/C/D=2400/300/300/900；paper/title/family overlap=0，temporal audit PASS；v1 venue-year reservation 明确 superseded。~~（2026-08-18）
- [x] ~~**Formal protocol gate**：Asset SHA、A/B/C/D isolation、temporal validity、canonical preprocessing、A/B membership、S2 future-leak audit 全部接入 formal 入口；任一失败拒绝启动。~~（2026-08-18）
- [x] ~~**Strict checkpoint/resume**：Candidate × lineage × replicate identity-bound checkpoint；Memory snapshot、completed prefix、next index、Program/Historical/config/S2 SHA；中断论文整篇重跑。~~（2026-08-18）
- [ ] **生成 time-safe Frozen S2 Pool**：覆盖正式 A/B/C/D 实际使用 paper IDs；每条含 cutoff、method/version、top_k/config、result SHA；未知日期和 future result 拒绝。当前 formal blocker。
- [ ] **从 temporal manifest 重建 canonical A/B/C/D task bundles**：旧 pilot/effectiveness selections 仅作历史分析，不进入 formal。
- [x] ~~**Evaluator feedback 旁路审计**：Reflector 仅接收 scalar score、数值 structured metrics 与 error type；question / gold / answer / context / rationale 全部剥离。~~（2026-08-17）
- [ ] **Promotion manifest 全链路自动化**（`verify_promoted_program()` 流程）。

当前正式数据口径只有一套：CoCoReviewBench 3900 篇是 benchmark source pool，由 frozen chronological manifest 划为 A/B/C/D=2400/300/300/900。B 是 300 篇 2023 Evolution Validation，不再从 OpenReview 另抽默认 30 篇。旧 20+20 screening 与 OpenReview 20–50/默认 30 规则只允许作为 historical/deprecated artifact 说明，不进入 formal gate。

## 4. 评价解耦

- [x] ~~**Benchmark Adapter 抽象与 D 路径接入**：sealed final suite 通过 `CoCoMetricBenchmarkAdapter`，Reviewer 不接收 benchmark context。~~（2026-08-18）

## 5. 实验

- [ ] **QUAL re-evolution**：time-safe S2 与 canonical A/B 重建后按 21 个 successful child candidate evaluations 完整预算重跑（三条 lineage 各 7 completed；失败尝试不占槽位）。
- [ ] **Effectiveness Holdout / proxy 20**：frozen program 的独立 measurement——2 replicates、Blind Judging、两臂同模型同参数同预算；结果只称 development / effectiveness measurement。2 replicates 采用全链路独立重复：每个 replicate 从 fresh empty Memory State 开始，独立 replay Historical Set、自产 Experience、重建 Program-specific Memory，再运行 Effectiveness Holdout。不得跨 replicate 共享已构建的 Memory State。
- [ ] **Official CoCo 终验**：Program fully frozen 后经 Benchmark Adapter 跑官方 pipeline。

## 6. CoCo 官方 pipeline 工程准备（独立轨道，不阻塞系统设计）

- [ ] 重新生成 `benchmark_reviewer_selection.json`：从配置的 `$COCOREVIEWBENCH_ROOT` 运行 `_0_human_review.py`。
- [ ] 验证 `ReviewClassify` checkpoint 完整性：`model/classify_model/` 存在 filepart 残留，核对 safetensors 清单，不完整则重下。
- [x] ~~修复活跃配置与运行脚本中的 `_0722` 绝对路径；项目内路径改为 project-relative，历史 run artifact 不改。~~（2026-08-17）
