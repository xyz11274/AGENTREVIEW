# 独立实验结果汇总

本工具落实 [ADR-0015](adr/0015-paper-fitness-and-separate-results-report.md)。评审生成和标准 CoCo 评分先各自保存原始产物；随后由独立入口读取结果，写出新的报告目录。

## 入口与产物

Python API：

```python
from agentreview.research_v2.experiment_report import generate_experiment_report

result = generate_experiment_report(
    "run/report-input.json",
    "reports/run-snapshot-001",
    stage="development",
)
```

`development` 读取开发分区，包括 `build`、`static`、`rotating`，并在打开任何分区资产前跳过 `sealed_test` 与 `historical`。`final` 显式开放这些额外分区的报告读取。每个分区、用途、运行、评分协议、评价 epoch、cohort、方案均独立汇总。

| 文件 | 内容 |
|---|---|
| `report.json` | 全部逐篇记录、方案汇总、谱系、角色诊断、原汇总与重算差异、实际读取的输入路径和 SHA-256 |
| `papers.csv` | 预定清单中的每篇论文、标准 Table-1 指标、各项有效分母、适用性、失败状态与来源 |
| `variants.csv` | 三种初始 seed、各自无 memory 对照、CoCo Native、候选、Champion 的独立汇总 |
| `role_diagnostics.csv` | 原角色诊断及其自身评价协议 |
| `README.md` | 本次报告的身份与文件索引 |

报告目录专用于派生产物。相同输入可在同一目录重复生成；输入追加或变化后使用新的报告目录。报告生成只读取清单和已保存的评分、诊断，正式排名与原始结果继续保留。

## 输入清单

最小结构示例：

```json
{
  "protocol": "research-v2-experiment-report-input-v1",
  "scopes": [
    {
      "partition": "static",
      "purpose": "selection",
      "run_id": "experiment-001",
      "scoring_protocol": {
        "protocol": "research-v2-standard-coco-v1",
        "config_sha256": "<评分配置 SHA-256>",
        "standard_source_sha256": "<标准评分源码身份>"
      },
      "evaluation_epoch": 0,
      "cohort_id": "<预定评价集合身份>",
      "papers": [{"paper_id": "paper-1", "title": "论文标题"}],
      "variants": [
        {
          "variant_id": "seed-vector",
          "kind": "seed",
          "seed": "vector_search",
          "candidate_id": "seed-vector",
          "assessment": {
            "path": "seed-vector/assessment.json",
            "sha256": "<原始评价凭据 SHA-256>"
          }
        },
        {
          "variant_id": "no-memory-vector",
          "kind": "no_memory",
          "matched_seed": "vector_search",
          "assessment": null
        }
      ]
    }
  ]
}
```

实验运行器将预先约定的完整方案清单写入 `variants`。三种初始 seed 的身份为 `vector_search`、`llm_summarizer`、`experience_learner`。每个无 memory 对照通过 `matched_seed` 指明比较对象。`kind` 还支持 `coco_native`、`candidate`、`champion`；谱系字段包括 `candidate_id`、`parent_id`、`seed`、`round`、`program_sha256`。候选与初始 seed 各自保留独立行。

`assessment` 指向标准 CoCo 适配器保存的 `assessment.json`。其中 `papers` 是 `{paper_id,path,sha256}` 的逐篇绑定列表，`aggregate` 保存原始汇总。逐篇文件采用 `research-v2-development-coco-paper-v1`，包含 `metrics`、`metric_counts`、`applicability`、失败信息及原始 Judge 输出。工具通过适配器的纯函数 `aggregate_paper_scores` 从标准逐篇 Table-1 分数重算聚合。

角色诊断可附于方案的 `role_diagnostics`：

```json
{
  "paper_id": "paper-1",
  "role": "QUAL",
  "scoring_protocol": "historical-internal-v1",
  "artifact": {"path": "role-evaluation.json", "sha256": "<SHA-256>"}
}
```

这些数据仅进入角色诊断表。历史单角色 `review_quality` 保留原评价身份；标准 CoCo 表只接受适配器协议的完整评审评分。

## 缺失、失败与分母

- `complete`：该论文完成标准评分。每项有效分母为 `0` 或 `1`，合法 `X` 的适用性为 `not_applicable`。
- `failed`：保留失败阶段、原因与空分数。
- `pending`：评分尚待完成，分数为空。
- `missing`：清单已约定这篇论文，当前报告输入快照中尚无评分产物。

失败、待评分和缺失项的适用性为 `unknown`，与合法 `X` 分开。预定篇数始终由 `papers` 决定；每项分数分母取实际有效论文数。任何未完整评价的方案，其成功子集汇总标为 `successful_subset_diagnostic`。报告记录原汇总与重算结果的逐指标差异；报告产物本身只承担展示职责。

历史没有生成的某 seed 标准 CoCo 结果在清单中使用 `assessment: null`，报告仍写出全部预定论文的 `missing` 行和空汇总。

评分产物尚未生成时，运行器可以在方案中提供 `paper_outcomes`，每项含 `paper_id` 和 `status`（`not_generated`、`not_scored`、`failed`），以及已有的 `review` 来源或 `failure`。报告据此区分未生成、已有评审但未评分、明确生成失败。已有标准评分文件优先作为该论文的评价记录。方案自身的 `status` 与 `failure` 单独保留，候选整体失败保持候选身份。

## 验证

`test_research_v2_experiment_report.py` 验证真实 JSON/CSV 输出、缺失行与分母、合法不适用、角色内评分隔离、开发报告对封存资产的读取隔离、跨 epoch/分区分组、输入哈希漂移及独立目录重放。
