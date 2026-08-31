# AgentReview 现行文档索引

本文列出代码仓当前保留的说明文件、各自所有权和更新规则。代码、测试与 versioned manifest 提供运行事实；说明文件把这些事实组织为可阅读的系统契约。

## 阅读顺序

1. [`AgentReview/README.md`](../AgentReview/README.md)：安装、入口、运行状态与常用命令。
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)：完整模块边界、执行流、数据流、产物与接口状态。
3. [`CONTEXT.md`](../CONTEXT.md)：Reviewer、MemoryProgram、Evolution、四区数据与评测术语。
4. [`adr/`](adr/)：当前架构决策及其约束。
5. 组件 README：Seed Programs 与 Evolution Viewer 的局部使用说明。

## 核心说明文件

| File | Ownership | Update trigger |
|---|---|---|
| `AgentReview/README.md` | 用户入口、依赖、三条执行面、当前资产状态 | CLI、安装方式、运行状态或主要命令变化 |
| `docs/ARCHITECTURE.md` | 全仓模块、Interface、Implementation、Seam、Adapter、数据流和产物 | 模块边界、运行协议、入口或正式流程变化 |
| `CONTEXT.md` | 当前领域词汇及精确定义 | 代码引入、重命名或改变领域概念 |
| `docs/DOCUMENT_INDEX.md` | 说明文件集合和所有权 | 新增、移动、合并或删除说明文件 |

## 冻结规范与组件说明

| File | Ownership | Status |
|---|---|---|
| `AgentReview/REVIEW_Request.md` | 五 Specialist 与 META 的 Frozen Reviewer Identity 内容源 | `reviewer_identity.py` 解析六个有序章节；`frozen_reviewer_identity_v2.json` 固化 source、task、Specialist/META request template 与 suite SHA256 |
| `AgentReview/memory_evolution/seeds/README.md` | 三个 Seed Program 的设计与区别 | 与 `seeds/*.py` 同步 |
| `AgentReview/evolution_viewer/README.md` | Evolution Viewer 参数、数据源和管理命令 | 与 `evolution_viewer/app.py`、`manage.sh` 同步 |
| `AgentReview/LICENSE` | 项目许可证 | 法律文件 |

## 现行架构决策

| ADR | Decision |
|---|---|
| [`0003`](adr/0003-path-discipline-and-asset-manifest.md) | 项目相对路径、显式外部根与 hash-bound Asset Manifest |
| [`0004`](adr/0004-proxy-vs-official-evaluation.md) | Evolution fitness、C 区 proxy、密封 D 测量与 official CoCo 分层 |
| [`0005`](adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md) | Frozen Reviewer Identity、可演化 Memory Harness 与外置 Benchmark |
| [`0006`](adr/0006-data-protocol-four-zones.md) | family-deduplicated temporal v2 和 A/B/C/D 四区协议 |
| [`0007`](adr/0007-evolution-semantics-fresh-state-lineage-budget.md) | fresh-state replay、全局候选池、lineage provenance 与 20-attempt 预算 |

## 配置与 manifest

以下 JSON/JSONL 文件属于机器可读运行契约，由代码校验。架构文档引用其状态，具体身份以文件内容和 SHA256 为准：

| Asset | Role |
|---|---|
| `AgentReview/configs/frozen_reviewer_identity_v2.json` | Reviewer Identity version、source SHA、六个 task prompt SHA、Specialist/META request protocol/template 与 suite SHA |
| `AgentReview/configs/asset_manifest_v2.json` | Frozen Reviewer Identity、temporal v2、Frozen S2 v2、Seed Program 与 embedding 身份 |
| `AgentReview/configs/formal_temporal_v3_qwen3e8b.json` | 正式 Qwen3 embedding 实验身份与 selection 资产状态 |
| `AgentReview/configs/coco_temporal_split_v2/manifest.json` | 3900 records、3889 families 与 A/B/C/D 冻结分配 |
| `AgentReview/configs/frozen_s2_temporal_v2.jsonl.manifest.json` | Frozen S2 v2 覆盖、查询、时间与内容审计 |
| `AgentReview/cache/qwen3_embedding_8b_cache.audit.json` | 12,705 个 4096 维 selection vectors、模型 fingerprint 与截断审计 |
| `AgentReview/configs/effectiveness_screening_v2.json` | C 区筛选设计、arm schema、阈值与激活条件 |

这些文件保留自身的版本化 provenance 字段。历史比较字段表达当前资产的生成来源。

## 数据类 Markdown

当前受版本控制的 Markdown 论文输入按数据资产管理：

- `AgentReview/test_data/**/md_content.md`
- `AgentReview/test_data/**/md_content_no_appendix.md`
- `AgentReview/papers_eval20/ICLR2024_*/md_content.md`

它们的内容进入 smoke/test paper identity、受控评测输入与 context bundle。Effectiveness `prepare.py` 在激活后生成 `paper_context/<split>/<paper_id>/paper.md` 运行数据。更新受版本控制的输入需要同步对应 hash 和测试预期。

## 文档维护规则

- 每个主题由上表指定的一份说明文件负责。
- 架构事实变化时，同一变更同步源码、测试、manifest 与权威说明。
- ADR 记录仍然生效的决策；新决策完整替换原决策时，仓库保留一份现行 ADR。
- 动态运行状态写入 run artifact、checkpoint 与 machine-readable report。
- 临时调查、会议快照、拼接 prompt、运行日志和生成报告存放在外部工作区或正式归档。
- 说明文件使用项目相对路径和稳定入口名称。
