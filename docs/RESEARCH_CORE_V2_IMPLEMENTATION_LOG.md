# Research Core v2 修改记录

> 状态：development 数据与 S2 已冻结；专用 27B 和真实 8 并发 smoke 已验证；正式 canonical bootstrap 与串行 CoCo S2 正在运行
> 实施日期：2026-09-01  
> 本轮实验起点 branch / commit：`20260831-minimal` / `827289a`

本文只记录实际执行的修改、删除、生成资产和测试证据。目标协议见 [RESEARCH_CORE_V2_DESIGN.md](RESEARCH_CORE_V2_DESIGN.md)。

## 1. 基线

- 工作区实施前无未提交修改。
- 测试解释器：`/data0/xyj/miniconda3/envs/env_shengao/bin/python`，Python 3.12.0、pytest 9.1.1。
- 非交互 shell 没有默认 `python` / `pytest`，测试均显式使用上述解释器。
- 基线定向测试：15 passed，3 subtests passed，0.76s。

## 2. Prompt 与 identity

- `REVIEW_Request.md` 删除 Meta 章节，只保留 `QUAL / CLAR / SIGN / ORIG / POL`。
- 新建 `META_SYNTHESIS_Request_v1.md`；输入仅为 Frozen Paper 和固定顺序五份完整 Specialist Review。
- Meta 明确禁止 related-work Memory、Internal Evaluation、Program、S2 和 CoCo；不输出自定义数字总分或 confidence 指标。
- `reviewer_identity.py` 改为 Specialist-only suite v3；`ALL_TASKS == SPECIALIST_TASKS`。
- Meta 使用独立 loader、manifest、prompt hash 和 generation config，不属于 Reviewer task。
- 新增 `frozen_specialist_identity_v3.json` 与 `frozen_meta_synthesis_v1.json`。

冻结 source SHA256：

```text
REVIEW_Request.md                 28c975c55fb2594346d52d0e548e9d6fe87d7f189d06189113c2f531a18ea3e2
META_SYNTHESIS_Request_v1.md      6beb7fb87506d9413ac1c3f8c7aee5915d130750794ec7fc647e806fbdd19fe5
```

## 3. Research Core v2 实现

新增 `agentreview/research_v2/`，其 stage 可以分别运行：

```text
prepare-data
prepare-coco-1300
build-s2
bootstrap-corpus
build-memory
review
internal-eval
evolve-step
run-evolution
freeze-system
coco-eval
```

主要边界：

- immutable JSON artifact I/O：同一路径只允许完全相同内容重放。
- Frozen Paper、完整 Specialist Review、Internal Evaluation、Final Review 独立 schema。
- Review 先保存 raw output，再保存未切块完整 Review；没有 evaluator hook 或 Memory writeback。
- Memory 只在 offline build 阶段写入；Review Run 只读 snapshot。
- Query 由 Frozen Paper title/abstract 和 role 确定性构造，不调用生成模型。
- Vector 与 Experience read path 是 deterministic top-k；Summarizer 只能在命中集合和 IDs 已记录后调用一次，失败不回退。
- S2 只在 data preparation 联网；runtime 按 exact paper ID 读取并拒绝 self-match / duplicate evidence。
- Internal Evaluation 后置运行，只读 Frozen Paper、角色、完整 Review 和 Reviewer 当时所见 evidence；输出四个已确认字段。
- Meta 和 concat 是两个明确 final mode；Meta 失败不回退 concat。
- CoCo 只由外部子进程读取 Final Review 的 `{id,text}` 视图。
- Frozen System 绑定确切 CoCo-1300 manifest；外部进程启动前严格验证 Final Review IDs 与冻结1,300篇完全相等。
- Canonical bootstrap 默认8个 bounded worker；每个完成角色立即更新 mutable `run.json` checkpoint，同 run ID 由 nonblocking `flock` 防止双进程重复请求。
- raw artifact 绑定完整 request、model generation、retry、Program 和 Memory identity；OpenAI SDK retry 显式关闭，唯一重试层最多执行两次完全相同请求。
- infrastructure failure 停止补充队列并以非零 CLI 状态退出；已完成角色和 pending 状态可由同 run ID 恢复。
- immutable JSON commit 使用 unique temp + `os.link` 原子 create-if-absent；并发不同内容只有一个进程可提交。

## 4. M★ 对齐实现

- 保留 Program、KnowledgeItem、Query、KnowledgeBase.write/read、Toolkit、sandbox 的核心协议。
- 新增三个 v2 seed：`vector_search`、`llm_summarizer`、`experience_learner`；另有 canonical bootstrap 专用 `no_memory`。
- 每个角色拥有独立 append-only pool、softmax parent selection（temperature 0.15）、Reflector、V4A patcher、candidate attempt artifact 和 lineage。
- Reflector 每次 attempt 允许一次主要 patch；失败时最多一次显式 repair。
- Candidate 先经过 AST capability check 和 sandbox protocol smoke；完成独立 static Review/Internal Evaluation 后才进入 pool。
- rotating feedback 包含完整 Review 与 qualitative Internal Evaluation，不包含 human reference、CoCo、Meta 或其他角色结果。

## 5. Data、S2 与 CoCo-1300

- development preparation 扫描 OpenReview `train_clean`，只接受实际存在的 Markdown asset。
- 采用 exact ID、canonical title、paperhash、full-text SHA256 的最小直接去重；三组 125/25/125 互斥。
- development 和 CoCo 分别配置 `frozen_s2_external_development_v1` 与 `frozen_s2_coco1300_v1`。
- CLI 根据 Frozen Paper `source.dataset` 自动选择正确 S2 pool；`freeze-system` 强制同时绑定两套 pool。
- 旧 `frozen_s2_temporal_v2` 只覆盖 CoCo family representative，且含大量 self-match，未复用到 v2 并已随 legacy 配置删除。

实际执行 `prepare-data`：

```text
protocol             research-v2-development-split-v1
usable candidates    34,052
canonical/static/rotating  125 / 25 / 125
development manifest b684aaf982c822e1538fc4c83369d1ef91e4f54139b758dc9fdefbff1d5efc5a
pairwise disjoint     verified
```

实际串行执行 development S2：

```text
targets               275
request concurrency   1 process / serial / at most 1 request start per second
records               275
zero-result records   71
selected evidence     877
pool SHA256            231430ed3ce7cff9d53bc5c962df7062b25807fcc79bc483e7fad720849ad8cc
```

构建使用 per-target immutable checkpoint 和 artifact-root nonblocking lock。首次运行揭示 Semantic Scholar 的合法零命中响应是 `total: 0` 且省略 `data`；客户端现将它明确解释为空列表，其他缺失 `data` 的响应仍失败。已有 checkpoint 在修复和受控重启后全部原位恢复。最终文件又通过一次无网络 CLI 重放、record hash、顺序 identity 与 runtime self-match 全量校验。

实际执行 `prepare-coco-1300`：

```text
protocol        coco_1300_protocol_compatible_resample_v1
groups          13
papers/group    100
total           1300
seed            0
manifest SHA256 9d1ff5ba4210cf69520c48c82a122d6619532abc42962d74cd8453ed4fcfac65
sampler SHA256  9566f69729240e486d02685bad2370a4d486b88c620718b38c8b015eb1988ecc
paper assets    63.21 MiB
```

逐份校验 1,300 个 Frozen Paper 只含 paper schema，不含 `reviews / opinions / sentence_texts / metareview`。生成资产位于 `artifacts/research_v2/`，由 Git ignore；正式归档依靠配置、commit 和 SHA256。

第一次实际运行发现每篇重复哈希 venue-year source JSONL，进程运行36秒仍未完成。停止该自有进程后修正为每个源文件只哈希一次；幂等复用已写 paper assets 的验证运行用时19.2秒。最终源码清理改变了 sampler 文件 SHA，因此旧生成目录被改名为 `coco_1300_pre_final_20260901` 保留，正式路径从空目录重建；最终 manifest 的 sampler SHA 与当前源码逐字节一致。

## 6. 模型与 embedding 配置

- 新增 `configs/research_v2_formal_v1.json`。
- generation：`Qwen/Qwen3.8-27B`，revision `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`，BF16，temperature 0，thinking false。
- 专用 live endpoint：物理 GPU 3、6 上的 `http://127.0.0.1:18001/v1`；TP=2、BF16、max model length 65536、`max_num_seqs=8`。
- server environment：vLLM `0.20.1+cu129`，Transformers `5.8.1`。
- embedding：`Qwen/Qwen3-Embedding-8B`，revision `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af`，本地 cache 路径独立配置。
- test/embedding environment：Transformers `4.57.1`；它与生成服务环境分开记录。
- tokenizer SHA256：`0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3`。
- chat template SHA256：`c3cf9e34abf4f9e36c2d72165aa9c132d3e2a725b6c2586aaa3a8af9d7a81041`。

服务测得 GPU KV cache 478,412 tokens，完整 65,536-token request 的理论并发为7.30。实际 Specialist 最大输入预检约25,621 tokens，加8,192输出预算约33,813，因此正式 workload 使用8 worker。2篇×5角色的完整 S2 live smoke 实测经历长 prompt 分批 prefill 后达到 `8 running / 0 waiting`，峰值 KV 约33%；10/10 首次调用成功。同 run ID 重放为10/10 resumed，模型 prompt/generation token counter 均未增加。32k 无法覆盖已测最大输入与输出预算，因此正式采用64k；当前没有必要牺牲并发升到96k。

## 6.1 CoCo 与 Review 排队边界

- stock CoCo 客户端 cap 是 Split 512 / Classify 1024；它们是 HTTP 在途上限，不是 GPU active sequence 数。
- 新 `scripts/run_official_candidate.py` 默认收紧为 Split 64 / Classify 128，只连接预先部署并完成 GPU pin 的 endpoint。
- 两个 CoCo launcher 都是单卡 TP=1，且未显式设置 `max_num_seqs`；实际服务端并行由 vLLM 默认与 KV 容量决定。
- runner 不生成 Review、不启动/停止服务、不执行全局 `pkill`，并在成功返回后输出固定 contract 与 split/classify/可选 evaluation SHA256。
- A 整臂 CoCo 与 B 整臂 Review 可在不同 GPU 上由两个独立进程同时运行；单臂内仍保留严格1,300篇 exact-set barrier，不做逐 paper microbatch 交错。

## 7. Legacy 删除记录

历史实现由 Git 保存，本批次不维护兼容路径。删除约 5.8 万行 legacy 源码、测试和配置，包括：

- online Memory writeback、旧 Reviewer Core session/episode/artifact engine；
- CoCo fitness、sealed/proxy adapters 和内联 evaluator；
- Effectiveness Screening、bundle promotion、formal readiness gates；
- temporal four-zone/family graph 配置和 builder；
- 六任务含 META 的旧 M★ CLI、search/evaluator/reflector/orchestration；
- old deployment runner、provider registry、Arena、OpenScholar snapshot、viewer；
- legacy shell scripts、old CoCo/S2 scripts 和对应测试。

保留的底层核心：

- `agentreview/reviewer_runtime/sandbox.py` 与 `sandbox_worker.py`；
- `agentreview/reviewer_runtime/toolkit.py`；
- offline `TaskAgent`；
- `memory_evolution/embedding_adapter.py`；
- frozen prompt identity、paper context/source anchors；
- v2 自身的 Program seeds、pool、Reflector、patcher 和 lineage。

## 8. 测试记录

| 阶段 | 结果 |
|---|---|
| Baseline targeted | 15 passed，3 subtests passed |
| v2 first targeted | 15 passed |
| v2 + CoCo quota + five-role Tiny | 18 passed，1.90s |
| final v2 suite + exact CoCo manifest binding | 19 passed，1.58s |
| artifact binding checks + final lint/compile/test | 20 passed，1.67s；Ruff passed；compileall passed |
| resumable/concurrent bootstrap + S2 lock + immutable race | 35 passed，6.89s；Ruff/compileall/diff-check passed |
| Qwen live S2 smoke | 2 papers × 5 roles，10/10 first-attempt；8 running verified；resume 10/10 |
| CoCo official candidate runner | CoCo repo 2 passed；AgentReview exact contract tests included above |
| compileall after legacy removal | passed |
| CLI `--help` | 10 stage entries loaded successfully |
| CoCo-1300 actual freeze | 1,300 assets、13 groups、paper-only boundary verified |

Tiny 测试按实际边界执行：五个 role-local no-memory snapshot → 五次独立完整 Review → 确认 Review 阶段没有 Evaluation → 五次后置 Internal Evaluation → 独立 Meta 与 concat Final Review；使用 fake completion client，不占用正在执行其他任务的正式 Qwen GPU 服务。

## 9. 正式运行状态与后续队列

`canonical-bootstrap-v1` 已于 2026-09-01 16:40 CST 启动：125篇 canonical paper × 5角色，共625个任务，S2 开启，`max_workers=8`。启动核验时已完成7个角色，0 paper failure、0 infrastructure failure，vLLM 为8 running / 0 waiting；tmux session 是 `ar0831_formal_canonical_c8`，进度日志是 `artifacts/research_v2/runtime/canonical_bootstrap_v1/stderr.log`。

尚未执行：

1. canonical Review 完成后的后置 Internal Evaluation、三 seed Memory 与五角色 Evolution；
2. 独立的 CoCo-1300 S2 pool 已在 development S2 释放全局锁后顺序启动；它与 GPU Review 并行，但全机仍只有这一个 S2 HTTP 流，tmux session 为 `ar0831_s2_coco1300`；
3. Frozen System、1,300份 CoCo Final Review，以及随后独立运行的 split/classify/evaluation。

正式服务与常见环境问题统一记录在 [EXPERIMENT_CONFIGURATION.md](EXPERIMENT_CONFIGURATION.md)。这些剩余项目是实验队列，不是当前核心代码或边界测试失败。
