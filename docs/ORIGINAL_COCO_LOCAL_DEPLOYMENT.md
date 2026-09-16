# 原版 CoCo 本机适配

## 范围

CoCo 工作目录 `/data/xyj/shengao/CoCoReviewBench`，当前基于上游 `2580687`，本地提交 `83452e5` 只修改 Judge 模型/端点/连接参数。评分、Split、Classify、解析与重试源码保持该版本不动。`archive/coco-0915-0467b41` 保存历史修改版，本次只从中读取参考资产。

正式实验未启动。没有把服务未部署或测试替身通过记为真实模型全链路成功。

## 已修改文件

| 文件 | 修改 |
|---|---|
| `AgentReview/agentreview/research_v2/development_coco.py` | 移除新增版 Table-1 模块依赖；冻结原版请求参数、评分 Python/包版本、离线模型缓存位置及共享锁路径；对子进程传递一致的环境 |
| `AgentReview/agentreview/research_v2/development_coco_worker.py` | 调用原版客户端和函数签名；移除新增版 runner、预检函数及 `require_complete` 参数；恢复预算/并发；结果名称和计数映射，另外保存原始表 |
| `AgentReview/agentreview/research_v2/judge_lease.py` | 默认锁位置从远端 `/data0` 改为本仓库实际路径，支持跨进程统一环境配置 |
| `AgentReview/tests/test_research_v2_development_coco.py` | 测试使用实际本地 CoCo 路径及原版请求契约 |
| `AgentReview/tests/test_research_v2_original_coco.py` | 增加原版模块、客户端参数、指标映射、缓存绑定测试；对照原版直接调用与适配器的 Split/Classify 请求和输出 |
| `AgentReview/tests/test_research_v2_coco_deployment.py` | 验证原版服务参数、缺少 GPU 分配时拒绝启动、只停止记录的进程、PID 复用拒绝和服务未上线报告 |
| `deployment/requirements-coco-original.txt` | 独立评分环境所需的包版本 |
| `deployment/coco_services.py` | 只管理本部署记录的进程；保存 PID、UID、启动时间、命令；不使用全局 `pkill` |
| `deployment/prepare_original_coco.py` | 从归档恢复/核验 selection、确定性准备独立开发样本与参考清单、生成服务模板和冻结评分配置 |
| `deployment/validate_original_metrics.py` | 使用原版函数在 CPU 离线计算固定文本的 BLEU/ROUGE/BERTScore，记录实际加载验证 |
| `deployment/validate_original_coco.py` | 三服务预检、真实 native 评审生成和原版三阶段评分的独立验收入口；不声称覆盖共享 Memory/Evolution 全链路 |
| `.gitignore` | 在保留原规则的基础上忽略本地部署产物和测试缓存 |
| `docs/STANDARD_COCO_ADAPTER.md`、`docs/DEPLOYMENT_0915.md`、本文 | 更新当前原版适配说明，保留历史交接证据 |

CoCo 目录只恢复了数据文件 `benchmark/benchmark_reviewer_selection.json`；此次没有修改其已跟踪源码。

## 参考资产

找到 7 份历史 selection，均为 3900 项且 SHA-256 相同：

`6d4c49fb61e93cfa60608f5ba2ddbf7ea489f2a250c0f70c57d54681204ebbfa`

该文件与归档提交中的文件逐字节一致。历史 C10 协议记录的 ICLR 2023 benchmark、完整人工分类、留出人工参考摘要与本机资产一致；原版 `left_out_one()` 校验全部 3900 篇成功。生成过程未重新随机选择 reviewer。

证据在 `artifacts/deployment/original-coco/references/provenance.json` 与 `reference-reuse.json`。基线和候选必须引用同一分区参考清单。验收论文 `-EHqoysUYLx` 来自历史开发样本，只用于能力验收，不作为未暴露封存测试。

## 环境和资源

- 主环境：`/data/xyj/envs/env_shengao/bin/python`，OpenAI 1.109.1。
- 评分环境：`/data/xyj/envs/coco-original/bin/python`。通过 `venv --system-site-packages` 复用主环境的 PyTorch 2.7.1 / Transformers 4.57.1，在自己的目录安装 OpenAI 2.6.0、NLTK 3.9.3、rouge-score 0.1.2、bert-score 0.3.13 与测试工具；没有替换主环境的包。`pip check` 通过。主环境升级后需重新冻结评分环境身份。
- 服务环境：`/data/xyj/envs/qwen38serve/bin/python`，当前安装 vLLM 0.27.1 / Transformers 5.15.1。服务兼容性仍需实机加载验证。
- BERTScore：原版默认 `roberta-large`、17 层、`rescale_with_baseline=False`。独立 HF 缓存固定 revision `722cf37b1afa9454edce342e7895e588b6ff1d59`；worker 在 CPU、离线模式运行。权重完整 SHA-256 已核验为 `047c85f0b96269cd62e6f732644f067004eebd95af5b5d35965ae2528f13bf38`，实际离线加载及计算已通过。
- `bwrap --unshare-all --ro-bind / / -- /bin/true` 已通过。
- 检查时 8 张 RTX 4090 均由用户 `lsl` 的两套 vLLM 服务占用，约剩 0.5 GiB/卡；未停止或重配这些服务。

## 运行顺序

从仓库根目录执行：

```bash
/data/xyj/envs/coco-original/bin/python deployment/prepare_original_coco.py
/data/xyj/envs/coco-original/bin/python deployment/validate_original_metrics.py
/data/xyj/envs/coco-original/bin/python deployment/prepare_original_coco.py --freeze-scoring
/data/xyj/envs/coco-original/bin/python deployment/validate_original_coco.py check
```

`--freeze-scoring` 要求完整 BERTScore 缓存，读取所有模型文件 SHA-256，生成新 `scoring.frozen.json`。该文件是评分配置，不是正式实验协议。

2026-09-16 已完成全部模型文件哈希及复核，生成评分配置 ID：`5e3ec1b3e4f6b6b021f328dcbb667e989b45c829d686372d63a734edff9f10b6`。文件位于 `artifacts/deployment/original-coco/scoring.frozen.json`，绑定源码、模型、环境、请求参数和锁路径；服务是否上线单独记录。

服务 GPU 必须先明确分配。把 `artifacts/deployment/original-coco/services.pending.json` 复制到独立配置，填写各服务的 `gpus` 数组，确认端口和资源后分别执行：

```bash
/data/xyj/envs/coco-original/bin/python deployment/coco_services.py plan --config /absolute/services.local.json --role split
/data/xyj/envs/coco-original/bin/python deployment/coco_services.py start --config /absolute/services.local.json --role split
/data/xyj/envs/coco-original/bin/python deployment/coco_services.py status --config /absolute/services.local.json --role split
```

Classify/Judge 替换 `--role`。启动记录只证明创建进程；`validate_original_coco.py check` 才检查 API 和服务进程身份。Split/Classify 保留 thinking 文本；Judge 在服务端启用 reasoning parser 分离思考，以兼容原版 JSON 解析。不要修改 CoCo 的解析规则。服务参数仍需真实响应验收。

所有服务就绪后：

```bash
/data/xyj/envs/coco-original/bin/python deployment/validate_original_coco.py run
```

该入口先关闭生成客户端并释放 Reviewer 锁，再进入 Judge 阶段。保存完整论文、请求身份、原始响应、Split/Classify、原始及映射指标、评测凭据。它覆盖 native generation → CoCo scoring；共享 Memory 构建、检索、五角色审稿及 Evolution 还必须通过独立的 `shared-experiment` 验收，不能以该入口代替。

## 已执行检查

- 原版接口、评分凭据、锁、共享后端、封存边界、实验协议及部署工具：57 passed，8 subtests passed。3 条警告来自测试进程使用 fork，与生产模型验收无关。JUnit 记录：`artifacts/deployment/original-coco/tests.xml`。
- 随后补充 Split/Classify 对照测试，原版兼容测试文件共 7 passed（其中 5 项已包含在上述 57 项中）；两条路径的请求参数与输出文件逐字节一致。记录：`original-differential-tests.xml`。
- CoCo 当前 93 个已跟踪 src 文件与提交版本逐字节一致。
- 参考配对、3900 篇留出 reviewer 校验、部署脚本语法、评分环境 `pip check` 已通过。
- 三服务预检实际返回连接拒绝，入口未发起任何生成请求。
- 原版指标实际离线计算已通过：相同文本 BLEU=1、ROUGE-L=1、BERTScore F1=0.9999999404，记录在 `metrics-validation.json`；这项检查不包含生成式模型请求。

## 正式实验门槛

归档 CoCo 分支只有参考资产及历史运行器，没有 `experiment.frozen.json`、四分区清单、family audit 或完整历史暴露清单。当前 AgentReview 仍只有 pending 协议模板。正式实验需要提供或有依据地构建这些资产，并确定搜索预算、Memory/S2/Meta 条件等科学参数；不能把历史修改版的常量或开发验收样本冒充正式方案。

真实服务 → 独立 native/评分验收 → 独立共享全链路验收 → 汇总实际修改与结果 → 正式运行。后续继续沿用用户已授权的执行范围，不重新请求启动许可。
