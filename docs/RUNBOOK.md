# AgentReview 运行手册

最近核验：2026-08-31

当前门禁：[STATUS.md](STATUS.md)

架构说明：[ARCHITECTURE.md](ARCHITECTURE.md)

本文给出代码中真实存在的入口。Core 正式模板表达目标执行顺序；当前仓库先完成 STATUS 中的 G2 前置项。

## 1. Shell 基线

从 Python 工程根目录执行：

~~~bash
cd /data0/xyj/shengao/AgentReview_0827/AgentReview
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
~~~

确认解释器与入口：

~~~bash
$PY --version
$PY -m memory_evolution.core_cli --help
~~~

运行时依赖：

~~~bash
$PY -m pip install -r requirements-runtime.txt
~~~

Viewer 和旧 provider 的附加依赖位于 requirements-optional.txt 与 evolution_viewer/requirements.txt。

## 2. Core v1 G1 合成检查

创建独立临时输出目录：

~~~bash
CORE_SMOKE_DIR=$(mktemp -d -t agentreview-core-smoke.XXXXXX)
$PY -m memory_evolution.core_cli integration-smoke \
  --output-dir "$CORE_SMOKE_DIR"
~~~

成功输出包含：

- status=PASS；
- role_order 为 QUAL、CLAR、SIGN、ORIG、POL；
- production_composition_root=CoreV1FormalApplication；
- protected_loader_calls_before_authorization=0；
- protected_loader_calls_after_suite_freeze=0；
- protected_loader_calls_after_official=1；
- official_result.status=REPORT_ONLY。

该命令使用 synthetic dependencies，适合验证代码组合与回归。

## 3. Closure audit

~~~bash
FORMAL_ROOT=/absolute/path/to/formal-artifacts
CLOSURE_OUTPUT=/absolute/path/to/core_v1_closure.json

$PY -m memory_evolution.core_cli closure-audit \
  --smoke-report "$CORE_SMOKE_DIR/integration_smoke_report.json" \
  --formal-artifact-root "$FORMAL_ROOT" \
  --output "$CLOSURE_OUTPUT"
~~~

读取 JSON 中三个独立状态：

~~~text
G1_PRODUCTION_WIRING.status
G2_PREFORMAL_ARTIFACTS.status
G3_FORMAL_CLOSURE.status
~~~

closure-audit 的进程退出码只表达 G1。正式完成判断读取三个状态及各自 missing 列表。

## 4. Core 正式运行顺序

### 4.1 前置条件

开始正式 CLI 前准备：

- schema 为 research-validity-core-v1-runtime-config-v1 的配置；
- canonical 625-review Corpus 与五个 role index；
- B-local manifest 与 Frozen Evidence pool；
- 五份 role rubric；
- Core-compatible embedding query factory；
- Core-compatible Official runner；
- 已选 qwen_capacity 与完整 runtime limits；
- review_memory_indices 与 role_rubrics 的对象键按 QUAL、CLAR、SIGN、ORIG、POL 插入；
- OPENAI_API_KEY 环境变量；本地 OpenAI-compatible endpoint 也需要一个部署占位值；
- 冻结 require_bubblewrap；值为 true 时主机提供 Bubblewrap；
- G2 preflight 工件。

当前仓库尚无完整配置实例。字段合同见 ARCHITECTURE 4.3，加载时由 core_runtime.py 使用闭集校验。

### 4.2 配置校验

~~~bash
CORE_CONFIG=/absolute/path/to/core_runtime_config.json
FORMAL_ROOT=/absolute/path/to/new-formal-artifacts-20260831T000000
FORMAL_OUTPUT=$FORMAL_ROOT/formal
test ! -e "$FORMAL_ROOT"

# 本地 OpenAI-compatible endpoint 可使用部署约定的非敏感占位值
export OPENAI_API_KEY=dummy

$PY -m memory_evolution.core_cli validate-inputs \
  --config "$CORE_CONFIG" \
  --output-dir "$FORMAL_OUTPUT"
~~~

PASS 输出应包含 application schema、B-local SHA、Evidence pool SHA、Corpus manifest SHA 和固定 role order。

Core application 使用 os.replace 写固定工件名。正式运行始终分配全新的 FORMAL_ROOT；当前实现尚未提供拒绝覆盖的不可变提交门禁。

### 4.3 五角色运行

五次命令共享同一个 FORMAL_OUTPUT：

~~~bash
for ROLE in QUAL CLAR SIGN ORIG POL; do
  $PY -m memory_evolution.core_cli run-role \
    --config "$CORE_CONFIG" \
    --output-dir "$FORMAL_OUTPUT" \
    --role "$ROLE"
done
~~~

每个完成角色写入：

~~~text
$FORMAL_OUTPUT/programs/<program-sha>.py
$FORMAL_OUTPUT/role_winners/<ROLE>.json
~~~

当前 run-role 为每个进程建立独立 Qwen service。正式调度方案应与 frozen capacity/runtime profile 保持一致。

### 4.4 Suite 冻结

~~~bash
$PY -m memory_evolution.core_cli freeze-suite \
  --config "$CORE_CONFIG" \
  --output-dir "$FORMAL_OUTPUT"
~~~

该命令读取五份 durable winner，校验角色与 role_run_identity，并写入 final_suite.json。

### 4.5 Official

~~~bash
$PY -m memory_evolution.core_cli run-official \
  --config "$CORE_CONFIG" \
  --output-dir "$FORMAL_OUTPUT"
~~~

Official runner 在 Final Suite 校验和授权后接收 protected inputs。当前授权状态是进程内状态；正式执行还需要外层 controller 写入 authorization 与 immutable final report 工件。

当前 application 会把 official_result.json 写入 FORMAL_OUTPUT。closure 合同使用 FORMAL_ROOT/official/official_result.json。外层正式 controller 应在同一原子提交中完成 Official 目录路由、authorization.json 和 final_report.json；该 controller 进入代码后再把本命令作为 G3 正式完成入口。

### 4.6 External factory

部署环境也可以提供 MODULE:CALLABLE：

~~~bash
$PY -m memory_evolution.core_cli validate-inputs \
  --application-factory package.module:create_application \
  --output-dir "$FORMAL_OUTPUT"
~~~

Factory 接收关键字参数 output_dir，并返回实现 validate_inputs、run_role、freeze_suite、run_official、close 的 application。

## 5. G2 数据准备入口

每个脚本都有独立参数合同。先查看帮助，再使用冻结配置执行：

~~~bash
$PY scripts/materialize_core_v1_corpus_bootstrap.py --help
$PY scripts/preflight_core_v1_corpus_service.py --help
$PY scripts/build_core_v1_review_corpus.py --help
$PY scripts/supervise_core_v1_corpus_bootstrap.py --help
$PY scripts/materialize_core_v1_review_corpus.py --help
$PY scripts/build_core_v1_review_memory_index.py --help
$PY scripts/build_core_v1_capacity_workload.py --help
$PY scripts/benchmark_core_v1_qwen_capacity.py --help
$PY scripts/select_core_v1_capacity.py --help
~~~

执行顺序：

1. 从预注册数据准备流程取得并冻结 A selection；当前 bootstrap 脚本把它作为必需输入。
2. 运行 materialize_core_v1_corpus_bootstrap.py，物化 A manifest、625 个请求和结构上下文。
3. 启动并预检 Corpus Qwen service。
4. 运行 checkpointed no-memory Corpus builder。
5. 选择一种物化路径：让并行 supervisor 监督 builder 并在成功后调用 materializer，或在 builder 成功后手工运行 materialize_core_v1_review_corpus.py。
6. 审计 canonical Full Review、projection 和 fragment。
7. 按 QUAL、CLAR、SIGN、ORIG、POL 构建五个 exact-flat index。
8. 统一 index 生成器与 closure 的名称合同：生成器当前写 <role>.review_index.json，closure 要求 review_memory/<ROLE>.json。
9. 物化 B-local、Frozen Evidence 和 evaluator identity/smoke。
10. 构建真实 mixed workload，执行 capacity repetitions 并选择 capacity。
11. 生成 runtime profile 与 final preflight。
12. 生成 Core runtime config。
13. 统一 formal/official 输出提交与 closure 布局，再对同一 artifact root 运行 closure-audit。

Corpus r7 checkpoint 当前有 185/625 条已提交记录。继续该运行时应使用完全相同的 request/corpus identity、输出根和 provider identity；新的正式运行使用新的输出根。

## 6. 测试

主测试：

~~~bash
$PY -m unittest discover -s tests -v
~~~

Evolution Viewer：

~~~bash
$PY -m unittest discover -s evolution_viewer/tests -v
~~~

聚焦 Core CLI/组合根时，可按文件模式运行：

~~~bash
$PY -m unittest discover -s tests -p 'test_core_application_integration.py' -v
$PY -m unittest discover -s tests -p 'test_core_closure.py' -v
$PY -m unittest discover -s tests -p 'test_core_search.py' -v
$PY -m unittest discover -s tests -p 'test_core_formal.py' -v
$PY -m unittest discover -s tests -p 'test_role_evaluator.py' -v
$PY -m unittest discover -s tests -p 'test_final_suite.py' -v
~~~

主套件当前已知 5 个 error，见 STATUS。修复后重新记录真实执行数量与结果。

## 7. 冻结资产核验

Rubric：

~~~bash
sha256sum REVIEW_Request.md
~~~

期望 SHA-256：

~~~text
609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42
~~~

Temporal v2 实体计数：

~~~bash
wc -l \
  configs/coco_temporal_split_v2/historical_experience.jsonl \
  configs/coco_temporal_split_v2/evolution_validation.jsonl \
  configs/coco_temporal_split_v2/effectiveness_holdout.jsonl \
  configs/coco_temporal_split_v2/held_out_benchmark_test.jsonl \
  configs/coco_temporal_split_v2/paper_families.jsonl
~~~

当前期望为 2391、299、300、899、3889。Manifest 与 JSONL 需要同时进入同一资产闭包。

## 8. Legacy 入口

### 8.1 Standalone Reviewer

~~~bash
$PY -m agentreview.cli --help
$PY -m agentreview.cli review --help
~~~

该入口接受 legacy Memory Program，并写 candidate review artifacts。

### 8.2 Legacy M★

~~~bash
$PY -m memory_evolution.cli --help
~~~

可用子命令：

~~~text
prepare-specialists
prepare-test
prepare-meta
cache-meta-inputs
evolve-task
evolve-specialists
evolve-meta
evaluate
final-evaluate-suite
~~~

这些命令实现旧 Human/CoCo/META 语义，并使用 legacy seeds。

### 8.3 Direct Markdown review

~~~bash
$PY run_single_paper_review_cli.py --help
~~~

默认产生五个 specialist。完整 legacy META 输出通过 --enable_meta 显式开启。输入选择支持 paper_md_path、paper_md_dir 和 paper_context_manifest。

### 8.4 CoCo evaluation

~~~bash
$PY -m agentreview.coco_eval --help
$PY -m agentreview._coco_official_runner --help
~~~

它们是 legacy file/CLI Adapter。Core Official 需要独立二参数 callable Adapter。

## 9. Viewer

Direct-review Viewer：

~~~bash
$PY viewer.py
~~~

Evolution Viewer：

~~~bash
$PY -m evolution_viewer.app \
  --runs-dir /absolute/path/to/runs \
  --host 127.0.0.1 \
  --port 7862
~~~

后台管理：

~~~bash
./evolution_viewer/manage.sh start
./evolution_viewer/manage.sh status
./evolution_viewer/manage.sh logs
./evolution_viewer/manage.sh stop
~~~

## 10. Effectiveness screening

当前命令用于复现配置阻塞：

~~~bash
$PY -m effectiveness_screening.preflight \
  --config configs/effectiveness_screening_v2.json
~~~

当前配置先读取缺失的 v1 temporal manifest，因此命令以 FileNotFoundError 退出，并且不会生成诊断 JSON。修正为已物化的 temporal v2 manifest 后，该命令才成为完整只读 preflight。随后统一 META topology 和 arm identity，并重新 freeze design。当前阻塞详见 [effectiveness_screening.md](effectiveness_screening.md)。

## 11. 运行记录最小集合

每次正式阶段至少记录：

- git commit 与 dirty state；
- config 与 manifest SHA；
- Python、模型、tokenizer、template、dtype 与 runtime identity；
- 输入和上游工件 identity；
- 命令、开始/结束时间和退出状态；
- 输出工件 SHA；
- gate 结果与 missing 列表；
- 资源分配与 capacity selection；
- 恢复来源与 checkpoint identity。

以上信息进入 machine-readable artifacts；STATUS 只汇总结论。
