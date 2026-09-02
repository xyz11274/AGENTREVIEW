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

完整设计见 [RESEARCH_CORE_V2_DESIGN.md](../docs/RESEARCH_CORE_V2_DESIGN.md)，领域术语见 [CONTEXT.md](../CONTEXT.md)，实际修改和测试证据见 [RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md](../docs/RESEARCH_CORE_V2_IMPLEMENTATION_LOG.md)。正式服务、S2 串行限流、CoCo 并行边界与常见环境问题统一记录在 [EXPERIMENT_CONFIGURATION.md](../docs/EXPERIMENT_CONFIGURATION.md)。

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

正式生成模型是物理 GPU 3、6 上的独立 vLLM 服务。当前冻结配置连接 `http://127.0.0.1:18001/v1`，served model name 为 `qwen3.8-27b`，TP=2、BF16、64k context、`max_num_seqs=8`、temperature 0、`enable_thinking=false`。Qwen3-Embedding-8B 在本地进程中只服务确定性 embedding retrieval。

## Stage CLI

```bash
python -m agentreview.research_v2 --config AgentReview/configs/research_v2_formal_v1.json --help
```

可独立启动的 stage：

```text
prepare-data
prepare-coco-1300
build-s2
bootstrap-corpus
build-memory
review
internal-eval
evaluate-corpus
evolve-step
run-evolution
formal-evolution
materialize-freeze-spec
materialize-coco-scoring-spec
freeze-system
generate-coco-reviews
seal-coco-pre-score
coco-eval
audit-coco-final
validate-coco-final
```

准备 development 的互斥 125/25/125 划分：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  prepare-data
```

已有 data artifact 时，独立串行获取并冻结 development S2，避免重新扫描原始数据：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  build-s2 --dataset development
```

S2 全机只运行一个构建进程，development 与 CoCo 不并发；每个进程严格串行，任意两次请求的启动间隔至少为1.2秒。限流和恢复规则见 [EXPERIMENT_CONFIGURATION.md](../docs/EXPERIMENT_CONFIGURATION.md)。

独立冻结 protocol-compatible CoCo-1300；该集合不是作者未公开的 original IDs：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  prepare-coco-1300
```

加 `--build-s2` 时构建独立 `frozen_s2_coco1300_v1`。development 与 CoCo 的 S2 pool 不允许混用。

五角色 Formal Evolution 与两套 S2 均完整后，先冻结干净的外部 CoCo 仓库、模型、
benchmark、服务参数和两臂 cache identity；随后从五个已验证 winner 确定性生成
self-hashed freeze spec，再冻结系统并生成可恢复的 1,300 篇 winner Review：

```bash
python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  materialize-coco-scoring-spec \
  --winner-run-id formal-coco1300-winner-v1 \
  --evaluation-python artifacts/research_v2/runtime/coco_eval_env_v1/bin/python \
  --evaluation-proxy-url http://127.0.0.1:7898 \
  --output artifacts/research_v2/frozen/formal-coco1300-scoring-spec-v1.json

python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  materialize-freeze-spec \
  --run-id formal-evolution-v1 \
  --output artifacts/research_v2/frozen/formal-evolution-v1.freeze-spec.json

python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  freeze-system \
  --spec artifacts/research_v2/frozen/formal-evolution-v1.freeze-spec.json \
  --output artifacts/research_v2/frozen/formal-evolution-v1.system.json

python -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  generate-coco-reviews \
  --frozen-system artifacts/research_v2/frozen/formal-evolution-v1.system.json \
  --run-id formal-coco1300-winner-v1 \
  --max-workers 8
```

`materialize-freeze-spec` 要求五角色 all-role summary、pool、coordinator、winner、
run-spec 与当前配置完全一致，并对语义和源哈希执行两遍一致性检查；它还分别记录
Evolution 的完整有序恢复审计链与 clean transition Git HEAD。审计链绑定同一 run 的
全部非 symlink audit、事件时间、前序 audit SHA、typed checkpoint schema，以及
AgentReview/外部 CoCo 仓中解析后的完整 commit 和线性祖先关系；不会把旧 Evolution
执行误写成新 transition commit，也不会人工改变 winner 的 S2 策略。`generate-coco-reviews` 的
Specialist 与 Meta 共用同一个最多8路的27B队列，每篇五份 Specialist 完成后即可排队
Meta；concat 不调用 LLM。相同 run ID 仅在正文、raw output、请求与完整 frozen identity
均一致时恢复。

冻结系统和两组各 1300 份 Final Review 齐备后，用 `seal-coco-pre-score` 固化候选与
运行态证明。`coco-eval`
只能按 seal 调用 CoCoReviewBench 的 `scripts/run_official_candidate.py`；该 runner
只连接已经常驻并完成 GPU pin 的 split/classify endpoint。正式 evaluation 为必需
阶段，凭据仅从 `COCO_EVALUATION_API_KEY` 注入子进程且不进入 artifact。两臂完成后
必须运行 `audit-coco-final`，并可用 `validate-coco-final` 离线复核完整闭包。CoCo
整臂与另一实验臂的 Review 可由两个独立进程并行启动；单个 sealed arm 保持整臂
执行。三阶段输出预算/上下文上限固定为 Split 8,192/40,960、Classify
4,096/32,768、Evaluation 32,768/400,000；runner 必须在对应 API 请求前执行封印的
离线 prompt preflight，超限即失败且不得截断。

其余 stage 的必需参数以各自 `--help` 为准。每一步读取上一阶段已经保存的 artifact；`run-evolution` 只是显式 spec 的薄协调器，不把 Review 与 Evaluation 合并成在线调用。

## 测试

```bash
PYTHONPATH=AgentReview /data0/xyj/miniconda3/envs/env_shengao/bin/python -m pytest -q AgentReview/tests
```

测试覆盖五角色 identity、完整 Review 一级保存、只读 Memory、deterministic retrieval、Summarizer 单次例外、后置 Internal Evaluation、Meta/Concat、S2 self-match 防护、CoCo largest-remainder 抽样、M★ pool/patch/lineage 以及五角色 Tiny E2E。

## 生成资产

`artifacts/research_v2/` 是本地不可变生成资产目录，不提交到 Git。正式归档通过 artifact 内容、配置、代码 commit 和实现日志中的 SHA256 标识。历史 legacy 实现由 Git 历史保留，不在 v2 分支继续提供兼容入口。
