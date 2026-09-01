# AgentReview 0831 实验修改记录（2026-09-01）

本文件是本轮 `0831` 仓库 GPU 实验的唯一修改记录。记录运行配置、生成资产、故障、诊断、恢复和代码修改。任何后续变更继续追加到此文件，不另建修改日志。

## 基线身份

- Repository: `/data/xyj/shengao/AgentReview_0831`
- Branch: `20260831-minimal`
- Commit: `f4fa1b5b454363cd2332070482369650354231c7`
- 初始工作树：clean
- Generation model: `/data/xyj/models/Qwen3.8-27B`
- Generation revision: `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`
- Embedding model: `Qwen/Qwen3-Embedding-8B`
- Embedding revision: `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af`
- vLLM: `0.27.1`
- CoCo code root: `/data/xyj/shengao/CoCoReviewBench_adapter_2580687`
- CoCo code commit: `2580687ee5d4ad2ac4b9730afb8f1dfda22e40db`
- ReviewSplit revision: `e405b49e472d1c4ddbf226eae7aded032a2d5c8a`
- ReviewClassify revision: `8607b97b893bf64be0de848609863093c7ecc095`

## 2026-08-31：服务与数据准备

1. 使用 GPU0-3 启动 Qwen3.8-27B BF16：TP=4、`max-model-len=32768`、`max-num-seqs=4`、port 8000。
2. Qwen 首次服务启动在图编译阶段失败：运行环境 `PATH` 未包含 `ninja`。失败日志：`runs/0831_e2e_smoke_tp4_seq4_runtime/qwen_tp4_seq4.log`。
3. 保留全部模型参数，仅补齐 `/data/xyj/envs/qwen38serve/bin` 到 `PATH` 后重启成功。日志：`runs/0831_e2e_smoke_tp4_seq4_runtime/qwen_tp4_seq4_r2.log`。
4. 使用 GPU4/5 分别启动 CoCo ReviewSplit/ReviewClassify，ports 13099/13098。
5. Qwen 4-worker 并发预检通过：4/4 返回 `stop` 和非空内容，wall time 约 0.95 秒。因此 smoke 保留 `max-num-seqs=4`，未降到 2。
6. 生成 `data/0831_e2e_smoke_tp4_seq4`：10 个 A family、5 个 Specialist B family、1 个 META B family；Frozen S2 16/16 命中；human-reference leakage count=0。
7. `prepare-meta` 仍依赖已清理掉的 OpenReview `train_clean.jsonl`。未修改源码；在 `runs/0831_e2e_smoke_tp4_seq4_runtime/meta_source` 生成仅含 11 个本轮 paper 的 CoCo compatibility projection，并在其 `manifest.json` 记录来源 SHA256。候选 Markdown 继续指向 pinned CoCo 根。
8. 新增 run-local launcher：`runs/0831_e2e_smoke_tp4_seq4_runtime/launch_e2e_smoke.sh`。该文件不是核心算法代码，仅固定本轮命令与环境。
9. launcher 首次运行因在 Conda activation 前启用 `set -u` 而退出；没有创建 Evolution run。随后改为先激活 `env_shengao`、再激活 `qwen38serve`，最后启用 `set -u`。

## 2026-08-31：失败 run `0831_e2e_smoke_tp4_seq4`

- 配置：Specialist smoke，static=1、rotating=1、episode budget=2、iteration=1；selection 与 runtime embedding 都指定 GPU6。
- 结果：selection embedding 成功加载并占用 GPU6 约 24.05 GiB；Vector Search quality smoke 的 runtime embedding 第二实例加载失败，错误为 `Qwen3-Embedding-8B has insufficient memory on cuda:0`。
- 在基础设施失败污染更多 seed 前，采集 process/GPU/log/hash 后仅对实验进程发送 SIGINT；Qwen/CoCo 服务保持运行。
- 失败 run 保留在 `runs/0831_e2e_smoke_tp4_seq4`，未覆盖、未恢复为有效结果。

## 2026-08-31 至 2026-09-01：诊断性 CPU run

- Run: `runs/0831_e2e_smoke_tp4_seq4_cpuemb`
- 目的：验证失败是否由 GPU6 双实例并存导致。
- 变化：embedding device 改为 CPU，sandbox memory 记录为 32768 MiB；Qwen/CoCo 模型与 MSTAR/fitness/data 参数不变。
- 观察：Qwen3-Embedding-8B CPU 单实例成功加载并完成首批 5 条缓存；10 条长文本 CPU embedding 吞吐过低，持续占用约 54-56 CPU cores。
- 在 `scores.jsonl`、`lineage.jsonl`、`candidate_reviews.jsonl` 均为 0 行时安全停止。该 run 仅为诊断证据，不作为 smoke 结果。

## 2026-09-01：确认的根因与恢复方案

- 确认根因：selection 与 candidate-runtime 使用两个独立 Qwen3-Embedding-8B client；二者同时首次 miss 时会在 GPU6 加载两个模型实例。单实例可部署在 GPU6，双实例加运行缓冲不能同时容纳。
- 恢复方案：先用 GPU6 独立预热完整 selection cache；预热进程退出并释放 GPU6。随后新 smoke 使用该只读语义的 selection cache，运行期 GPU6 只加载一个 candidate-runtime embedding 实例。
- 最小红/绿信号：Vector Search seed 的 quality smoke 触发一个 runtime embedding miss；失败 run 在此处报显存不足，恢复 run 必须完成该 miss 并进入 static evaluation。
- 核心算法状态：截至本节，没有修改 MSTAR parent selection、fitness、fresh-state replay、数据分区或 CoCo metric 逻辑。

## 后续追加区

### 2026-09-01 00:16：GPU6 selection 预热

- 使用仓库入口 `scripts/prewarm_embedding_cache.py`，在物理 GPU6 对五个 Specialist 的全部 75 条 selection 文本预热。
- 结果：75/75 committed；4096 维；最大预截断 token count=4365；truncated=0；ordered matrix SHA256=`e0a17d1e6ab15ed16661b239058f0da307bb11ea941c349aed37084cdaa44224`。
- Cache: `runs/0831_e2e_smoke_tp4_seq4_gpu6_runtime/selection_embedding.sqlite3`。
- Audit: `runs/0831_e2e_smoke_tp4_seq4_gpu6_runtime/selection_embedding.audit.json`。
- 预热结束后 GPU6 回收为空闲，不常驻 selection 模型。
- Run-local launcher 增加 META 数据生成后的独立 GPU6 预热步骤。该步骤复用同一个 selection cache，在 META Evolution 前退出并释放模型，避免 META selection/runtime 双实例冲突。
- 源码修改：无。MSTAR、fitness、fresh-state、CoCo 与数据语义修改：无。

### 2026-09-01 00:18：启动 GPU6 恢复 smoke

- Run: `runs/0831_e2e_smoke_tp4_seq4_gpu6`；runtime: `runs/0831_e2e_smoke_tp4_seq4_gpu6_runtime`；tmux: `ar0831_e2e_gpu6`。
- Selection validation/episode 查询分别为 5/5 与 10/10 cache hit，因此 selection model 未在运行进程中加载。
- Vector Search quality smoke 的 runtime embedding 首次 miss 已完成，单个 Qwen3-Embedding-8B 实例驻留 GPU6 约 15.5 GiB，并成功进入 `static evaluation started`。这满足本轮显存故障的最小红/绿验证信号。
- 进入静态评分后，Frozen CoCo ReviewSplit 的 0/0.5/1 三档请求均返回 `finish_reason=stop`；ReviewClassify 正按每个拆分 opinion 执行三档投票。00:36 时已启动 54 次 classify 请求，尚未写入首条 `scores.jsonl`，故此处只记录“已越过故障点”，不宣称 smoke 已通过。
- 观察到前两次 `Memory Response` 在 4096 tokens 截断后用 8192 tokens 重试。当前 smoke 保持原配置不热改；后续独立全流程 run 将以 8192 为初始上限，避免重复生成开销。
- 源码修改：无。当前仅新增 run/data 资产、run-local launcher 与本统一记录文件。

### 2026-09-01 00:39-00:48：首条完整评分与 CoCo 有界并发修复

- 首条完整链路结果已落盘：`QUAL/seed_0/program_000_seed_vector_search`，`static_score=0.8636363636363636`、`quality_passed=true`、`completed_evaluation=true`、`traces=3`、`failed_cases=0`。这确认 Evolution、GPU6 runtime embedding、Qwen review generation、Frozen CoCo Split/Classify、fitness 及 score/lineage 持久化均已打通。
- 首条 review 被 Split 为 23 个 opinion，Classify 共执行 69 个串行请求，用时使单候选总 wall time 超过 21 分钟。继续原配置将令 smoke 耗时不可接受，因此在第二个 seed 尚未提交任何结果时停止该实验进程；Qwen/CoCo 服务未停止。
- 停止前证据 SHA256：`scores.jsonl=06a8833ceffb66d301c838e8c6e241130ac20c29db16795a108e860a5631546a`；`lineage.jsonl=296eb261e3618cd04ae6b83c1951361fc49ce7037fa504adf32656218a59f755`；`candidate_reviews.jsonl=0fbc637518da6b23f2c914d4bdcd4901d00b8a939772beca1c94ed9da8310756`；`traces/seed_000_static.jsonl=eadea824e3ccf6527048641a9b854994a313c2051bb4267bd786bd050cb952a7`。
- 执行层修改：`memory_evolution/coco_adapter.py` 新增 `COCO_CLASSIFY_WORKERS`（默认 4），按 opinion 使用有界线程池；每个 opinion 仍执行 0/0.5/1 三档请求，投票规则不变；`executor.map` 保持最终输出顺序与输入一致。文件 SHA256=`438d85b2c8bcd2daeb9b4ce9118b13d2b4b1436438c23e88c6f6f7888de5d080`。
- 测试修改：`tests/test_coco_adapter.py` 增加并发上限、请求数和输出顺序测试。`python -m unittest tests.test_coco_adapter -v`：6/6 passed。文件 SHA256=`0dee7a5088601db83f3a0d2dddc8746a3fa6b511933080fb65fb08518f9296fb`。
- 在线 4-worker 检查：4 个短 opinion 的 12 个三档请求全部 `finish_reason=stop`，55.974 秒完成，输出标签数与顺序正确；证明当前 GPU5 ReviewClassify 服务可承载 4 个并发请求。
- Launcher 显式导出 `COCO_CLASSIFY_WORKERS=4`，并把 Qwen 初始/重试输出上限改为 8192/16384，避免已观察到的 4096 截断后整次重算。launcher SHA256=`a0662ad4b87f17911e5bbaa4ee719887288523934493958382328679d407a301`。
- MSTAR selection、fitness 公式、三档 CoCo 投票、fresh-state replay、数据分区及模型 checkpoint/revision 均未改变。

### 2026-09-01 00:49-02:17：并发 smoke 与全流程自动门控

- 新 smoke: `runs/0831_e2e_smoke_tp4_seq4_gpu6_coco4`；tmux=`ar0831_e2e_gpu6_coco4`；Qwen TP4 `max-num-seqs=4`；CoCo Classify workers=4；Qwen output limits=8192/16384。
- 新 runtime 通过 reflink 复制已验证的 selection/runtime 内容寻址缓存；复制前后 SHA256 分别一致：selection=`e29311939433009428732aacb068893a2aaea17730a83821c50437e5633453dd`，runtime=`96e2113db4c6bf975de051e5c7314e0e470c851f288fafb798be64150eeb9005`。
- QUAL 三个 mandatory seed score=`0.8636363636, 0.8636363636, 0.8095238095`，child score=`0.8636363636`；每个已提交 evaluation 均为 3 traces、0 failed case。冠军为 `program_000_seed_vector_search`，QUAL 已完成并切换 CLAR。
- 并发一致性对照：首个 Vector Search seed 的 score 与串行 run 同为 `0.8636363636`；三档请求总数仍为 69，说明有界并发未减少 CoCo 投票调用。
- 一次 `Memory Harness` 在 8192 tokens 返回空正文，自动以 16384 重试后成功：总 elapsed=449.8 秒、completion_tokens=14104、`finish_reason=stop`。运行未丢弃已提交 score。
- 单个 QUAL smoke 的实测 wall time 约 83 分钟，因此完整 smoke 为数小时任务。新增独立 pilot launcher `runs/0831_e2e_smoke_tp4_seq4_runtime/launch_e2e_pilot_after_smoke.sh`，SHA256=`84298be1448572abd6a4410d97779b26d04d37c93fc549abcbd6f56bc4abe82c`。
- Pilot 是独立、非报告性小规模全流程：run tag=`0831_e2e_pilot_tp4_seq4_gpu6_coco4`；seed=0；iterations=2；static=2；rotating=2；episode budget=4；selection repeats/top-k=2/2；五 Specialist、META input cache、META prepare、META selection prewarm、META evolution、pilot report 全部包含。使用独立 data/run/runtime roots，不覆盖 smoke。
- 新增成功门控 `gate_smoke_then_pilot.sh`，SHA256=`d68bd30ee0337c055297828eb08ac4758fa0b64c4d75717a03348efd6f5a9224`，tmux=`ar0831_pilot_gate`。它只在 smoke session 结束、日志精确包含 `[E2E] COMPLETE` 且 8000/13099/13098 三个服务健康时启动 pilot；否则以错误退出，禁止把失败 smoke 后的 pilot 误标为已启动。
- 两个 launcher 均通过 `bash -n`；当前 smoke 继续运行，门控仅等待，不占 GPU。

后续 smoke、全流程实验、任何代码修改、验证命令与结果继续追加在此处。

### 2026-09-01 09:54-10:13：META cache 参数修复与断点续跑准备

- 状态检查确认五个 Specialist 已冻结并写出 `suite_manifest.json`，但原 smoke 在 `cache-meta-inputs` argparse 阶段因 `--sandbox-memory-mb 1024` 不受支持而于 07:53 退出；旧成功门控正确禁止 pilot 启动。
- 最小复现连续两次稳定返回 exit 2 和 `unrecognized arguments: --sandbox-memory-mb 1024`。删除该参数后，同一 cache 命令越过 argparse，直到故意使用的不存在数据路径才返回 `FileNotFoundError`。
- 根因：run-local launcher 把 `_add_search_args` 专属的 sandbox 参数放入 `COMMON_RUNTIME_ARGS`，并透传给只注册 `_add_runtime_args` 的 `cache-meta-inputs`。
- 修复：`launch_e2e_smoke.sh` 与 `launch_e2e_pilot_after_smoke.sh` 的共用数组不再包含 sandbox 参数；`evolve-specialists` 和 `evolve-meta` 各自显式携带 `--sandbox-memory-mb`。没有修改 CLI、MSTAR、fitness、数据分区或 CoCo 评分逻辑。
- 新增断点续跑脚本 `resume_e2e_smoke_meta.sh`：要求已有 Specialist suite manifest、META 尚不存在、meta input 尚不存在，从 cache-meta-inputs 开始依次执行 prepare-meta、META selection prewarm、META smoke evolution 和 pilot report。
- 新增门控 `gate_meta_resume_then_pilot.sh`：仅当 resume 日志精确包含 `[E2E-RESUME] COMPLETE` 且 8000/13099/13098 均健康时启动独立 pilot。
- 修复后 SHA256：smoke launcher=`b1316898a1e5a6cc2956c3fd6cf4f49533b339ab072ad17ab42a687b51a2368c`；pilot launcher=`eea17074f8b86907007c3dbe28ff500c027d3c5128c25e92b2a43f03eddf9a1c`；resume=`ba4892c6a2d89038800dd1704e1c98f8ad4e74ca533c971fcf2a3ee4c7e43c92`；gate=`09e19a9599dc291b863d3bc79d39f95ac67508f037cb3b77c89f8f062dfdb169`。
- 验证：四个 shell 均通过 `bash -n`；launcher 参数契约检查由修复前 assertion failure 变为 PASS；包含全部 runtime 参数的 cache CLI 不再出现 unknown argument；`git diff --check` 通过。
- 本轮小规模物化数据：10 个 A train family；5 个 Specialist B validation family；1 个独立 META validation family；跨任务共享同一 paper set。每个 Specialist 为 10 episode rows、5 validation rows、11 query rows（10 train + 1 meta-validation）。五任务合计 50/25/55 task rows，对应 10/5/11 unique papers。
- META cache 预期生成 55 份 Specialist outputs，并合并成 11 个完整 Specialist Review Bundle；prepare-meta 预期生成 META episodes=10、validation=1。Smoke 每 candidate 使用 A=2、static=1、rotating=1；Pilot 使用同一物理池，选择 A=4、static=2、rotating=2。
- 正式冻结数据协议（非本轮小规模 pilot）：CoCo source records=3900，family targets=3889；A/B/C/D=2391/299/300/899。当前 pilot 不执行 sealed D 或 Official CoCo；正式 sealed D 工作量为 899 papers × 6 reviewer tasks=5394 task-review evaluations，Official CoCo 若对每篇最终 META review 计分则输入 899 persisted reviews。`benchmark_left_out_human.jsonl` 的 3900 行是官方 human selection/reference 资产，不是本轮最终 target 数。

### 2026-09-01 10:15：META smoke 续跑已启动

- tmux=`ar0831_e2e_gpu6_coco4_meta_resume`；log=`runs/0831_e2e_smoke_tp4_seq4_gpu6_coco4_runtime/meta_resume.log`。
- 成功门控 tmux=`ar0831_meta_resume_pilot_gate`；log=`runs/0831_e2e_smoke_tp4_seq4_gpu6_coco4_runtime/meta_resume_gate.log`。
- 8000/13099/13098 启动前健康检查均通过；GPU6 启动前仅 15 MiB，无重复 cache/meta/pilot 进程。
- 续跑已越过原 argparse 故障，进入真实 `cache-meta-inputs`；Qwen3-Embedding-8B 单实例在 GPU6 占用约 15.4 GiB，已完成首批 runtime embedding miss 并开始生成 Specialist outputs。
- 门控等待 `[E2E-RESUME] COMPLETE`；在 META smoke 完整结束前不会启动 pilot。
- 数据容量复核发现 META validation 物理池仅 1 个 family；pilot launcher 的 Specialist 配置保留 static/rotating=2/2，但 META 单独修正为 1/1。META iterations=2、episode budget=4 不变；不复制 validation target、不扩大数据池。

### 2026-09-01 10:19：Pilot 容量契约复验

- `launch_e2e_pilot_after_smoke.sh` 再次通过 `bash -n`。
- 静态参数契约通过：Specialist `static/rotating=2/2`；META `static/rotating=1/1`；`--sandbox-memory-mb` 只存在于两个 evolution 搜索命令，不再进入 `cache-meta-inputs`。
- 修正后 pilot launcher SHA256=`dde2eb87888fe64972f30c69a6ebe51d93987d93e60c572786148da8a4c80a92`。
- 10:19 实时状态：META resume 与 pilot gate 两个 tmux session 均存活；cache 已连续完成两份 Specialist `Memory Response` 并在生成下一份，日志未出现 Traceback/ERROR/FAILED；`meta_inputs.jsonl` 尚未原子提交，符合仍处于 55 份输出生成阶段的预期。

### 2026-09-01 10:38-11:01：切换为 META-OFF，五冠军直接评测 D

- 用户明确取消 META 与后续 pilot，直接使用现有五个 `suite_manifest.promoted` overall champions。停止前保存 `suite_manifest.json` 与五个 `best_program.py` SHA256；向 META resume 与自动门禁各发送 SIGINT。两个 tmux session、`cache-meta-inputs` 及 gate 进程均已退出，GPU6 已释放；共享 Qwen/CoCo 服务 8000/13099/13098 保持运行。未生成 `meta_inputs.jsonl` 或 META Program。
- Memory 语义核对：每个 Candidate 从空状态开始，按冻结 A Episode 顺序执行 `Paper_i review -> self-generated review -> KnowledgeItem -> write`；历史 replay 后冻结 snapshot，每个验证/D target 从同一 snapshot 读取。META input cache 是 Specialist Review Bundle 构建，不是 Specialist Memory 构建。
- 新增 `final-evaluate-suite --specialists-only`：只解析并校验五个 promoted overall champions，不要求 `meta_manifest.json`，不执行 META，结果身份单独记为 `sealed_d_specialists_coco_style`。默认六任务路径不变。CLI SHA256=`e257c9bf6c98d27799508b63bbb25612292b0fefc805a7089317f88e0e52b472`。
- 修复 D Specialist evaluator reference 物化：旧 `prepare-test` 错误读取 legacy `opinions_md_by_category_dict`，导致五个 D `test.jsonl` 全为0条；现改为调用仓库已有 Official CoCo bridge `load_human_review_data` 与冻结 `benchmark_left_out_human.jsonl` selection。没有把 reference 放入 Candidate question/Memory。
- 正确 D 数据位于 `data/0831_smoke_champions_specialists_d_official`，manifest SHA256=`37884f6f22f1f4e79782a91664b051a08a79a854aac4be152a189bd707c00aad`；paper families=899、missing markdown=0、Frozen S2 required=899/passed。实际角色条数：QUAL=899、CLAR=878、SIGN=804、ORIG=872、POL=338，共3791篇-角色评测；META 文件虽然由统一 D materializer 生成，但 Specialists-only 入口不会读取或执行 META。
- 清洗后的 `scripts/build_coco_temporal_split.py` 与 Git HEAD 一致，但 canonical asset manifest 保留旧 generator SHA。临时重生成验证五个核心文件 `paper_families/A/B/C/D` 与冻结资产逐字节一致；建立 run-local `asset_manifest_v2_runtime.json`，仅更新 generator SHA，canonical manifest 未覆盖。run-local manifest SHA256=`6a6bd9f58b8850fa8f53827daee9bf477ee2e25a856906a881f5c4517661360a`；formal D gate passed，held_out=899、temporal audit passed、S2 audit passed。
- 验证：CLI tests 12/12 passed；AgentReview data/CLI/CoCo reference tests 29/29 passed；protocol/temporal tests 29/29 passed；`git diff --check` passed。D launcher 静态契约确认包含 `--specialists-only` 且不含 `evolve-meta`/`cache-meta-inputs`，SHA256=`b62c78d400e6c7e5784aeb4122951e4d8074857a6cab71d94cda4b070d366306`。
- 11:01 启动 Specialists-only D：tmux=`ar0831_smoke_champions_d`；log=`runs/0831_smoke_champions_specialists_d_runtime/sealed_d_specialists.log`；report target=`runs/0831_smoke_champions_specialists_d/sealed_d_specialists_report.json`。启动前 GPU6 空闲，三个服务健康；启动后 formal gate、CoCo Split/Classify preflight、QUAL champion provenance 与首个 embedding cache hit 均通过，正在执行 QUAL 历史 A replay，未发现错误。

### 2026-09-01 11:15-11:20：缩减为 2 项/角色的 D 全链路试验

- 为优先在 12 小时内观察完整链路，先保存全量 D 现场并向
  `ar0831_smoke_champions_d` 发送 SIGINT。停止时实验已完成 QUAL 的两条 A replay，
  正在首条 D 评审；日志已有 3 次 `Memory Response`、3 次 CoCo Split 完成及 72 次
  CoCo Classify 完成，但尚无首条 D case 原子提交。QUAL checkpoint 保留在
  `runs/0831_smoke_champions_specialists_d/sealed_d_specialists_report.json.QUAL.checkpoint.json`，
  SHA256=`c7eaa3882cc055b01b06e6b0414dfc21402fd1284981c31c83cbd9b97a479bd4`。
  实验进程退出并释放 GPU6；8000/13099/13098 三个共享服务未停止。
- 新增 `--max-specialist-test-items` 与 `--specialist-test-subset-seed`。仅允许与
  `--specialists-only` 联用；按 `seed + task + EvalItem.id` 的 SHA-256 排名稳定选择，
  不依赖 JSONL 行顺序。子集进入 checkpoint identity，并在最终报告标记
  `evaluation_identity=d_subset_specialists_coco_style`、`final_test=false`、
  `non_reportable=true`，不能与 sealed-D 正式成绩混用。默认全量 D 行为不变。
- 本轮选择 seed=0、每角色 2 项：QUAL 2/899、CLAR 2/878、SIGN 2/804、
  ORIG 2/872、POL 2/338，总计 10 次角色-D 评审。仍执行五个 overall champion 的
  A memory replay、D review generation、冻结 CoCo Split/Classify 与 fitness 聚合；META
  始终关闭。
- 验证：`python -m unittest -v tests.test_cli_modes tests.test_agentreview_data` 为
  30/30 passed；CoCo adapter、memory program、protocol boundary、temporal protocol 与
  temporal split 测试另为 48/48 passed；launcher `bash -n`、CLI help 参数合同和
  `git diff --check` 均通过。
  CLI SHA256=`79c3c3a7b102822c9af97db4a37798575cd873f35b4fbb43734bfe08c783a044`；
  subset launcher SHA256=`abdbabff8e76afe248b35775d77de3ddb7e2ede0266357a855bdb80f92b4dbcc`。
- 11:19 启动子集 run：tmux=`ar0831_smoke_champions_d_subset2`；
  log=`runs/0831_smoke_champions_specialists_d_subset2_runtime/d_subset2_specialists.log`；
  report target=`runs/0831_smoke_champions_specialists_d_subset2/d_subset2_specialists_report.json`。
  启动检查显示 formal gate 的 Split/Classify preflight 均通过、缓存命中并进入 QUAL
  历史 memory replay；进程和三个共享服务健康。

### 2026-09-01 11:23-11:25：按用户要求暂停并改为 10 项/角色

- 先保存 subset2 现场并向 `ar0831_smoke_champions_d_subset2` 发送 SIGINT。停止时只完成
  QUAL 第 1 条 A memory replay，尚未进入 D/CoCo 样本；checkpoint SHA256=
  `d043ab58d7a9af9334c326375303c47ecae24fb3f6754ece7aff75485cdfe2d7`。实验进程退出、
  GPU6 空闲，8000/13099/13098 保持健康。该不完整 run 保留且不覆盖。
- “10 篇”落实为每个 Specialist D 数据集稳定选择 10 项：QUAL 10/899、CLAR
  10/878、SIGN 10/804、ORIG 10/872、POL 10/338，共 50 次角色评审。seed=0，
  SHA-256 稳定选择协议、五个 champion、A replay、CoCo 配置和 META-OFF 均不变。
- 新 launcher：`runs/0831_e2e_smoke_tp4_seq4_runtime/launch_specialist_champions_d_subset10.sh`，
  SHA256=`344276629540f7dc87dec3309ca14f3ddc4409962f41aa0d4d4509acb0681739`；
  `bash -n`、参数合同和 `git diff --check` 通过。
- 11:24 启动 tmux=`ar0831_smoke_champions_d_subset10`；
  log=`runs/0831_smoke_champions_specialists_d_subset10_runtime/d_subset10_specialists.log`；
  report target=`runs/0831_smoke_champions_specialists_d_subset10/d_subset10_specialists_report.json`。
  独立 run 不读取 subset2 的 memory checkpoint，只复用内容寻址 embedding cache；
  formal gate 的 Split/Classify preflight 已通过，三个共享服务健康。

### 2026-09-01 14:31-15:06：停止高失败率 run，切换 64K 并定向重测

- subset10 共发现 6 个上下文长度失败：`EfpZNpkrm2::QUAL`，以及
  `URCfZ2NgaR::CLAR`、`Y58T1MQhh6::CLAR`、`Mb5vJijcHn::CLAR`、
  `veiSkPqIXm::CLAR`、`zyGrziIVdE::CLAR`。共同错误是输入至少 16385 tokens，
  加 retry 输出预算 16384 后超过旧服务的 32768 token 上限。停止 subset10 时保留
  QUAL/CLAR/SIGN/ORIG checkpoints；最终日志 SHA256=
  `7b241111a3c68fd5cf8dc098691282d4896c93f0180ac5b1c998c37fb9fdda54`。
- 先安全停止旧 32K Qwen 服务，再在 GPU0-3 启动 TP=4、
  `max-model-len=65536`、`max-num-seqs=4` 的 Qwen3.8-27B。vLLM 报告 KV cache
  499529 tokens、64K 满上下文理论并发 7.62x；选择 64K 而不是 128K，是因为
  128K 满上下文理论并发约 3.8x，无法为四个最坏请求提供同等余量。CoCo Split/
  Classify 服务保持不变。64K launcher：
  `runs/0831_e2e_smoke_tp4_seq4_runtime/launch_qwen_64k_seq4.sh`。
- 在线并发回归使用四个实际各 16501 prompt tokens、128 output tokens 的请求；
  4/4 返回非空 `PASS`、`finish_reason=stop`，wall time=11.01 秒。说明 64K 配置
  仍支持本轮代表性 4 并发，且不是只依赖 vLLM 理论值。
- `final-evaluate-suite` 新增 `--specialist-test-item-ids`：读取 task 到明确 D EvalItem
  ID 的 JSON 映射，只运行清单内任务；要求 `--specialists-only`，并与随机 subset
  参数互斥。报告身份为 `d_retry_specialists_coco_style`，明确标记
  `final_test=false`、`non_reportable=true`，不会冒充 sealed-D 正式结果。
  `tests.test_cli_modes` 增加 parser 与端到端报告合同测试；CLI、数据与 CoCo adapter
  组合回归 37/37 passed，CLI help 合同、`bash -n`、`git diff --check` 均通过。
- 最初按 6 个失败项启动 `ar0831_failed6_64k`；formal gate、服务预检与两条 QUAL
  A 历史均正常，尚未进入任何 D 评分。用户随后要求只随机测 2 篇，故发送 SIGINT
  停止并保留 QUAL checkpoint（SHA256=
  `581d9583d0bc87098fd8b9977fe654f73418d87bf0879381bccfe55832336eb9`）和日志
  （SHA256=`f430152dca4b38c1176d73e576e9794bf88932e14d67bac93dd5c8a92c42b9ba`）。
- 对上述 6 个失败 ID 使用固定种子 `20260901` 随机抽取 2 个：
  `EfpZNpkrm2::QUAL` 与 `Mb5vJijcHn::CLAR`。选择清单位于
  `runs/0831_smoke_champions_failed2_64k_runtime/failed_ids.json`，SHA256=
  `616240a307fa67a8d151515424c833beaffef379819816cb88c07c6245d1d32b`；两项均在
  对应 D 源数据中存在。15:06 启动 tmux=`ar0831_failed2_64k`，目标报告为
  `runs/0831_smoke_champions_failed2_64k/failed2_64k_report.json`。META 关闭；
  只回放 QUAL/CLAR 两个冠军的 A 历史并各评一篇 D。

### 2026-09-01 15:32：两篇 64K 全链路重测完成

- tmux=`ar0831_failed2_64k` 正常结束并写出 `[D-RETRY-64K] COMPLETE`；总墙钟约
  26 分钟。报告 identity=`d_retry_specialists_coco_style`、scope=
  `selected_specialist_failures_d_retry`、`final_test=false`、
  `non_reportable=true`、META disabled。
- `EfpZNpkrm2::QUAL`：1/1 success，error=null，CoCo-style score=
  `0.8372093023255813`。实际 context size=62638 chars；Qwen runtime 8 calls；
  evaluator 1 call；CoCo 正式 Classify 66 calls（另有 1 次启动预检）。
- `Mb5vJijcHn::CLAR`：1/1 success，error=null，CoCo-style score=
  `0.888888888888889`。实际 context size=60820 chars；Qwen runtime 8 calls；
  evaluator 1 call；CoCo 正式 Classify 72 calls。历史 replay 中一次 8192-token
  Response 被截断后以 16384 重试，最终 9240 tokens、`finish_reason=stop`，证明
  64K 服务可承载本轮真实 retry 路径。
- 全程 HTTP 400=0、Traceback/Error/Exception=0；两篇共完成 6 次正式 Split、
  138 次正式 Classify，三档投票和 4-worker 语义不变。实验退出后 GPU6 回到
  15 MiB；Qwen 8000、CoCo 13098/13099 仍健康，不再启动其他实验。
- 最终证据 SHA256：report=
  `217de803dadcb11bb68df4478b6f1ff4510ce66f5fb50195841097769bc0b6af`；log=
  `f62b4e33be9bcd6fe83d88a7a2730364df77c878f16c0f993a0d19359b444420`；
  QUAL checkpoint=`184dce36385c22e93e5c0dc1376ec28e0e5caac1ba8ad6dbbe99ebb6be0cc9fd`；
  CLAR checkpoint=`7827b90fca386ae33645826d76fce9bc52fa9b55d8e8411eb59ec658cc06f054`。
- 完成后回归：`tests.test_cli_modes`、`tests.test_agentreview_data`、
  `tests.test_coco_adapter` 合计 37/37 passed；`git diff --check` passed；未留下
  `[DEBUG-*]` 临时标记。当前 CLI SHA256=
  `93aa5408ceafed59b55d9604ef768ff35dc0343c0387f853d6fe24330053ab75`；
  64K Qwen launcher SHA256=
  `ecf47e7c355a5664d48414deb258f99c494c8ba3afba9a0e663b3798e101b9e9`；
  通用 retry launcher SHA256=
  `585e363e087b692db5ff80f0a84cfe7ddd201c4b3559a10d1e1cb4a0024044ed`。

### 2026-09-01 15:56-16:16：最终 D review/CoCo 有界流水线与两篇在线验证

- 新增 `memory_evolution/evaluation_orchestrator.py`，只在 Candidate review 已冻结为
  `ReviewSubmission` 后进入深度可控的 evaluator queue。主线程生产 review，单一
  score worker 消费；最大未提交深度默认仍为 1，D launcher 显式使用 2。结果严格按
  EvalItem 输入顺序提交并施加背压。A/history replay、逐篇 snapshot restore、fitness
  公式、CoCo 三档投票和 benchmark adapter 均未更改。
- `evaluate_program` 新增 `evaluation_pipeline_depth`，runtime_stats 记录版本、开关、
  配置/实测最大深度、score worker 数和实际 review/score overlap 次数。CLI 新增
  `--evaluation-pipeline-depth`，并把完整 topology 写入 checkpoint identity 与最终报告。
  四个现有 Specialist D launcher 默认 `D_EVALUATION_PIPELINE_DEPTH=2`，仍可通过环境
  变量回退为 1。
- Specialist CoCo fitness 在 opt-in 流水线路径中并行启动独立的 27B Judge 和
  Split/Classify 分支；META 不启用该内部 fork。外层始终只有 1 个 score worker，
  因而不会把每篇内部 `COCO_CLASSIFY_WORKERS=4` 乘成 8/12。最终部署合同为 Qwen
  review+judge 最大 2 路、Split 1 路、Classify 全局 4 路。
- 新增合同测试覆盖：depth=1 保持串行、depth=2 真实 overlap、输入顺序提交、非法深度
  fail-fast、`evaluate_program` 接线，以及 Judge/CoCo 并行前后分数不漂移。仓库当前
  220/220 项 tests 全通过；最终聚焦回归 24/24 passed；`git diff --check` 与四个
  launcher `bash -n` 通过。环境没有安装 optional `ruff`，因此未把 ruff 计入通过项。
- 在线验证选择两篇此前 32K 失败的同角色 D：`URCfZ2NgaR::CLAR`、
  `Y58T1MQhh6::CLAR`。run=`ar0831_pipeline2_clar2_64k`，报告=
  `runs/0831_smoke_champions_pipeline2_clar2_64k/pipeline2_clar2_64k_report.json`，
  日志=`runs/0831_smoke_champions_pipeline2_clar2_64k_runtime/pipeline2_clar2_64k.log`。
  两篇均 success：score 分别为 `0.888888888888889`、`0.7179487179487178`，mean=
  `0.8034188034188035`；HTTP 400/Traceback/Exception/ERROR 均为 0。该 retry pilot
  明确为 `final_test=false`、`non_reportable=true`。
- 在线 runtime_stats 证实 depth configured/observed=`2/2`、score_workers=1、
  `reviews_produced_while_scoring=1`、历史 replay 2/2 完成且 write_failures=0。
  应用日志第 24-26 行依次交错出现第一篇 `Memory Harness`、第二篇 `Memory Query` 和
  CoCo Split；Qwen 服务日志 16:11:05 为 `Running: 2, Waiting: 0`，Classify 服务
  日志 16:11:07 为 `Running: 4, Waiting: 0`。两篇共 6 次正式 Split、114 次正式
  Classify、2 次 Judge；总墙钟 `1205.19s`。
- 验证结束后 experiment tmux 正常退出，GPU6 回到 15 MiB；8000/13099/13098 三个
  共享服务保持健康。证据 SHA256：report=
  `530c69fa52cba079c0da5527668ecffa077ec644a508731a11b246e1c20a3814`；log=
  `e8f3ba91d446f6efbc9859f05b07bca8967c42ad4f66938cee410b74e4bdf782`；CLAR checkpoint=
  `5acb16fd610004a75ce6b6d59a327c447b6d76d931b1399f8682c919f085b8f7`；selection manifest=
  `c26091ddd8cb98d4b1d747ed453d81e2f4dba83325a1ce1ddd2e253c6588e2ad`。
