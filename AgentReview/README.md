# AgentReview M★

AgentReview 对论文执行五个独立 Specialist Review，再由 Final Meta-Reviewer 汇总：

```text
Markdown paper
  → Canonical Paper Context
  → QUAL ┐
    CLAR │
    SIGN ├─ Specialist Review Bundle → META → final review
    ORIG │
    POL  ┘
```

每个任务绑定一个独立 M★ MemoryProgram。`agentreview/reviewer_identity.py` 从 [REVIEW_Request.md](REVIEW_Request.md) 的六个有序章节渲染统一 Frozen Reviewer Identity，并用 [configs/frozen_reviewer_identity_v2.json](configs/frozen_reviewer_identity_v2.json) 校验语义 manifest。当前身份版本为 `frozen-reviewer-identity-v2`：

```text
source SHA256: ecd42d44c1df2cb27f606c48cb5071eea844e9dc93a7b8a36bb78352806d148d
semantic manifest SHA256: a480246958ec0371e577c89a946ddf1e92bad766a076ec55201cbe1c111f7cf2
suite SHA256:  84d1bab744e2d63f4d2168b5c0bd8d9a3bdf3543b69bd11ad5e81d931a3acaa4
Specialist protocol/template SHA256: specialist-review-request-v1 / 3c56e5bcf6f71488f34515192c625162c028ce09e726c207d3ac99618d295dc5
META protocol/template SHA256: meta-synthesis-request-v2 / 85263700054ef329e55bf103bba66d93b6537263ec867ea9d6c304e079257502
```

META v2 请求包含同一论文的 Canonical Paper Context（正文与 Source Anchors）以及按 `QUAL/CLAR/SIGN/ORIG/POL` 排列的五份 Specialist Review。

## 文档入口

- [完整代码架构](../docs/ARCHITECTURE.md)：模块边界、执行流、数据流、产物、配置和接口状态。
- [领域术语](../CONTEXT.md)：Reviewer、MemoryProgram、Evolution、四区数据与评测术语。
- [文档索引](../docs/DOCUMENT_INDEX.md)：当前说明文件及其所有权。
- [Evolution Viewer](evolution_viewer/README.md)：演化 lineage、分数、源码 diff 与 trace 查看器。
- [Seed Programs](memory_evolution/seeds/README.md)：三个初始 MemoryProgram 的设计。

## 环境

所有命令从本目录执行。当前验证解释器为：

```text
/data0/xyj/miniconda3/envs/env_shengao/bin/python
```

安装正式运行依赖：

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` 是 `requirements-runtime.txt` 的兼容入口。Viewer、注册的可选 provider、保留的 OpenScholar 快照、OpenReview 数据导入和开发工具使用：

```bash
python -m pip install -r requirements-optional.txt
```

运行测试：

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s evolution_viewer/tests -v
```

## 三条执行面

| 执行面 | 入口 | 用途 | 主要产物 |
|---|---|---|---|
| 部署审稿 | `run_single_paper_review_cli.py` / `run.sh` | Markdown 抽取、五 Specialist 并发、META、checkpoint、在线 writeback | `reviews.json`、`candidate_reviews.jsonl`、`memory.json`、`review_history.json`、`timing.json` |
| Reviewer Core | `python -m agentreview.cli review` | 直接执行一个 MemoryProgram 的完整 review/write/query/read/response 协议 | 严格 `candidate_reviews.jsonl` |
| M★ Evolution | `python -m memory_evolution.cli` | 数据准备、六任务演化、META cache、评估、正式套件评测 | program pool、lineage、scores、trace、promoted program、report |

部署编排把 MemoryProgram 用作检索上下文提供者，并由 `Player` backend 生成最终文本。Reviewer Core CLI 由 MemoryProgram 与固定 TaskAgent 共同完成完整评审协议。两条链路共享 Program 协议、sandbox、toolkit 和 provenance。

## 运行完整审稿

准备六个已提升 Program：

```text
<program-root>/
  suite_manifest.json
  meta_manifest.json
  QUAL/
    best_program.py
    seed_<n>/
      config.json
      selection.json
  CLAR/ ...
  SIGN/ ...
  ORIG/ ...
  POL/ ...
  META/
    best_program.py
    seed_<n>/
      config.json
      selection.json
  lineages/<lineage>/<TASK>/best_program.py
```

`suite_manifest.json` 与 `meta_manifest.json` 的 `program`、`source_run` 记录套件根相对路径；resolver 以 manifest 所在目录定位 Program 与 `<source_run>/config.json`，并校验源码和 embedding provenance。

部署审稿与 Reviewer Core 写出的候选记录采用 `agentreview-candidate-review-v2`。记录同时绑定 task prompt SHA、request template SHA 与 composite suite SHA，读取端按当前 Frozen Reviewer Identity 严格校验完整任务身份。

配置 OpenAI-compatible 模型与 embedding 身份后运行：

```bash
export OPENAI_API_KEY=dummy
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export MSTAR_PROGRAM_DIR=/absolute/path/to/program-root
export MSTAR_EMBEDDING_REVISION=1d8ad4ca9b3dd8059ad90a75d4983776a23d44af
export PAPER_MD_DIR=/absolute/path/to/papers
export REVIEW_OUTPUT_DIR=/absolute/path/to/output
bash run.sh
```

本地 vLLM 可以使用任意非空 API key。远程服务使用服务方签发的 key。凭据通过环境变量或对应入口的显式 CLI 参数进入进程。

结果查看器：

```bash
python viewer.py --output_dir /absolute/path/to/output --host 127.0.0.1 --port 7861
```

## M★ 演化

当前演化协议包含三个 Seed Program 和 20 个 Evolution Attempts：

- `vector_search`：lossless write 与向量检索；
- `llm_summarizer`：lossless write 与 query-focused read compression；
- `experience_learner`：write-time abstraction 与结构化 recall；
- 父代从通过质量门禁且完成 Static Evaluation 的全局候选池按分数 softmax 选择；
- 变异、门禁、编译与评估的每次结果均消费一个 attempt；
- Lineage 记录来源并支持各 lineage champion；
- 当前默认 Static Selection 为 25，Rotating Selection 为 5，parent temperature 为 `0.15`；
- 默认历史 Episode 预算为 `5 × Static Selection = 125`，每个候选从空状态按同一冻结顺序 replay 该子集。

验证端到端小规模流程：

```bash
export OPENAI_API_KEY=dummy
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENREVIEW_ROOT=/absolute/path/to/OpenReview
export COCOREVIEWBENCH_ROOT=/absolute/path/to/CoCoReviewBench
bash pilot_full.sh
```

查看演化结果：

```bash
python -m evolution_viewer.app \
  --runs-dir /absolute/path/to/runs \
  --host 127.0.0.1 \
  --port 7862
```

Specialist Evolution 结构冒烟入口：

```bash
export OPENREVIEW_ROOT=/absolute/path/to/OpenReview
export COCOREVIEWBENCH_ROOT=/absolute/path/to/CoCoReviewBench
bash end2end_smoke.sh
```

该脚本先在 `data/specialist_smoke` 准备 temporal v2 小样本，再执行
`evolve-specialists --smoke`。产物写入 `runs/specialist_smoke`，身份为
`smoke_non_reportable`。

列出全部演化子命令与参数：

```bash
python -m memory_evolution.cli --help
python -m memory_evolution.cli evolve-task --help
python -m memory_evolution.cli final-evaluate-suite --help
```

## 正式数据与资产

当前权威数据协议是 family-deduplicated temporal v2：

| Zone | 语义 | Family targets |
|---|---|---:|
| A | Historical Experience | 2391 |
| B | Evolution Validation | 299 |
| C | Effectiveness Holdout | 300 |
| D | Held-out Benchmark Test | 899 |

3900 条 CoCo source records 归并为 3889 个 Paper Families。每个 Specialist task bundle 包含 2391 个 A 区 Episode 和 150 个 B 区 Validation；其余 149 个 B 区 family 保留给 META Validation。Frozen S2 v2 覆盖全部 3889 个 target。

`configs/asset_manifest_v2.json` 绑定 Frozen Reviewer Identity v2、temporal split、Frozen S2、三个 Seed Program 和 Qwen3 embedding fingerprint。该 manifest 的 `formal_ready` 范围覆盖 pre-evolution assets 与 A/B task bundles。当前 Qwen3 selection cache audit 记录 12,705 个 4096 维向量，截断数量为 0。

`configs/formal_temporal_v3_qwen3e8b.json` 将 selection assets 标记为 `regeneration_required`；完成正式演化前需要生成 specialist/META selections、rotating metadata、embedding cache 与 readiness manifest。

Frozen S2 完整性核验：

```bash
python scripts/build_frozen_s2_pool_v2.py --check-only
```

密封 D 区六任务测量通过：

```bash
python -m memory_evolution.cli final-evaluate-suite --help
```

该入口执行 formal protocol gate，在每份 Review 生成后通过 `CoCoMetricBenchmarkAdapter` 与 `CoCoReviewFitness` 内联计分，最终持久化紧凑的 `sealed_d_coco_style` 套件报告。`evaluate` 服务单任务开发评估。

CoCoReviewBench 自带的 split、classify、evaluation 三阶段由独立官方 Adapter 执行：

```bash
python -m agentreview.coco_eval --help
```

该入口消费已持久化的 `candidate_reviews.jsonl`，依次持久化 split、classify、reviewer selection、protocol identity 与 evaluation 产物，输出 official CoCo evaluation 身份。两类结果使用独立 metric identity。

## 接口状态

当前主执行面包括 OpenAI-compatible backend、Azure OpenAI 鉴权、部署审稿 CLI、Reviewer Core CLI、M★ CLI、CoCo Adapter 和两个只读 Viewer。Backend registry 同时注册 Anthropic、Cohere、Transformers、Human 与 Dummy Adapter，安装可选依赖并提供凭据后即可选择。

仓库保留以下暂未启用接口：

- ChatArena 与 Supabase 快照；
- Bard 与 LangChain provider Interface；
- OpenScholar core/API 字符串快照；其 Semantic Scholar search helper 继续服务 legacy retrieval Interface；
- BGE-M3 embedding Adapter，当前正式配置选择 Qwen3-Embedding-8B；
- OpenReview 数据下载和整理 CLI；
- Effectiveness Screening 的 C 区三类基础 arm 与 lineage-expanded Program arm。

Effectiveness Screening 配置明确记录激活所需的 evolution data、六任务 promoted programs 和 C 区 materialized context。现行设计选择 20 个 A 区历史目标与 20 个 C 区评测目标，执行 7 arms × 2 replicates，形成 280 个目标审稿包和 12 条 Program 历史 replay 命令。

## 生成目录

`runs/`、`outputs*/`、embedding SQLite、Frozen S2 work checkpoint、Viewer runtime 和日志属于可再生运行产物，Git 通过 `.gitignore` 管理这些路径。正式结果通过外部归档和 manifest/hash 保存身份。

## License

见 [LICENSE](LICENSE)。
