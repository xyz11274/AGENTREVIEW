# AgentReview 0930

0930 是基于 0915 **当前工作区**建立的独立开发基线，包含原先未提交和未跟踪的实现与回归测试。共享 MemoryProgram、单候选池、五个独立角色记忆库、标准 CoCo `Paper.` 选优、三次搜索与冻结后终评的逻辑保持不变。

代码与实验输入位于本目录；不读取 0831、0811 或 0915 的数据，也不共享这些项目的 `.git`。模型权重、Python 环境和 CoCo 源码/服务继续使用现有公共安装。Git 分支为 `0930`，保留 0915 的提交历史；当前工作树不包含旧候选、检查点、日志、PID、运行结果或嵌套旧仓库。

## 目录

| 路径 | 用途 |
|---|---|
| `AgentReview/agentreview/research_v2/` | 原有共享进化、Memory、Review、Meta、CoCo 与完整性校验 |
| `AgentReview/agentreview/reviewer_runtime/` | 原有 sandbox、worker、Toolkit 和记忆预算 |
| `AgentReview/tests/` | 从当前 0915 保留的回归测试 |
| `config/0930/` | 三次独立搜索的冻结输入协议、服务配置、执行配置与评分配置 |
| `data/paired/` | 当前配对实验的完整论文、分区清单和仅供评分的参考数据 |
| `data/original/` | 原始冻结论文、划分、1300 篇基准清单和 S2 输入；原先存放在顶层 0831 |
| `data/provenance/` | 历史暴露身份、用途注释、cohort 与分区审核；保留来源摘要，不继承旧搜索状态 |
| `assets/hf/` | 从旧项目中独立复制的 BERTScore 模型缓存；相对软链接均解析到本目录内 |
| `metadata/` | 数据摘要清单、迁移证明与本次验证结果 |
| `deployment/workspace_0930.py` | 0930 的独立检查/运行入口 |
| `deployment/`、`docs/` | 保留的部署工具和设计文档；日期型工具用于原阶段重建，不预装旧实验产物 |
| `artifacts/` | 新运行时才产生的结果、缓存、服务状态与日志；Git 忽略 |

`data/` 和 `config/0930/` 随当前分支保存。大型 `assets/hf/` 模型缓存不提交 Git；复制整个工作目录时应保留它，只有 Git clone 的机器还需提供对应模型与公共环境。新目录内输入路径已重新绑定并计算摘要，旧源路径不是新运行的依赖。

## 已继承的实验条件

- 分区：build 125、static 25、rotating 125、sealed_test 1300；完整正文与顺序不变。
- 三次独立搜索：随机种子 `0 / 1000 / 2000`；每次 20 个 mutation attempt，rotating 5，父代温度 0.6。
- 初始方案：Vector Search、LLM Summarizer、Experience Learner；各自匹配的 no-memory 对照、CoCo Native 与 Champion。
- 固定五角色、Memory-only 变异、Meta 汇总、S2 off；完整输入与失败处理预算沿用 0915 的现有协议。
- 保留现有基准曾被使用的注释，不把它重新宣称为完全未见的盲测集。
- 运行状态从空白开始；没有迁入旧 winner、pool、checkpoint、旧模型请求或已完成评价。

以上冻结文件绑定的是本次复制的实现和输入，不等于当前 GPU 服务已验收，也没有自动启动新实验。之后修改算法或实验条件，需要建立新的实验身份和输出目录。

## 检查

```bash
cd /data/xyj/shengao/AgentReview_0930
/data/xyj/envs/coco-original/bin/python -B deployment/workspace_0930.py check
/data/xyj/envs/coco-original/bin/python -B -m pytest -q -p no:cacheprovider
```

`check` 校验本地输入摘要、三份实验协议、分区与实现身份，并禁止打开其他 AgentReview 版本的文件。它不调用模型，不检测运行中服务，也不生成候选或评价结果。

原有 CLI 仍可使用：

```bash
PYTHONPATH=AgentReview /data/xyj/envs/coco-original/bin/python -B \
  -m agentreview.research_v2 --help
```

## 运行入口

需要实际开始新实验时，先按 `config/0930/services.json` 核实共享服务与模型配置，再显式执行：

```bash
cd /data/xyj/shengao/AgentReview_0930
/data/xyj/envs/coco-original/bin/python -B deployment/workspace_0930.py run
```

此命令委托原有 `three_search_study.run()`，结果写入新的 `artifacts/study-0930/`，仍执行三次独立搜索、全部系统冻结后再终评的顺序。它不会替用户自动启动 vLLM 服务。现有服务配置保留原 GPU 编号，使用前按实际部署核对。

`deployment/` 中带 20260916/20260917 产物路径的历史工具保留其算法与接口，但对应旧运行结果已清理；日常开发和新实验使用本页的 0930 入口，不把历史工具默认路径当成预装产物。

## 公共依赖

- `/data/xyj/envs/coco-original`：评分与本次测试环境。
- `/data/xyj/envs/env_shengao`、`/data/xyj/envs/qwen38serve`：现有 Python/服务环境。
- `/data/xyj/models/Qwen3.8-27B` 与现有 Qwen3-Embedding-8B 缓存。
- 相邻 `CoCoReviewBench` 的标准评分代码、Split/Classify 模型，以及配置中的本地服务。

迁移记录见 `metadata/0930-migration.json`，输入文件摘要见 `metadata/inputs.sha256.json`。正文内容摘要保留不变；1300 篇终评 Paper JSON 的来源指针改为本地 `data/original` 后，外围 JSON 的摘要已相应更新。

## 本次验证基线

独立检查通过：3192 个本地输入文件、1575 篇完整论文、三份协议；旧版本文件读取次数为 0。CLI 帮助加载及全部 Python 源码语法检查通过。

使用相同 `coco-original` 环境对当前 0915 和 0930 运行全量测试，两者均为 **464 passed、214 subtests passed、36 failed、9 skipped**，失败测试集合完全相同，没有迁移新增失败。失败主要集中在既有 bootstrap/批量评审恢复、旧模型根路径校验及 Meta 身份测试；本次保留原算法，没有为迁移顺带修复这些问题。详情见 `metadata/validation.json`。

69 个核心 Python 文件中 68 个与源工作区字节一致；另一个仅将旧 Judge 工作目录改为当前工程位置。生成、搜索、模型和失败策略参数与原协议相同；54 个原测试文件原样保留。代码核验见 `metadata/code-compatibility.json`。本次没有启动模型服务或正式实验。
