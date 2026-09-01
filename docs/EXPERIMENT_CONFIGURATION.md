# 正式实验配置与常见问题

本文集中记录 Research Core v2 正式实验的运行配置、已验证容量和容易重复出现的环境问题。命令默认从 `/data0/xyj/shengao/AgentReview_0831` 执行；密钥只从环境变量读取，不写入配置或日志。

## Python 与仓库路径

非交互 shell 中可能没有 `python` 和 `pytest`。统一使用：

```bash
export PYTHONPATH=/data0/xyj/shengao/AgentReview_0831/AgentReview
PYTHON=/data0/xyj/miniconda3/envs/env_shengao/bin/python
```

例如：

```bash
$PYTHON -m pytest AgentReview/tests -q
$PYTHON -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json --help
```

若出现 `ModuleNotFoundError: No module named 'agentreview'`，先确认当前目录是外层仓库根目录，并设置上述 `PYTHONPATH`。这不是测试或模块实现失败。

部分 Python HTTP client 会继承 `HTTP_PROXY/HTTPS_PROXY`，即使目标是 `127.0.0.1` 也可能错误地返回代理侧 `502 Bad Gateway`。本地 endpoint 应设置：

```bash
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
```

如果 Python 探活报 502 而同一地址的 `curl --noproxy '*'` 成功，优先检查 proxy bypass，不要重启正常的 vLLM 服务。

## 专用 Qwen3.8-27B 服务

本实验使用物理 GPU 3、6 独立部署，不复用 GPU 0、1 上已有的旧实验服务：

| 配置 | 正式值 |
|---|---|
| endpoint | `http://127.0.0.1:18001/v1` |
| served model | `qwen3.8-27b` |
| physical GPUs | `3,6` |
| tensor parallel | `2` |
| precision | `BF16` |
| max model length | `65,536` |
| max sequences | `8` |
| GPU memory utilization | `0.90` |
| thinking | `false` |

服务状态：

```bash
curl -fsS http://127.0.0.1:18001/v1/models
curl -fsS http://127.0.0.1:18001/metrics | \
  rg '^vllm:(num_requests_running|num_requests_waiting|kv_cache_usage_perc)'
tmux capture-pane -pt ar0831_qwen_gpu36_tp2c8_64k:0 -S -80
```

`CUDA_VISIBLE_DEVICES=3,6` 后，服务进程内的逻辑设备 0、1 分别对应物理卡 3、6；日志中出现 device 0、1 不表示占用了物理卡 0、1。

当前服务测得 KV 容量为 478,412 tokens；如果每个请求都占满 65,536 tokens，无抢占并发容量是 7.30。正式 Specialist 请求的已测最大输入约 25,621 tokens，加 8,192 输出预算约 33,813 tokens，因此 `max_num_seqs=8` 适合当前负载。八个请求仍可能在长 prompt prefill 阶段短暂排队；这是 scheduler batching，不代表客户端并发失效。若未来请求普遍接近完整 64k，应把 worker 降到 7，或接受第八条排队。

32k 不足以容纳已测最大 Specialist 输入与输出预算，因此正式配置使用 64k。当前不建议直接升到 96k：它会减少 KV 并发，而且现有实际请求不需要。

本地 vLLM 不要求真实 OpenAI key；客户端会使用本地占位值。`OPENAI_API_KEY` 不应指向外部付费服务，以免端口配置错误时产生误调用。

## Semantic Scholar：严格单路串行

S2 有请求并发和速率限制，正式规则是：

- 全机同一时间只运行一个 Research v2 `build-s2` 进程；
- development 与 CoCo S2 不并行；
- 单个进程按目标顺序串行查询，不使用线程池或异步请求；
- 任意两次 HTTP 请求的启动时间至少间隔 1.2 秒，即最多约 0.83 request/s；
- retry 也在同一串行调用内完成，不形成额外并发。

正式 CoCo S2 构建使用 `--retry-until-success`。对于 408、425、429、500、
502、503、504 和连接异常，进程持续指数退避（单次最多60秒）并保持同一个
paper checkpoint，不因固定重试次数耗尽而退出；非重试型 HTTP、响应 schema、
checkpoint identity 或本地落盘错误仍立即退出。

若 `S2_API_KEY` 写在登录 shell 配置中，非交互命令可能看不到它。只检查是否存在，不打印值：

```bash
bash -lic '[[ -n "$S2_API_KEY" ]] && echo S2_API_KEY_READY'
```

已有 development 数据准备结果时，使用独立 stage，避免重新扫描约 2 GB 原始数据：

```bash
bash -lic '
  cd /data0/xyj/shengao/AgentReview_0831
  export PYTHONPATH=/data0/xyj/shengao/AgentReview_0831/AgentReview
  /data0/xyj/miniconda3/envs/env_shengao/bin/python \
    -m agentreview.research_v2 \
    --config AgentReview/configs/research_v2_formal_v1.json \
    build-s2 --dataset development \
    --min-request-interval-seconds 1.2 \
    --retry-until-success
'
```

每条成功结果立即写入 `*.jsonl.parts/`。进程中断后用同一命令恢复，不删除 parts，也不要另开第二个恢复进程。Semantic Scholar 对零命中查询会返回 `total: 0` 且省略 `data`；该响应应冻结为空结果。`total > 0` 却没有 `data` 仍按异常退出，防止冻结损坏响应。

## S2 是 Program capability，不是 Primary 开关

Frozen S2 pool 只决定 evidence capability 是否可用，不决定某篇 Review 是否使用它。正式 Primary 的 intervention identity 固定为：

```json
{
  "protocol": "s2-intervention-v1",
  "mode": "program_autonomous",
  "label": "formal-primary-v1"
}
```

Program 对每篇论文自主执行 call/skip；调用后也可以记录 `consulted_no_use`，或选择候选子集并给出 usage instruction。Development 与 CoCo pool 都必须冻结，是因为 winner 在两类论文上都需要同一能力接口；CoCo Split/Classify scorer 本身不读取 S2。

当前 pool 的 per-paper 上限是 top-5。因此第一版能进化是否调用、查询后不用、子集、context 顺序和使用方式，不能从更宽候选集中学习 reranking。若未来扩为 top-20，development 与 CoCo 两个 pool、配置 identity 和 freeze binding 必须在 sealed evaluation 前一起升级。

`--no-s2` 只表示有独立 run ID 的诊断干预：

```json
{
  "protocol": "s2-intervention-v1",
  "mode": "forced_off",
  "label": "cli-no-s2-diagnostic-v1"
}
```

它不能用于 Primary、winner 选择或替代 Autonomous S2 Policy。`forced_consult` 仅预留给预注册的强制查询诊断，并不强制选择或注入；固定 intervention 结果不能反馈 Evolution。

Review provenance v2 必须同时保存 Memory `logs/context`，以及 S2 的 capability、intervention、disposition、decision label、lookup count/limits、candidate/selected/injected IDs、usage instruction、context order、evidence、pool version/SHA 和 context SHA。缺少这些字段的旧 canonical Review 保持历史 artifact 身份，但不进入 autonomous usage 统计。

`summarize_s2_usage(review_paths)` 固定报告 eligible、lookup、injection、consulted-but-unused、empty-result rates 与平均 selected 数，并按 role、program version、decision label 分组。policy-rate 分母只包含 `capability_available=true` 且 `mode=program_autonomous` 的 Review；forced diagnostics 单列。

Memory 与 S2 policy 的效果归因使用冻结 development set 的四格诊断：baseline/evolved Memory × baseline/evolved autonomous S2 policy。四格都让各自 policy 自主决策，不用 forced on/off 代替；报告 Memory/S2 主效应与 interaction，但不参与 winner 选择。若计划在 CoCo 上报告四格，必须在打开 sealed set 前预注册，结果不得返回 Evolution。

## CoCo 两个服务与 Review 并行

CoCo Split 和 Classify 是两个独立的单 GPU、TP=1 vLLM endpoint。当前服务端已显式冻结 `max_num_seqs`：Split 为8、Classify 为16；这是服务端最大活跃序列数，实际并行仍受 KV 容量约束。客户端 semaphore 只是 HTTP 在途/排队上限，不等于 GPU 真并行数。

旧本地配置中的 Split 512、Classify 1024 是 HTTP 在途上限。安全的 official candidate runner 默认收紧为 Split 64、Classify 128，并允许显式覆盖；它们仍只是排队上限，不是“64/128 路 GPU 同时推理”。正式 Split 切换到40K配置时必须再次显式冻结 `--max-num-seqs`，并用 metrics 与真实 prompt smoke 确认运行/等待数。

一条实验臂 A 的完整 1,300 篇 CoCo scoring 可以与另一条实验臂 B 的 Review 同时排队和运行，条件是：

- CoCo 与 27B 使用不同物理 GPU；
- CoCo 只连接预先启动的 Split/Classify endpoint；
- 不运行会执行全局 `pkill -f vllm...` 的旧生命周期脚本；
- 每条 CoCo 臂仍先满足严格 1,300 ID exact-set barrier；
- CoCo 输出不进入 Review、Internal Evaluation 或 Evolution。

`scripts/run_official_candidate.py` 不负责启动、停止或清理任何 vLLM 服务，因此不会让专用 27B 在 CoCo 期间休息，也不会误杀其他实验服务。

### GPU 0/1 预留服务（2026-09-01）

为防止正式实验期间 GPU 0/1 被其他任务占用，当前已部署两个真实 CoCo
endpoint，而不是无业务意义的显存占位进程：

| GPU | tmux | endpoint | 模型 | 当前参数 | 用途 |
|---|---|---|---|---|---|
| 0 | `ar0831_coco_split_gpu0` | `127.0.0.1:13099/v1` | `Qwen3-8B-Split` | 8K、`max_num_seqs=8`、`gpu_memory_utilization=0.40` | 预留兼容模式，为 Evolution 的 Qwen3-Embedding-8B 保留显存 |
| 1 | `ar0831_coco_classify_gpu1` | `127.0.0.1:13098/v1` | `Qwen3-8B-Classify` | 32K、`max_num_seqs=16`、`gpu_memory_utilization=0.90` | 正式 Classify 常驻服务 |

GPU 0 在 Split 占用约19.7 GiB 时，已实际加载正式
`Qwen/Qwen3-Embedding-8B` 并成功生成4096维向量；两者可共存。预检日志是
`artifacts/research_v2/runtime/coco_split_gpu0_reserve/embedding_coexist_preflight.log`。

当前 GPU 0 Split 的 8K 是预留兼容参数，不是最终 CoCo scoring 参数。Formal
Evolution 完成后也不能立即切换40K：winner batch 仍会在 GPU 0 加载
Qwen3-Embedding-8B。必须保持8K/0.40预留服务直到 winner batch 完整退出、
Specialist/Meta/concat 各自通过1,300 exact-set barrier并释放 embedding，随后才定向停止
该 Split tmux、重启冻结的40K服务并执行真实 CoCo prompt smoke。不得直接用8K预留
服务开始1,300篇正式 Split，也不得让40K Split与 winner embedding争用 GPU 0。

服务日志：

```text
artifacts/research_v2/runtime/coco_split_gpu0_reserve/server.log
artifacts/research_v2/runtime/coco_classify_gpu1/server.log
```

健康检查：

```bash
curl --fail --silent http://127.0.0.1:13099/v1/models
curl --fail --silent http://127.0.0.1:13098/v1/models
```

不要使用 `env_shengao/bin/vllm` 启动这两个服务。该环境当前的 vLLM 0.11.2
扩展与已安装 PyTorch CUDA ABI 不一致，会在导入 `vllm._C` 时报告
`undefined symbol`。当前两个服务使用已验证的
`/data0/xyj/miniconda3/envs/qwen38serve/bin/python` 和 vLLM 0.20.1；切换环境或
版本属于服务 identity 变更，必须重新做 endpoint smoke 并记录版本。

## Run ID 与不可变 artifact

正式 canonical bootstrap 保留 run ID `canonical-bootstrap-v1`。它是 migration 前已完成的固定 S2 来源 corpus，不改写。smoke、`--limit` 或 `--no-s2` diagnostic 必须使用独立 run ID，例如 `qwen-live-smoke-20260901-01`；seed/static/winner Primary 使用 `program_autonomous` intervention，不沿用 canonical 的固定 S2 控制面。

Review raw、完整 Review、Memory snapshot 和 S2 parts 是不可变 artifact；同路径只允许字节等价/内容等价重放。模型身份、generation 参数、S2 pool 或选中论文集合改变后，应使用新 run ID。恢复正式任务时使用原 run ID；runner 会验证完整 invocation identity 后跳过已完成角色。

不要同时启动两个相同 run ID 的 bootstrap 进程。正式 runner 使用 run-scoped lock；第二个进程应立即失败，而不是把模型并发翻倍。

## 当前 tmux 与日志

```text
27B service:  ar0831_qwen_gpu36_tp2c8_64k
canonical run: complete（历史 session ar0831_formal_canonical_c8 已退出）
internal eval: complete（历史 session 已退出）
Formal Evolution: ar0831_formal_evolution_v1
S2 build:     complete（历史 session ar0831_s2_coco1300 已退出）
hourly probe: ar0831_hourly_probe
service log:  artifacts/research_v2/runtime/qwen_tp2_gpu36_64k/qwen.log
development S2 log: artifacts/research_v2/runtime/s2_development/build.log
CoCo S2 log:  artifacts/research_v2/runtime/s2_coco1300/build.log
formal log:   artifacts/research_v2/runtime/formal_evolution_v1/stderr.log
internal eval log: artifacts/research_v2/runtime/canonical_internal_eval_v1/stderr.log
```

查看正式进度：

```bash
tail -f artifacts/research_v2/runtime/formal_evolution_v1/stderr.log
```

若 Formal Evolution 意外退出，在 endpoint 恢复、确认旧 PID 与 flock owner 均不存在
后，使用同一 run ID 与相同输入恢复；已验证 artifact 会直接 resume：

```bash
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  formal-evolution --action run-all-roles \
  --run-id formal-evolution-v1 \
  --canonical-evaluation-summary artifacts/research_v2/runs/canonical-internal-eval-v1/internal_evaluation.json \
  --canonical-split artifacts/research_v2/data/splits/canonical_review_corpus.json \
  --static-split artifacts/research_v2/data/splits/evolution_static.json \
  --rotating-split artifacts/research_v2/data/splits/evolution_rotating_pool.json
```

生成资产位于 `artifacts/research_v2/` 且被 Git ignore；源码配置、commit、manifest SHA256 和运行日志共同构成归档身份。

## 每小时实验探针与故障恢复

正式实验使用独立 tmux `ar0831_hourly_probe`，每3600秒从上一次探针开始时间
执行一次只读检查：

```bash
PYTHONPATH=AgentReview \
  /data0/xyj/miniconda3/envs/env_shengao/bin/python -u \
  -m agentreview.research_v2.probe --loop-seconds 3600
```

探针同时核验 Formal/S2 精确进程数与启动参数、实际 lock owner、三个 endpoint
模型身份、27B running/waiting/KV、GPU、剩余磁盘、Git HEAD、日志新增错误、
Formal artifact 计数和 CoCo S2 parts。它不会删除 lock/part/sidecar，不会执行
`pkill`，也不会自行启动第二个 runner。审计输出位于：

```text
artifacts/research_v2/runtime/hourly_probe/latest.json
artifacts/research_v2/runtime/hourly_probe/probes.jsonl
artifacts/research_v2/runtime/hourly_probe/action.json
artifacts/research_v2/runtime/hourly_probe/runner.log
```

`healthy` 表示继续运行；`degraded` 要求诊断但不立即重启；
`action_required` 表示精确进程缺失、重复 S2、身份错误、连续停滞或容量危险；
`transition_ready` 表示 Formal 与 CoCo S2 均完成，应进入 winner/freeze/CoCo 阶段。
Formal 单个长请求一小时无新 artifact 先记 warning，连续两个探针间隔无增长才记
critical。S2 若 parts 一小时不增长但 retry log 仍更新，只记上游 degraded；log 和
parts 同时一个完整探针间隔不增长才记 stalled。磁盘低于200 GiB warning、低于
50 GiB critical。

探针协议 v3 使用覆盖整个 daemon 生命周期的 `controller.lock`，因此 sleep 期间也
不允许第二个 loop 或 one-shot 交替写状态。日志首次读取只建立基线；之后通过 inode
与前一 offset 的内容 checkpoint 同时识别普通 append、inode 轮转和 copytruncate，
轮转/截断后的全部新日志也会检查。探针自身采样异常会将 `probe_failed` 以
`action_required` 写入 `latest.json` 和 `action.json`，不会留下过期的 healthy 状态。
CoCo S2 只有在1300个 JSON record 与1300个 query 逐项通过 paper ID、builder identity、
record/result hash、top-5 数量、去重，以及 exact title、same-family、S2 ID 和 external
ID self-match 校验后才会标记 complete，单纯达到1300行不能触发 `transition_ready`。
Formal 与 CoCo S2 的精确 PID 还必须分别等于其 flock owner PID，只有进程或只有 lock
均为 critical。

## Formal 完成后的确定性过渡

只有小时探针给出 `transition_ready=true`，且 Git worktree 已提交并保持干净时，才按
以下固定身份从五角色 winner 进入 sealed CoCo 阶段：

```bash
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  materialize-freeze-spec \
  --run-id formal-evolution-v1 \
  --output artifacts/research_v2/frozen/formal-evolution-v1.freeze-spec.json

PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  freeze-system \
  --spec artifacts/research_v2/frozen/formal-evolution-v1.freeze-spec.json \
  --output artifacts/research_v2/frozen/formal-evolution-v1.system.json
```

materializer 会验证 all-role summary 的五角色 complete 状态，以及每个角色的
pool/coordinator/winner/run-spec、所有 candidate Program/snapshot 与 attempt artifact、
三种 seed 的 static Review/Evaluation、每轮 rotating feedback 和成功 candidate 的
static Review/Evaluation；它从证据重算 seed/candidate 分数、Reflector feedback、历史
pool 顺序和确定性 parent，拒绝未完成 attempt、游离 candidate 与未绑定的部分产物。
同时验证 run-spec 引用的 canonical summary/三份 split/seed Program、模型和 embedding identity、
autonomous S2 intervention、development S2 SHA/版本/275计数。语义验证与源哈希绑定必须
连续两遍完全一致，随后 freeze spec 才绑定两套 S2、development/CoCo manifest 与当前
clean transition Git HEAD。`resume_20260901T230625+0800.json` 及其 SHA 单独绑定
Evolution 的 original/context-fix/resume commits；`code_commit` 不得把这段多提交执行史
误归因成 transition commit。`primary_meta_enabled` 固定为 true，指的是最终系统包含
独立 Meta synthesizer，并不改变 evolved Program 是否调用 S2 的自主权。

过渡还会绑定独立 `CoCoReviewBench` 仓库的 clean Git commit、仓库根路径、
`scripts/run_official_candidate.py` 路径与 SHA256；该文件也进入 freeze spec 的 source
bindings。`coco-eval` 在外部 subprocess 前后都会复核这份身份，并拒绝 dirty worktree、
commit漂移、runner内容漂移或绕过 frozen runner 的命令。

随后以唯一正式 run ID 启动可恢复的 winner batch：

启动前 Git 必须继续保持干净，且 HEAD 必须与 freeze spec/frozen system 中记录的
transition commit 完全一致；仅“代码内容看起来相同”不能替代提交身份一致。

```bash
CUDA_VISIBLE_DEVICES=0 TOKENIZERS_PARALLELISM=false \
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python -u \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  generate-coco-reviews \
  --frozen-system artifacts/research_v2/frozen/formal-evolution-v1.system.json \
  --run-id formal-coco1300-winner-v1 \
  --max-workers 8
```

这里 `CUDA_VISIBLE_DEVICES=0` 只约束进程内 Qwen3-Embedding-8B；27B 请求仍连接
GPU 3/6 的 endpoint。Specialist 与 Meta 共享同一个最多8路 executor：论文 A 的五份
Specialist 齐备后，其 Meta 可以与论文 B 的 Specialist 同时排队；不会出现 Meta 阶段
让27B空闲。每篇 concat 在五份 Specialist 齐备后确定性生成，不占27B。正式输出为：

```text
artifacts/research_v2/reviews/formal-coco1300-winner-v1/<paper_id>/<role>.json
artifacts/research_v2/final_reviews/formal-coco1300-winner-v1-meta/<paper_id>.json
artifacts/research_v2/final_reviews/formal-coco1300-winner-v1-concat/<paper_id>.json
artifacts/research_v2/runs/formal-coco1300-winner-v1/coco_review_batch.json
```

Meta 与 concat 必须分别达到严格1300 ID exact-set 后再作为两个独立 CoCo scoring arm；
两臂使用不同输出目录与缓存 identity，不能把一个臂的结果反馈到另一个臂或 Review。
恢复时 Specialist/Meta/concat ID 均绑定正文内容；Specialist 必须回连匹配的 raw output，
Meta 必须回连匹配的 meta raw/request。改写正文后重算旧式 ID 也必须在新请求前失败。

每个 winner task 使用 run-scoped、确定性 persistent staging。进程在 raw/request/final
依次 promotion 的任一点退出后，同一 run ID 会从已经验证的 staged prefix 继续；只有
完整 source identity 一致的 final 才进入 canonical 路径。PaperFailure 或基础设施失败会
保留该不可变 staging prefix 供审计，因此同一 run ID 会确定性重现同一失败。需要重新
请求时使用新 run ID；受控清理 staging 只允许在没有 canonical promotion、已记录原因并
建立 Git/运行审计后人工执行，小时探针不会自动清理或改写 staging。

外部 CoCo runner 同样采用 output-root scoped 非阻塞锁。Split、Classify 与可选
Evaluation 各自在 `.candidate_official_stages/<stage>/` 写入输入、设置、输出 SHA 和
checkpoint ID；只有完整验证后才通过硬链接发布 fixed artifact。相同身份恢复会跳过
已完成阶段，不再请求模型；失败 attempt 保留在隐藏 staging 中。任一输入、endpoint、
model、worker、effort、cache version 或 evaluation 设置改变都会在新调用前失败，必须
使用新的 scoring arm output root，而不是覆盖旧产物。

故障恢复顺序固定为：确认精确 PID 为0且 flock 无 owner；保护当前 worktree 并建立
Git checkpoint；复现根因并添加回归测试；提交修复；记录启动前/恢复后的 commit；
使用相同 run ID、输入身份与参数追加日志恢复。不得删除不可变 artifact 来“重跑”。

### 64K Reflector 上下文门禁

2026-09-01 首个 QUAL mutation 暴露了固定8192输出与超长 feedback prompt 的组合
越界：原输入精确为61,796 tokens。根因是 prompt 在保存完整 Memory/S2 trace 后，
又重复展开相同的 query、evidence、decision 与 selected fields。Reflector 渲染现在
保留五个 failure case、完整 Review、完整 Memory trace、完整 S2 trace 与 Internal
Evaluation，但每份事实只出现一次；同一真实请求降为38,172 input tokens。

Reflector 的 mutation budget 仍对所有 attempt 固定为8192 output tokens，不能按
prompt 长度动态降低。每次真实调用前通过冻结27B的本地 `/tokenize` endpoint 使用
相同 chat template 精确计数，并要求：

```text
input_tokens + 8192 + 512 <= 65536
```

其中512是固定安全余量。门禁不满足时在模型调用前中止并保留 attempt plan，不消费
mutation attempt；不得把 context 提升、缩短 feedback case 数或动态减少输出预算
作为无记录的临时修复。
