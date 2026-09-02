# 正式实验配置与常见问题

本文集中记录 Research Core v2 正式实验的运行配置、已验证容量和容易重复出现的环境问题。命令默认从 `/data0/xyj/shengao/AgentReview_0831` 执行。Semantic Scholar 密钥只从环境变量读取，不写入配置或日志；本地 CoCo Judge 不需要 API key。

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

正式 Split、Classify 和 Judge 客户端都使用不继承 shell 环境的直连 HTTP
transport，封印的 CoCo 子进程也不注入任何 proxy 变量。不要为正式 Judge
配置本地或外部 proxy。只在手工调试会继承 shell 环境的普通 Python HTTP
client 时，可设置：

```bash
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost
```

如果手工 Python 探活报 502 而同一地址的 `curl --noproxy '*'` 成功，
说明错误在手工 client 的 proxy 继承，不是 vLLM 服务；正式 runner 不应通过
增加 proxy bypass 变量修改封印运行环境。

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

同一服务有两种互斥用途：Reviewer/Evolution/winner Review 生成，以及
CoCo Table-1 Judge。Judge 的正式请求配置是 `max_completion_tokens=8192`、
temperature 0、effort `none`、`enable_thinking=false`；每个 meta/concat arm
最多4个 client request，两臂同时运行时合计8个，与服务端
`max_num_seqs=8` 一致。这里的8是两臂共享的物理上限，不是每臂8路。

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

本地 vLLM Judge 不读取任何外部 API key 环境变量。客户端在
进程内使用固定的非秘密本地占位值，且 base URL 必须精确为
`http://127.0.0.1:18001/v1`；环境中的外部 key 和端口都不能改变正式
Judge identity。

### 27B Reviewer/Judge 常见配置问题

- `/v1/models` 正常但 seal 报 restart barrier：活跃的 listener/engine 是生成
  Review 时启动的旧实例。先固定 scoring spec 和两份 candidate input，在它们
  最新 mtime 之后至少等待2秒，再定向重启27B；仅在旧服务上等待2秒
  无效。
- Judge lease 冲突：仍有 Reviewer/Evolution/winner Review 客户端持有排他 lease。
  查明并让该进程正常退出，然后排队重试；不要删除或替换
  `qwen38_27b_mode.lock`，也不要绕过 lock 直接请求 endpoint。
- Judge 运行中 service instance/lease generation 漂移：当前 arm 会 fail closed，已有的
  旧实例 cache 不会被当作新实例结果。保留失败 staging 供审计，按新实例
  的 seal/spec 恢复流程；不要改写 complete artifact。
- seal 前 metrics 不为零：新 Judge 实例在共享 lease 下应保持 idle，且自本次
  listener/engine 启动以来的 running/waiting、generation request/token 与
  prefix-cache 流量指标必须全为零。非零说明重启后又有 Reviewer 或其他请求
  污染了实例，不能建立 seal。一旦 `coco-eval` 开始，这些累计指标非零属于
  正常 Judge 流量，运行中和最终审计不再要求它们为零。
- 人工 client 报 502：先用 `curl --noproxy '*'` 对比。若 curl 成功，是手工
  client 继承了 proxy；正式 runner 应保持无 proxy，不需重启 vLLM。
- `/tokenize` 超限：Judge 要求 `prompt_tokens + 8192 <= 65536`。查看预检
  中的请求类型、paper ID 和 token-count hash；禁止截断、动态降低输出预算或
  临时扩大 context。
- KV usage 只有约37% 不表示可以改为16并发。正式物理上限仍是8，Judge 两臂
  固定4+4；KV 比例只是当前时刻已分配 cache，不包含后续长 prompt 的最坏情况。
- 模型名不匹配：模型目录是 `Qwen3.8-27B`，正式 served model name 必须是
  小写 `qwen3.8-27b`。修改大小写、端口、GPU、TP、context 或 chat template
  都是服务 identity 变更，不能在封印后热切换。

## Semantic Scholar：严格单路串行

S2 有请求并发和速率限制，正式规则是：

- 全机同一时间只运行一个 Research v2 `build-s2` 进程；
- development 与 CoCo S2 不并行；
- 单个进程按目标顺序串行查询，不使用线程池或异步请求；
- 任意两次 HTTP 请求的启动时间至少间隔 1.2 秒，即最多约 0.83 request/s；
- retry 也在同一串行调用内完成，不形成额外并发。

截至2026-09-02，正式 CoCo S2 pool 已按上述规则完成严格的1,300/1,300
校验，文件 SHA256 为
`9d5eb43166d9f8b9b34fcc5dcf9382b54e40adb006d8e8aa227ad6befe63956e`。
后续恢复 Formal、生成 winner 或执行 CoCo 评分都不能重新构建、追加或改写这份
pool。S2 只供 evolved Reviewer Program 在生成 Review 时自主使用；下游 CoCo
Split、Classify 与 Evaluation 只读取已经冻结的最终 Review，不发起 S2 请求，也不依赖
S2 服务是否在线。

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

## CoCo 三个本地服务与并行边界

CoCo 正式评分连接三个本地 endpoint：GPU 0 的 Split、GPU 1 的
Classify，以及 GPU 3、6 TP=2 的 Qwen3.8-27B Judge。Split 和 Classify
服务端冻结的 `max_num_seqs` 分别为8和16；Judge 为8。这是服务端
最大活跃序列数，实际并行仍受 KV 容量约束。客户端 semaphore 只是
HTTP 在途/排队上限，不等于 GPU 真并行数。

旧本地配置中的 Split 512、Classify 1024 是 HTTP 在途上限。安全的 official candidate runner 默认收紧为 Split 64、Classify 128，并允许显式覆盖；它们仍只是排队上限，不是“64/128 路 GPU 同时推理”。正式 Split 切换到40K配置时必须再次显式冻结 `--max-num-seqs`，并用 metrics 与真实 prompt smoke 确认运行/等待数。

27B 的 Reviewer 和 Judge 模式由
`artifacts/research_v2/runtime/qwen38_27b_mode.lock` 隔离。Reviewer/Evolution/
winner Review 客户端持有排他 lease，CoCo Judge 进程持有共享 lease。因此：

- Review、Evolution 或 winner Review 生成不能与任何 CoCo Judge 请求重叠；
- meta 和 concat 两个 Judge arm 可以同时持有共享 lease；
- 每臂固定4路 Judge client 并发，两臂合计8路，不允许再叠加第三条
  Judge 队列；
- lease 冲突时当前进程 fail closed，控制器可在冲突消失后用同一封印
  identity 排队重试，不得绕过 lease；
- 每条 CoCo 臂必须先满足严格 1,300 ID exact-set barrier，且 CoCo 输出不进入
  Review、Internal Evaluation 或 Evolution。

`scripts/run_official_candidate.py` 不负责启动、停止或清理任何 vLLM
服务。服务切换由实验控制器在 barrier 外定向执行；不得运行包含全局
`pkill -f vllm...` 的旧生命周期脚本。

### GPU 0/1 预留服务（2026-09-01）

为防止正式实验期间 GPU 0/1 被其他任务占用，当前已部署两个真实 CoCo
endpoint，而不是无业务意义的显存占位进程：

| GPU | tmux | endpoint | 模型 | 当前参数 | 用途 |
|---|---|---|---|---|---|
| 0 | `ar0831_coco_split_gpu0` | `127.0.0.1:13099/v1` | `Qwen3-8B-Split` | `max_model_len=8192`、`max_num_seqs=8`、`gpu_memory_utilization=0.40` | 预留兼容模式，为 Evolution 的 Qwen3-Embedding-8B 保留显存 |
| 1 | `ar0831_coco_classify_gpu1` | `127.0.0.1:13098/v1` | `Qwen3-8B-Classify` | `max_model_len=32768`、`max_num_seqs=16`、`gpu_memory_utilization=0.90` | 正式 Classify 常驻服务 |

GPU 0 在 Split 占用约19.7 GiB 时，已实际加载正式
`Qwen/Qwen3-Embedding-8B` 并成功生成4096维向量；两者可共存。预检日志是
`artifacts/research_v2/runtime/coco_split_gpu0_reserve/embedding_coexist_preflight.log`。

当前 GPU 0 Split 的 `max_model_len=8192` 是预留兼容参数，不是最终 CoCo
scoring 参数。Formal
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

### 两类已验证的恢复门禁

- Memory 结构化 item 必须在单次模型请求中使用
  `response_format=json_schema` 强制 schema，不能只在 prompt 中要求模型返回 JSON。
  单条 item 失败时保留已经提交的 item checkpoint，修复 endpoint 或可恢复基础设施问题后
  使用同一 run ID 继续；不得删除完整前缀重新生成。对应修复 commit 为 `a5dc464`。
- 正式进程运行期间禁止修改会进入 request identity 的代码或字段。历史 v1 request
  缺少 `require_bubblewrap` 时，只允许 Formal 私有兼容路径复用，并且必须同时满足：当前
  expected 值为 `true`、其余 identity 字段完全相等、所有已绑定 artifact hash 仍有效。
  其他缺字段、值漂移或 hash 失效一律 fail closed。对应修复 commit 为 `14eb9fb`。

## 当前 tmux 与日志

```text
27B service:  ar0831_qwen_gpu36_tp2c8_64k
canonical run: complete（历史 session ar0831_formal_canonical_c8 已退出）
internal eval: complete（历史 session 已退出）
Formal Evolution: ar0831_formal_evolution_v1
S2 build:     complete（历史 session ar0831_s2_coco1300 已退出）
hourly probe: stopped（历史 session ar0831_hourly_probe；未收到新指令时不重启）
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

## 可选的每小时实验探针与故障恢复

小时 probe daemon 当前已按控制指令停止，不得自动重启。若后续明确要求启用，
使用独立 tmux `ar0831_hourly_probe`，每3600秒从上一次探针开始时间执行
一次只读检查：

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

只有 transition-readiness 门禁确认 Formal 与 CoCo S2 完整，且两个 Git
worktree 的变更都已提交并保持干净时，才从五角色 winner 进入 sealed
CoCo 阶段。该门禁可由 one-shot probe 或控制器的等价完整检查给出，不要为了
过渡而重启已停止的小时 daemon。顺序固定为：Formal 完整结束 → 在 clean
`CoCoReviewBench` 上物化 CoCo scoring spec → 物化并冻结 Formal system →
以唯一 run ID 生成 winner batch 和 meta/concat 两份 candidate input → 将 GPU 0
Split 从预留服务切换到正式服务 → 退出全部 Reviewer 客户端并定向重启27B
为 Judge 模式 → 通过2秒 restart barrier 并建立 pre-score seal → 并行或排队执行
meta/concat 两臂 → final audit。任何一步失败都停在该步恢复，不能越过
barrier 启动下游。

首先确认外部仓库状态为空，然后物化唯一的评分环境身份。该步骤绑定外部 commit、runner、
benchmark/evaluation closure、Split/Classify/Judge 模型文件、独立 Evaluation Python 的完整
prefix tree、固定 BERTScore 模型 tree、正式 endpoint 参数，以及两臂不同的 output root
和三阶段 cache namespace；此时 GPU 0 仍可保持8K预留服务：

```bash
git -C /data0/xyj/shengao/CoCoReviewBench status --porcelain=v1

PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  materialize-coco-scoring-spec \
  --winner-run-id formal-coco1300-winner-v1 \
  --evaluation-python /data0/xyj/shengao/AgentReview_0831/artifacts/research_v2/runtime/coco_eval_env_v1/bin/python \
  --output artifacts/research_v2/frozen/formal-coco1300-scoring-spec-v1.json
```

命令必须看到空的 CoCo worktree；不能临时忽略 untracked benchmark 或 runner 文件。
正式 Evaluation 的 CPU metrics/runner 只能使用上面固定的
`coco_eval_env_v1`，不能复用服务 GPU endpoint 的 `qwen38serve` Python。其运行
方式固定为 CPU、offline（仅模型/数据下载离线，Judge 仍直连 loopback vLLM）、
`python -s -B`：禁用 user site，禁止写入新 bytecode，并设置 `HF_HUB_OFFLINE=1` 与
`TRANSFORMERS_OFFLINE=1`。scoring spec 会在清空环境的 probe 中验证 Python flags 和
openai、requests、tqdm、numpy、torch、nltk、rouge-score、bert-score、transformers
版本，再由父进程绑定整个 `sys.prefix` 的 ordinary file bytes、已有 `.pyc` 与 symlink
target；运行时与 runner checkpoint 必须复用同一身份。

正式 Judge 的网络 transport 由 spec 固定为直连
`http://127.0.0.1:18001/v1`。outer runner 使用完整白名单环境，三个本地
endpoint client 均不继承 ambient proxy、不注入 proxy 变量，也不读取 API key。
Evaluation 固定 `require_complete=true`、temperature 0、effort `none`、
`enable_thinking=false`；任一 Judge 提交、解析或重试最终失败都会使阶段
非零退出。

Split 的输出预算固定为 `max_completion_tokens=8192`，上下文上限固定为 40,960。
runner 在发出任何 Split API 请求前，必须用封印的 Split tokenizer、正式 system prompt
和 `apply_chat_template(..., add_generation_prompt=true, enable_thinking=true)` 对每条实际
candidate 做离线 token preflight；只有 `prompt_tokens + 8192 <= 40960` 才能继续，禁止
截断，任一超限必须列出 paper ID 并使评分失败。该算法、预算和实际摘要均进入 runner
身份与 final audit。

在125篇 canonical bootstrap concat candidate 上已用同一算法做过离线 smoke：prompt
token 的 median/p95/max 分别为 4,390/4,966/5,487，最大总预算为
13,679/40,960。该结果只说明当前样本有充足余量，不代替对正式1,300篇 candidate 的
逐条 exact preflight；正式 gate 仍必须在任何 Split API 请求前执行。

Classify 的输出预算固定为 `max_completion_tokens=4096`，上下文上限固定为
32,768。runner 必须在 Split artifact 完整验证后、发出任何 Classify API 请求前，按
JSONL paper 顺序与每篇 `opinions` 索引顺序构造逐 opinion 正式 prompt，并使用封印的
Classify tokenizer 与 `enable_thinking=true` chat template 做同样的 exact preflight。
动态摘要同时绑定 paper `candidate_count`、扁平 `opinion_count`、最大 prompt/总 token 与
完整 token-count 列表 hash；任何 `prompt_tokens + 4096 > 32768` 的 opinion 都必须列出
paper ID/opinion index 后失败，禁止截断或部分请求。

Evaluation Judge 的输出预算固定为 `max_completion_tokens=8192`，上下文上限固定为
65,536。`_4_evaluation.py` 会先按固定顺序构造该臂所有实际 Judge prompt，
然后用同一服务的 `/tokenize`、同一 messages/chat template、
`add_generation_prompt=true` 和 `enable_thinking=false` 精确计数。只有每个
`prompt_tokens + 8192 <= 65536` 都成立时才发送第一个 completion；后面才
发现的超限也不能留下部分 Judge 结果。该 `vllm-chat-tokenize-v1` 算法、
全臂 token-count/hash 摘要、预算与上下文进入 scoring spec、runner checkpoint 和
final audit；禁止截断或在正式命令中扩大预算。

BERTScore 只能读取
`/data0/xyj/shengao/CoCoReviewBench/model/bertscore_roberta_large`，固定
`model_type=roberta-large`、`num_layers=17`、`rescale_with_baseline=false`。spec 会绑定
该目录的全部普通文件 inventory 与 tree ID；runner 通过封印的
`COCO_BERT_SCORE_MODEL_ROOT`、`COCO_BERT_SCORE_NUM_LAYERS` 使用它，不得从 Hub 下载、
改用缓存模型或自动推断层数。
该本地目录来源固定为 Hugging Face `FacebookAI/roberta-large` revision
`722cf37b1afa9454edce342e7895e588b6ff1d59`；revision 只用于可恢复 provenance，正式
运行身份仍以目录内 7 个普通文件的逐文件 hash 与 tree ID 为准。

随后 materialize/freeze 会把上述 scoring spec 的 path、SHA256、spec ID 和唯一 winner
run ID 一并冻结：

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
clean transition Git HEAD。同一 Formal run 的全部 `resume_*.json` 构成按 timezone-aware
`recorded_at` 严格递增的完整恢复审计链；每项绑定文件 SHA、前序 audit SHA、typed
checkpoint schema、原始/解析后 commit、仓库角色和 resume commit，整链再生成
`chain_id`。主仓 checkpoint（包括 legacy audit 内嵌的 identity-hardening commit）必须形成
线性祖先链并最终祖先于 `code_commit`，外部 CoCo checkpoint 必须最终祖先于冻结的外仓
HEAD；跨事件还复核 structured-output checkpoint、CLAR 26→27 进度和历史进程起点连续性。
因此 `code_commit` 不得把这段多次恢复的执行史误归因成 transition commit。
`primary_meta_enabled` 固定为 true，指的是最终系统包含
独立 Meta synthesizer，并不改变 evolved Program 是否调用 S2 的自主权。

过渡还会绑定完整 CoCo scoring spec，并据此复核独立 `CoCoReviewBench` 仓库的 clean
Git commit、仓库根路径、`scripts/run_official_candidate.py` 路径与 SHA256；该文件也进入
freeze spec 的 source bindings。`coco-eval` 在外部 subprocess 前后都会复核这份身份，
并拒绝 dirty worktree、commit漂移、runner内容漂移或绕过 frozen runner 的命令。

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

winner batch 的进程完整退出并释放 GPU 0 embedding 后，定向停止
`ar0831_coco_split_gpu0` 的8K/0.40预留服务，将其重启为 scoring spec 冻结的正式身份：
GPU 0、`Qwen3-8B-Split`、端口13099、`max_model_len=40960`、
`max_num_seqs=8`、`gpu_memory_utilization=0.90`。GPU 1 Classify 继续保持端口13098、
`max_model_len=32768`、`max_num_seqs=16`、`gpu_memory_utilization=0.90`；禁止
全局 `pkill` 或无故重启 Classify。

然后使用下面的唯一 seal 命令物化两份 candidate input。如果当前27B还是生成
Review 时启动的旧进程，命令会在 candidate 完整落盘后因 restart barrier
fail closed，不会提交 pre-score seal。这是预期的两步过渡：

1. 确认 Formal Evolution、winner Review 及所有27B Reviewer client 均已退出，排他
   Reviewer lease 已释放；
2. 确认 scoring spec 和 meta/concat 两份 candidate input 已固定，并在其中最新
   mtime 之后至少等待2秒；
3. 定向停止 GPU 3、6 的旧27B listener/engine，用同一封印参数重启为
   Judge 实例；进程重启是清空 KV/prefix cache 的强制边界；
4. 用同一命令恢复，不删除已物化的 candidate，不更改 run ID。

新 Judge listener 和 engine 的启动时间必须都晚于三份绑定输入（scoring spec
和两份 candidate）的最新 mtime 至少2秒。只在服务启动后等待2秒不能弥补
“服务启动得太早”的身份错误；应先固定输入、等待间隔，再启动新进程。
在 pre-score seal 提交之前，控制器还会在新 Judge 实例持有共享 lease 且
idle 时检查 `/metrics`：自该实例启动以来的 running/waiting、generation
request/token 和 prefix-cache 流量必须全为零，以证明重启后没有再被 Reviewer
请求污染。该零流量门禁只用于 seal creation；两臂评分启动后 metrics 非零
是正常现象，运行中连续性检查不应再要求它们为零。

```bash
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  seal-coco-pre-score \
  --frozen-system artifacts/research_v2/frozen/formal-evolution-v1.system.json \
  --winner-run-id formal-coco1300-winner-v1 \
  --output artifacts/research_v2/frozen/formal-coco1300-pre-score-seal-v1.json
```

seal 会 fail closed 地验证 Split、Classify 与 Judge 三个运行中服务的完整冻结
身份，而不只检查 `/v1/models`；任一 GPU、进程参数、模型树、软件版本、
endpoint 或容量参数不一致时都不生成 seal。它还会从 frozen system 和
winner batch 完整重建 meta/concat 两份1,300条 candidate input，并验证
exact-set、内容链、独立 output roots 与六个互异 cache namespaces。8K预留 Split、
旧27B进程、未释放的 Reviewer lease 或未满足2秒的 restart barrier 都不能通过该步骤。

seal 成功后，meta 与 concat 两个 `coco-eval` 可在独立 tmux 中同时启动，也可排队执行。
两臂必须使用 scoring spec/seal 分配的不同 output root 和 Split/Classify/Evaluation cache
identity；两者可以共享三个 endpoint，让 vLLM scheduler 排队，但不能共享
staging、缓存或结果文件。此时 Formal 与 winner Review 均已冻结，27B 不再执行
Review，而是作为 CoCo Judge 工作。两臂分别固定4个 Judge client request，同时
运行时总计8个；如果只运行一臂，不得临时将它扩到8路。`coco-eval` 的执行参数
必须全部从 seal 与 scoring spec 注入，不得用 shell 环境临时覆盖 model、worker、
effort、benchmark 或 cache version。

正式 Evaluation 不需要任何 API 凭据或 proxy。控制器为外部 runner 构造完整的
proxy-free 白名单环境，ambient API key 或 proxy 变量都不是正式身份的一部分。
已完成 stage 会按冻结 checkpoint 恢复；
任一服务进程、lease generation 或 Judge instance 变化都会使旧 cache 身份失配并
fail closed。

科学解释上，CoCo Judge 与 Reviewer 属于同一 Qwen3.8-27B 模型族，这是预注册、
封印且对两臂完全相同的 self-judge。它可用于当前 meta/concat 在同一 Judge
条件下的相对对比，但得分不能与历史 GPT-judged Table-1 数值直接横比或
合并成同一序列。报告时必须显式标注 Judge 模型、revision、temperature、上下文和
thinking 设置。

```bash
# meta arm（可与下一条放入两个独立 tmux）
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python -u \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  coco-eval \
  --pre-score-seal artifacts/research_v2/frozen/formal-coco1300-pre-score-seal-v1.json \
  --final-mode meta \
  -- \
  /data0/xyj/shengao/AgentReview_0831/artifacts/research_v2/runtime/coco_eval_env_v1/bin/python3.10 \
  -s -B \
  /data0/xyj/shengao/CoCoReviewBench/scripts/run_official_candidate.py

# concat arm
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python -u \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  coco-eval \
  --pre-score-seal artifacts/research_v2/frozen/formal-coco1300-pre-score-seal-v1.json \
  --final-mode concat \
  -- \
  /data0/xyj/shengao/AgentReview_0831/artifacts/research_v2/runtime/coco_eval_env_v1/bin/python3.10 \
  -s -B \
  /data0/xyj/shengao/CoCoReviewBench/scripts/run_official_candidate.py
```

CLI 不接受 winner root、CoCo root 或 output root 参数；这些路径只能从 seal/spec 推导，
从而避免把某一臂指向另一臂的 namespace。

两臂都产生 complete `run.json` 和完整 Evaluation artifact 后，执行最终审计：

```bash
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  audit-coco-final \
  --pre-score-seal artifacts/research_v2/frozen/formal-coco1300-pre-score-seal-v1.json \
  --output artifacts/research_v2/frozen/formal-coco1300-final-audit-v1.json
```

只有 final audit 自哈希 artifact 成功提交后，全流程才算完成；单臂完成、进程返回0或
仅有部分指标都不构成正式结果。

服务释放后可用离线门禁重复验证 audit 自哈希以及 seal、spec、两臂 run、candidate 和
全部评分 artifact 的绑定；默认不会要求 GPU 0/1 服务仍在线。需要同时复核在线服务时
显式增加 `--live-service`：

```bash
PYTHONPATH=AgentReview \
/data0/xyj/miniconda3/envs/env_shengao/bin/python \
  -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  validate-coco-final \
  --audit artifacts/research_v2/frozen/formal-coco1300-final-audit-v1.json
```

外部 CoCo runner 同样采用 output-root scoped 非阻塞锁。Split、Classify 与必需
Evaluation 各自在 `.candidate_official_stages/<stage>/` 写入输入、设置、输出 SHA 和
checkpoint ID；只有完整验证后才通过硬链接发布 fixed artifact。相同身份恢复会跳过
已完成阶段，不再请求模型；失败 attempt 保留在隐藏 staging 中。任一输入、endpoint、
model、worker、effort、cache version 或 evaluation 设置改变都会在新调用前失败，必须
使用新的 scoring arm output root，而不是覆盖旧产物。

Evaluation 不能只凭进程返回0或输出一个 JSON object 判定成功。外部 runner 与
AgentReview 边界会独立要求完整 Table-1 schema；BLEU、ROUGE-L、BERT、五项 judge、
Average、Paper 与 Complete 的 `paper_counts` 均必须精确等于该臂冻结的1,300 IDs，分数
必须为有限数，category coverage 也必须覆盖1,300。任何 judge skip、空 `{}` 或部分输出
都会保留为失败 evidence，不能进入 complete contract。

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
