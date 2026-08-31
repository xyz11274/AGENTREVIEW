# M★ Evolution Viewer

这是可独立抽离的只读 legacy M★ Viewer。它扫描 lineage.jsonl、scores.jsonl 和候选 Python 文件，以 AST 与文本差异展示 Program 演化；应用只读取工件。

当前 Viewer 面向旧 M★ artifact layout。Core v1 只写 role_winners/*.json、final_suite.json 和 official_result.json，因此 Core 工件需要独立适配后才能进入本界面。

## 启动

从 AgentReview/ 执行：

~~~bash
PY=/data0/xyj/miniconda3/envs/env_shengao/bin/python
$PY -m pip install -r evolution_viewer/requirements.txt
$PY -m evolution_viewer.app \
  --runs-dir /absolute/path/to/runs \
  --host 127.0.0.1 \
  --port 7862
~~~

管理脚本提供后台生命周期：

~~~bash
./evolution_viewer/manage.sh start
./evolution_viewer/manage.sh status
./evolution_viewer/manage.sh logs
./evolution_viewer/manage.sh restart
./evolution_viewer/manage.sh stop
~~~

可通过 EVOLUTION_VIEWER_PORT 和 EVOLUTION_VIEWER_RUNS_DIR 选择端口与数据目录。默认本机监听适合查看包含代码、错误和 trace 的实验数据。

## 输入布局

~~~text
runs/<suite>/<TASK>/seed_<n>/
  config.json
  selection.json
  pool/*.py
  scores.jsonl
  lineage.jsonl
  traces/iter_*_rotating.jsonl
  best_eval_report.json
~~~

config.json、selection.json 和 best_eval_report.json 属于可选输入。损坏或写入中的 JSONL 会形成行级告警，其余记录继续展示。

## 测试

~~~bash
$PY -m unittest discover -s evolution_viewer/tests -v
~~~

截至 2026-08-31，该命令执行 10 项并通过。
