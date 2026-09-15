# CoCo Native 模型基线

CoCo Native 是模型直接使用 CoCo 原生评审提示的独立对照。它与保持五角色评审流程、关闭 Memory 的匹配对照具有独立的方案身份，结果分别展示。

## 原始提示与提取逻辑

`research_v2/coco_native.py` 从本地未修改的 CoCo 源码加载原始定义：

- [CoCo 原始评审入口](../../CoCoReviewBench/src/evaluation/_1_query_ai_reviewer.py)的 `_build_prompts`、`build_ai_scientist_review`、`extract_review_text`。
- [AI-Scientist 评审提示](../../CoCoReviewBench/src/evaluation/ai_scientist/perform_llm_review.py)的 `reviewer_system_prompt_base`、`neurips_form`、few-shot 资产列表及 `get_review_fewshot_examples_wo_paper`。
- [原始 JSON 提取器](../../CoCoReviewBench/src/evaluation/ai_scientist/llm.py)的 `extract_json_between_markers`。

加载使用选定的原始 AST 定义，函数体和提示表达式直接来自文件；服务启停、批量数据加载等模块顶层代码保持在 CoCo 自身运行器中。模型 `qwen3.8-27b` 对应 CoCo 的 AI-Scientist 分支。模型 identity 中的 `enable_thinking` 决定原始提示末尾的思考指令。

`num_fewshot` 由实验协议显式给出，范围为 `0..3`。启用的原始 few-shot JSON/TXT 资产保存路径与 SHA-256。源码身份同时记录上述三个原始文件的 SHA-256。输入使用保存的 `Paper.text` 完整正文，由统一模型请求包装器检验输入及总 token 预算。

## API 与产物

```python
review = generate_native_review(
    paper_path=paper_json,
    coco_root=coco_repository,
    run_id=run_id,
    output_root=paper_output_directory,
    completion_client=frozen_completion_client,
    model_identity=model_identity,
    max_tokens=completion_budget,
    num_fewshot=explicit_fewshot_count,
)
```

每篇论文使用独立的 `paper_output_directory`。返回评审对象含 `paper_id`、`review_text`、`raw_model_output`、`identity`、`artifact_path`。输出目录保存：

- `request.json`：完整实际消息、论文及源码身份、生成参数。
- `attempt.json`：请求发出前的持久化记录。
- `raw.json`：原始模型返回或带分类的失败信息。
- `review.json`：原始 CoCo 提取逻辑得到的评审及结构化提取结果。

源码、正文、请求或参数变化需要新的输出身份。完整原始响应可直接恢复提取；终止失败与中断请求转交外层全局恢复。外层请求包装器承担有限瞬态重试。原始提取器支持的 raw-text fallback 保留相同语义。

## 验证范围

`test_research_v2_coco_native.py` 使用真实本地 CoCo 源码及 few-shot 资产，验证完整正文末尾进入请求、原始提示与提取一致、模型返回与源码身份保存、恢复过程的调用次数、失败持久化、显式参数与独立输出目录。模型调用由测试客户端提供；本轮尚未调用部署模型运行 Native 实验，正式模型行为通过开发集全链路验收确认。
