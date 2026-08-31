# AgentReview

本仓库研究“论文评审 Memory Program 进化”并保留早期 AgentReview 运行路径。当前目标架构是 **Research Validity Core v1**：五个 specialist 独立进化、冻结为 Final Review Suite，随后授权 Official 评价。

## 当前状态

| 层级 | 状态 | 含义 |
|---|---|---|
| Core v1 核心算法与领域模块 | 主体已实现 | Corpus、Review Memory、三种 read mode、reference-free evaluator、shared-pool search、Final Suite 与受保护 Official seam 均有代码和测试；正式阻塞语义见 STATUS |
| G1 组合接线 | 合成闭环通过 | 独立 Core CLI 和生产组合根完成五角色、Suite、受保护输入的合成贯通 |
| Core v1 blocking seams | 待实现/接线 | Evaluator event derivation、Candidate Evidence API、最小 journal/provenance、不可变 Official 提交与 closure 布局待闭合 |
| G2 正式输入 | 待物化 | 625 条 Corpus、五个索引、B-local、Evidence、capacity、runtime profile 与 preflight 工件待形成同一闭包 |
| G3 正式实验 | 待运行 | 五个正式 winner、Final Suite 与 Official 结果待生成 |
| Audit Hardening v2 | 延期目标 | 高可用 journal、复杂恢复、外部归档和发布治理在 Core v1 之后实施 |

G1 证明控制流接线；正式研究结论由 G2 和 G3 工件共同支撑。最新核验事实集中记录在 [系统状态](docs/STATUS.md)。

## 权威说明

- [领域术语](CONTEXT.md)：统一概念，只定义业务语言。
- [完整架构](docs/ARCHITECTURE.md)：代码路径、依赖方向、数据流、工件和保留接口。
- [系统状态](docs/STATUS.md)：当前测试、门禁、配置缺口与风险。
- [运行手册](docs/RUNBOOK.md)：可执行入口和操作顺序。
- [文档索引](docs/DOCUMENT_INDEX.md)：全部保留说明的性质与可信层级。
- [协议索引](docs/protocol/README.md)：冻结协议、待接线接口和 Hardening v2 范围。

代码工程位于 [AgentReview/](AgentReview/README.md)。冻结 rubric 是 [AgentReview/REVIEW_Request.md](AgentReview/REVIEW_Request.md)，其字节内容属于实验身份。

## 仓库布局

~~~text
AgentReview_0827/
├── AgentReview/              # Python 工程、配置、脚本、测试与样例数据
├── docs/                     # 当前说明、ADR 和冻结协议
├── artifacts/                # 运行证据与阶段工件
├── outputs_baseline/         # 历史输出
├── outputs_md/               # 历史输出
├── CONTEXT.md                # 领域词汇表
└── README.md                 # 仓库入口
~~~

papers_eval20/、test_data/ 中的 Markdown 是论文输入；artifacts/ 中的 Markdown 是运行证据。二者均按数据身份管理。

## 文档维护原则

现行代码决定实现事实，冻结协议决定实验合同，工件决定运行完成度。架构描述进入 docs/ARCHITECTURE.md，可变状态进入 docs/STATUS.md，命令进入 docs/RUNBOOK.md。Git 历史承载被替代的说明快照。
