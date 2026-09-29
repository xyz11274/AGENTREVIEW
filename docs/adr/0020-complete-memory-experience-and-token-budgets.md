---
status: accepted
---

# ADR-0020：完整经验、反馈边界和 token 预算

2026-09-17，按用户要求修复反馈传递和硬截断，保持五角色、共享 MemoryProgram、MSTAR 选优和原版 CoCo。依据 [MSTAR v2](https://arxiv.org/html/2604.11811v2)、本地 `/data/xyj/shengao/mstar` 的 `09a8d34f69588a85fde78b20702d14f056267956`，以及 ADR-0014、0017、0019。本地 mstar 和 CoCo 源码未修改。

## 原设计与问题归属

| 项目 | 原设计 / 已确认问题 | 本轮处理 |
|---|---|---|
| build 和 rotating | MSTAR offline ingestion 从历史 raw_text 提炼并写入；交互式 train 分支可使用 compare_fn 产生的训练反馈；rotating 轨迹用于 Reflector 改程序 | 保留两条不同路径；不把开发选优或测试反馈塞进 build |
| 显式 build 反馈丢失 | 适配宿主 `_experience_source` 含完整审稿和纠错，但 `_deterministic_item.content` 只有审稿；适配种子优先取 content。原版 Vector Search 写 raw_text | 修复宿主 content/raw_text 别名，种子优先写完整 raw_text；保留来源 ID 和 SHA |
| 空 evaluation_paths | 共享流程取消角色评分后刻意传空；不是反馈丢失证据 | 继续为空，不重新生成角色分数。旧独立 Internal Evaluator 接口仍可显式提供获准 build 纠错 |
| 字符硬切 | 原版种子也有 3000/30000 字符切片，是实现限制，不是 MSTAR 算法要求；本地又有 1200 分块、6000/12000/1500 硬切 | 取消材料硬切，宿主提供完整条目的 token 选择；返回超预算则失败 |
| 子代重建 | MSTAR 的新程序重新构建知识库；本地共享流程已经按 candidate、epoch、revision 隔离构建 | 保留该架构；补齐宿主契约、tokenizer、completion 和经验来源的缓存绑定 |
| 字符限制复发 | 宿主、反思提示和 validator 原来要求 6000 字符，容易重新引入切片 | 同步改 token 契约；静态检查拒绝文本及无法证明为集合的切片，允许完整列表条目选择 |
| Toolkit max_tokens | 宿主声明可指定额度，但适配器实际忽略该参数 | 在冻结上限内兑现显式参数；超限/无效额度失败。五角色 8192、Meta 16384、原 CoCo 输出预算不变 |
| embedding | Qwen encoder 的 max_seq_length 可能隐式截断输入 | 编码前按其自身 tokenizer 校验完整输入；超限失败，缓存 fingerprint 更新为不截断契约 |

`run_internal_evaluation()` 是旧内部评分/纠错的生产者。共享搜索不调用它：build 生成固定策略的完整角色审稿；`development_coco_worker` 调用原版 Split/Classify/Judge 生产最终评审的 CoCo 评分；`LocalSharedBackend.propose()` 将本轮 rotating 的评分、完整角色轨迹、最终评审和变更历史交给 Reflector。static `Paper.` 用于父代抽样和最终选优。测试反馈不进入这两条路径。

## 明确记录的适配差异

1. Vector Search 把完整经验及其显式纠错作为一个检索单元，防止召回错误断言却把纠错切走；不再按任意字符位置切块。排序后按 token 选择完整单元，过大的条目不进入本次上下文，选择/未选择和 SHA 全部记录。该策略可能降低一次召回数量，是内容完整性约束下的可测取舍。
2. 三种 seed 的 read 使用同一宿主 token 工具。正式运行固定 Qwen3.8 tokenizer SHA；Memory 上下文 4096 tokens，Summarizer 完整输入条目上限 32768 tokens。摘要绑定完整来源、提示、原始输出和 source IDs，不做后处理截断；模型未完整结束或摘要超预算均失败。离线测试和旧非正式入口的 UTF-8 字节上界被明确标识，不冒充模型 tokenizer。
3. Reflector 按原文的 underperforming-case 思路优先低分案例，只选择能完整容纳五角色轨迹及最终评审的论文级案例；完整历史记录也作为整体选择。全部原反馈另存，预算收据记录选择及未选择。原评分聚合、父代 softmax、rotating 抽样和子代入池规则不变。如果一个完整案例也放不下，显式失败，不切材料。
4. `complete-experience-token-budget-v2` 写入 build identity。修改纠错内容即使 evaluation_id 不变，也不能复用旧 snapshot 或逐条 checkpoint。旧契约的记忆不能进入新评审；历史文件仍可被审计读取，不被改写。各论文恢复独立快照，角色内容及可写状态不共享。
5. 修复后的三个原始 seed 家族复制到本轮冻结输入目录；它们不是小实验候选。协议可显式绑定初始程序，运行时检验其文件哈希。重复提案仍沿既定候选 ID/尝试预算规则记录，不改写历史 pilot 的重复结果。

本轮 build 没有角色正确性标签；此修复不会凭空生成纠错意见，也不保证每条原始 build 审稿正确。对这些经验的改进来自 rotating 反馈驱动程序变异及随后重建，最终效果须由统一条件下的配对实验衡量。仅代码哈希或 memory payload 变化都不构成效果证据。

## 回归与执行

针对性回归覆盖：已知纠错经过真实宿主构建、Sandbox 写入、检索、评审输入；纠错变化使两层缓存失效；摘要完整尾部保留；超预算输出阻断；子代字符切片被拒绝；五角色及逐论文隔离；完整 rotating 评分和轨迹实际进入 Reflector；静态或测试反馈不能替代 rotating；原版 CoCo 差分、收据验证、失败不入池和冻结测试边界。

模型返回在回归中使用明确的测试替身，不能将其当成新的真实模型全流程结果。历史真实全流程验收不重复跑，本轮不新增 pilot 或调参。原复现失败记录保留，新修复结果另存。完整规模配置沿用 build 125 / static 25 / rotating 125 / 最终配对集合 821，原 1300 抽样清单不变。

启动适配入口：`deployment/apply_memory_repair_protocol.py`。它校验确切测试代码身份，冻结修复后种子及运行实现，核验三服务和 tokenizer，在另一个协议文件中适配已有数据。只有完整搜索数值方案明确后，才发布正式执行协议并调用既有全流程入口；不会填入小实验预算。
