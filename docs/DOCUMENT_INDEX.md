# AgentReview 现行文档索引

本文列出代码仓当前保留的说明文件、各自所有权和更新规则。代码、测试与 versioned manifest 提供运行事实；说明文件把这些事实组织为可阅读的系统契约。

## 阅读顺序

1. [`RESEARCH_CORE_V2_DESIGN.md`](RESEARCH_CORE_V2_DESIGN.md)：已确认的 Research Core v2 目标边界、唯一主链与实施顺序。
2. [`CONTEXT.md`](../CONTEXT.md)：Research Core v2 的规范术语。
3. [`adr/`](adr/)：Research Core v2 的现行边界决策与 legacy 架构决策。
4. [`ARCHITECTURE.md`](ARCHITECTURE.md)：旧六任务实现的 legacy 架构快照，用于迁移核对。
5. [`AgentReview/README.md`](../AgentReview/README.md)：当前 legacy 代码的安装、入口与运行命令。

## 核心说明文件

| File | Ownership | Update trigger |
|---|---|---|
| `docs/RESEARCH_CORE_V2_DESIGN.md` | 已确认的 Research Core v2 目标主链、边界与实施顺序 | 目标架构或已确认实验协议变化 |
| `AgentReview/README.md` | 用户入口、依赖、三条执行面、当前资产状态 | CLI、安装方式、运行状态或主要命令变化 |
| `docs/ARCHITECTURE.md` | 2026-08-31 legacy 实现快照 | 仅在迁移核对需要时更新 |
| `CONTEXT.md` | Research Core v2 领域词汇及精确定义 | 设计树确认、重命名或改变领域概念 |
| `docs/DOCUMENT_INDEX.md` | 说明文件集合和所有权 | 新增、移动、合并或删除说明文件 |

## 冻结规范与组件说明

| File | Ownership | Status |
|---|---|---|
| `AgentReview/REVIEW_Request.md` | Research Core v2 的五 Specialist identity、职责与输出要求 | v2 目标只保留 `QUAL/CLAR/SIGN/ORIG/POL`；当前六章节内容属于待替换 legacy 状态 |
| `AgentReview/META_SYNTHESIS_Request_v1.md` | Research Core v2 的独立无状态 Meta 汇总请求 | 待实现；单独冻结版本与内容 hash，不属于 Reviewer Role 或 Evolution |
| `AgentReview/memory_evolution/seeds/README.md` | 三个 Seed Program 的设计与区别 | 与 `seeds/*.py` 同步 |
| `AgentReview/evolution_viewer/README.md` | Evolution Viewer 参数、数据源和管理命令 | 与 `evolution_viewer/app.py`、`manage.sh` 同步 |
| `AgentReview/LICENSE` | 项目许可证 | 法律文件 |

## 现行架构决策

| ADR | Decision |
|---|---|
| [`0010`](adr/0010-external-development-sealed-coco.md) | OpenReview train development 与冻结的 CoCo-1300 隔离，并采用最小直接去重 |
| [`0009`](adr/0009-offline-generative-evolution-deterministic-retrieval.md) | Evolution 可离线生成 Derived Memory；召回确定性，LLM Summarizer 仅处理冻结命中集合 |
| [`0008`](adr/0008-stage-and-evaluation-boundaries.md) | Research Core v2 分离 Review、Internal Evaluation、Evolution 与外部 CoCo；Meta 无状态且不进化 |
| [`0003`](adr/0003-path-discipline-and-asset-manifest.md) | Legacy：项目相对路径、显式外部根与 hash-bound Asset Manifest |
| [`0004`](adr/0004-proxy-vs-official-evaluation.md) | Legacy：Evolution fitness、C 区 proxy、密封 D 测量与 official CoCo 分层 |
| [`0005`](adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md) | Legacy：Frozen Reviewer Identity、可演化 Memory Harness 与外置 Benchmark |
| [`0006`](adr/0006-data-protocol-four-zones.md) | Legacy：family-deduplicated temporal v2 和 A/B/C/D 四区协议 |
| [`0007`](adr/0007-evolution-semantics-fresh-state-lineage-budget.md) | Legacy：fresh-state replay、全局候选池、lineage provenance 与 20-attempt 预算 |

## 配置与 manifest

以下 JSON/JSONL 文件属于机器可读运行契约，由代码校验。架构文档引用其状态，具体身份以文件内容和 SHA256 为准：

| Asset | Role |
|---|---|
| `AgentReview/configs/frozen_reviewer_identity_v2.json` | Legacy 六任务 Reviewer Suite identity；v2 实现时由五 Specialist identity 与独立 Meta prompt identity 取代，不保留兼容要求 |
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
