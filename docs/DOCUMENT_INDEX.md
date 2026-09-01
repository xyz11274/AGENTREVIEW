# Research Core v2 文档索引

## 阅读顺序

1. [RESEARCH_CORE_V2_DESIGN.md](RESEARCH_CORE_V2_DESIGN.md)：已确认的目标协议与边界。
2. [ARCHITECTURE.md](ARCHITECTURE.md)：当前 v2 实现结构。
3. [AgentReview/README.md](../AgentReview/README.md)：安装、CLI 与测试命令。
4. [CONTEXT.md](../CONTEXT.md)：领域词汇。
5. [RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md](RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md)：源码批次、删除记录、运行和测试证据。

## 冻结规范

| 文件 | 所有权 |
|---|---|
| `AgentReview/REVIEW_Request.md` | 五个 Specialist identity、职责、输出要求 |
| `AgentReview/META_SYNTHESIS_Request_v1.md` | 独立无状态 Meta synthesis |
| `AgentReview/configs/frozen_specialist_identity_v3.json` | Specialist identity manifest |
| `AgentReview/configs/frozen_meta_synthesis_v1.json` | Meta identity manifest |
| `AgentReview/configs/research_v2_formal_v1.json` | 正式数据、模型、embedding 和隔离 S2 配置 |

## ADR

- [0010](adr/0010-external-development-sealed-coco.md)：development 与 CoCo 隔离、最小直接去重、protocol-compatible CoCo-1300。
- [0009](adr/0009-offline-generative-evolution-deterministic-retrieval.md)：offline generative Memory/Evolution 与 deterministic Review retrieval。
- [0008](adr/0008-stage-and-evaluation-boundaries.md)：Review、Internal Evaluation、Evolution、Meta 和 CoCo 的边界。
- 0003–0007 是 Git 历史保留的 legacy 决策背景，不定义当前 v2 runtime。

架构、prompt、artifact schema 或正式协议变化时，同一变更必须更新源码、测试、对应配置和实现日志。
