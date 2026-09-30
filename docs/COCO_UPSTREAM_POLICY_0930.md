# CoCo 官方源码原则与适配修复

当前架构与运行入口见 [ARCHITECTURE_0930.md](ARCHITECTURE_0930.md) 和 [仓库 README](../README.md)。本文保留 2026-09-29 的阶段记录。

当前状态（2026-09-29 晚）：用户已要求停机，CoCo、守护、转发和实验服务均已停止；后续 M★ 适配与独立验收见 [MSTAR_ADAPTATION_0930.md](MSTAR_ADAPTATION_0930.md)。下文启动和评分记录属于此前运行。

用户于 2026-09-29 明确要求：**除了必要的适配，不修改 CoCo 源码；以核实过的官方最新版为准。** 这条原则也适用于 AgentReview 包装层，不能通过外层检查改变官方允许的解析、补齐、分类、评分、缺项处理和重试行为。

## 官方来源

官方仓库：[hexuandeng/CoCoReviewBench](https://github.com/hexuandeng/CoCoReviewBench)。2026-09-29 实际查询远端 `main` 得到提交 [`2580687ee5d4ad2ac4b9730afb8f1dfda22e40db`](https://github.com/hexuandeng/CoCoReviewBench/commit/2580687ee5d4ad2ac4b9730afb8f1dfda22e40db)，提交时间 2026-05-02，说明为 `Update README.md`。这是本次核实的最新官方版本；后续官方更新应另外核实并冻结修订。

独立 checkout：`/data/xyj/shengao/CoCoReviewBench_official_2580687`，官方源码 **零改动**。旧目录 `/data/xyj/shengao/CoCoReviewBench` 保留；其中 `f9b16fa` 是本地后续提交，其日期更晚不代表它是官方最新版。[来源和逐文件哈希](../metadata/coco-official-source-verification.json)已保存。

## 适配边界

允许最小的服务地址、API 参数名称、认证环境变量、响应模型身份、部署位置及模型容量适配。原版提示、切分、遗漏补齐、分类、N/A 处理、评分解析、指标公式、有效分母、重试与结果选择均调用官方实现。

- 本地 `Qwen3-8B-Split`、`Qwen3-8B-Classify` 权重保留。Split 使用 139 的 GPU 5，Classify 使用本地 18 的 GPU 6、7；守护服务和转发沿用现有部署。
- 官方 Judge 默认 `gpt-5-mini`，按用户先前授权适配为方舟 `deepseek-v4.1-flash`。这项模型替换必须在实验记录中明示，不能称为原 Judge 的完全复现。
- 作者请求预算仍是 Split 32768、Classify 12288、Judge 32768；生成评审和进化程序的长度策略继续使用已修复的模型容量配置。必要的物理容量检查不截断输入，不另加宿主短输出上限。
- 保存原始请求、响应、结束原因及官方输出。`finish_reason`、原始覆盖率和缺项情况作为诊断记录；不额外以它们否决官方接受的结果。响应属于另一模型、文件哈希被改动属于配置或数据身份不一致。

## 本次修复及证据

失败样本 `f-FQE1fjPK` 有 95 个文本段，三次 Split 响应均只标注前 94 段。官方 `_2_split.py` 的缺项补齐正常生成完整结果，但原包装层要求原始响应覆盖率必须为 100%，导致原实验暂停。这是适配层错误。

仅修改 AgentReview 的三个运行文件：`coco_integrity.py`、`development_coco.py`、`development_coco_worker.py`。可逐行检查[相对修复前的完整差异](../metadata/coco-upstream-adapter.diff)。删除额外的 Split 全覆盖、Classify 格式成功、Judge 全指标成功、响应必须 stop、三次全局调用封顶等行为；恢复直接调用官方客户端的重试、缓存写入和结果处理。

CoCo 的缺失指标及有效分母原样保存。若某篇没有实际 `Paper.` 分数，评估产物仍保留；进化比较在读取它时明确报告缺少 fitness，不补零、不把部分样本均值当作完整固定集成绩。

- [失败请求离线重放](../metadata/coco-split-replay-current.json)：同一批已保存响应通过官方恢复，最终覆盖 95/95，无新增模型请求。
- [真实全流程验收](../metadata/coco-upstream-live-acceptance.json)：保持原失败评审文本，重新调用现有服务，原始覆盖率仍为 94/95，官方补齐后 95/95，95 个意见完成分类，6 个类别 Judge 加论文级 Judge 完成；`Paper.` = **4.0**，耗时 **233.437 秒**。这证明适配可运行，不代表自进化提高了分数。
- [最终回归日志](../metadata/coco-upstream-regression.log)：**299 passed，101 subtests passed**。覆盖官方缺项恢复、N/A、缺失指标、直接调用与适配调用一致、原版响应及重试处理，以及缓存和数据身份。

## 小规模实验修订

[新协议](../config/0930-pilot-coco-upstream/protocol.frozen.json)绑定独立官方 checkout 和修复后的包装层。15 篇划分仍为构建 4、固定比较 3、轮换 5、最终测试 3；最多五次进化尝试，不启动完整实验。

[修订记录](../artifacts/pilot-0930-coco-upstream/revision.json)保存旧协议、旧状态、修复前源码归档，以及复用响应的来源与哈希。旧实验没有产生任何进化尝试，三个随机流抽样数与两类失败预算使用量均为 0。旧检查点、失败账本、评分和协议没有改写。

复用 103 条输入、模型身份、响应哈希一致且完整结束的生成 API 响应，以及同一 Qwen Embedding 缓存；重新构建产物清单和记忆，重新进行 CoCo 评分，不导入旧候选和旧分数。这是修复后的同一小规模实验接续，不是一次独立的随机重复实验。

查看新运行：

- [状态](../artifacts/pilot-0930-coco-upstream/pilot-state.json)
- [逐轮改动和效果报告](../artifacts/pilot-0930-coco-upstream/report/自进化逐轮报告.md)
- [运行日志](../artifacts/pilot-0930-coco-upstream/launcher.log)
- [启动前检查](../artifacts/pilot-0930-coco-upstream/readiness.json)

后台服务为 `agentreview-0930-pilot-coco-upstream.service`，不依赖交互式 SSH。以状态和逐轮回执判断完成情况，不以进程存在认定实验完成。

Embedding API 的独立速度实测见[诊断文档](EMBEDDING_API_COCO_DIAGNOSIS_0930.md)。本修订没有更换向量模型。
