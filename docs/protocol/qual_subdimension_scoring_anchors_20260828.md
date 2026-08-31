# QUAL Subdimension Scoring Anchors

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen（2026-08-28），第 1/5 轮 Q46–Q50 已接受。本文的 “scoring anchors” 是 evaluator rubric，不是论文 Source Anchors。

Reviewer question taxonomy 以 `AgentReview/REVIEW_Request.md` 第 1–21 行为冻结基础，SHA256 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`；本文件是详细 evaluator scoring anchors 的权威。通用信息边界、Evidence ID、Candidate Independence、两阶段 applicability、`0–4 + N/A` 外壳、validity rules 与聚合公式继承自 `role_specific_reference_free_evaluator_protocol_20260828.md`。

Evaluator 评分对象是 **Candidate Review 对论文质量问题的审查表现**，不是论文自身的质量。高分既可来自准确识别 material strengths，也可来自准确识别 material weaknesses；不得按 criticism 数量计分。

## QUAL Event-Code Vocabulary

以下 code 只能以结构化 `{code, subdimension}` 形式出现，不携带逐 paper 自由文本反馈：

- `MATERIAL_OMISSION`：遗漏会实质影响该 subdimension 审查结论的内容。
- `MATERIAL_MISJUDGMENT`：对 material 内容作出与 admissible evidence 明显不一致的判断。
- `FABRICATED_MATERIAL_CLAIM`：编造足以影响 review judgment 的论文事实或证据。
- `INVALID_PAPER_EVIDENCE`：引用的 PAPER/STRUCTURAL identity 不存在或不支持判断。
- `INVALID_EXTERNAL_EVIDENCE_ID`：引用的 ID 不属于当前 target paper 的 Frozen External Evidence Pool。
- `UNBOUND_EXTERNAL_FACT`：论文外事实影响 score，却没有绑定合法 evidence ID。
- `DUPLICATE_PADDING`：重复等价意见以制造虚假覆盖；确定性去重后评分。
- `ROLE_LEAKAGE_LOCAL`：局部越界内容忽略。
- `ROLE_LEAKAGE_SEVERE`：主体不再评价 QUAL，paper fitness 记 `0`。
- `SYSTEMATIC_HALLUCINATION`：满足已冻结的整篇归零条件。

Authority 修正：LLM Evaluator 只能输出 atomic judgment events；`DUPLICATE_PADDING` 由 deterministic duplicate normalizer 派生，`SYSTEMATIC_HALLUCINATION` 由 parser/aggregator 按 Q30 阈值派生。二者保留在统一 registry 中用于描述最终处理结果，但不得由 Evaluator 直接输出或决定。

Parser/protocol code（例如 `APPLICABILITY_INCONSISTENCY`、非法 schema、retry exhaustion）不属于 QUAL 质量分，但可使 formal run fail-fast。

## QUAL-MET — Methodological Soundness

### Applicability / N/A

- Applicable：论文提出、分析或使用会支撑主要 claims 的算法、模型、目标函数、理论过程、系统架构或技术流程；即使方法描述缺失或存在缺陷仍为 applicable。
- `N/A`：论文没有可评价的技术方法 claim，例如纯立场/评论性文章，且其主要贡献不依赖方法正确性。

### Allowed Evidence

- `PAPER`：方法定义、假设、目标函数、算法、证明、实现说明、附录。
- `STRUCTURAL`：公式、算法块、章节与 cross-reference 位置。
- `FROZEN_EXTERNAL`：仅用于核验明确依赖的外部方法事实；影响 score 时必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有对核心方法正确性作出有效评价；或把核心方法、假设、目标、算法步骤作 material misreading/fabrication，使判断失效。
- `1`：只有“方法合理/不合理”等泛化评价，基本未核验关键假设、机制或 claim-support relation；遗漏核心 soundness 问题或强项。
- `2`：正确评价部分关键设计并有 PAPER 依据，但遗漏至少一个会明显改变 soundness 判断的 material assumption、failure mode 或技术强项；结论校准不足。
- `3`：准确、具体地评价主要技术机制、关键假设与 claim support，识别主要 material strength/weakness；仅有不改变总体判断的次要遗漏。
- `4`：对所有 material 方法内容作准确、具体、充分 grounded 且校准良好的审查，能区分成立条件、局限与实际错误；不要求穷举非 material 细节。

### Material Error

包括但不限于：误述核心 objective/algorithm；把论文没有采用的方法当作事实；忽略会使主要 claim 不成立的假设；错误宣称数学/实现缺陷；以无 evidence ID 的外部方法知识决定分数。

### Role Leakage

纯粹讨论 novelty、impact、写作风格或 policy 属于越界；只有当这些内容直接影响方法是否可理解、可执行或技术正确时，相关部分才可作为 QUAL-MET 证据。

### Admissible Event Codes

`MATERIAL_OMISSION`、`MATERIAL_MISJUDGMENT`、`FABRICATED_MATERIAL_CLAIM`、`INVALID_PAPER_EVIDENCE`、`INVALID_EXTERNAL_EVIDENCE_ID`、`UNBOUND_EXTERNAL_FACT`、`DUPLICATE_PADDING`、`ROLE_LEAKAGE_LOCAL`、`ROLE_LEAKAGE_SEVERE`、`SYSTEMATIC_HALLUCINATION`。

## QUAL-EXP — Experimental Design & Evaluation

### Applicability / N/A

- Applicable：论文报告 empirical claims/experiments，或其主要 claims 按论文自身范围需要实验验证但该验证缺失。缺少实验不是 `N/A`，而是可评价的 weakness。
- `N/A`：主要贡献为不依赖 empirical validation 的纯理论、形式化或评论性工作，且没有实质经验性 claim。

### Allowed Evidence

- `PAPER`：datasets、splits、baselines、metrics、ablations、tables/figures、实验协议与 claims。
- `STRUCTURAL`：表格、图、实验章节、appendix 与 artifact presence。
- `FROZEN_EXTERNAL`：baseline relevance、benchmark convention 等论文外事实；必须绑定 evidence ID，池未覆盖的 baseline 不得用于处罚。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价实验是否支撑主要 claims；或 materially misread/fabricate dataset、baseline、metric、result、ablation 或实验结论。
- `1`：只给出“实验充分/不足”等泛化结论，未联系主要 claims 与具体设计；遗漏核心验证缺口或关键实验强项。
- `2`：正确评价部分 datasets/baselines/metrics/ablations，但遗漏至少一个会明显改变 claim support 的 material 设计问题或强项，或对结果强度校准不足。
- `3`：准确、具体、grounded 地评价主要实验设计与 claim coverage，识别主要 material strengths/weaknesses；仅有次要遗漏。
- `4`：全面覆盖所有 material 实验设计因素，准确判断每项主要 claim 获得何种程度支持，并对缺失验证、适用范围与负面结果保持良好校准；不奖励罗列更多实验建议。

### Material Error

包括：误读核心 table/result；声称不存在的 baseline/ablation；把不相关 metric 当作主要支持；忽略导致泄漏或不公平比较的数据划分；用未绑定 external fact 宣称某 baseline 必须存在或已过时。

### Role Leakage

单纯评价方法 novelty、论文影响或写作质量越界；实验展示是否清楚仅在它妨碍正确理解 experimental evidence 时属于 QUAL-EXP。

### Admissible Event Codes

与 QUAL-MET 相同的十个结构化 codes，并将 `subdimension` 固定为 `QUAL-EXP`。

## QUAL-REP — Reproducibility & Implementation Details

### Applicability / N/A

- Applicable：论文包含可复现的技术过程、实验、数据处理、实现、artifact 或形式化推导，其细节充分性会影响复核主要 claims。
- `N/A`：没有可执行/可复核过程的纯立场、评论或叙述性工作。缺少应当提供的实现细节本身不导致 `N/A`。

### Allowed Evidence

- `PAPER`：hyperparameters、data splits、preprocessing、training/evaluation procedure、proof detail、code/data statements 与 appendix。
- `STRUCTURAL`：appendix、artifact/checklist、algorithm、table 与 repository-link presence。
- `FROZEN_EXTERNAL`：仅用于核验论文明确引用 artifact/licence 页面在 cutoff 前的状态，必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价复现所需信息；或 fabricated/misread code availability、hyperparameters、splits、procedure 或 artifact 状态。
- `1`：只泛称“可复现/不可复现”，未指出哪些 material details 已提供或缺失。
- `2`：正确识别部分复现信息与缺口，但遗漏至少一个会实质阻碍复核主要 claims 的 material detail，或错误估计缺失影响。
- `3`：准确、具体、grounded 地评价主要复现链条，覆盖 material details、artifact statements 与关键缺口；仅有次要遗漏。
- `4`：对复现主要 claims 所需的全部 material information 作充分且校准良好的审查，区分 paper-contained evidence、外部 artifact 可用性和仍不可核验部分；不要求机械列举所有参数。

### Material Error

包括：把未提供的 code/data/hyperparameter 说成已提供；忽略会改变结果的 split/preprocessing/training detail；使用当前网页替代 cutoff snapshot；把 repository presence 等同于可复现性。

### Role Leakage

代码质量、工程美观或 licence 合规本身不属于 QUAL-REP；只有它们影响复现主要 claims 时才计入。Licence/IRB 是否合规属于 POL。

### Admissible Event Codes

与 QUAL-MET 相同的十个结构化 codes，并将 `subdimension` 固定为 `QUAL-REP`。

## QUAL-CMP — Comparisons to Prior Work

### Applicability / N/A

- Applicable：论文提出 relative performance、novelty、superiority 或 positioning claim，报告 baselines/related work，或 frozen prior-work evidence 能支持对比较充分性的核验。
- `N/A`：论文没有实质比较性 claim，且 Frozen External Evidence Pool 没有足以形成可核验比较判断的 evidence。Pool 为空本身不构成 weakness。

### Allowed Evidence

- `PAPER`：related work、citations、baseline tables、comparison claims 与 limitation statements。
- `STRUCTURAL`：bibliography、related-work section、comparison table presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：唯一可用于补充论文外 comparator facts 的来源；每个影响 score 的外部事实必须绑定 evidence ID。Parametric knowledge 不可作为 external evidence。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价比较充分性；或 fabricated/misidentified prior work、baseline relation 或 external fact，导致比较判断失效。
- `1`：泛称比较充分/不足，未绑定论文内比较或 frozen evidence；遗漏显而易见的 material comparison issue/strength。
- `2`：正确评价部分论文内 comparisons，并合法使用部分 frozen evidence，但遗漏至少一个会明显改变 positioning/claim support 的 material comparison，或对 evidence coverage 过度外推。
- `3`：准确、具体、grounded 地评价主要 comparison claims、baseline appropriateness 与可见 prior-work coverage，明确 frozen pool 的证据边界；仅有次要遗漏。
- `4`：在 PAPER 与 frozen evidence 所允许的范围内，充分评价所有 material comparison claims，区分已验证、未覆盖与不可判断部分，不宣称 pool 外工作的存在或不存在；不奖励列举更多论文。

### Material Error

包括：用模型记忆声称缺少某工作；虚构 baseline 结果；把 pool 未覆盖解释为论文比较充分；把 `POLICY`/Human/CoCo 当 prior-work evidence；无 evidence ID 的 external fact 影响 score。

### Role Leakage

判断工作是否“新”属于 ORIG；QUAL-CMP 只评价比较设计、baseline relevance 与 claims 是否被比较证据充分支撑。二者重叠时只保留 comparison-quality 部分。

### Admissible Event Codes

与 QUAL-MET 相同的十个结构化 codes，并将 `subdimension` 固定为 `QUAL-CMP`；`UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID` 对该项为强制可用 code。

## QUAL-STA — Statistical Rigor & Validation

### Applicability / N/A

- Applicable：主要 claims 依赖随机训练/采样、有限样本估计、显著性、置信区间、方差、human study、multiple comparisons 或其他不确定性判断。论文缺少应有统计验证仍为 applicable。
- `N/A`：结果完全由确定性证明/计算建立，且主要 claims 不依赖经验变异或统计推断。

### Allowed Evidence

- `PAPER`：run count、seeds、variance/error bars、confidence intervals、tests、effect sizes、sampling、tables/figures 与 claim wording。
- `STRUCTURAL`：error-bar/CI/test reporting、实验重复与统计 appendix presence。
- `FROZEN_EXTERNAL`：仅在论文明确依赖外部统计 protocol 时使用，影响 score 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价统计支持；或 materially misread/fabricate sample size、runs、variance、test、CI、effect size 或 significance。
- `1`：只泛称“缺少统计检验/统计充分”，未联系具体 claims、变异来源或 evidence。
- `2`：正确评价部分统计报告，但遗漏至少一个会明显改变结论可信度的 material issue/strength，或把统计显著性、实际效应与稳健性混为一谈。
- `3`：准确、具体、grounded 地评价主要不确定性来源、validation 与 claim calibration，识别主要 material strengths/weaknesses；仅有次要遗漏。
- `4`：充分评价所有 material statistical support，正确区分显著性、effect size、variance、robustness 与结论范围，并对缺失检验是否真正必要保持校准；不要求无差别增加统计测试。

### Material Error

包括：把单次结果当多次均值；误读 error bar/test；无依据要求不适用的显著性检验；忽略会推翻主要 claim 的 variance/uncertainty；fabricate sample size 或统计结论。

### Role Leakage

实验整体覆盖属于 QUAL-EXP；QUAL-STA 只处理不确定性、统计推断和验证强度。写作是否清楚属于 CLAR，除非表述导致统计结论被实质误读。

### Admissible Event Codes

与 QUAL-MET 相同的十个结构化 codes，并将 `subdimension` 固定为 `QUAL-STA`。

## Frozen Round-1 Decisions

- 五项 applicability/N/A 与本文各节一致。
- 主要 claims 按自身性质需要 empirical validation 时，完全缺少实验是 `QUAL-EXP` material weakness，不是 `N/A`。
- Frozen prior-work pool 为空但论文有内部 baselines/comparisons/relative claims 时，`QUAL-CMP` 继续基于 PAPER 评分；只有论文外 completeness 判断为不可判定。
- Claims 依赖随机性、有限样本、variance 或 statistical inference 时，缺少统计验证是 `QUAL-STA` weakness；确定性结果不机械要求 significance test。
- Event schema 采用统一 `code + subdimension`，支持按 code 或 subdimension 聚合，不创建五套同义 code。
