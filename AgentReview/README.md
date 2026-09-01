# AgentReview Research Core v2

Research Core v2 的正式主链只有五个固定 Specialist Reviewer：`QUAL / CLAR / SIGN / ORIG / POL`。

```text
Frozen Paper
  → deterministic Frozen S2 / role-local Memory retrieval
  → five independent Specialist Reviews
  → save five complete Reviews
  → stateless Meta synthesis or deterministic concatenation
  → save Final Review
  → external CoCo process
```

Review Run 不执行 Internal Evaluation、Reflection、Evolution 或 Memory writeback。Evolution 只读取已保存的完整 Review 与后置 Internal Evaluation：

```text
Saved complete Reviews
  → offline Internal Evaluation
  → role-local Reflection and V4A patch
  → sandbox validation
  → static evaluation
  → append candidate to that role's pool
```

完整设计见 [RESEARCH_CORE_V2_DESIGN.md](../docs/RESEARCH_CORE_V2_DESIGN.md)，领域术语见 [CONTEXT.md](../CONTEXT.md)，实际修改和测试证据见 [RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md](../docs/RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md)。

## 冻结身份

- [REVIEW_Request.md](REVIEW_Request.md)：仅包含五个 Specialist 的 identity、职责和输出要求。
- [META_SYNTHESIS_Request_v1.md](META_SYNTHESIS_Request_v1.md)：独立无状态 Meta；只读取 Frozen Paper 与固定顺序的五份完整 Review。
- [frozen_specialist_identity_v3.json](configs/frozen_specialist_identity_v3.json)：五角色 identity manifest。
- [frozen_meta_synthesis_v1.json](configs/frozen_meta_synthesis_v1.json)：独立 Meta manifest。
- [research_v2_formal_v1.json](configs/research_v2_formal_v1.json)：正式模型、embedding、数据根和两套隔离 S2 pool 路径。

Meta 不属于 Reviewer Role，不拥有 Memory 或 Program，不参与 Evolution，也不输出自定义数字总分或置信度指标。

## 环境

从外层仓库根目录 `AgentReview_0831/` 执行：

```bash
python -m pip install -r AgentReview/requirements.txt
export PYTHONPATH=AgentReview
export OPENAI_API_KEY=dummy
```

正式生成模型是独立 vLLM 服务。当前冻结配置连接 `http://127.0.0.1:18000/v1`，served model name 为 `qwen3.8-27b`，BF16、temperature 0、`enable_thinking=false`。Qwen3-Embedding-8B 在本地进程中只服务确定性 embedding retrieval。

## Stage CLI

```bash
python -m agentreview.research_v2 --config AgentReview/configs/research_v2_formal_v1.json --help
```

可独立启动的 stage：

```text
prepare-data
prepare-coco-1300
bootstrap-corpus
build-memory
review
internal-eval
evolve-step
run-evolution
freeze-system
coco-eval
```

准备 development 的互斥 125/25/125 划分：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  prepare-data
```

如需在数据准备阶段获取并冻结 S2：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  prepare-data --build-s2
```

独立冻结 protocol-compatible CoCo-1300；该集合不是作者未公开的 original IDs：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  prepare-coco-1300
```

加 `--build-s2` 时构建独立 `frozen_s2_coco1300_v1`。development 与 CoCo 的 S2 pool 不允许混用。

其余 stage 的必需参数以各自 `--help` 为准。每一步读取上一阶段已经保存的 artifact；`run-evolution` 只是显式 spec 的薄协调器，不把 Review 与 Evaluation 合并成在线调用。

## 测试

```bash
PYTHONPATH=AgentReview /data0/xyj/miniconda3/envs/env_shengao/bin/python -m pytest -q AgentReview/tests
```

测试覆盖五角色 identity、完整 Review 一级保存、只读 Memory、deterministic retrieval、Summarizer 单次例外、后置 Internal Evaluation、Meta/Concat、S2 self-match 防护、CoCo largest-remainder 抽样、M★ pool/patch/lineage 以及五角色 Tiny E2E。

## 生成资产

`artifacts/research_v2/` 是本地不可变生成资产目录，不提交到 Git。正式归档通过 artifact 内容、配置、代码 commit 和实现日志中的 SHA256 标识。历史 legacy 实现由 Git 历史保留，不在 v2 分支继续提供兼容入口。
