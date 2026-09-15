# 标准 CoCo 开发评分适配

`AgentReview/agentreview/research_v2/development_coco.py` 落实 ADR-0011、0013、0015、0016 的评分边界。共享运行器交付完整、冻结的 Review 清单，独立评分阶段产出逐篇 Table-1 指标和完整评价凭据。候选选择使用最终完整评审的 `Paper.`；五角色分别生成诊断凭据。

## 标准实现与输入

独立 worker 导入本地 CoCoReviewBench 原有 `split_all`、`classify_opinions`、参考评审加载、Judge、类别覆盖与 `build_table_1_summary` 函数，调用原有提示预算预检。标准类别、Split 规则、Judge 提示、`X` 语义与 Table-1 公式由 CoCo 源码定义。

开发数据的参考评审选择通过 `benchmark_reviewer_selection_file` 输入路径绑定到冻结的分区文件；留出参考评审的算法仍为原有 `left_out_one`。CoCoReviewBench 仓库与原始参考资产保持原样。分区文件包含该分区完整参考资产，固定评价与轮换反馈每次评分使用各自预定论文清单。

## Python 入口

```python
receipt = evaluate_final_reviews(
    review_manifest_path=review_manifest,
    reference_manifest_path=reference_manifest,
    scoring_config_path=scoring_config,
    output_root=score_directory,
    partition="static",
    run_id=experiment_id,
    candidate_id=candidate_id,
    evaluation_epoch=epoch,
    candidate_identity=program_and_memory_identity,
    purpose="development",
    review_target="final",
)
```

角色诊断使用 `review_target="role:QUAL"` 等独立入口参数和输出目录。最终测试使用 `partition="sealed_test"`、`purpose="final_test"`；共享运行器先验证 Frozen System，再开放测试资产。

Review 清单：

```json
{
  "protocol": "research-v2-coco-review-manifest-v1",
  "partition": "static",
  "paper_ids": ["paper-1"],
  "records": [{"paper_id": "paper-1", "path": "/absolute/review.json", "sha256": "<sha256>"}]
}
```

参考清单：

```json
{
  "protocol": "research-v2-coco-reference-manifest-v1",
  "partition": "static",
  "paper_ids": ["paper-1"],
  "benchmark_files": [{"path": "/absolute/benchmark.jsonl", "sha256": "<sha256>"}],
  "complete_human_classify_file": {"path": "/absolute/human-classify.jsonl", "sha256": "<sha256>"},
  "reviewer_selection_file": {"path": "/absolute/reviewer-selection.json", "sha256": "<sha256>"}
}
```

`materialize_scoring_config` 接收明确配置的 CoCo 路径、Python、Split/Classify/Judge 本地服务、各模型目录、BERTScore 模型目录、阶段时间上限和重试上限，冻结源码与模型文件身份。Judge 固定为本地 `qwen3.8-27b`，其请求参数沿用标准 CoCo。阶段执行前后验证实际服务模型路径、模型名、上下文与服务实例。

## 产物和准入

`assessment.json` 包含完整论文清单、逐篇文件绑定、原始输入绑定、程序与 Memory 身份、分区、评价 epoch、评分协议、逐篇指标与有效分母、全体聚合和 `mean_score`。`papers/*.json` 保存逐篇结果，`stages/*` 保存原始阶段结果、日志和持久化尝试预算，`cache/*` 保存分区、候选和 epoch 隔离的模型响应。

`validate_assessment_receipt` 验证完整清单、所有文件 hash、逐篇与原始标准评分的一致性、冻结请求身份、适用性、真实分母与聚合。`Paper.` 使用标准五质量维度中的适用值；标准 Judge 额外 `average` 包含 actionability，保留其原始诊断身份。

评分故障保存 `status="paused"`、失败原因与空分数，并向共享运行器抛出 `DevelopmentCoCoFailure`。各阶段在启动前保存尝试预算，恢复沿用已消费记录；已终止失败恢复时产生零次额外评分调用。标准内部有限尝试完成后，确定性失败直接进入原因分类。共享运行器负责全局暂停、修复证据、新 epoch 与受影响候选统一重评。

独立报告使用 `experiment_report.py` 读取这些原始产物；生成与评分阶段只保存各自原始结果。

## 验证范围

CoCo 适配、真实共享 backend 与独立报告的针对测试合跑为 **30 passed**。包含实际 CoCo 参考评审过滤、Judge 提示参数、Table-1 聚合与 `X` 语义，以及五角色独立 Memory、完整 Receipt、新 epoch、角色诊断失败暂停。测试中的模型和 embedding 返回使用 fixture；部署模型的真实端到端实验继续由正式实验验收执行。
