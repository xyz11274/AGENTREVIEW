# Effectiveness screening 接口状态

状态：保留接口，等待配置与身份统一。

运行路径：legacy direct review。

Core v1 关系：独立实验 workflow。

## 设计链

~~~text
frozen selection
  → related-work retrieval
  → context bundles
  → Program/history freeze
  → generation matrix
  → proxy classification and judging
  → blind pairs
  → summary gate
~~~

模块：

- prepare.py：selection、related work 与 bundle；
- memory_cache.py：Program 与 history/cache 冻结；
- run_matrix.py：设计冻结、命令矩阵与 run audit；
- evaluate_proxy.py：proxy classify、judge 与 blind pairs；
- summarize.py：指标汇总与 screening gate；
- preflight.py：只读前置检查。

## 当前阻塞

1. configs/effectiveness_screening_v2.json 指向缺失的 v1 temporal manifest。
2. Direct-review generation 默认输出五 specialist；proxy judge 读取 Final Meta-Reviewer。
3. 三个 lineage 展开成七个实际 arm label；分类路径仍聚合到 seed/evolved，lineage 身份发生覆盖。
4. Blind pair 与 summary 使用 seed、seed_experience 和 expanded label 三套命名。

完成条件是统一 temporal v2 manifest、META topology、arm identity、输出目录和 summary grouping，并重新 freeze design。

## 当前诊断

以下命令复现 temporal manifest 阻塞：

~~~bash
cd /data0/xyj/shengao/AgentReview_0827/AgentReview
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
$PY -m effectiveness_screening.preflight \
  --config configs/effectiveness_screening_v2.json
~~~

当前配置在读取缺失的 v1 manifest 时抛出 FileNotFoundError，因此不会生成完整诊断 JSON。把配置统一到已物化的 temporal v2 manifest 后，再把该命令用作只读 preflight。正式矩阵在上述四项形成同一冻结身份后启动。该实验的 proxy 指标保持与 Official CoCo 结果分开报告。
