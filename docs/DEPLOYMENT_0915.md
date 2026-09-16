# 0915 跨服务器部署交接

> 本机后续按用户要求改为“上游 CoCo `2580687`，仅替换本地 Qwen Judge 连接”。本文保留原服务器交接记录；当前源码版本、锁路径、请求预算和部署命令以 [原版 CoCo 本机适配](ORIGINAL_COCO_LOCAL_DEPLOYMENT.md) 为准，不能再按下文将 CoCo 切回 `0467b41` 用于新实验。

记录日期：2026-09-15。代码分支：`0915`。当前交付包含共享 M★ 实现、ADR-0011–0016、历史实验审计、回归测试与独立报告工具。实验参数模板处于 `pending`；目标服务器完成资产准备、配置冻结和真实模型小规模验收后启动正式实验。

## 1. 获取代码及配套 CoCo 版本

本版共享 Reviewer/Judge 使用固定锁目录 `/data0/xyj/shengao/AgentReview_0915/artifacts/research_v2/runtime/`。以下部署命令沿用该目录布局，运行账户须有写权限；锁目录各级使用真实目录。采用其他代码目录时仍须准备这一固定锁目录，并让使用同一模型端点的进程共享它。

在新服务器执行：

```bash
mkdir -p /data0/xyj/shengao
cd /data0/xyj/shengao
git clone --branch 0915 --single-branch git@github.com:xyz11274/AGENTREVIEW.git AgentReview_0915
git -C AgentReview_0915 rev-parse HEAD
```

配套 CoCo 为本机已验证源码版本 `0467b41a1ee7fdffd9d2f89b71877bf87b6f57ca`。它在上游基点 `2580687ee5d4ad2ac4b9730afb8f1dfda22e40db` 之后包含 9 个历史本地提交；本轮保留该仓库原样，评分调用其既有实现。

仓库携带 [CoCo 增量 Git 包](../deployment/CoCoReviewBench-0467b41.bundle)，用于精确恢复这些历史提交。包的 SHA-256 为 `2fedb58e752ad38c5cb8ede0c7a4aa497fb69fbd18b82a03117084e2a52d1b67`。先完整克隆上游以取得前置历史，再导入增量包：

```bash
cd /data0/xyj/shengao
git clone https://github.com/hexuandeng/CoCoReviewBench.git CoCoReviewBench
git -C CoCoReviewBench cat-file -e 2580687ee5d4ad2ac4b9730afb8f1dfda22e40db^{commit}
sha256sum AgentReview_0915/deployment/CoCoReviewBench-0467b41.bundle
git -C CoCoReviewBench bundle verify ../AgentReview_0915/deployment/CoCoReviewBench-0467b41.bundle
git -C CoCoReviewBench fetch ../AgentReview_0915/deployment/CoCoReviewBench-0467b41.bundle HEAD
git -C CoCoReviewBench checkout --detach 0467b41a1ee7fdffd9d2f89b71877bf87b6f57ca
git -C CoCoReviewBench rev-parse HEAD
```

已有 CoCo checkout 可在其工作区干净且具备前置历史时，从 `bundle verify` 开始执行。该包保存 Git 源码差异；运行所需权重和实验资产按下一节另行准备。

## 2. 环境与外部资产

本轮测试环境为 Python 3.12。分开准备 Python 环境：

| 环境 | 安装与用途 |
|---|---|
| AgentReview 主环境 | 在仓库根目录执行 `python3 -m pip install -r AgentReview/requirements.txt`；包括 OpenAI 1.109.1、PyTorch 2.7.1、Transformers 4.57.1 和 embedding 依赖 |
| CoCo 评分环境 | 按配套 CoCo 的 `requirements.txt` 准备，通过评分配置 `python_executable` 指向它；其 OpenAI 为 2.6.0 |
| Qwen27B 服务环境 | 模板记录 vLLM `0.20.1+cu129`、Transformers `5.8.1`，与评分和 embedding 环境分别维护 |

CoCo requirements 中的 vLLM `0.8.5.post1` 属于其环境记录。目标服务器根据实际 CUDA/PyTorch 配套安装服务环境，并将采用的版本核对到冻结协议。可选 `flash-attn` 的安装说明见 `AgentReview/requirements-optional.txt`。

外部资产清单：

- Qwen3.8-27B 权重、tokenizer 和 chat template；版本摘要见待填写协议。
- 标准 CoCo 的 Split、Classify 权重，以及 BERTScore 的 `roberta-large` 本地权重。
- Qwen3-Embedding-8B 的固定 revision；provider 使用 `qwen3_embedding`，配置实际 device 与可写 `cache_path`。
- build、static、rotating、sealed_test 四份身份清单及各自完整论文正文；static、rotating、sealed_test 各自的 CoCo 参考评审、人工分类与留出评审选择资产。
- 版本家族审核清单、历史暴露身份清单，以及实验选定的冻结 S2 资产。

模型、数据、`outputs*`、embedding 缓存和 `artifacts/research_v2/` 运行产物采用单独存储与传输。仓库内 `docs/audit/20260915/` 保存的是此前审计证据，其历史绝对路径用于溯源。

## 3. 本机服务与 Sandbox

新共享评分 worker 从 Linux `/proc` 核验本机服务，要求端口对应唯一可识别进程。各服务启动参数显式包含 `--model /absolute/model/root`、`--served-model-name NAME` 和 `--max-model-len N`。可使用对应环境的 `python3 -m vllm.entrypoints.openai.api_server` 传入这些参数。

| 服务 | 上下文 `N` | 端点与模型名 |
|---|---:|---|
| Split | 40960 | 使用评分配置的本机端点及别名 |
| Classify | 32768 | 使用评分配置的本机端点及别名 |
| Judge / Qwen27B 生成 | 65536 | `http://127.0.0.1:18001/v1`，`qwen3.8-27b` |

Qwen 模板记录 BF16、`max_num_seqs=8`。生成客户端使用 `/tokenize` 返回的 `count` 与 `max_model_len` 做完整请求预算检查。阶段前后服务实例须保持一致，服务由部署侧提前启动。

共享 Memory Sandbox 要求可用的 bubblewrap 和 Linux namespace 权限。安装系统 `bubblewrap` 后，以实际运行账户检查：

```bash
bwrap --unshare-all --ro-bind / / -- /bin/true
```

## 4. 生成目标服务器的冻结配置

按 [冻结协议与数据清单](SHARED_EXPERIMENT_PROTOCOL.md) 安置资产后，以目标服务器的实际绝对路径生成配置。评分配置使用 [标准 CoCo 适配](STANDARD_COCO_ADAPTER.md) 中的 `materialize_scoring_config()`，显式传入 CoCo 根目录、评分 Python、三个服务和各自权重目录、BERTScore 目录、阶段超时及重试预算。

复制 [待填写协议](../AgentReview/configs/shared_memory_experiment.pending.json)，填写分区清单与摘要、family audit、历史暴露清单、固定非记忆审稿策略、S2、embedding、生成设置、搜索及失败预算，并绑定评分配置。完成后设为 `status: frozen`，保存为独立的 `experiment.frozen.json`。当前共享入口支持 `native_num_fewshot: 0`；带示例版本的来源审核仍待补齐。

文件路径和 SHA-256 共同参与身份。迁移后的新实验在目标路径重新生成冻结身份；已有运行的恢复则保留原冻结身份及其引用资产。最终测试继续引用开发运行中的 Champion 凭据、策略状态与五份 Memory 快照，开发运行目录应完整保留。

## 5. 验收与启动

从 `AgentReview_0915` 仓库根目录执行，以下 `/absolute/...` 替换为已冻结资产的实际路径：

```bash
PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action preflight --protocol /absolute/experiment.frozen.json \
  --output /absolute/development-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action run --protocol /absolute/experiment.frozen.json \
  --output /absolute/development-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 shared-experiment \
  --action final-test --protocol /absolute/experiment.frozen.json \
  --frozen-system /absolute/development-run/winner.json \
  --output /absolute/final-test-run

PYTHONPATH=AgentReview python3 -m agentreview.research_v2 experiment-report \
  --manifest /absolute/development-run/report-input.json \
  --output /absolute/reports/development
```

`preflight` 检查配置、开发资产、标准 CoCo 源码与权重摘要、初始策略和参考清单。服务在线状态、embedding 实际加载、Sandbox 和完整模型调用通过独立的小规模全链路验收确认。验收使用独立实验身份与开发论文，封存测试保持其最终评价用途。

公共故障记录修复原因、修复方法和验证证据后，按 [共享 M★ 实现说明](SHARED_MSTAR_IMPLEMENTATION.md) 的 `--repair-evidence` 入口恢复，触发新 epoch 下的统一重评。独立报告在原始结果保存后另行运行；测试报告使用其来源清单及 `--report-stage final`。

## 6. 本次交付的验证状态

- 28 文件组合：256 项测试、96 项子测试通过。
- 最后变更相关 8 文件组合：84 项测试、25 项子测试通过。
- 模型返回使用测试替身；真实模型全链路验收与正式实验由目标服务器执行。

测试命令及具体覆盖见 [实现验收记录](audit/20260915/SHARED_IMPLEMENTATION_VALIDATION.md)。讨论决定与实现对应关系见 [文档索引](DOCUMENT_INDEX.md)。
