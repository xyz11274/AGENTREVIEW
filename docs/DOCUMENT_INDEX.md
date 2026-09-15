# Research Core v2 文档索引

## 阅读顺序

跨服务器部署先读 [0915 部署交接](DEPLOYMENT_0915.md)，其中记录配套 CoCo 增量源码包、环境与资产要求、配置冻结和验收步骤。

0915 当前代码入口先读 [共享 M★ 实现](SHARED_MSTAR_IMPLEMENTATION.md)、[冻结协议与数据清单](SHARED_EXPERIMENT_PROTOCOL.md)、[标准 CoCo 适配](STANDARD_COCO_ADAPTER.md)、[Native 基线](COCO_NATIVE_BASELINE.md) 和 [独立结果报告](EXPERIMENT_REPORT.md)。未确定实验值保存在待填写模板；正式新实验使用 `shared-experiment`。

0915 当前设计决定先读 [数据隔离 ADR-0011](adr/0011-development-coco-and-sealed-test.md)、[无记忆对照 ADR-0012](adr/0012-no-memory-controlled-comparison.md)、[共享策略、五库独立 ADR-0013](adr/0013-shared-strategy-independent-role-memory.md)、[遵循 M★ 原文 ADR-0014](adr/0014-mstar-paper-guided-memory-evolution.md)、[Paper. 主指标与独立结果汇总 ADR-0015](adr/0015-paper-fitness-and-separate-results-report.md) 和 [有限失败恢复 ADR-0016](adr/0016-bounded-failure-recovery.md)；已核验故障、本轮修复范围和其余待讨论条件见 [系统重审记录](audit/20260915/SYSTEM_REASSESSMENT.md)。[GEPA 源码核验](audit/20260915/LOCAL_GEPA_SOURCE_REVIEW.md)保留为已结束方案研究。以下设计与架构文档同时保留 0831 的实现背景。

1. [RESEARCH_CORE_V2_DESIGN.md](RESEARCH_CORE_V2_DESIGN.md)：0831 历史设计基线与 0915 决策入口。
2. [ARCHITECTURE.md](ARCHITECTURE.md)：当前 v2 实现结构。
3. [AgentReview/README.md](../AgentReview/README.md)：安装、CLI 与测试命令。
4. [CONTEXT.md](../CONTEXT.md)：领域词汇。
5. [RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md](RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md)：源码批次、删除记录、运行和测试证据。

## 冻结规范

| 文件 | 所有权 |
|---|---|
| `AgentReview/REVIEW_Request.md` | 五个 Specialist identity、职责、输出要求 |
| `AgentReview/META_SYNTHESIS_Request_v2.md` | 独立无状态 Meta 结构化观点与引用契约 |
| `AgentReview/configs/frozen_specialist_identity_v3.json` | Specialist identity manifest |
| `AgentReview/configs/frozen_meta_synthesis_v2.json` | Meta 生成、输出和渲染 identity manifest |
| `AgentReview/configs/shared_memory_experiment.pending.json` | 0915 待填写的显式实验协议模板 |
| `AgentReview/configs/research_v2_formal_v1.json` | 历史角色实验的数据、模型、embedding 和 S2 配置 |

Meta v1 prompt 与冻结 identity 文件保留历史身份。当前 v2 的结构校验、无损渲染和标准 CoCo 边界测试见 [系统重审记录第 3.2 节](audit/20260915/SYSTEM_REASSESSMENT.md#32-meta-根因修复与验证)。

## ADR

- [0016](adr/0016-bounded-failure-recovery.md)：有限失败预算、修复凭据与统一重评、完整评价准入，以及本轮 Meta 修复边界。
- [0015](adr/0015-paper-fitness-and-separate-results-report.md)：采用 Paper. 主指标，统一结果由独立后处理另外生成。
- [0014](adr/0014-mstar-paper-guided-memory-evolution.md)：采用 M★ 原文的记忆程序种群搜索，结束 GEPA 方案讨论。
- [0013](adr/0013-shared-strategy-independent-role-memory.md)：共同进化一套记忆策略，五角色各自维护独立记忆库。
- [0012](adr/0012-no-memory-controlled-comparison.md)：保留相同审稿流程、仅关闭记忆的对照，CoCo Native 单列。
- [0011](adr/0011-development-coco-and-sealed-test.md)：0915 已确认的开发标准 CoCo 与严格隔离最终测试。
- [0010](adr/0010-external-development-sealed-coco.md)：0831 历史数据协议，已由 0011 取代。
- [0009](adr/0009-offline-generative-evolution-deterministic-retrieval.md)：offline generative Memory/Evolution 与 deterministic Review retrieval。
- [0008](adr/0008-stage-and-evaluation-boundaries.md)：Review、Internal Evaluation、Evolution、Meta 和 CoCo 的边界。
- 0003–0007 是 Git 历史保留的 legacy 决策背景，不定义当前 v2 runtime。

运行实现迁移时同步更新源码、测试、配置与实现日志。访谈期间接受的设计决定记录实现状态，并保留旧运行配置的历史身份。
