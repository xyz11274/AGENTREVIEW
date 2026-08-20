# 文档索引与新旧判定（2026-08-20）

> 目的：`AgentReview_0811` 路径下 md 文档较多且新旧混杂，本文给出每份文档的分类与可信度判定，作为「读哪份」的入口。
> 判定基准：git HEAD `7256ffc`（2026-08-20）与代码实测。
> 代码架构的唯一权威描述：[../AgentReview M★ 代码架构全景解析.md](../AgentReview%20M★%20代码架构全景解析.md)（2026-08-20 版）。

## 阅读顺序建议

1. [../CONTEXT.md](../CONTEXT.md) -- 术语与系统主张（先读）
2. [../AgentReview M★ 代码架构全景解析.md](../AgentReview%20M★%20代码架构全景解析.md) -- 代码架构
3. [adr/0005](adr/0005-frozen-identity-evolvable-harness-outermost-benchmark.md)、[adr/0006](adr/0006-data-protocol-four-zones.md)、[adr/0007](adr/0007-evolution-semantics-fresh-state-lineage-budget.md) -- 三份现行核心决策
4. [../AgentReview/AGENTS.md](../AgentReview/AGENTS.md) + [../AgentReview/docs/memory_evolution.md](../AgentReview/docs/memory_evolution.md) -- 仓库契约与进化规范
5. [../AgentReview/docs/CLOUD_FORMAL_ALIGNMENT_20260820.md](../AgentReview/docs/CLOUD_FORMAL_ALIGNMENT_20260820.md) -- 最新运行指引（云端正式 run 前必读）

## 分类清单

### A. 仍然权威（以最新版本为准）

| 文档 | 性质 |
|---|---|
| `CONTEXT.md` | 统一语言表 / 术语权威 |
| `docs/adr/0003` | 路径纪律与 Asset Manifest |
| `docs/adr/0005` | 系统边界唯一权威（supersede 0001/0002 的过时部分） |
| `docs/adr/0006` | 四区数据协议（2026-08-18 修订） |
| `docs/adr/0007` | 进化语义（fresh-state、lineage、21 评测预算） |
| `AgentReview/AGENTS.md` | 仓库地图与不变量契约 |
| `AgentReview/REVIEW_Request.md` | 五维度 23 子类 rubric（不可变冻结资产，SHA 运行时校验） |
| `AgentReview/docs/memory_evolution.md` | M★ 复现契约（搜索协议/产物布局/数据隔离） |
| `docs/DEPRECATED.md` | 已废弃代码注册表（活文档） |
| `AgentReview/evolution_viewer/README.md` | 进化查看器使用说明 |

### B. 基本权威、局部滞后

| 文档 | 滞后点 |
|---|---|
| `AgentReview/README.md` | 示例模型仍写 gpt-5-mini（parser 默认已是 kimi-k3）；仍把 `prepare-specialists` 当常规步骤展示（canonical 数据已冻结，不应重跑） |
| `docs/adr/0004` | 「D manifest 900 篇」应为 899（899 为准） |
| `docs/RUN_AUDIT_20260818.md` | 在线管线问题清单（M1–M4、L1–L6、B1 环境变量分裂）仍有效；formal 侧结论被 08-18~08-20 提交与云端审计部分超越 |
| `docs/TODO.md` | 第 3 节多项实质已完成（S2 v2 池生成、A/B/C/D task bundles 冻结）但未回填 |

### C. 部分过时但仍有参考价值

| 文档 | 价值与过时点 |
|---|---|
| `AgentReview/AGENTREVIEW_架构解读.md` | 活/死代码判定与 import 链分析仍准；但缺 08-17 后新增模块（formal_protocol/assets/checkpoint/subset_selection/coco_reference/finalize_formal_readiness），附录协议数字为旧口径。**已被全景解析（0820 版）取代，仅作沿革参考** |
| `AgentReview/docs/effectiveness_screening.md` | 数据面是 v2，但执行流程仍是已废弃的 Frozen Library 路线（`freeze-memory-library` / `seed_experience` 臂），与 ADR-0005/DEPRECATED.md 直接矛盾。**流程部分勿用，待重写** |

### D. 历史归档（仅历史价值，勿作依据）

| 文档 | 说明 |
|---|---|
| `AgentReview/GROUP_MEETING_REPORT.md` | 2026-07-23 组会汇报，写于上一代目录（AgentReview_0722），旧协议数字、旧数据面 |
| `docs/adr/0001`、`docs/adr/0002` | 已被 0005 部分取代（文档内已声明），保留作决策沿革 |
| `AgentReview/configs/configs_README.md` | 0 字节空文件 |

### 已废弃路线（文档层面，勿再引用）

- Frozen Library Route：`frozen_memory_library.jsonl` -> 二次加工（违反 Self-Generated Experience，见 ADR-0005 / DEPRECATED.md）
- `seed_experience` 臂名（CONTEXT.md 禁用；现用三条 lineage 名）
- `coco_temporal_split_v1` 与「venue-year 全量 benchmark_reserved」规则（ADR-0006 禁止进入新 formal run）
- `runs/pilot_full_iter5_202607300117` 产物（违反 Human Reference Boundary，仅历史分析用）

## 数据类 md（不属于文档，盘点时排除）

`AgentReview/test_data/`、`AgentReview/papers_eval20/`、`AgentReview/data/**/paper.md` 下的 `md_content*.md` / `paper.md` 均为论文语料/实验输入，不是项目文档。
