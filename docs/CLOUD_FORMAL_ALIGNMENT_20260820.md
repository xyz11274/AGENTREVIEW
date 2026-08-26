# AgentReview 云端正式 Evolution 开跑前全面对齐审计

审计日期：2026-08-20  
审计对象：仓库提交 `30fb8f97e13043b10974b10f917a2d9cde8dc63b`  
审计边界：只读检查当前源码与冻结资产；本报告之外未修改程序、配置、冻结 JSON/JSONL 或数据。

# 1. Executive Summary

## 结论：BLOCKED

**现在不能直接开始“真实 CoCo + 真实 BGE + 真实 Kimi”的 canonical QUAL smoke。** 数据本身已就绪，`--smoke` 也不受 formal readiness 和 D-3 无限重试阻断；但在下列运行前条件闭合前，不能把 smoke 判为可开跑：

1. **已确认 blocker：CoCo adapter 严格 Git 身份不对齐。** 当前被审计的 CoCo checkout HEAD 是 `8b606ade92201db210a4add3db8f18219146425e`，源码固定要求 `2580687ee5d4ad2ac4b9730afb8f1dfda22e40db`（`memory_evolution/tasks.py:25`），adapter 在不相等时直接抛错（`memory_evolution/coco_adapter.py:24-47`）。当前额外提交虽只改 onboarding/vLLM 启动脚本，且 evaluator/benchmark 哈希仍匹配，但 adapter 看的是整个 HEAD，不看差异范围。云端必须先执行第 12 节第 1 步确认；若仍为 `8b606...`，需使用独立的 pinned adapter checkout，不能直接把正在服务的 checkout 强行切回旧提交。
2. **需要云端实际检查：** Evolution 所在 allocation 必须能通过 `127.0.0.1:13099/13098` 看到两个服务；代码没有可配置远端 hostname endpoint（`memory_evolution/coco_adapter.py:18-21,74-81`）。
3. **需要云端实际检查：** `bubblewrap` 必须允许 user namespace；smoke 也会创建默认 `require_bubblewrap=True` 的 MemoryProgram sandbox，失败即终止（`memory_evolution/sandbox.py:24-50,75-112`）。
4. **需要云端实际检查：** 指定 revision 的 BGE-M3 必须可在 CPU 模式加载；代码允许 CPU，但模型和 tokenizer 必须存在或可下载（`memory_evolution/embedding_adapter.py:16-33`）。
5. **运行条件：** Kimi key 需现场输入，并同时设置 `OPENAI_API_BASE` 与 `OPENAI_BASE_URL`；第 7 节 preflight 必须成功。

以下不是 QUAL smoke blocker，但会阻塞 formal seed0：

- 当前 `configs/asset_manifest_v2.json` 仍是 `formal_ready=false` 的旧注册状态（`configs/asset_manifest_v2.json:78-83`）。必须先完成一次 readiness 注册，再 `--check-only` 复核。
- formal gate 会真实读取 frozen data 内的旧 `/data0/xyj/shengao/CoCoReviewBench/**`，目前没有 root remapping。必须提供兼容挂载/软链，或以后修改代码；不能改 frozen JSONL。
- D-3 外层 formal candidate loop 无总 attempt/failure cap，正式 21-iteration 长跑前建议修复；见第 11 节。

### 状态总览

| 项目 | 状态 | 结论 |
|---|---|---|
| Frozen S2 v2、temporal v2 | **PASS** | 哈希、数量与审计结构完整，不重建 |
| `data/evolution_coco_v2_20260819` | **PASS** | 当前 canonical specialist data root；不要运行 `prepare-specialists` |
| Human review 隔离 | **PASS** | Candidate 数据无 answer，reference 位于 evaluator-only |
| OpenReview 213GB | **PASS / NOT NEEDED** | 不用于 specialist evolve/smoke/formal gate；不建议传输 |
| Split/Classify vLLM 部署 | **PASS（按已给实测事实）** | 不重复调查安装或 shard |
| CoCo adapter Git identity | **BLOCKER** | cloud checkout 必须等于固定 commit，或另建 pinned clone |
| 旧 CoCo 绝对路径 | **formal BLOCKER；smoke 非 blocker** | readiness/formal gate 真正解引用；需兼容路径 |
| asset manifest v2 注册 | **formal BLOCKER；smoke 非 blocker** | 先注册、后 check-only |
| D-3 无限 retry | **formal HIGH/BLOCKER；smoke 非 blocker** | smoke 外层按一次 attempt 截止；formal 无失败上限 |
| bubblewrap、BGE CPU、localhost 可见性、Kimi | **云端实际检查** | 最小命令见第 12 节 |

# 2. Canonical Frozen Assets

## 2.1 当前 canonical data root

正式五 specialist 的唯一当前数据根应为：

```text
data/evolution_coco_v2_20260819
```

不是 `data/formal_temporal_v2`。后者在当前仓库不存在；前者约 1.5 GB，根 manifest SHA256 为：

```text
b484499815ffdfaf8f4fcccb8b81562af3ee8328450c18dfca65a37fc04e3815
```

`evolve-task` 直接用 `--data-root/<TASK>/episodes.jsonl` 和 `validation.jsonl`，再从 `evaluator_only/<TASK>/validation_references.jsonl` 拼 evaluator answer（`memory_evolution/cli.py:481-533,550-554`）。它不会依据旧执行稿目录名选择数据。

每个 task 已核实：episodes 2391、queries 2540、validation 150、evaluator-only references 150；validation/reference ID 集合完全一致。五 task 文件哈希如下：

| Task | `episodes.jsonl` | `queries.jsonl` | `validation.jsonl` | `evaluator_only/.../validation_references.jsonl` |
|---|---|---|---|---|
| QUAL | `1e79c14fdb9b1273619685d5b334010375ba2583e1d99acb9f2a3e9c38f03138` | `3587dc9a1b304b3a00d90051c7eb9717f69e50fb0ce08383df5c372118919d5e` | `98e44f0fba03b927fc55100c73d9b65966df9dae91cc99e618f7f4f94e33f8d2` | `d813cc34bda4debb228b2e5cc8c87b925a21845ab957676d58a2bbe3a3982fad` |
| CLAR | `2a64be9a6e59f52df3a13a58700c0303b08476b2213d81dbf5203e569c5bf781` | `e9060266634bc1845c592dcf298b047cdd27a586dbcf5047e996a28eaa66d932` | `85bf691feeba47514bd209b9bce5bf33aa452c047b13c261852d4cbaa795c4a8` | `109c29a9814f3039479925be14ad4fbd4adbfa1de888ab76d3451a5f616259dc` |
| SIGN | `481d5012f39bafa1dc9c85099be91f67e48f14919929aef06c9b84585bdb8e08` | `64d04192d3c6e42b8c153ff0f6d47d2d732146393c431f59f44d9fbca46cac18` | `9876b5f85940c86b63cb83f64c46afc854226bf7278936b94dbecc0da3b792ec` | `c3fe0c6424b02c93673398a5c17e545d670dac31a60ea1e077f9af933f0cde5b` |
| ORIG | `0598bf4075313b16f983ffee58f552ebd64fa13ee8ccc0029592332741609ddc` | `37dc6e7d76c6f1ffaa548542bf7314f60c54f4ab855ae91f707626f35a79d729` | `61e1655d169326d7f5260da6f2b14291f91bb024db761712c055a3bd4a752a7a` | `3cefcea1f015ad92c4c8f96bbd6ae77094275b2bfffe5ff26ccc6f6040a48bcf` |
| POL | `3140d172dac2f0a616104936f2787327b8c71a0412952baa7631e44c99d813f4` | `3ca936e03bde83cfbe71ab866cc44e8f7e42c945892c040f70bf0ffc3c9db3a7` | `88ccc79bf66635ddde05f01dcedf7b6b20e175eaa667497d472472f04eabfb33` | `6e222183845683de1c7c2479bad4d2c404afe845f872d2dcdacfacee2897b8ca` |

## 2.2 冻结基础资产

| 路径 | SHA256 | 用途 | runtime 读取 |
|---|---|---|---|
| `configs/frozen_s2_temporal_v2.jsonl` | `367cdff9f2255e280ec2de2843e87d79d7d6cc8cbc08f626b0500a0d2482ef49` | 3889 paper、top-k=10 frozen S2 retrieval | **是**；Toolkit lookup，formal gate 校验 |
| `configs/frozen_s2_temporal_v2.jsonl.manifest.json` | `559fa1ec2961ed911f3f48f9afb4aef6ca494eafd1b5d440cfab11a53b75b03e` | S2 temporal/query-integrity sidecar | **是**；formal gate 校验（`memory_evolution/formal_protocol.py:95-121,163-168`） |
| `configs/coco_temporal_split_v2/manifest.json` | `65e524e644a58cac83aa5538f58c3bdc58528215640632d7c507cd8e51fefedb` | A/B/C/D split 入口 | **是**；formal gate/readiness |
| `.../historical_experience.jsonl` | `ad02e5e933993564c20ab8607cee59dda62afbdf20c8df577dd67758f37b407f` | Zone A，2391 families | **是**；manifest loader |
| `.../evolution_validation.jsonl` | `0721c39c146a163429df182bcf59bb2511a34afb06a5b6b705f95babd6f53fa5` | Zone B，299 families | **是**；manifest loader |
| `.../effectiveness_holdout.jsonl` | `edd88ae292d69edc692935e09adb0b4b00a84b3625bc26bfeb0c83feb4e58dcf` | Zone C，300 families | gate 读取；specialist search 不取 C |
| `.../held_out_benchmark_test.jsonl` | `a5b91c501b30338296ae40727f75ae086005dacef393ec959515a8f08028e783` | Zone D，899 families | gate 读取；specialist search 不取 D |
| `.../paper_families.jsonl` | `b42c19ca3603c5ab291f5b69d64b9b641280d9c74cf50264ec9dcb76fc229a3a` | 3889 family registry | manifest loader/readiness |
| `REVIEW_Request.md` | `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42` | immutable reviewer identity | **是**；加载 reviewer task 时核验（`memory_evolution/tasks.py:36-45`） |
| `memory_evolution/seeds/vector_search.py` | `45ed77aaef8ac54a2955192ee61a85b66b7f4667f9c34d1bb302ddd2a7353bda` | seed program | **是** |
| `memory_evolution/seeds/llm_summarizer.py` | `0f120fcd98f0d99bcab6709b08f5165b9194646ea46352b2a5ddfaafb505652f` | seed program | **是** |
| `memory_evolution/seeds/experience_learner.py` | `2d8954a65c596721df3e832c5a23ee1d104920eb8a23e19b34783c01971eb5d8` | seed program | **是** |
| `configs/benchmark_reserved_venue_year_v1.json` | `d999696ff9e62d8d29745dc162cb78397194ab8ccfa854ab22a04ccec355268e` | preparation reservation | evolve runtime **否**；文件内无 `/data0` 绝对路径 |
| `configs/asset_manifest_v2.json`（审计时） | `1f088652031d3c55d8d06c2cd0d6d01a7099e8aa30b7e894754aa331f4ca1c75` | formal asset registry | formal **是**，smoke gate **否**；当前未注册 v2、`formal_ready=false` |

结论：**不要重建 S2，不要重建 episode/validation，不要再运行 `prepare-specialists`。** `prepare-specialists` 仍存在于 parser（`memory_evolution/cli.py:348-390`），但它是数据准备器，不是对已冻结 canonical bundle 的必要步骤；入口会调用 `prepare_openreview_data`（`memory_evolution/cli.py:1021-1033`），重新执行反而制造第二套数据与身份风险。

# 3. Runtime Data Dependency

## OpenReview 213GB：NOT NEEDED

在已迁移 `data/evolution_coco_v2_20260819` 的前提下，**不建议传输 `/data0/xyj/shengao/OpenReview` 213GB**。

- `evolve-task` 只按 `--data-root` 打开 task JSONL（`memory_evolution/cli.py:481-554`）。`Episode.from_value` 直接取 JSON 的 `text`（`memory_evolution/models.py:5-19`），evaluator 后续使用 `episode.text`（`memory_evolution/evaluator.py:157-200`）。2391 个 episode 的正文均非空且 inline。
- `paper_md_path` 不用于生成 reviewer 输入；但 formal gate 为重新做 Human Reference leak audit 会读取它。该路径指向 **CoCoReviewBench `benchmark/MD`**，不是 OpenReview（`memory_evolution/formal_protocol.py:270-298`）。
- CoCo adapter 只 import 指定 CoCo checkout 的 evaluator modules，并调用 localhost 服务（`memory_evolution/coco_adapter.py:42-55,88-183`），不碰 OpenReview。
- readiness 复用同一 formal gate，不检查 OpenReview；`asset_manifest_v2.external_roots.openreview` 当前只是声明，因为已登记 asset 的 `path` 都是 project-relative，resolver 只有遇到 `external://` asset path 才会解析 external root（`memory_evolution/assets.py:345-376`）。

按实验类型区分：

| 路径 | OpenReview 需要性 | 原因 |
|---|---|---|
| formal specialist Evolution | **NOT NEEDED** | frozen A/B 正文与任务已 inline |
| canonical smoke | **NOT NEEDED** | 同一 frozen data loader；smoke 仅缩小搜索 |
| effectiveness screening | **视是否已 prepared** | 重新执行 preparation 会读 OpenReview `train_clean.jsonl`/`test.jsonl` 与 Markdown index（`effectiveness_screening/prepare.py:416-481`）；运行既有 prepared artifact 不必然需要 |
| single-paper review | **不需要整个 213GB** | 只需要 CLI 指定的目标 Markdown/目录/bundle（`run_single_paper_review_cli.py:167-285`） |
| final benchmark | **NOT NEEDED** | `prepare-test` 从 CoCo Zone D 构建（`memory_evolution/agentreview_data.py:1227-1422`）；`final-evaluate-suite` 读 prepared test root |
| `prepare-meta` | **需要一个明确源文件** | 当前实现读 `<openreview_root>/train_clean.jsonl`（`memory_evolution/agentreview_data.py:1087-1102`），因此未来 META preparation 需最小必要子集，而非现在搬整库 |
| legacy/pilot/no-CoCo preparation | **可能需要** | 老 preparation 分支使用 OpenReview；不属于当前 frozen formal specialist 流程 |

## CoCoReviewBench 需要的具体内容

| 目录/文件 | specialist evolve runtime | 仅 preparation | final evaluation / service |
|---|---|---|---|
| `.git/HEAD`、refs/packed-refs | **是**；adapter 严格检查 commit | 否 | 否 |
| `src/evaluation/_2_split.py`、`_3_classify.py`、`src/utils.py`、`src/benchmark/prompt_registry.py` | **是**；adapter import（`memory_evolution/coco_adapter.py:42-55`） | 同时可用于 prep | scorer runtime |
| `src/evaluation/_4_evaluation.py`、`_0_human_review.py` | formal gate **按 frozen SHA 打开** | `load_human_review_data` 构建 reference | provenance/final |
| `benchmark/benchmark_{ICLR_2017..2023,NeurIPS_2021..2023}.jsonl` | formal gate **按路径和 SHA 打开** | 构建 human reference | final benchmark source |
| `benchmark/benchmark_left_out_human.jsonl` | **formal gate 必需**，尽管 evolve 不重新 load human review | preparation selection | final provenance |
| `benchmark/MD/**` | **formal gate 必需**；按每条 `paper_md_path` 读文本重做 leak audit | paper content source | single-paper/prepare |
| `model/**` | Evolution Python 进程 **不读取** | 否 | 仅两个 vLLM service job 读取；已实测通过 |

官方 `load_human_review_data` 在数据准备阶段由 `memory_evolution/coco_reference.py:30-135` 包装，并在 `memory_evolution/agentreview_data.py:739-756` 调用。当前 evolve 不重新调用它；evaluator reference 已冻结在 `evaluator_only/.../validation_references.jsonl`，由 `memory_evolution/cli.py:486-533` 按 ID join。

# 4. Path Migration Audit

扫描 frozen evolution bundle 得到 2,725 个唯一 `/data0/...` 绝对路径、76,270 次出现；主要是 25,405 个 `markdown_path`、25,405 个 `internal_provenance.paper_md_path`、25,406 个 per-record temporal manifest provenance，以及 root manifest 的 provenance/任务路径。

| 字段/文件 | 示例旧路径 | evolve runtime 是否访问 | readiness 是否访问 | 是否必须修复 |
|---|---|---|---|---|
| record `metadata.internal_provenance.paper_md_path` | `/data0/xyj/shengao/CoCoReviewBench/benchmark/MD/...md` | smoke：否；**formal gate：`Path.is_file/read_text`**（`formal_protocol.py:270-289`） | **是** | **必须兼容 CoCo 前缀** |
| record `metadata.markdown_path` | 同上 | reviewer/evaluator 不读取；formal gate 读取的是 internal field | 间接由同一 paper source 覆盖 | 不单独改 JSON；前缀兼容即可 |
| data manifest `evaluator_reference_source.implementation_path` | `.../src/evaluation/_4_evaluation.py` | **formal 按 SHA 读取**（`formal_protocol.py:203-230`） | **是** | **必须兼容** |
| data manifest `selection_loader_path` | `.../src/evaluation/_0_human_review.py` | **formal 按 SHA 读取** | **是** | **必须兼容** |
| data manifest `left_out_selection` | `.../benchmark/benchmark_left_out_human.jsonl` | **formal 按 SHA 读取** | **是** | **必须兼容** |
| data manifest `benchmark_file_sha256` keys | 十个旧 CoCo benchmark JSONL | **formal 逐个按 SHA 读取** | **是** | **必须兼容** |
| data manifest task `episodes/validation/queries/evaluator_references` | 旧 AgentReview repo 路径 | 否；loader 根据 CLI `--data-root` 组路径 | gate 也根据传入 data root 组路径 | 仅 metadata，不修复 |
| data manifest `semantic_scholar_cache` | 旧 AgentReview S2 路径 | 只有未传 `--s2-retrieval-pool` 才作 fallback（`cli.py:565-568`） | readiness 使用显式参数 | 完整命令已显式传，不修复 |
| data manifest `temporal_split_manifest` | 旧 AgentReview temporal path | 不作为 CLI manifest 读取 | readiness 使用显式参数 | 仅 provenance，不修复 |
| record per-item temporal manifest path | 旧 AgentReview manifest | 不读取 | 不读取 | 仅 metadata，不修复 |
| temporal v2 manifest `superseded_manifest` | 旧 v1 absolute path（`manifest.json:327`） | 不读取 superseded v1 | 不读取 | 完全不用 |
| temporal v2 zone/family paths | relative paths | manifest loader 会读取 | 会读取 | 已可迁移，无修复 |
| temporal `source_pool` external URI | source provenance | 不读取原库 | 不读取原库 | metadata |
| `benchmark_reserved_venue_year_v1.json` | 无 `/data0` 路径 | 不读取 | 不读取 | 完全不用 |
| `asset_manifest_v2.external_roots` | old OpenReview/CoCo | 仅在 asset path 使用 `external://` 时解析 | 当前 candidate registration 的必需资产均 project-relative | 目前只是声明；不能解决 data-record abs path |

### 路径结论

1. **formal readiness 和 formal evolution 必须让旧路径 `/data0/xyj/shengao/CoCoReviewBench` 可读。** 最小兼容是该前缀指向 `/home/share/u25s151225/CoCoReviewBench` 的 symlink 或 bind mount。一个前缀兼容即可覆盖 MD、src、benchmark。
2. **smoke 不执行 formal gate**，因此旧绝对路径不阻塞 smoke；它仍需通过 `--coco-root` 给 adapter 一个合法 checkout。
3. `--coco-root` 只传给 classifier adapter（`memory_evolution/cli.py:79-102,136-160`），不会 remap frozen data provenance。asset resolver 也不改 record/manifest 内任意字符串。因此 `/data0` 不可写时，当前代码**没有**完整 root remapping；需管理员 bind/symlink，或在未来改 formal gate 加显式 remap。不得修改 frozen JSON/JSONL。
4. 不需要复刻旧 AgentReview repo 路径；完整 CLI 已显式提供 data root、S2 和 temporal manifest。

安全兼容命令（只在确认目标不存在后执行；若权限不足交给管理员）：

```bash
COCOREVIEWBENCH_ROOT=/home/share/u25s151225/CoCoReviewBench
test ! -e /data0/xyj/shengao/CoCoReviewBench
mkdir -p /data0/xyj/shengao
ln -s "$COCOREVIEWBENCH_ROOT" /data0/xyj/shengao/CoCoReviewBench
readlink -f /data0/xyj/shengao/CoCoReviewBench
```

# 5. Runtime Topology

```text
workstation (login only)
    |
    | sbatch/srun: submit and monitor; do not run Evolution here
    v
gpu3 host network
  +-- Slurm Job A, A100 #0
  |     Qwen3-8B-Split (vLLM)
  |     127.0.0.1:13099/v1
  |
  +-- Slurm Job B, A100 #1
  |     Qwen3-8B-Classify (vLLM)
  |     127.0.0.1:13098/v1
  |
  +-- Slurm Job C, CPU-only, CUDA_VISIBLE_DEVICES=""
        AgentReview Evolution parent
          +-- Kimi K3 ----------------------> Volcengine remote API
          +-- BGE-M3 -----------------------> local CPU
          +-- CoCo Split/Classify ----------> gpu3 localhost ports
          +-- bubblewrap MemoryProgram child
                capabilities are proxied by parent
```

推荐 **方案 B：独立 CPU-only job 固定在 gpu3**。Evolution 不需要第三张 GPU：BGE adapter 自动选择 `cuda` 或 `cpu`，CPU 是正确路径而非 fallback（`memory_evolution/embedding_adapter.py:16-33`）。必须显式 `CUDA_VISIBLE_DEVICES=""`，否则 parent 若看见 GPU 会自动抢用它。

方案 A `srun --jobid=<SplitJob> --overlap` 在 Slurm 允许 overlap、CPU/RAM 足够且 host network 未隔离时可用于短 smoke；但会共享 Split allocation 的 CPU/内存/cgroup，资源争用和站点 policy 风险更高，不推荐正式长跑。源码无 Slurm 特判。sandbox 子进程继承所在 allocation/cgroup，并以 `env={}` 清空环境（`memory_evolution/sandbox.py:183-192`）；Kimi/BGE/CoCo tool call 实际由 parent 代理（`memory_evolution/sandbox.py:239-264`），所以 CUDA 隐藏必须施加在 Evolution parent。

BGE 运行语义：

- 新 run 的 static selection 会对 150 validation question embedding；episode subset selection 会对 2391 episode text 和 static items embedding（`memory_evolution/subset_selection.py:53-115,129-188`）。formal resume 已保存 selection IDs 后不会重算（`memory_evolution/search.py:501-553`）。
- Frozen S2 是 related-work lookup，不能代替 validation/episode embedding；formal 模式仍强制 `--embedding-adapter` 和 immutable revision（`memory_evolution/cli.py:638-644`）。
- adapter instance/model 在一个 Python 进程内 lazy load 一次并缓存实例（`memory_evolution/embedding_adapter.py:16-18,61-68`），但没有跨进程向量 cache。
- `vector_search` seed 在写入每个新 500-char chunk 和每次 query 时继续调用 BGE（`memory_evolution/seeds/vector_search.py:52-92`）。因此不能说“每个 candidate 固定调用一次”；实际调用量取决于 lineage/program，vector lineage 的每次 replay 会大量调用。CPU 能跑但可能很慢，是 HIGH 性能风险，不是第三 GPU 的正确性要求。
- smoke 将 episodes 限为 10、static=2、rotating=1、selection repeat=1，调用量远低于 formal；仍走真实 BGE。

# 6. Readiness Gate

`scripts/finalize_formal_readiness.py` 接受任意现存 `--data-root`，明确只 audit/register、不生成数据（`scripts/finalize_formal_readiness.py:145-175`）；`data/evolution_coco_v2_20260819` 可直接使用。

它实际检查：temporal v2 version/comparison/four-zone isolation、3889 条 S2 的 cutoff/query/sidecar/SHA、reviewer/seed/asset hashes、五个 specialist 的 A/B bundle、reference coverage/access、Human Reference leak，以及 frozen CoCo source/benchmark/MD 的 existence/SHA/content boundary（`scripts/finalize_formal_readiness.py:177-266`；`memory_evolution/formal_protocol.py:124-298`）。它**不检查** Kimi live API、OpenReview、BGE 是否能加载、bubblewrap、localhost vLLM、或真正控制服务模型的 revision。

当前必须：

1. 先建立第 4 节 CoCo 旧前缀兼容。
2. 首次运行**注册模式**（无 `--check-only`），它在所有五 task gate 通过后原子更新 `configs/asset_manifest_v2.json`（`finalize_formal_readiness.py:220-269`）。这是注册现存冻结资产，不是重生成数据。
3. 再运行同命令加 `--check-only`。输出必须 `passed=true`、五 specialist `passed=true`、`qual_evolution_may_start=true`（`finalize_formal_readiness.py:270-297`）。

注册命令：

```bash
REPO_ROOT=/home/u25s151225/AgentReview
PY=/home/u25s151225/.conda/envs/env_shengao/bin/python
cd "$REPO_ROOT"
"$PY" scripts/finalize_formal_readiness.py \
  --asset-manifest "$REPO_ROOT/configs/asset_manifest_v2.json" \
  --temporal-split-manifest "$REPO_ROOT/configs/coco_temporal_split_v2/manifest.json" \
  --s2-pool "$REPO_ROOT/configs/frozen_s2_temporal_v2.jsonl" \
  --data-root "$REPO_ROOT/data/evolution_coco_v2_20260819"
```

只读复核命令：

```bash
"$PY" scripts/finalize_formal_readiness.py \
  --asset-manifest "$REPO_ROOT/configs/asset_manifest_v2.json" \
  --temporal-split-manifest "$REPO_ROOT/configs/coco_temporal_split_v2/manifest.json" \
  --s2-pool "$REPO_ROOT/configs/frozen_s2_temporal_v2.jsonl" \
  --data-root "$REPO_ROOT/data/evolution_coco_v2_20260819" \
  --check-only
```

`qual_evolution_may_start` 是 readiness 输出的总结标志；`evolve-task` 真正执行的是 asset manifest `formal_ready` 和 `run_formal_protocol_gate`（`memory_evolution/formal_protocol.py:124-162`；`memory_evolution/cli.py:577-603`）。

# 7. Kimi Preflight

当前存在配置分裂（即原 B1 仍存在）：preflight 默认读 `OPENAI_API_BASE`（`memory_evolution/api_preflight.py:76-90`），实际 OpenAI client 只读 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`（`agentreview/utility/authentication_utils.py:24-34`）。所以两个 base env 必须设置为相同 URL。

```bash
REPO_ROOT=/home/u25s151225/AgentReview
PY=/home/u25s151225/.conda/envs/env_shengao/bin/python
cd "$REPO_ROOT"
export OPENAI_API_BASE='https://ark.cn-beijing.volces.com/api/plan/v3'
export OPENAI_BASE_URL="$OPENAI_API_BASE"
read -rsp 'OPENAI_API_KEY: ' OPENAI_API_KEY; export OPENAI_API_KEY; echo
"$PY" -m memory_evolution.api_preflight --base-url "$OPENAI_API_BASE"
```

PASS：HTTP 2xx，输出 `[API PREFLIGHT] HTTP=200`。该 preflight 只 GET `<base>/models`（`api_preflight.py:27-73`），验证 DNS/TLS/auth/routing，不等价于一次 `kimi-k3` chat completion。

Parser 接受 `reasoning_effort=low`、`max_tokens=8192`、`retry_max_tokens=16384`（`memory_evolution/cli.py:304-310`）。但 backend 仅对模型名以 `gpt-5/o1/o3/o4` 开头的模型发送 `reasoning_effort`；`kimi-k3` 走普通 `max_tokens` 分支，因此 `low` 合法且会进入 config/provenance，但不会发给 provider（`agentreview/backends/openai.py:182-216`）。16384 只在 HTTP 成功但空输出/长度耗尽重试时作为上限，网络/限流重试另有最多 3 次（`agentreview/backends/openai.py:268-282,397-449`）。

# 8. QUAL Smoke

先决条件：Evolution shell 在 gpu3；两个 curl、CoCo pinned adapter checkout、bubblewrap、BGE CPU probe、Kimi preflight 均 PASS。`--smoke` 是 nonformal：跳过 formal gate并允许 fallback；为保证此次 smoke 有意义，命令仍显式传真实 CoCo/BGE，日志中不得出现 fallback。

CoCo service checkout 可以保留当前 vLLM 脚本提交；若云端 HEAD 不是固定 commit，创建一个**普通 clone**作为 adapter code root（不能用 `git worktree`，因为 adapter 假定 `.git` 是目录）：

```bash
COCOREVIEWBENCH_ROOT=/home/share/u25s151225/CoCoReviewBench
COCO_ADAPTER_ROOT=/home/u25s151225/CoCoReviewBench_adapter_2580687
test ! -e "$COCO_ADAPTER_ROOT"
git clone --shared --no-checkout "$COCOREVIEWBENCH_ROOT" "$COCO_ADAPTER_ROOT"
git -C "$COCO_ADAPTER_ROOT" checkout --detach 2580687ee5d4ad2ac4b9730afb8f1dfda22e40db
git -C "$COCO_ADAPTER_ROOT" rev-parse HEAD
```

若原 root 本身已是 `2580687...`，直接 `COCO_ADAPTER_ROOT="$COCOREVIEWBENCH_ROOT"`，不创建 clone。

完整 canonical smoke 命令：

```bash
REPO_ROOT=/home/u25s151225/AgentReview
COCOREVIEWBENCH_ROOT=/home/share/u25s151225/CoCoReviewBench
COCO_ADAPTER_ROOT=/home/u25s151225/CoCoReviewBench_adapter_2580687
PY=/home/u25s151225/.conda/envs/env_shengao/bin/python
BGE_REVISION=5617a9f61b028005a4858fdac845db406aefb181
SMOKE_OUTPUT="$REPO_ROOT/runs/cloud_qual_smoke_20260820"
cd "$REPO_ROOT"
test ! -e "$SMOKE_OUTPUT"
CUDA_VISIBLE_DEVICES="" "$PY" -m memory_evolution.cli evolve-task \
  --task-id QUAL \
  --data-root "$REPO_ROOT/data/evolution_coco_v2_20260819" \
  --output "$SMOKE_OUTPUT" \
  --smoke \
  --iterations 21 \
  --static-size 25 \
  --rotating-size 5 \
  --temperature 0.15 \
  --episode-budget 125 \
  --seed 0 \
  --selection-repeats 3 \
  --selection-top-k 3 \
  --max-fix-attempts 3 \
  --parent-policy softmax \
  --mutation-scope full \
  --task-model-name kimi-k3 \
  --reflector-model-name kimi-k3 \
  --judge-model-name kimi-k3 \
  --reasoning-effort low \
  --max-tokens 8192 \
  --retry-max-tokens 16384 \
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter \
  --review-split-revision e405b49e472d1c4ddbf226eae7aded032a2d5c8a \
  --review-classify-revision 8607b97b893bf64be0de848609863093c7ecc095 \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-model-name BAAI/bge-m3 \
  --embedding-revision "$BGE_REVISION" \
  --s2-retrieval-pool "$REPO_ROOT/configs/frozen_s2_temporal_v2.jsonl" \
  --asset-manifest "$REPO_ROOT/configs/asset_manifest_v2.json" \
  --temporal-split-manifest "$REPO_ROOT/configs/coco_temporal_split_v2/manifest.json" \
  --coco-root "$COCO_ADAPTER_ROOT"
```

`BGE_REVISION` 是 immutable full commit；云端应先用第 12 节 CPU probe 验证该 revision 可加载。该固定值可从 [BAAI/bge-m3 官方 Hugging Face 提交历史](https://huggingface.co/BAAI/bge-m3/commit/5617a9f61b028005a4858fdac845db406aefb181)追溯。

### `--smoke` 的精确语义与 PASS 标准

源码把实际参数改成 iterations=1、static=min(25,2)=2、rotating=min(5,1)=1、episode budget=min(125,10)=10、selection repeats=1、top-k=1（`memory_evolution/cli.py:659-667`）。它仍调用真实 Kimi、MemoryProgram sandbox、BGE、ReviewSplit、ReviewClassify、frozen CoCo reference、以及一次 mutation attempt；但不跑 readiness/formal gate。

PASS 必须同时满足：

1. 进程 exit 0；真实 mutation attempt 完成；生成 `best_program.py`、`best_eval_report.json`、`scores.jsonl`、`selection.json` 等结果。
2. 日志确认 `kimi-k3`、BGE adapter、CoCo adapter 被使用；两个 localhost service 有请求；无 random/heuristic/fallback、无 sandbox/API/adapter error。
3. 选择数据量显示 static=2、selected episodes=10、rotating=1、repeat=1。
4. output dir 与 formal 完全分离。smoke 没有 formal controller 的严格 resume/残留保护；复用目录可能改写 nonformal artifacts，因此每次用新的独立目录，绝不作为 promoted suite 输入。

# 9. QUAL Formal Seed0

以下参数名全部由当前 parser 支持（`memory_evolution/cli.py:235-339,412-417`），数值与 formal 默认/约束一致；21 可被三个 seed lineages 整除（`memory_evolution/cli.py:569-574`）。Split/Classify revision 参数只是 nonempty provenance/config identity，真正请求仍使用固定 model 名和 localhost endpoint（`memory_evolution/coco_adapter.py:58-81,88-183`），不会切换服务 checkpoint。

**HOLD：在 readiness 全 PASS、旧 CoCo 路径兼容、CoCo commit 对齐、smoke PASS，且 D-3 attempt cap 修复并重新审计前，不执行。**

```bash
REPO_ROOT=/home/u25s151225/AgentReview
COCOREVIEWBENCH_ROOT=/home/share/u25s151225/CoCoReviewBench
COCO_ADAPTER_ROOT=/home/u25s151225/CoCoReviewBench_adapter_2580687
PY=/home/u25s151225/.conda/envs/env_shengao/bin/python
BGE_REVISION=5617a9f61b028005a4858fdac845db406aefb181
FORMAL_OUTPUT="$REPO_ROOT/runs/formal_qual_seed0_20260820"
cd "$REPO_ROOT"
CUDA_VISIBLE_DEVICES="" "$PY" -m memory_evolution.cli evolve-task \
  --task-id QUAL \
  --data-root "$REPO_ROOT/data/evolution_coco_v2_20260819" \
  --output "$FORMAL_OUTPUT" \
  --iterations 21 \
  --static-size 25 \
  --rotating-size 5 \
  --temperature 0.15 \
  --episode-budget 125 \
  --seed 0 \
  --selection-repeats 3 \
  --selection-top-k 3 \
  --max-fix-attempts 3 \
  --parent-policy softmax \
  --mutation-scope full \
  --task-model-name kimi-k3 \
  --reflector-model-name kimi-k3 \
  --judge-model-name kimi-k3 \
  --reasoning-effort low \
  --max-tokens 8192 \
  --retry-max-tokens 16384 \
  --classifier-adapter memory_evolution.coco_adapter:create_frozen_coco_adapter \
  --review-split-revision e405b49e472d1c4ddbf226eae7aded032a2d5c8a \
  --review-classify-revision 8607b97b893bf64be0de848609863093c7ecc095 \
  --embedding-adapter memory_evolution.embedding_adapter:create_bge_m3 \
  --embedding-model-name BAAI/bge-m3 \
  --embedding-revision "$BGE_REVISION" \
  --s2-retrieval-pool "$REPO_ROOT/configs/frozen_s2_temporal_v2.jsonl" \
  --asset-manifest "$REPO_ROOT/configs/asset_manifest_v2.json" \
  --temporal-split-manifest "$REPO_ROOT/configs/coco_temporal_split_v2/manifest.json" \
  --coco-root "$COCO_ADAPTER_ROOT"
```

五 specialist 与 META 边界：`evolve-task QUAL` 不依赖 META。`evolve-specialists` 可先生成只含五 specialist 的 `suite_manifest.json`，并明确记录 `meta_status="run prepare-meta after freezing specialist winners"`（`memory_evolution/cli.py:1071-1136`）。因此“尚无正式 promoted suite”（B2）正应由本轮 specialist evolution/promotion 解决，不阻塞 QUAL。之后 `prepare-meta/evolve-meta` 才建立 META；完整 `final-evaluate-suite` 要求 META 并会在缺失时失败（`memory_evolution/cli.py:751-1007`），但这不阻塞 specialist-only suite 的产生。

# 10. Resume Procedure

正式 run 使用 `search_controller_checkpoint.json`。controller identity 是完整 `EvolutionConfig`（包含 output path、模型、revision、adapter、formal/search/timeouts 等）、三个 seed program SHA、按顺序的 episode IDs 与 validation IDs 的 canonical hash（`memory_evolution/search.py:363-379`）。API key 和环境变量不在 identity。

Kimi key 中途失效后的操作：

```bash
cd /home/u25s151225/AgentReview
export OPENAI_API_BASE='https://ark.cn-beijing.volces.com/api/plan/v3'
export OPENAI_BASE_URL="$OPENAI_API_BASE"
read -rsp 'NEW OPENAI_API_KEY: ' OPENAI_API_KEY; export OPENAI_API_KEY; echo
/home/u25s151225/.conda/envs/env_shengao/bin/python -m memory_evolution.api_preflight \
  --base-url "$OPENAI_API_BASE"
# 然后原样重跑第 9 节命令；FORMAL_OUTPUT 和所有 CLI 参数必须完全相同。
```

- 相同 identity 会恢复 RNG、candidate、lineage budget、subset IDs、scores 行数、selection repeats（`memory_evolution/search.py:380-441,501-586,1107-1164`）。completed candidates/selection repeats 不会重算。
- historical replay checkpoint 要求 completed IDs 是 frozen order 的严格前缀，且核验 candidate/program/config/S2 identity（`memory_evolution/checkpoint.py:13-50,136-170`）。已完成 episode 不重复；相邻未提交 crash record 可丢弃后从该项继续。
- 已完成 run 会直接返回保存结果（`memory_evolution/search.py:588-589`）。
- 参数、output path、revision 或 seed 文件变更会报 identity mismatch，不会静默覆盖（`search.py:381-404`）。无 controller 但已有正式残留也 fail closed。
- 更换 API key 可安全 resume；更换 `OPENAI_BASE_URL` 也不会触发 identity mismatch，这是 provenance 风险，正式 run 不应在未记录的情况下换 provider/base URL。
- smoke 是 nonformal，没有上述强一致 controller 语义；失败后应换新 smoke output，而非把它当正式 resume。

# 11. Remaining Risks

## BLOCKER

1. **CoCo strict commit。** 固定 `2580687...`，当前审计 checkout 为 `8b606...`；见第 1、8 节。云端若同样不匹配，canonical real-CoCo smoke 初始化即失败。
2. **formal path compatibility。** `formal_protocol.py:209-230,270-289` 真正读取 frozen data 写入的旧 CoCo绝对路径。无 symlink/bind/remap 时 readiness 与 formal seed0 失败；不阻塞 `--smoke`。
3. **formal asset registration。** 当前 asset manifest 仍 v1 entries/`formal_ready=false`；formal gate 在 `memory_evolution/formal_protocol.py:134-142` fail closed。先注册再 check-only；不阻塞 `--smoke`。
4. **D-3 formal 无限 retry。** formal 搜索在 `while True` 中只以每 lineage 的成功数到标退出（`memory_evolution/search.py:809-836`）。reflector exception 记录后 `continue`（`search.py:920-959`）；quality failure 也不消费 successful budget。没有 outer max attempts/failure budget。`max_fix_attempts=3` 只限制 compile-fix（`search.py:967-1004`）。最坏会无限调用 Kimi、CoCo、BGE，持续增长日志/artifact/费用，永远达不到 7×3 个成功 child。**不阻塞 smoke**，因为 nonformal 在 `attempt >= iterations` 时退出；**阻塞正式 seed0 开跑**，建议长跑前增加并审计总 attempt/failure cap。本次只审计，未修改。

## HIGH

1. **BGE CPU 性能/内存。** correctness 可行，但 2391 episode 初筛及 vector lineage 的反复 chunk/query embedding 可能非常慢；无跨 candidate/cache。先用 smoke 实测 wall time/RSS。
2. **localhost/Slurm placement。** endpoint hardcoded `localhost`，Evolution 必须在 gpu3 host network；workstation 无法直连，除非人为 SSH tunnel 令 workstation localhost 映射到 gpu3，而当前 CLI 没有 `--split-url/--classify-url`。
3. **bubblewrap namespace。** 二进制存在不等于集群允许 `--unshare-all`；不通过会同时阻塞 smoke/formal。
4. **CoCo revision 参数不控制 live model。** 两个 revision 只要求非空并记入身份；adapter 始终向固定 model names 请求。服务的真实 checkpoint/model revision 仍由 vLLM job 决定，readiness 不验证。

## MEDIUM

1. `reasoning_effort=low` 对 `kimi-k3` 不下发，只留在配置；这不导致失败，但实际 provider 推理强度不受该参数控制。
2. api preflight 只验证 `/models`，不验证 `kimi-k3` chat；smoke 才是端到端验证。
3. CPU-only Job C 是否可与两个 GPU jobs 同节点共存、可分配多少 RAM/CPU、network namespace 是否共享，属于 Slurm deployment concern，源码不保证。
4. `asset_manifest_v2.external_roots` 保留旧路径声明容易误读，但当前不是 record remapper，也不是 OpenReview runtime dependency。

## LOW / 后续问题

1. `tests/test_cli_modes.py` 仍断言默认模型为 `gpt-5-mini`，而 parser 当前默认已是 `kimi-k3`；本次选跑 28 tests 中 27 PASS、该 1 项失败。这是陈旧测试，不阻塞显式参数运行。
2. META 尚未正式 evolution，不阻塞 QUAL/五 specialist；仅阻塞完整 final suite。
3. 当前 CoCo extra commit 只改 vLLM 0.11.1 服务脚本而 adapter 仍按整仓 HEAD 拒绝，属于身份策略过严；短期 pinned clone，长期可在代码层改为 evaluator 文件 hash identity，需另行变更审查。

## Human review 数据边界（PASS）

- Candidate-visible episodes/validation/queries 均无 human `answer`；episodes 的 `text` 非空，metadata 标记 `contains_human_reference=false`。formal preparation 会在 `_training_episode` 删除 reference（`memory_evolution/agentreview_data.py:588-595`），并执行 leak audit（`agentreview_data.py:381-508,848-950`）。
- Human reference 只在 `evaluator_only` artifact；CLI 要求 `evaluator_only=true`、`candidate_forbidden=true` 后才 join 到 `EvalItem.answer`（`memory_evolution/cli.py:486-533`）。实际非空 answer 数为 QUAL 150、CLAR 149、SIGN 134、ORIG 147、POL 53；空值反映该 dimension 无 opinion，不是缺文件。
- formal gate 重新验证 reference coverage/access 和 leak boundary（`memory_evolution/formal_protocol.py:191-202,232-298`）。
- `benchmark_left_out_human`/annotations 是 evaluator/reference construction 与 provenance，未进入 MemoryProgram prompt。当前 specialist 主实验不读 META。

# 12. Exact Next Actions

按“现在这一刻”排序，最多 8 步；不重复模型安装、CUDA、vLLM 或 shard 工作。

1. **在 cloud root 做一个最小身份/文件检查。下一条应执行的命令就是这一组：**

   ```bash
   REPO_ROOT=/home/u25s151225/AgentReview
   COCOREVIEWBENCH_ROOT=/home/share/u25s151225/CoCoReviewBench
   git -C "$COCOREVIEWBENCH_ROOT" rev-parse HEAD
   sha256sum "$REPO_ROOT/configs/frozen_s2_temporal_v2.jsonl" \
     "$REPO_ROOT/configs/coco_temporal_split_v2/manifest.json" \
     "$REPO_ROOT/data/evolution_coco_v2_20260819/manifest.json"
   ```

   期望依次为 `2580687...`（若为 `8b606...` 则走第 8 节 pinned clone）、`367cdff...`、`65e524...`、`b484499...`。

2. **闭合 smoke 的三个 node-local 检查：**在将运行 Evolution 的 gpu3 shell 内执行：

   ```bash
   curl -fsS http://127.0.0.1:13099/v1/models >/dev/null
   curl -fsS http://127.0.0.1:13098/v1/models >/dev/null
   command -v bwrap
   bwrap --unshare-all --ro-bind / / -- /bin/true
   CUDA_VISIBLE_DEVICES="" HF_HUB_OFFLINE=1 \
     /home/u25s151225/.conda/envs/env_shengao/bin/python -c \
     'from memory_evolution.embedding_adapter import create_bge_m3; x=create_bge_m3("BAAI/bge-m3","5617a9f61b028005a4858fdac845db406aefb181").embed(["probe"]); assert len(x)==1 and len(x[0])>0; print(len(x[0]))'
   ```

   若 BGE offline probe 失败，先把该**模型 revision**缓存到 AgentReview 环境；无需重建任何 frozen data。

3. **若 CoCo HEAD 不匹配，建立第 8 节独立 pinned adapter clone；同时为 formal 建立第 4 节旧 CoCo 前缀 symlink/bind。** 若 `/data0` 不可写，停止 readiness，申请管理员挂载；当前代码无 remap。不要改 frozen JSON/JSONL。

4. **运行第 6 节 readiness 注册命令，然后立即运行 `--check-only` 命令。** 两次都必须五 specialist PASS。该步只注册现存资产，不执行 `prepare-specialists`。

5. **以 CPU-only job 固定 gpu3 启动 Evolution shell。** 若站点暂时不能独立 co-schedule，短 smoke 可进入现有 Split allocation：

   ```bash
   srun --jobid="$SPLIT_JOB_ID" --overlap --pty env CUDA_VISIBLE_DEVICES="" bash -l
   ```

   正式长跑仍推荐单独 CPU/RAM allocation；具体 partition/CPU/RAM 参数由集群 policy 决定，不是源码参数。

6. **运行第 7 节 Kimi preflight，现场输入 key。** 确认 `OPENAI_API_BASE` 与 `OPENAI_BASE_URL` 相等。

7. **运行第 8 节完整 QUAL smoke。** 仅按第 8 节 PASS 标准验收，保存独立 output；不要把 smoke artifact promotion。

8. **先修复并复审 D-3 attempt cap，再解除第 9 节 HOLD 并执行原样 formal seed0 命令。** 在此之前不要启动 21-iteration 计费长跑。

---

## 附录 A：旧执行稿过时项

| OLD STEP | CURRENT STATUS | WHY | REPLACEMENT |
|---|---|---|---|
| Frozen S2 v2 后运行 `prepare-specialists` | **删除** | canonical A/B 五 task bundle 已冻结完整；重跑会读源数据并生成新 identity | 迁移 `data/evolution_coco_v2_20260819`，核 SHA |
| 输出到 `data/formal_temporal_v2` | **淘汰** | 当前目录不存在，当前 loader 不要求该名字 | `--data-root "$REPO_ROOT/data/evolution_coco_v2_20260819"` |
| `QUAL_test`/pilot data 作为 formal root | **淘汰** | 不满足当前完整 formal manifest/evaluator-only gate | 使用 evolution v2 root |
| 先传 OpenReview 213GB | **删除** | frozen specialist runtime 不读取 OpenReview | 不传；未来 `prepare-meta` 只准备所需源子集 |
| 只加 `--coco-root` 即解决迁移路径 | **错误** | 该参数只配置 adapter；formal gate 直接解引用 frozen abs paths | `/data0/.../CoCoReviewBench` symlink/bind；或未来代码 remap |
| readiness 直接 `--check-only` | **首次不够** | 当前 asset manifest 仍 `formal_ready=false`；check-only 不写注册结果 | 先注册模式一次，再 check-only |
| Evolution 可在 workstation 请求 gpu3 hostname | **当前不支持** | endpoints hardcoded localhost，无 URL CLI/env | Evolution 固定在 gpu3；或未来新增 endpoint config |
| Evolution 必须第三张 GPU | **错误** | BGE 自动 CPU，Kimi remote，CoCo GPU 在服务 jobs | CPU-only Evolution job + `CUDA_VISIBLE_DEVICES=""` |
| `--review-*-revision` 控制 vLLM 模型 | **错误** | adapter 只校验非空/记录 provenance，请求固定 model 名 | 用 vLLM job 自身配置保证真实 checkpoint；参数保留 immutable provenance |
| smoke 可复用 formal output | **禁止** | smoke nonformal 且缩参，不能成为 promotion artifact | 独立 `runs/cloud_qual_smoke_20260820` |
| formal 失败总会在 21 attempts 后结束 | **错误** | formal 以 successful lineage budget 截止，失败不消费 budget，无 attempt cap | D-3 修复后再长跑 |

## 附录 B：本地验证命令与结果摘要

本次审计执行的代表性只读命令：

```bash
git status --short
git log -1 --format='%H %cs %s'
du -sh data/evolution_coco_v2_20260819
find data/evolution_coco_v2_20260819 -type f
sha256sum configs/frozen_s2_temporal_v2.jsonl \
  configs/frozen_s2_temporal_v2.jsonl.manifest.json \
  configs/coco_temporal_split_v2/manifest.json \
  data/evolution_coco_v2_20260819/manifest.json
rg -n '/data0/xyj/shengao' configs data/evolution_coco_v2_20260819
git -C /data0/xyj/shengao/CoCoReviewBench rev-parse HEAD
git -C /data0/xyj/shengao/CoCoReviewBench diff --quiet \
  2580687ee5d4ad2ac4b9730afb8f1dfda22e40db..HEAD -- src benchmark_left_out
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests.test_cli_modes tests.test_protocol_boundaries tests.test_search_loop
```

结果：AgentReview 起始 worktree clean；数据根 1.5 GB；冻结 hash 与第 2 节一致；CoCo evaluator/benchmark 内容相对固定 commit 未变但 HEAD 身份不等；上述定向 13 tests 中 12 PASS，1 个陈旧默认模型断言失败。审计结束前再次核验 worktree，只允许新增本报告。
