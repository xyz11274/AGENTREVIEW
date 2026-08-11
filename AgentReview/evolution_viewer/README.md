# M★ Evolution Viewer

这是一个可整体抽离的、只读的 M★ 程序转变前端。它不导入
`agentreview` 或 `memory_evolution`，不会执行候选程序，也不会修改实验产物。

## 展示内容

- 自动扫描 `runs/` 下包含 `lineage.jsonl` 或 `scores.jsonl` 的运行；
- 按实验套件、角色任务和 seed/run 切换；
- 按 iteration 展示完整转变卡片流，包括未产生候选的失败迭代；
- 确定性解析四个 Prompt、`KnowledgeItem`/`Query` Schema、
  `KnowledgeBase.write()`、`KnowledgeBase.read()` 和辅助代码；
- Prompt 修改前/后词级高亮，可切换完整文本、仅新增和仅删除；
- 展示完整 Python unified diff；
- 展示 static/rotating 分数、质量门禁、编译修复和截断后的 trace；
- 支持同一 run 内任意两个实际候选程序的累计对比；
- 损坏或未写完的 JSONL 会产生行级告警，其余可用内容仍会显示。

页面中的结构性说明来自 Python AST，修改原因直接引用原始
`change_log.summary/details`。应用不会调用 LLM，也不会推断某次代码变化与分数
之间存在因果关系。

## 安装与启动

从包含 `evolution_viewer/` 的目录执行：

```bash
python3 -m pip install -r evolution_viewer/requirements.txt
python3 -m evolution_viewer.app \
  --runs-dir /absolute/path/to/runs \
  --host 127.0.0.1 \
  --port 7862
```

也可以直接运行：

```bash
python3 evolution_viewer/app.py --runs-dir /absolute/path/to/runs
```

默认仅监听本机地址。只有明确需要外部访问时才应改变 `--host` 或启用
`--share`。

## 日常后台管理

本仓库已经提供管理脚本，默认监听所有网卡的 `7862` 端口：

```bash
./evolution_viewer/manage.sh start
./evolution_viewer/manage.sh status
./evolution_viewer/manage.sh logs
./evolution_viewer/manage.sh stop
./evolution_viewer/manage.sh restart
```

`start` 会在后台运行，关闭当前终端不会停止 Viewer。PID 与日志保存在
`evolution_viewer/.runtime/`，不会写入实验目录。

切换端口或 runs 目录：

```bash
EVOLUTION_VIEWER_PORT=8080 \
EVOLUTION_VIEWER_RUNS_DIR=/absolute/path/to/runs \
./evolution_viewer/manage.sh start
```

监听 `0.0.0.0` 只代表应用接受所有网卡的连接。公网访问仍取决于服务器是否
具有公网 IP，以及云安全组、机房网关或主机防火墙是否允许相应端口。此页面会
展示实验代码、错误与截断后的 trace；不要在不可信网络中无保护地公开。

## 可抽离性

复制整个 `evolution_viewer/` 目录即可。运行时输入只要求具有类似结构：

```text
runs/<suite>/<TASK>/seed_<n>/
  config.json                 # 可选
  selection.json              # 可选
  pool/*.py
  scores.jsonl
  lineage.jsonl
  traces/iter_*_rotating.jsonl
  best_eval_report.json       # 可选
```

直接把单次运行放在 `runs/<run_name>/` 下同样可以被发现。所有 JSON、JSONL
和 Python 文件都以只读方式加载。

## 测试

核心测试不需要 Gradio，也不使用真实论文或 API：

```bash
python3 -m unittest discover -s evolution_viewer/tests -v
```
