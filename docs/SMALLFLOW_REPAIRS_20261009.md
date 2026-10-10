# 小样本测试前的运行修复

范围：停止正式运行、修复已确认监控和运行控制问题、准备小样本计划；不启动模型实验。用户本次明确要求等待其开始指令。

## 正式任务停止与保留

`agentreview-0930-study-20261009.service` 于 11:41:42 收到停止，SIGINT 后等待线程中的任务退出超过原 30 秒窗口，于 11:42:13 被 systemd SIGKILL。保留了 216 个完整构建角色评审。不能把这次人为停机的 `failed/timeout` 当作原自进化业务故障。

旧 `study-state.json` 残留 `running`，未手工修改历史状态或评分。停机事实和旧状态副本独立保存于 `metadata/smallflow-20261009/formal-stop.json`、`formal-stop-journal.log`、`paused-formal/`。新进度查询核对 `/proc` 进程与启动 ticks，在显示层标记中断并保留 `recorded_status`。CoCo 服务保持就绪。

## 确认的问题与修改

| 问题 | 证据与修改 |
|---|---|
| 正式耗时报告读错遥测目录，`stage` 被忽略 | 真实主目录已有 API 与角色事件，种子报告仍空白。`runtime_report.py` 接受明确遥测根并兼容 `stage`；`three_search_study.py` 后续按种子写入与读取遥测，避免跨种子累计混用 |
| 反思提示未写明验证器实际限制 | 本地回归及上一轮真实修复记录确认禁用 getattr 和额外顶层常量。提示从验证器常量生成禁用调用/属性列表，并明确四个顶层常量约束；M★ 协议升级 v4，修复次数不增加 |
| 运行预算仅限制发送前，持续流和队列可能超时 | 新 `runtime_limits.py` 提供跨准入、连接、流式读取的请求截止时间；同步流在消费者截止后丢弃迟到输出，异步流取消超时协程；原 SDK 读取超时与 CoCo 重试不变 |
| 超时后台线程仍可能迟发请求或改写结束回执 | 两个额外回归先失败。HTTP observer 在实际发送前复核请求截止时间；迟到响应头不修改已终止的请求进度 |
| 正式启动器删除预算环境，整个进程缺少硬截止 | 不再清除预算；缺少显式预算的正式启动拒绝运行。新 `run_budgeted_0930.py` 保留累计账本和截止时间，监督工作进程，提前发 SIGINT、截止时 SIGKILL；已用次数和 SDK 重试均保留 |
| 清理后的 CoCo 子进程缺少新时限配置 | `_run_stage` 明确传递请求时限环境；原独立沙箱仍不继承凭据或网络能力 |

原版 CoCo 源码、提示、解析、恢复、权重及评分公式不修改。状态类型契约、共享程序、五角色独立记忆、种群和父代选择不变。新小样本结果与旧 pilot、旧验收、停止的正式运行分别保存。

## 本地证据与小样本准备

回归先出现 7 个失败，随后两个迟到传输问题也先复现失败。日志位于 `metadata/smallflow-20261009/`：`regression-before.log`、`supervisor-before.log`、`late-transport-before.log`、`runtime-tests.log`、`compatibility-tests.log`、`preflight.log`。这些测试使用本地假端点、模拟工具包或已有产物，不构成新代码的真实模型验收。

小样本协议绑定当前实际代码、生产启动器和完成核查程序。候选身份链核查复用原验收函数，扩展为可指定每个有效候选；末尾重新读取并重放完成检查点，要求账本与失败预算不变化。新准备版本 `v2` 加入迟到传输修复，先前未运行版本保留。

计划见 `docs/SMALLFLOW_PLAN_20261009.md`。实际入口 `config/0930-smallflow-20261009-v2/profile.json`，产出 `artifacts/smallflow-0930-20261009-v2/`。当前只完成配置、本地检查与服务预检；真实全流程效果仍待用户启动后验证。正式运行不会自动恢复或衔接。

最终本地验证：`83 passed in 29.92s`；v2 生产配置预检 `ready`、沙箱 `passed`、模型业务调用为 0。历史保护文件 2329 个无变化，当前新冻结 70 个文件无漂移。证据为 `metadata/smallflow-20261009/final-tests.log`、`preflight-v2.log`、`preservation-and-freeze.json` 及新产物根目录 `local-validation.json`。
