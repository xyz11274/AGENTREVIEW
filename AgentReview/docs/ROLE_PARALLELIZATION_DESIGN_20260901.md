# 五 Specialist 角色并行化设计与审计（2026-09-01）

## 1. 文档状态与边界

本文是针对后续 reduced/full-flow 实验的独立设计，不是当前 formal full 实验的运行变更。

- 当前正在运行的实验不停止、不重启、不热修改。
- 当前运行使用的 checkpoint、artifact、配置和启动脚本不改写。
- 本次只新增本文档；不修改 Python 代码，不启动新进程，不执行实验。
- 方案确认后，应在单独的 parallel 实验分支实现和验证。

目标运行形态：

- 一个 Evolution Python 编排进程；
- `role_workers=4`；
- `QUAL / CLAR / SIGN / ORIG / POL` 五个 specialist 作为五个独立 role job，最多四个同时执行，第五个在进程内队列等待；
- 一个 GPU 驻留的 `Qwen3-Embedding-8B` 模型实例；
- embedding 调用在进程内串行；
- 每个角色继续拥有独立的 pool、candidate、controller checkpoint 和 evaluator checkpoint。

“单 Python 进程”指只有一个 Evolution 编排进程。现有 sandbox/bubblewrap 为隔离候选程序而创建的短生命周期子进程仍须保留；它不属于重复启动 specialist 或 embedding 服务。

## 2. 设计不变量

并行化只改变五个 role job 的外层调度，不改变单个角色内部的 MSTAR 状态机。以下语义必须保持：

1. selection、fitness、mutation、compile-fix、winner selection 的实现和调用顺序不变；
2. 每个角色内部的三个结构 seed 仍按当前顺序执行；
3. 每个角色内部的 RNG、pool、candidate lineage、A/B 数据和 memory state 不与其他角色共享；
4. checkpoint schema、checkpoint identity 及已提交记录的恢复规则不变；
5. `role_workers` 只属于 suite orchestration，不进入 `EvolutionConfig`，也不进入 evolution protocol/search controller identity；
6. 共享的只有只读模型客户端或明确具备并发保护的基础设施对象，不共享角色可变状态；
7. 角色完成顺序不得改变 suite manifest 中的规范角色顺序。

换言之，目标等价关系是：

> 同一角色的 serial execution 与 parallel suite 中该角色的 execution 使用相同输入、相同 MSTAR 配置、相同 checkpoint identity 和相同角色目录；差异只在多个角色的墙钟时间重叠。

## 3. 当前代码审计

以下结论基于 2026-09-01 当前 checkout 的静态审计。

### 3.1 Specialist role 调度入口

入口位于 `memory_evolution/cli.py`：

- `evolve-specialists` 子命令定义：约第 931–945 行；当前没有 `--role-workers` 参数。
- suite 调度：约第 2079–2129 行。
- 当前逻辑依次遍历 `SPECIALIST_TASKS`，对一个角色的所有 seed 调用 `_run_task(...)`，完成该角色 winner promotion 后才进入下一个角色。
- `suite_manifest.json` 在五个角色全部结束后写出，但当前写法不是原子替换。

因此当前是严格的角色级串行流程：

```text
QUAL(all seeds) -> CLAR(all seeds) -> SIGN(all seeds)
                -> ORIG(all seeds) -> POL(all seeds)
                -> suite manifest
```

### 3.2 单角色准备和 Evolution loop

`memory_evolution/cli.py` 的 `_run_task(...)`（约第 1166–1386 行）负责：

1. 解析该角色的 episode/validation 数据；
2. 执行 formal gate；
3. 创建该角色的 task/reflector/judge toolkits；
4. 创建 selection embedding client；
5. 校验 selection/runtime embedding identity；
6. 构造 `EvolutionConfig`；
7. 调用 `memory_evolution.search.run_evolution(...)`。

核心 Evolution loop 位于 `memory_evolution/search.py` 的 `run_evolution(...)`（约第 419 行起）。其内部按当前规则完成：

- controller identity 校验与恢复；
- A/B 选择及提交；
- 三个结构 seed 的评估与提交；
- pool 初始化和维护；
- mutation、compile-fix、candidate evaluation；
- fitness/admission；
- winner selection 和完成态提交。

并行设计不进入或重写这个 loop，而是把五次彼此独立的 `_run_task(...)` 调用放到有界 executor 中。

### 3.3 Embedding 初始化与对象生命周期

相关位置：

- `memory_evolution/cli.py` `_task_toolkit(...)`（约第 282–319 行）：创建角色本地 `MemoryToolkit`，并为 runtime embedding 调用 adapter factory；
- `_selection_embedding_client(...)`（约第 322–333 行）：创建 formal selection cache 视图；
- `_toolkits(...)`（约第 336–348 行）：创建 task/reflector/judge 三类角色本地 toolkit；
- `memory_evolution/embedding_adapter.py` `CachedDenseEmbedding`（约第 60–102 行）：每个 adapter 对象持有实例级 `threading.Lock`；
- 同文件 `embed(...)`（约第 417–502 行）：cache 查询、必要的模型推理及 cache 写入均在该实例锁内；
- 同文件 `create_qwen3_embedding(...)`（约第 799–859 行）：通过进程全局 `_INSTANCES` 按模型、cache、device、namespace 等参数复用 adapter。

关键结论：

- 同一个 runtime adapter 对象的 `embed(...)` 已经串行化，适合由四个角色共享；
- factory 的首次 `if key not in _INSTANCES` 创建路径没有全局并发锁，因此不能让四个 worker 同时懒初始化；
- 应在主线程启动 executor 之前预创建 runtime embedding，并把同一个对象引用显式注入所有角色；
- selection cache 与 runtime cache 的访问语义不同：selection 是 formal 只读/fail-on-miss 视图，runtime 是可写且必要时调用模型的视图。允许保留两个 adapter 视图，但底层只能有一个 GPU 驻留的 Qwen3-Embedding-8B 模型实例；这不等于部署两个 embedding 模型。

不建议在第一版并行实现中修改 `embedding_adapter.py`。当前实例锁已经提供所需的请求串行化，而且其 SHA 被纳入 Evolution 配置身份；修改该文件会无谓增加旧 checkpoint identity 不兼容的风险。

### 3.4 角色可变状态隔离

`agentreview/reviewer_runtime/toolkit.py` 中每个 `MemoryToolkit` 拥有自己的：

- vector collections；
- operation/thread-local 状态；
- role resource counters；
- memory reset 生命周期。

因此每个 role worker 必须创建自己的 task/reflector/judge toolkit、LLM backend、sandbox/session 和结果对象，只共享 embedding client 引用。禁止把同一个 `MemoryToolkit` 或同一个角色 pool 交给多个 role worker。

### 3.5 Checkpoint 保存与恢复

Search controller：

- `memory_evolution/search.py` `search_controller_identity(...)`（约第 328–349 行）由 `EvolutionConfig`、seed program SHA、episode IDs 和 validation IDs 等语义输入计算身份；
- `run_evolution(...)` 约第 456–472 行在 formal resume 时加载 controller，校验版本和 identity，并按 committed count 截断未提交的 JSONL 尾部、恢复 RNG；
- `_atomic_write_json(...)`（约第 141–148 行）使用临时文件加 `os.replace` 原子提交；
- candidate evaluator checkpoint 位于每个角色输出根下的 `checkpoints/<program>/<replicate>.json`；
- `search_inflight` 保存待恢复 child，并在恢复时校验 program SHA；
- controller 只在相应 seed/child 完成并正式提交后推进。

历史回放 checkpoint：

- `agentreview/history_checkpoint.py` 负责 identity、已完成前缀和 memory snapshot hash 校验；
- 保存顺序为先写单条 history record，再以临时文件加 `os.replace` 原子更新 checkpoint；
- `memory_evolution/evaluator.py` 恢复 A replay checkpoint，按时间顺序继续 A；A 完成后冻结 snapshot，B item 从同一冻结状态独立评估。

当前 role 输出目录天然形成隔离边界，例如：

```text
RUN_ROOT/
  QUAL/seed_<N>/...
  CLAR/seed_<N>/...
  SIGN/seed_<N>/...
  ORIG/seed_<N>/...
  POL/seed_<N>/...
```

只要并行实现保持这些路径和语义配置不变，各角色不会写入同一 controller/candidate/history checkpoint。

## 4. 并行后的架构

### 4.1 推荐模块边界

新增一个小而深的 suite orchestration 模块：

```text
memory_evolution/specialist_orchestrator.py
```

它只负责角色任务的有界并发、共享资源注入、失败传播和确定性聚合。它不理解或实现 MSTAR selection/fitness/mutation。

建议接口概念如下，最终命名可在实现审查时调整：

```python
@dataclass(frozen=True)
class SharedEvolutionResources:
    runtime_embedding: object
    selection_embedding: object
    coco_adapter: object

@dataclass(frozen=True)
class RoleJob:
    task_id: str
    seeds: tuple[str, ...]
    primary_seed: str
    output_root: Path
    args: argparse.Namespace

def run_specialist_suite(
    jobs: Sequence[RoleJob],
    *,
    role_workers: int,
    shared_resources: SharedEvolutionResources,
    run_task: Callable[..., RoleResult],
) -> dict[str, RoleResult]: ...
```

该接口形成一个明确的 seam：外层决定“何时运行哪个角色”，现有 `_run_task/run_evolution` 决定“一个角色如何演化”。这样并行调度实现细节具有较高 locality，不会渗入 MSTAR 核心。

### 4.2 调度图

```text
                         single Python orchestrator
                                    |
        main-thread preflight + shared resource construction
                                    |
                  ThreadPoolExecutor(max_workers=4)
                   /           |          |          \
              worker-1     worker-2   worker-3    worker-4
                QUAL          CLAR       SIGN         ORIG
                   \           |          |          /
                    \---------- bounded queue -------/
                                  POL

Every role owns:                  Shared process resources:
- role args clone                - one runtime embedding adapter/model
- task/reflector/judge toolkit   - serialized embed() lock
- LLM backend/session            - selection cache view
- A/B data                       - CoCo adapter/service clients
- pool/candidates/RNG
- ROLE/seed_N checkpoints
```

角色按 `SPECIALIST_TASKS` 的规范顺序提交。`role_workers=4` 时前四个角色可开始执行，第五个由 executor 排队；某个 worker 释放后第五个开始。实际完成顺序允许变化，但汇总顺序固定。

### 4.3 主线程初始化顺序

为避免懒初始化竞态和共享 `argparse.Namespace` 的可变状态竞态，顺序应固定为：

1. 解析 CLI 和校验 `1 <= role_workers <= 5`；
2. 在主线程执行一次 Qwen/CoCo/sandbox 必要 preflight；
3. 在主线程创建 runtime embedding adapter，触发并验证唯一底层模型实例；
4. 在主线程创建 selection embedding cache 视图和 CoCo adapter；
5. 冻结共享资源容器；
6. 为每个角色复制独立的 `argparse.Namespace`，禁止 worker 共享可变 `args`；
7. 构造五个互不重叠输出目录的 `RoleJob`；
8. 创建 `ThreadPoolExecutor(max_workers=role_workers)` 并按规范角色顺序提交；
9. worker 调用现有单角色 `_run_task(...)`，并显式接收共享资源；
10. 主线程收集结果，按 `SPECIALIST_TASKS` 顺序执行 winner promotion/汇总；
11. 仅当五个角色全部成功时，原子写出最终 suite manifest。

`_prepare_llm_runtime(...)` 当前会在 `args` 上写入 preflight key/result。并行实现必须先在主线程完成 preflight，再复制已准备好的 namespace；不能让多个 worker 同时修改同一 `args`。

### 4.4 Embedding 串行化约束

运行时必须同时满足以下断言：

- 所有 role toolkits 收到的 runtime embedding 对象具有相同 Python object identity；
- runtime embedding factory 在 worker 启动前只调用一次；
- instrumentation 测得 `embed()` 最大活跃调用数为 1；
- GPU 进程/显存检查只出现一个 Qwen3-Embedding-8B 模型实例；
- selection cache 仍保持 formal read-only/fail-on-miss 语义；
- runtime cache 仍保持现有 namespace、写入和模型 fallback 语义。

不要通过为每个角色创建 adapter/model、复制 cache 文件或启动多个 embedding server 来换取并发。

### 4.5 Qwen 和 CoCo 并发边界

- Qwen3.8-27B 服务保持既有模型、TP 和 64K context；角色级并发上限为 4，与已验证服务容量对齐。
- role worker 并发并不保证四倍加速：Qwen GPU 饱和、串行 embedding、CoCo 服务和长上下文请求都会形成背压。
- CoCo Classify 本身已有内部线程池。若每个角色都保留 `COCO_CLASSIFY_WORKERS=4`，四个 role worker 理论上可能形成最多 16 路 classify 请求，超过现有安全容量。
- 第一版 parallel 实验建议把进程级 `COCO_CLASSIFY_WORKERS=1`，使四个角色合计最多约四路 classify 请求。它只改变请求调度，不改变 Split/Classify/Judge 或评分语义。
- 若后续容量测试证明服务可承受更高并发，再单独调整基础设施参数并记录在 run config；不要把它混入 MSTAR 算法参数。

## 5. 需要修改的文件（设计确认后的独立分支）

### 5.1 应修改/新增

| 文件 | 计划变更 | 是否涉及 MSTAR 语义 |
|---|---|---|
| `memory_evolution/specialist_orchestrator.py`（新增） | 有界 role executor、共享资源容器、结果收集、失败传播、规范顺序汇总 | 否 |
| `memory_evolution/cli.py` | 新增 `--role-workers`（默认 `1`）；主线程预初始化；向 `_run_task/_toolkits` 注入共享资源；suite 调度委托给 orchestrator；manifest 原子提交 | 否 |
| `tests/test_specialist_orchestrator.py`（新增） | 调度、第五角色排队、资源共享、失败行为、确定性汇总测试 | 否 |
| `tests/test_cli_modes.py` | CLI 默认值、范围校验、serial compatibility 测试 | 否 |
| `tests/test_embedding_cache.py` | 单实例和串行 `embed()` 并发测试 | 否 |
| `tests/test_search_loop.py` | serial checkpoint 由 parallel suite 恢复的兼容测试 | 否 |
| 独立 parallel launcher/config | 仅在新分支、新 run root 中设置 `role_workers=4` 和安全的 CoCo 并发 | 否 |

### 5.2 第一版明确不修改

- `memory_evolution/search.py`
- `memory_evolution/evaluator.py`
- `agentreview/history_checkpoint.py`
- `memory_evolution/embedding_adapter.py`
- selection、fitness、mutation、compile-fix、winner 实现
- 当前 formal full 的 launcher/config/run root/checkpoint/artifact

不修改这些文件既缩小审计面，也避免相关源文件 SHA 或配置身份发生变化，从而提高 checkpoint 兼容性。

## 6. Checkpoint 兼容方案

### 6.1 Identity 规则

`role_workers` 是 suite 的执行容量，不是候选程序或 MSTAR protocol 的语义输入。因此：

- 不加入 `EvolutionConfig`；
- 不加入 `evolution_protocol_config_identity(...)`；
- 不加入 `search_controller_identity(...)`；
- 不加入 candidate evaluator identity；
- 不改变 episode/validation ID、seed program SHA、模型配置和 role 输出路径。

由此，使用 `role_workers=1` 创建的角色 checkpoint 可由 `role_workers=4` 的 suite runner 恢复；反向也成立。若数据、程序、模型或算法配置真的变化，现有 fail-closed identity 校验仍应拒绝恢复。

### 6.2 路径规则

并行 runner 必须沿用当前路径：

```text
RUN_ROOT/<ROLE>/seed_<N>/...
```

启动前校验五个角色的规范化输出根互不相同。不得在多个 future 中使用同一个 role output directory，也不得为了并行把 checkpoint 合并成进程级单文件。

### 6.3 提交与失败规则

- 每个角色继续由现有原子 controller/history checkpoint 独立提交；
- worker 只返回不可变 `RoleResult`，不直接修改共享 suite result 字典；
- winner promotion 由主线程执行，路径仍位于对应角色目录；
- 五个角色全部成功后，主线程把 suite manifest 写入同目录临时文件并 `os.replace`；
- 任一角色失败时，不生成“完整成功”的 suite manifest；其他角色已经提交的 checkpoint 保留，可在下次 resume 时跳过已提交工作；
- 可另写 orchestration-only status JSONL 记录 role started/completed/failed，但它不能替代任何角色 controller，也不能参与 fitness/winner；如采用，必须使用独立新文件并逐行追加/刷新。

### 6.4 中断恢复

SIGINT 由主线程处理：

- 尚未开始的第五个 future 可以取消；
- 不强制杀死正在写 checkpoint 的 worker；
- 当前候选/episode 依赖既有原子提交边界，重启后由 controller/history checkpoint 恢复；
- 不以进程存活或 future 完成代替 committed checkpoint 作为进度真相。

Python 无法安全地强制终止运行中的线程。实现阶段必须用故障注入测试明确 SIGINT 行为；如果需要更细粒度的 cooperative cancellation，也只能在候选/阶段边界检查，不能在 selection/fitness/mutation 中间改变状态机。

## 7. 风险分析与缓解

| 风险 | 影响 | 缓解/验证 |
|---|---|---|
| 四个 worker 并发懒初始化 embedding | 重复模型实例、GPU OOM | 主线程预创建并注入；断言 object identity；监控只出现一个模型实例 |
| 共享可变 `args` | preflight/cache key 被竞态覆盖 | preflight 一次；每角色复制 namespace；worker 不回写共享 args |
| 错误共享 `MemoryToolkit` | vector/memory/计数跨角色污染 | 每角色独立三类 toolkit 和 session；仅共享 embedding client |
| CoCo 内外层并发相乘 | GPU5 过载、超时、失败率上升 | 第一版 `COCO_CLASSIFY_WORKERS=1`；容量 smoke 后再调整 |
| Qwen 四路长上下文饱和 | latency 激增或服务错误 | `role_workers=4` 上限；64K 四并发单独 capacity gate；保留背压/重试证据 |
| 线程日志交错 | 难以归因失败 | 每条 orchestration event 带 role/seed；角色 artifact/checkpoint 仍为权威证据 |
| worker 完成顺序不确定 | suite manifest 顺序漂移 | 主线程按 `SPECIALIST_TASKS` 规范顺序聚合和 promotion |
| 一个角色失败后生成部分 manifest | 下游误判为完整实验 | 只在五角色成功后原子发布最终 manifest；失败态使用不同文件/状态 |
| 修改 embedding/search 源码 SHA | 旧 checkpoint identity 不兼容 | 第一版不修改这些文件；兼容测试比较 identity hash |
| 主线程 signal 与 worker thread 交互 | 中断时存在在途 API 请求 | 只在安全边界取消；依赖原子 checkpoint 恢复；做 SIGINT 故障注入 |
| 非主线程的外层 signal timeout 不可用 | 某些线程内步骤超时保护变化 | 保留 sandbox 子进程自身 timeout；做卡死/超时集成测试，不以猜测替代证据 |
| 并发改变服务侧调度 | 生成时延或非严格确定性变化 | 固定所有语义配置和 seed，记录并发运行身份；先作为方向验证，不直接视作论文最终可比结果 |

## 8. 测试与准入方案

设计确认并在独立分支实现后，按以下顺序验证；任何一步失败都不启动 reduced/full-flow。

### 8.1 单元测试

1. `role_workers=1` 保持当前五角色串行顺序和结果结构；
2. `role_workers=4` 的最大活跃 role job 数为 4，第五个在队列中等待；
3. 每个 role job 的数据、输出目录、toolkit、pool/result 对象互不相同；
4. 所有 role toolkit 的 runtime embedding object identity 相同，factory/model load 计数为 1；
5. 用带计数的 fake embedding 验证最大同时活跃 `embed()` 数为 1；
6. 一个角色写入/清空 vector collection 不影响其他角色；
7. completion order 被打乱时，最终结果仍按 `QUAL/CLAR/SIGN/ORIG/POL` 排列；
8. 一个角色故障时不发布完整 suite manifest，已完成角色 artifact 保留；
9. worker 修改自己的 args 不影响其他角色或主线程；
10. `role_workers=1` 与 `4` 的 protocol/search controller identity 完全一致；
11. CoCo fake service 测得总 classify 活跃请求数不超过配置上限。

### 8.2 Checkpoint resume 测试

1. serial 运行在 A history 中途停止，再以 parallel suite 恢复，必须从准确的下一条继续；
2. serial 已提交一个结构 seed 后以 parallel 恢复，已提交 seed 不重复评估；
3. serial 留下 `search_inflight` child 后以 parallel 恢复，继续同一 program SHA；
4. 已完成角色在 parallel resume 中直接恢复结果，不重放已提交候选；
5. 数据、seed program 或模型 identity 不匹配时继续 fail closed；
6. 对比恢复前后 `scores.jsonl`、`lineage_budget.json` committed count 与 controller 声明一致；
7. 对 serial/parallel 相同角色的 controller identity、候选 SHA 和 winner 汇总字段做逐项比较。

### 8.3 集成和 GPU gate

1. 使用 fake LLM/embedding 的五角色完整 suite，验证单进程、四 worker、第五排队；
2. 使用全新临时 run root 做最小真实模型 smoke，不接触 formal full 路径；
3. 用 GPU 进程和显存证据确认只有一个 Qwen3-Embedding-8B 模型实例；
4. 对四个真实长上下文请求做 Qwen capacity gate，记录成功率、finish reason、P50/P95 latency 和峰值显存；
5. 对 CoCo Split/Classify/Judge 做四角色交叠 smoke，确认无并发乘法导致的错误；
6. 在 A replay、seed evaluation、mutation child 三种阶段分别注入 SIGINT/异常并恢复；
7. 运行现有完整 test/gate、并行专项测试、`git diff --check` 和 launcher shell syntax check；
8. 只有所有 gate 通过后，才创建新的 parallel reduced/full-flow run root 并启动。

### 8.4 验收证据

实现阶段至少保留：

- 进程树：一个 Evolution 编排 Python 进程；
- role event 时间线：证明最大四个 active role 和第五排队；
- embedding factory/load count、对象 identity 和最大 active embed count；
- GPU 进程、显存及 Qwen/CoCo 服务错误统计；
- 五个角色独立 checkpoint 路径和 controller identity；
- serial checkpoint -> parallel resume 的 committed count 对照；
- 最终 suite manifest 的规范角色顺序及原子发布证据；
- `role_workers=1` 回归结果。

## 9. 建议的实施顺序

在本文设计获得确认后：

1. 确认当前 formal full 实验状态，但不停止或修改它；
2. 从确认的代码基线创建日期命名的独立 parallel 分支；
3. 先实现 orchestrator 和 fake-resource 单元测试；
4. 再改 CLI 做共享资源显式注入，保持默认 `role_workers=1`；
5. 完成 checkpoint resume 和失败注入测试；
6. 使用全新 run root 做真实 GPU smoke；
7. 通过全部 gate 后，单独生成 parallel reduced/full-flow 配置并运行。

## 10. 审计结论

该目标可以在不改 MSTAR 核心语义的前提下实现。最安全的 seam 是现有 `_run_task(...)` 外层的 suite orchestration：每个角色仍运行原封不动的 Evolution loop，四个 role worker 只提供墙钟时间重叠。

第一版实现的关键约束是：主线程预创建并注入唯一 runtime embedding 实例、每角色保留独立可变状态和输出根、`role_workers` 不进入任何算法/checkpoint identity、最终 suite manifest 只由主线程按规范顺序原子提交。这样既能让第五角色自然排队，也能保持现有 serial checkpoint 的恢复兼容性。

## 11. 实施状态（2026-09-01）

本设计已在独立分支 `parallel-role-workers-20260901`、独立 worktree
`/data/xyj/shengao/AgentReview_0901_parallel` 实施，未在原 0831 工作目录热修改。

已完成：

- 新增 `memory_evolution/specialist_orchestrator.py`，以小 interface 封装四 worker
  有界调度、角色内 seed 串行、第五角色排队和规范顺序结果聚合；
- `evolve-specialists` 新增 `--role-workers`，默认 `1`，合法范围 `1..5`；
- 主线程在 role executor 启动前创建 Qwen/CoCo preflight 状态、runtime embedding、
  selection embedding 和 CoCo adapter；
- 五个 role 使用五份独立 `argparse.Namespace`、独立 toolkit、pool、candidate 和
  checkpoint 目录，仅共享 process-scoped adapter 引用；
- suite manifest 只在所有 role 成功后由主线程通过临时文件加 `os.replace` 原子发布；
- manifest 记录 `role_workers`、规范角色顺序和 embedding 串行化声明，但这些字段不进入
  `EvolutionConfig`、formal protocol identity 或 search controller identity；
- 未修改 `embedding_adapter.py`、`coco_adapter.py`、evaluator、history checkpoint 或
  MSTAR loop。并行分支中的 `search.py`、三个 seed 和 adapter SHA 与暂停的 0831 运行一致；
- 新增 `scripts/launch_reduced_fullflow_parallel.sh`，设置 `role_workers=4`、
  `COCO_CLASSIFY_WORKERS=1`。正式启动前重新计算 0831 reduced-v2 QUAL controller
  identity；完全一致时复用原 run root、已提交 A/history checkpoint 和 runtime embedding
  cache，未提交 B 与其余角色重新运行。parallel runtime log 与最终 summary 留在 0901
  worktree；可通过 `PARALLEL_*` 环境变量切换到全新输出根；
- 新增调度、第五角色排队、规范顺序、角色 args 隔离、共享 adapter、embedding 串行和
  identity 不变测试。

验证结果：

- 专项测试通过；
- 全仓 `236/236` tests passed；
- launcher `bash -n` 通过；
- launcher `PARALLEL_PREFLIGHT_ONLY=1` 通过；五角色 A300/B50 selection embedding
  全部为 frozen-cache hit，formal readiness gate 通过；
- `git diff --check` 通过；
- 本节验证时尚未启动 parallel Evolution 或创建正式 suite artifact；实际启动状态以
  `EXPERIMENT_MODIFICATION_LOG_20260901.md` 和 runtime `stages.tsv` 为准。
