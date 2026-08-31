# AgentReview 文档索引

最近核验：2026-08-31
索引原则：代码表达实现事实，协议表达目标合同，工件表达完成度，Git 历史保存被替代快照。

## 1. 阅读顺序

1. [仓库入口](../README.md)
2. [领域术语](../CONTEXT.md)
3. [完整架构](ARCHITECTURE.md)
4. [当前状态](STATUS.md)
5. [运行手册](RUNBOOK.md)
6. [协议索引](protocol/README.md)

## 2. 当前权威说明

| 文件 | 职责 | 更新触发 |
|---|---|---|
| [../README.md](../README.md) | 仓库身份、总状态、阅读路由 | 主路径或门禁层级变化 |
| [../CONTEXT.md](../CONTEXT.md) | 实现无关的统一术语 | 领域概念变化 |
| [../AgentReview/README.md](../AgentReview/README.md) | Python 工程入口、依赖、CLI 分类 | 公开入口或工程布局变化 |
| ARCHITECTURE.md | 模块、interface、implementation、seam、数据流与工件 | 依赖方向或实现结构变化 |
| STATUS.md | 测试、配置、门禁、阻塞和风险 | 每次核验或正式阶段变化 |
| RUNBOOK.md | 可执行命令与操作顺序 | CLI、脚本、参数合同变化 |
| DOCUMENT_INDEX.md | 全部说明的权威层级 | 文档增删或身份变化 |

同一事实只在一个权威文件中完整展开，其他文件使用链接。

## 3. 当前 ADR

| ADR | 决策 |
|---|---|
| [0003](adr/0003-path-discipline-and-asset-manifest.md) | 路径解析、内容身份、manifest 与 provenance |
| [0006](adr/0006-data-protocol-four-zones.md) | A Corpus、B-local Evolution、C Official、D legacy reserved |
| [0008](adr/0008-frozen-no-memory-review-corpus-and-derived-retrieval.md) | 冻结 Full Review Corpus 与确定性 Review Memory |
| [0010](adr/0010-reference-free-specialist-evolution-and-post-freeze-official-coco.md) | Reference-free specialist evolution 与冻结后 Official |
| [0011](adr/0011-research-validity-core-v1-and-audit-hardening-v2.md) | Core v1 与 Audit Hardening v2 分层 |

旧 ADR-0001、0002、0004、0005、0007、0009 的有效决策已经进入上述 ADR；其原文由 Git 历史保存。

## 4. Protocol

[protocol/README.md](protocol/README.md) 是 22 份协议的状态入口。协议文件保留以下身份：

- Frozen semantic contract：定义当前研究语义。
- G2 materialization contract：定义正式资产、registry、identity 和 gate。
- Retained interface：代码 seam 已保留，生产组合待接线。
- Audit Hardening v2：首次 Core v1 之后的强化目标。
- Draft：仍在收敛的接口说明。

协议中的目标状态用于指导实现；[STATUS.md](STATUS.md) 决定当前完成度。

## 5. 模块说明与冻结资产

| 文件 | 身份 |
|---|---|
| [../AgentReview/REVIEW_Request.md](../AgentReview/REVIEW_Request.md) | 冻结五角色 rubric 字节资产；保持原始 SHA |
| [../AgentReview/memory_evolution/seeds/README.md](../AgentReview/memory_evolution/seeds/README.md) | Core v2 seeds 与 legacy seeds 的协议分流 |
| [../AgentReview/evolution_viewer/README.md](../AgentReview/evolution_viewer/README.md) | 只读 legacy M★ Viewer 使用说明 |
| [effectiveness_screening.md](effectiveness_screening.md) | 暂未启用的 legacy 三臂实验接口状态 |

REVIEW_Request.md 同时是说明文本和实验输入。正式运行按冻结字节资产管理。

## 6. 数据、运行证据与工具文件

以下 Markdown 按数据或证据身份保留，并排除在说明权威之外：

| 路径 | 身份 |
|---|---|
| AgentReview/papers_eval20/**/md_content.md | 论文输入 |
| AgentReview/test_data/**/md_content*.md | 测试论文输入 |
| artifacts/**/*.md | 运行失败、服务快照等证据 |
| AgentReview/.pytest_cache/README.md | 测试工具缓存 |

JSON、JSONL、Python、日志、输出目录和二进制工件由各自 schema、manifest 或生成流程管理。

## 7. 已清理说明的归并关系

| 旧说明类型 | 当前归并位置 |
|---|---|
| 多份 0811–0825 架构解读 | ARCHITECTURE.md |
| Cloud/Local Qwen 与环境审计 | STATUS.md、RUNBOOK.md |
| Core implementation matrix 与阶段复核 | STATUS.md |
| TODO 与 deprecated 清单 | STATUS.md 的阻塞、ARCHITECTURE.md 的保留接口 |
| Reviewer evolution decoupling 双语稿 | ARCHITECTURE.md 的依赖方向与 runtime 分支 |
| legacy memory evolution 长说明 | ARCHITECTURE.md 与 protocol/ |
| 待阅读、整理、会议、Prompt inventory | Git 历史与运行工件 |
| 被替代 ADR | 当前五份 ADR |
| 2026-08-20 PPT 工程 | Git 历史 |

## 8. 维护检查

文档变更完成后执行：

1. 对所有相对 Markdown 链接做存在性检查。
2. 用 CLI --help 核对入口和子命令。
3. 用真实测试输出更新 STATUS。
4. 用 closure JSON 更新 G1/G2/G3。
5. 用 SHA 和行数核对冻结资产。
6. 把实现细节留在 ARCHITECTURE，把可变结论留在 STATUS，把目标语义留在 protocol。
7. 删除被新权威完整覆盖的说明，让 Git 历史承担沿革。
