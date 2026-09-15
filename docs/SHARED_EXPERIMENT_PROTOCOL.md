# 共享 Memory 实验配置与数据契约

本接口落实 [ADR-0011](adr/0011-development-coco-and-sealed-test.md) 的数据用途边界、[ADR-0012](adr/0012-no-memory-controlled-comparison.md) 的匹配对照，以及 [ADR-0014](adr/0014-mstar-paper-guided-memory-evolution.md) 的固定集评价与轮换反馈。冻结配置模板位于 [shared_memory_experiment.pending.json](../AgentReview/configs/shared_memory_experiment.pending.json)。模板使用 `status: pending` 和显式待填值；`load_experiment_protocol()` 要求完整有效配置与 `status: frozen`。

## 配置的身份

`protocol` 固定为 `shared-memory-experiment-v1`，`experiment_id` 标识本轮实验。配置使用明确的生成预算、搜索参数、故障预算、模型与检索条件；运行器按照配置加载数据。样本量等于各身份清单的记录数，实验参数由实验方案填写。

模型字段绑定本地 `qwen3.8-27b` / `Qwen/Qwen3.8-27B`、revision、precision、tokenizer/chat template 摘要、框架版本、temperature、enable_thinking 和上下文容量。`base_url` 指向本地或私有网络部署。模型生成设置、运行库身份以及数据与资产的摘要共同组成冻结身份。

`generation` 显式配置 `review_max_tokens`、`meta_max_tokens`、`memory_max_tokens`、`native_max_tokens`、`input_max_tokens`、`paper_context_max_chars`、`memory_build_llm_calls`、`memory_read_llm_calls`、`memory_query_mode` 与 `native_num_fewshot`。`paper_context_max_chars: null` 表示保留完整字符输入；模型请求仍受 `input_max_tokens` 与模型上下文容量约束。LLM Summarizer 的读取预算至少为一次。

当前冻结协议支持 `native_num_fewshot: 0`，调用 CoCo 原始函数提供的零示例模式。示例数量仍属于待确定实验条件，pending 模板保留待填状态。有示例模式需要先补充示例论文、评审、版本家族及分区审核的身份清单，再扩展运行契约。CoCo 自带的 `132_automated_relational`、`attention`、`2_carpe_diem` 示例位于 `src/evaluation/ai_scientist/fewshot_examples/`；原始 `get_review_fewshot_examples_wo_paper` 会读取其文本与 JSON 评审，并将评审作为提示示例注入。这些文件当前具有文件名与摘要身份，缺少本实验要求的完整研究版本关系，因而文件摘要本身不足以核验与封存测试的隔离。原始 CoCo 提示内容、示例函数和评审提取逻辑保持其来源身份。

`evolution` 显式配置 `attempts`、`rotating_sample_size`、`parent_temperature`、`random_seed` 与 `max_reflection_tokens`。当前实现支持 `mutable_scope: memory` 与 `reflection_scope: scores_and_traces`，使反思消费评分与生成轨迹。`failure_policy` 显式配置三个正数预算 `candidate_repairs`、`round_repairs`、`candidate_failures`，以及 `request_attempts`。

`non_memory_conditions` 绑定 `final_mode`、`s2_mode`、`review_policy_program` 与 `frozen_s2`。Memory 开关使用相同固定审稿策略和其他生成条件；响应使用指令及固定记忆知识作为 Memory 内容注入。`scoring_config` 绑定标准 CoCo 评分配置文件的路径与摘要；`embedding` 记录实际的 provider、model、revision、cache_path 和 device。

## 四种数据用途

| 清单名 | 运行用途 | 访问入口的 purpose |
|---|---|---|
| `build` | 构建五个角色各自的记忆库 | `memory_build` |
| `static` | 完整候选评价与选优 | `selection` |
| `rotating` | 当前父代的轮换评价与反思 | `reflection` |
| `sealed_test` | 系统冻结后的最终评价 | `final_test` |

各清单使用如下结构，顶层配置的 `splits` 逐个绑定清单文件的 `path` 与文件字节 `sha256`。清单内资产路径相对所在清单文件解析。

```json
{
  "protocol": "shared-memory-split-v1",
  "split": "static",
  "records": [
    {
      "paper_id": "paper-id",
      "title": "Paper title",
      "content_sha256": "sha256-of-complete-paper-text-utf8",
      "normalized_content_sha256": "sha256-of-normalized-complete-text",
      "work_family": "curated-research-family-id",
      "version_ids": ["paper-id", "same-research-preprint-id"],
      "source_partition": "static",
      "assets": {
        "paper": {"path": "paper.json", "sha256": "file-sha256"},
        "coco_reference": {"path": "references.json", "sha256": "file-sha256"}
      }
    }
  ]
}
```

`paper` 是保存完整 `Paper` 的 JSON，`Paper.source.source_partition` 与所属清单一致。静态评价、轮换反馈和封存测试均要求符合标准 CoCo 的参考资产；同一分区中的多篇论文可以引用同一个完整参考清单，评分适配器核验其内部样本集合和参考结构。开发配置加载时核验开发文件内容与摘要；封存测试只读取身份清单和摘要声明。

`s2_mode: frozen` 要求 `frozen_s2` 为四个分区分别绑定 JSONL 文件。开发侧文件的 `paper_id` 集合恰好等于对应论文清单，每行显式记录 `source_partition` 和 `contains_human_review_data: false`。返回证据的 ID、标题、已知版本和研究家族也接受分区交集校验，使检索资产与当前分区保持一致。`s2_mode: off` 使用空 `frozen_s2` 对象。

## 去重与历史暴露

身份核验覆盖 paper ID、全部 version IDs、标准化标题、原始完整正文摘要、规范化正文摘要和 research family。规范化正文使用 Unicode NFKC、casefold 和空白归一化。同一研究的版本关系由具有明确方法与来源的 family audit 补充，运行器核验该审核绑定四份清单的精确摘要。

`deduplication` 使用 `policy: identity-family-normalized-content-v1` 与 `family_audit: {path, sha256}`。Family audit JSON 包含 `status: complete`、`method` 与 `split_manifest_sha256` 四分区映射。近重复和研究版本的归并方法属于实验数据准备的一部分，应在 `method` 与审核来源中写清楚。

`historical_exposure_manifests` 绑定此前已经查看过的 1300/902/805 及其他历史集合的完整身份清单。每个文件的 `records` 使用上述身份字段；历史身份核验只需身份元信息。封存测试与这些历史身份的 ID、标题、正文及版本家族保持分离。历史库存的覆盖范围由数据准备者核验，运行器核验所提供库存的身份交集。

## 完整正文与来源锚点

Canonical paper context 使用 `canonical-complete-paper-v2`，保留完整正文与附录。明确配置字符预算时，超预算输入抛出 `PaperContextBudgetError`，由共享数据故障路径处理。增大预算或更换具有足够容量的已批准配置后重新生成对应输入身份。

具备结构化提取结果时使用实际来源锚点。纯文本来源使用真实字符区间 `[start,end)`；缺少提取页码时记录字符位置或 `page=unknown`。锚点只包含短定位文本，正文完整保留在主输入区。完整文本资产选择 `md_content.md` 或显式指定的完整文件，UTF-8 解码严格校验。

## 程序接口

`load_experiment_protocol(path)` 返回 `FrozenExperimentProtocol`。`records(split)` 返回身份记录副本；`paper_paths(split, purpose=...)` 核验用途；`reference_assets()` 供评价阶段取参考资产。最终测试入口先验证系统冻结凭据，再调用 `validate_final_test_assets(frozen_system_sha256=...)` 打开并核验全部测试资产、真实正文身份和 S2 内容。`freeze(path)` 保存不可变实验凭据及四分区身份。

开发加载阶段保持封存测试正文、参考原文和 S2 文件关闭。测试开放后，最终评价入口继续核验文件摘要和身份，最终分数由独立结果汇总读取。
