# AgentReview 开发实验吞吐与完整性记录

更新时间：2026-09-17T03:40:10.075849+00:00；执行服务器：10.249.47.18。

Pilot 已收尾：13 份 assessment、36 篇最终评审通过收据验证；Champion 为 **seed-vector_search**，static Paper. 3.4。
candidate-0002 与 candidate-0001 代码相同。现有 shared runner 和原 MSTAR 按候选 ID 入池，无源码哈希去重拒绝规则；保留其评价、入池、消耗的提案次数和父代采样权重。6 个池条目对应 5 份不同代码。历史文件未改写。
详见 [pilot-audit.json](pilot-audit.json) 与 [pilot-runtime-archive.json](pilot-runtime-archive.json)。原运行代码按字节归档；当前优化代码不冒充历史 runtime。

## 真正完成的全流程

每篇必须经过五角色 → Meta → 原版 CoCo Split/Classify/Judge/Paper.，并通过原子收据与组合收据验证。固定输入 Judge 重放不计入本表。

| 测量 | 完整评审 | 墙钟秒 | 有效评审/小时 | 失败率 | Paper. |
|---|---:|---:|---:|---:|---|
| baseline | 2 | 740.66 | 9.72 | 0.0% | [3.2, 3.4] |
| paper2 | 2 | 607.02 | 11.86 | 0.0% | [3.0, 3.4] |

仅两篇已暴露开发论文，Qwen embedding 缓存已热；不能外推为大样本或正式泛化结果。新生成的 review 文本不同，其 Paper. 差异不等同于固定评审上的评分漂移。
应用层保持每篇、每角色独立可写状态；共用冻结 build 记忆的独立副本。论文并发上限 2，18001 跨进程总 admission 上限 16；有依赖的 MSTAR 轮次仍串行。

## 阶段及排队证据

| 测量 / 服务 | running 峰值 | waiting 峰值 | 排队秒 | KV 峰值 | 抢占 |
|---|---:|---:|---:|---:|---:|
| baseline / 18001 | 8 | 12 | 73.0 | 16.9% | 0 |
| baseline / 13099 | 6 | 0 | 0.0 | 4.7% | 0 |
| baseline / 13098 | 8 | 49 | 32.2 | 4.6% | 0 |
| paper2 / 18001 | 8 | 8 | 105.2 | 18.0% | 0 |
| paper2 / 13099 | 4 | 0 | 0.0 | 4.7% | 0 |
| paper2 / 13098 | 8 | 8 | 40.9 | 5.2% | 0 |

重复模型校验从约 31 秒降至约 0.2 秒；首次校验及 Champion 冻结仍做完整内容哈希。缓存同时检查文件身份和 inotify 修改事件，回退到全量核对。
Qwen embedding 在两次原测量中实际加载/新编码均为 0；既有 encoder/cache 已避免重复加载和重复文本编码。CPU 文本指标、模型哈希、提交等待与推理阶段分别保存。
阶段耗时、每秒三服务 running/waiting、KV、抢占、前缀命中及结束原因见各测量目录的 summary.json、events.jsonl、worker-events.jsonl、metrics.jsonl。并行/嵌套阶段秒数不能相加当作墙钟时间；采样计数可能漏掉最后一秒，完成数以收据为准。

## 固定输入评分及服务参数对照

基准完整 Paper. 为 [3.2, 3.4]。按用户确认，以 Paper. 为主门槛：单篇绝对变化 ≤0.2、两篇均分绝对变化 ≤0.1；分类/必需评分完整，全部数值变化如实保存。旧门槛失败记录保留。
每次重放同一组 16 个原版 Judge 请求；完整性校验复用的 Split/Classify 收据及新 Judge 产物。原配置复测本身出现 [-0.2,+0.2] 波动，不声称逐位确定性。

| 对照 | 秒 | Paper. | 均分变化 | 主门槛 |
|---|---:|---|---:|---|
| judge-batch4096 | 242.32 | [3.2, 3.6] | +0.10 | passed |
| judge-batch65536 | — | — | — | not_started |
| judge-prefix4096 | — | — | — | not_started |
| judge-prefix4096-r1 | — | — | — | cancelled_by_user_direction |
| judge-reference | — | — | — | failed |
| judge-reference-r1 | 296.45 | [3.0, 3.6] | +0.00 | passed |
| judge-seqs16 | 227.95 | [3.0, 3.2] | -0.20 | rejected |
| judge-unchunked | — | — | — | not_started |

参数和所有分类指标差异详见 [judge-comparison.json](judge-comparison.json)、services.*.json。Judge 耗时还受生成长度变化影响，不当作纯硬件加速。服务加载/编译单独记录，不计作完成评审。
最初 judge-reference 因继承 IDE proxy 得到 502，失败证据保留；修正本地免代理环境后另建 r1 运行。曾发生安全重启时 TIME_WAIT 端口探测误拒绝，已修正 bind 语义，保留原失败 transition。

用户要求有可行方案即启动实验后，已停止边缘调参：采用经过完整两篇验证的应用层并发/哈希优化，恢复原服务参数（8 sequences、batch 2048、chunked prefill 开、18001 prefix 关）。4096 仅保留为通过诊断的候选；prefix 对照在发送评分请求前取消。详见 selection-user-stop-tuning.json。

## 扩大开发实验

build=8、static=6、rotating=8，计划 6 次有依赖的 MSTAR 提案，最终冻结 Champion 后再做 6 篇开发终评。预计完整搜索与终评产生 96 篇有效评审（不含 build 角色产物）。
正式数据隔离门槛未通过：历史暴露清单不完整、出版版本/论文族链接不完备，并复用了已暴露开发论文。因此仅标为开发实验，sealed test 不开放。
22 篇开发论文完整文本预检最长 23,573 tokens；该检查不能代替实际提示预算校验。每次真实请求保留完整输出预算，拒绝截断，有限重试失败记失败且不赋零、不入池。

65,536 批处理上限启动失败：KV 仅剩 0.17 GiB，保留完整 65,536 上下文需要至少 1.04 GiB。关闭 chunked prefill 所需的共同完整预填充配置在当前 0.85 显存分配下不可用，未伪称完成开关评分对照。保持 chunked prefill 开启，未缩短上下文、论文或输出预算。
相关 [chunked-prefill-feasibility.json](chunked-prefill-feasibility.json) 保留根因；启动监控同步修正了子进程失败后僵尸状态未及时识别的问题，已用真实退出子进程验证。

已冻结 [throughput-gate.frozen.json](throughput-gate.frozen.json) 和 [expanded/protocol.frozen.json](expanded/protocol.frozen.json)。
扩大实验记录：[expanded/launched.json](expanded/launched.json)。

launched.json 只表示已提交启动；有效进度以 run/search.json 中已入池评价及完整 assessment 为准。

当前已进入真实 build 角色评审生成；本轮尚无新增完整 CoCo 评审。已有 sealed 分区只绑定 1 篇封存记录，未开放；正式测试规模与正式数据划分尚未冻结。
