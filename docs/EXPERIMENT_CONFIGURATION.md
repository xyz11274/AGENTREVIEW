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
- 任意两次 HTTP 请求的启动时间至少间隔 1 秒，即最多 1 request/s；
- retry 也在同一串行调用内完成，不形成额外并发。

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
    build-s2 --dataset development
'
```

每条成功结果立即写入 `*.jsonl.parts/`。进程中断后用同一命令恢复，不删除 parts，也不要另开第二个恢复进程。Semantic Scholar 对零命中查询会返回 `total: 0` 且省略 `data`；该响应应冻结为空结果。`total > 0` 却没有 `data` 仍按异常退出，防止冻结损坏响应。

## CoCo 两个服务与 Review 并行

CoCo Split 和 Classify 是两个独立的单 GPU、TP=1 vLLM endpoint。服务端启动参数目前没有显式 `max_num_seqs`，因此服务端实际活跃序列数由 vLLM 默认值和 KV 容量动态决定；客户端 semaphore 不是 GPU 真并行数。

旧本地配置中的 Split 512、Classify 1024 是 HTTP 在途上限。安全的 official candidate runner 默认收紧为 Split 64、Classify 128，并允许显式覆盖；它们仍是排队上限，不是“512/1024 路 GPU 同时推理”。部署服务时应另外显式冻结 `--max-num-seqs`，再用 metrics 确认实际运行/等待数。

一条实验臂 A 的完整 1,300 篇 CoCo scoring 可以与另一条实验臂 B 的 Review 同时排队和运行，条件是：

- CoCo 与 27B 使用不同物理 GPU；
- CoCo 只连接预先启动的 Split/Classify endpoint；
- 不运行会执行全局 `pkill -f vllm...` 的旧生命周期脚本；
- 每条 CoCo 臂仍先满足严格 1,300 ID exact-set barrier；
- CoCo 输出不进入 Review、Internal Evaluation 或 Evolution。

`scripts/run_official_candidate.py` 不负责启动、停止或清理任何 vLLM 服务，因此不会让专用 27B 在 CoCo 期间休息，也不会误杀其他实验服务。

## Run ID 与不可变 artifact

正式 canonical bootstrap 保留 run ID `canonical-bootstrap-v1`。smoke、`--limit` 或 `--no-s2` 必须使用独立 run ID，例如 `qwen-live-smoke-20260901-01`。

Review raw、完整 Review、Memory snapshot 和 S2 parts 是不可变 artifact；同路径只允许字节等价/内容等价重放。模型身份、generation 参数、S2 pool 或选中论文集合改变后，应使用新 run ID。恢复正式任务时使用原 run ID；runner 会验证完整 invocation identity 后跳过已完成角色。

不要同时启动两个相同 run ID 的 bootstrap 进程。正式 runner 使用 run-scoped lock；第二个进程应立即失败，而不是把模型并发翻倍。

## 当前 tmux 与日志

```text
27B service:  ar0831_qwen_gpu36_tp2c8_64k
formal run:   ar0831_formal_canonical_c8
S2 build:     ar0831_s2_coco1300（development 275/275 完成后顺序启动）
service log:  artifacts/research_v2/runtime/qwen_tp2_gpu36_64k/qwen.log
development S2 log: artifacts/research_v2/runtime/s2_development/build.log
CoCo S2 log:  artifacts/research_v2/runtime/s2_coco1300/build.log
formal log:   artifacts/research_v2/runtime/canonical_bootstrap_v1/stderr.log
```

查看正式进度：

```bash
tail -f artifacts/research_v2/runtime/canonical_bootstrap_v1/stderr.log
```

若正式进程意外退出，在 endpoint 恢复后使用同一命令和默认 run ID 重启；已验证的完整角色会直接 resume：

```bash
$PYTHON -m agentreview.research_v2 \
  --config AgentReview/configs/research_v2_formal_v1.json \
  bootstrap-corpus --max-workers 8
```

生成资产位于 `artifacts/research_v2/` 且被 Git ignore；源码配置、commit、manifest SHA256 和运行日志共同构成归档身份。
