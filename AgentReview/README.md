# AgentReview Python 工程

本目录包含 Research Validity Core v1、旧 Reviewer/M★ 路径、数据准备脚本、只读 Viewer 和测试。仓库级架构见 [完整架构](../docs/ARCHITECTURE.md)，实时完成度见 [系统状态](../docs/STATUS.md)。

## 运行环境

仓库当前使用显式 Python 解释器。运行时依赖以 requirements-runtime.txt 为主，Viewer 和旧 provider 的附加依赖位于 requirements-optional.txt。

~~~bash
cd /data0/xyj/shengao/AgentReview_0827/AgentReview
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
$PY -m pip install -r requirements-runtime.txt
~~~

setup.py 只提供基础包元数据；命令从本目录按模块方式执行。

## 执行入口

| 入口 | 用途 | 状态 |
|---|---|---|
| python -m memory_evolution.core_cli | Core v1 验证、角色运行、Suite 冻结、Official 与闭包审计 | 当前目标入口；正式配置和 G2 工件待生成 |
| python -m agentreview.cli review | 独立旧 MemoryProgram Reviewer | 兼容入口；接受 legacy Program |
| python -m memory_evolution.cli | 旧 M★ prepare/evolve/evaluate/META/CoCo | 可达的 legacy 实验入口 |
| python run_single_paper_review_cli.py | Markdown 论文的五 specialist 直接评审，可显式开启 META | 可达的 legacy 部署入口 |
| python -m agentreview.coco_eval | 候选评审文件的独立 CoCo 评价 | legacy 评价入口 |
| python viewer.py | 直接评审输出 Viewer | 只读 legacy Viewer |
| python -m evolution_viewer.app | M★ lineage/scores Viewer | 只读 legacy 演化 Viewer |
| python -m effectiveness_screening.preflight | 三臂筛选配置核验 | 保留接口；当前配置存在结构性失配 |

先运行 --help 获取参数事实。正式操作顺序集中记录在 [运行手册](../docs/RUNBOOK.md)。

## Core v1 最短检查

~~~bash
$PY -m memory_evolution.core_cli --help
$PY -m memory_evolution.core_cli integration-smoke --output-dir /tmp/agentreview-core-smoke
~~~

integration-smoke 使用合成 Corpus、hash embedding、Fake Qwen、空 Evidence 和 REPORT_ONLY Official runner；它验证 G1 控制流。正式运行使用 --config 或 --application-factory 注入真实依赖。

## 代码布局

~~~text
agentreview/                 # 领域数据、Reviewer runtime、Core 数据接口、旧 Arena/Provider
memory_evolution/            # Core v1 组合与搜索；旧 M★ 兼容实现
scripts/                     # Corpus、索引、capacity 和资产准备
effectiveness_screening/     # 保留的 legacy 三臂筛选 workflow
evolution_viewer/            # 可独立抽离的只读 M★ Viewer
configs/                     # temporal、资产和 legacy 实验配置
tests/                       # 主测试集
papers_eval20/, test_data/   # 论文输入和测试数据
artifacts/, runs/            # 已存在的运行工件与历史结果
outputs/                     # direct review 的运行时生成目录
~~~

## 测试

~~~bash
$PY -m unittest discover -s tests -v
$PY -m unittest discover -s evolution_viewer/tests -v
~~~

截至 2026-08-31，主命令执行 243 项并产生 5 个 error；Evolution Viewer 执行 10 项并通过。根因与复核结果见 [系统状态](../docs/STATUS.md)。

## 冻结资产

REVIEW_Request.md 是五角色 rubric 的冻结字节资产，当前 SHA-256 为 609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42。论文 Markdown、JSONL 数据和运行工件按输入/证据身份管理。
