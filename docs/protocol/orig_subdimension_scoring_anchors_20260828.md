# ORIG Subdimension Scoring Anchors

状态：frozen（2026-08-28），第 4/5 轮 Q61–Q65 已接受。本文的 “scoring anchors” 是 evaluator rubric，不是论文 Source Anchors。

权威 rubric：`AgentReview/REVIEW_Request.md` 第 52–76 行，SHA256 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`。通用协议继承自 `role_specific_reference_free_evaluator_protocol_20260828.md`。

Evaluator 评分对象是 **Candidate Review 对论文 originality 的审查表现**，不是直接给论文的新颖程度打分。Candidate 可以因准确说明某项贡献并不新而获得高分，也可以因准确识别有证据支持的原创性而获得高分。不得按“新颖点数量”、正面评价数量或 criticism 数量计分。

## ORIG Event-Code Vocabulary

ORIG 使用统一 `{code, subdimension}` schema：

- `MATERIAL_OMISSION`
- `MATERIAL_MISJUDGMENT`
- `FABRICATED_MATERIAL_CLAIM`
- `INVALID_PAPER_EVIDENCE`
- `INVALID_EXTERNAL_EVIDENCE_ID`
- `UNBOUND_EXTERNAL_FACT`
- `DUPLICATE_PADDING`
- `ROLE_LEAKAGE_LOCAL`
- `ROLE_LEAKAGE_SEVERE`
- `SYSTEMATIC_HALLUCINATION`

Authority 修正：LLM Evaluator 只能输出 atomic judgment events；`DUPLICATE_PADDING` 由 deterministic duplicate normalizer 派生，`SYSTEMATIC_HALLUCINATION` 由 parser/aggregator 按 Q30 阈值派生。二者保留在统一 registry 中用于描述最终处理结果，但不得由 Evaluator 直接输出或决定。

所有关于 prior work、first、novel、already known 或 derivative 的论文外事实必须绑定 `FROZEN_EXTERNAL/PRIOR_WORK` evidence ID。Frozen pool 只能支持“相对于该 pool”的判断，不能证明绝对首次或穷尽 prior art。

## ORIG-PROB — Novel Problem Formulation

### Applicability / N/A

- Applicable：论文提出或重新定义 material problem、task、research question、evaluation target 或 dataset-as-problem formulation；即使该 formulation 实际是已有问题的轻微变体仍为 applicable。
- `N/A`：论文没有 material problem/task/dataset-formulation contribution，原创性主张完全属于其他 subdimensions。

### Allowed Evidence

- `PAPER`：problem statement、task definition、motivation、claimed novelty、dataset purpose、differences from prior formulations。
- `STRUCTURAL`：problem/contribution/related-work section 与 task/dataset definition presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：相近 problems/tasks/datasets；影响 originality judgment 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 problem-formulation originality；或 fabricated/misread problem、task、dataset purpose 或 prior formulation，使判断失效。
- `1`：只称问题“新/不新”，未说明 formulation 的 material difference、motivation 或 evidence；遗漏核心 originality strength/limitation。
- `2`：正确评价部分 formulation contribution，但遗漏至少一个会明显改变 originality 判断的 material difference、scope、motivation 或 frozen comparator，或把微小变体夸大为新问题。
- `3`：准确、具体、grounded 地评价主要 formulation differences、意义、scope 与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material formulation contributions，清楚区分新问题、新数据载体和已有任务变体，并明确证据边界；不要求问题必须真正全新。

### Material Error

包括：把 dataset collection 自动等同于新问题；虚构 task definition；忽略会使“新问题”退化为已有任务变体的 material overlap；无 evidence ID 宣称绝对首次或已有同类工作。

### Role Leakage

数据/benchmark 的具体实验资源创新属于 ORIG-EXP；问题是否重要属于 SIGN；formulation 是否技术正确属于 QUAL。ORIG-PROB 只评价问题/任务定义本身的原创性。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-PROB"`。

## ORIG-MTH — Novel Methodology or Algorithm

### Applicability / N/A

- Applicable：论文提出、修改或组合 material algorithm、model、architecture、objective、training/inference procedure 或 system method；即使只是 minor tweak 仍为 applicable，minor novelty 是可评分判断。
- `N/A`：论文没有 material methodological contribution，主要贡献属于 problem、analysis、data/experiment 或 critical assessment。

### Allowed Evidence

- `PAPER`：method definition、claimed contribution、component differences、design rationale、related-work comparison。
- `STRUCTURAL`：method/algorithm/equation/architecture/contribution section presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：相近 methods/components；所有 external novelty judgments 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 method originality；或 fabricated/misread method、component、prior relation 或 novelty claim，使判断失效。
- `1`：只称方法“新/只是改进”，未指出 material conceptual difference、rationale 或 evidence；遗漏核心 originality point。
- `2`：正确评价部分 method differences，但遗漏至少一个会明显改变原创性判断的 material component、dependency、combination 或 frozen comparator，或把实现变化夸大为概念创新。
- `3`：准确、具体、grounded 地评价主要 conceptual differences、design rationale 与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material methodological contributions，清楚区分新机制、已有组件重用、engineering refinement 与 minor tweak，并严格限定证据范围；不要求 Candidate 给出正面 novelty 结论。

### Material Error

包括：把技术正确性等同于 novelty；把参数/规模变化自动称为新方法；虚构组件或 prior relation；忽略使方法实质等同于已有设计的 material overlap；无 evidence ID 作外部 novelty judgment。

### Role Leakage

方法是否成立属于 QUAL-MET，效果是否更强属于 SIGN-SOT。ORIG-MTH 只评价 conceptual/methodological difference 与创新理由。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-MTH"`。

## ORIG-ANL — Novel Analysis or Insights

### Applicability / N/A

- Applicable：论文把 material theoretical insight、analysis、explanation、diagnostic finding、property、failure mechanism 或新的理解作为贡献；即使 insight 被证明已有或较浅仍为 applicable。
- `N/A`：论文没有 material analysis/insight contribution，分析仅是支持其他贡献的常规步骤。

### Allowed Evidence

- `PAPER`：theorems、analysis、diagnostics、observations、claimed insight、explanation 与 relation to known results。
- `STRUCTURAL`：analysis/theory/diagnostic/ablation section presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：已有理论、analysis 或 known phenomenon；external originality judgment 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 analysis/insight originality；或 fabricated/misread insight、theorem、phenomenon 或 prior knowledge，使判断失效。
- `1`：只称 insight“新/显然/已知”，没有具体说明 material understanding 增量或 evidence。
- `2`：正确评价部分 analysis contribution，但遗漏至少一个会明显改变原创性判断的 material insight、scope、known-result relation 或 frozen comparator，或把常规 observation 夸大为深层洞见。
- `3`：准确、具体、grounded 地评价主要 understanding gains、分析边界与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material analysis/insight contributions，区分新理解、已知现象复现、技术证明与常规 diagnostic，并保持严格证据边界。

### Material Error

包括：把结果提升本身称为 insight；虚构 theorem/phenomenon；把已知事实凭参数知识判为新或旧；忽略限制 insight generality 的 material condition。

### Role Leakage

分析是否正确属于 QUAL-MET/STA，分析产生多大影响属于 SIGN。ORIG-ANL 只评价 understanding/insight 的原创性。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-ANL"`。

## ORIG-EXP — Novel Experimental Setup or Data

### Applicability / N/A

- Applicable：论文把新 dataset、benchmark、evaluation protocol、experimental setup、measurement design 或 data collection 作为 material contribution；即使它实际 derivative 或价值有限仍为 applicable。
- `N/A`：实验仅用于验证其他贡献，没有 material data/benchmark/protocol/setup originality claim。

### Allowed Evidence

- `PAPER`：dataset/benchmark/protocol/setup definition、collection、claimed difference、use case 与 related comparisons。
- `STRUCTURAL`：dataset/benchmark/evaluation-protocol/data-card section 与 artifact presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：已有 datasets/benchmarks/protocols/setups；external originality judgment 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 experimental-resource originality；或 fabricated/misread dataset、benchmark、protocol、setup 或 prior relation，使判断失效。
- `1`：只称资源/设置“新/不新”，未说明 material difference、coverage、measurement idea 或 evidence。
- `2`：正确评价部分创新，但遗漏至少一个会明显改变原创性判断的 material design difference、reuse、scope 或 frozen comparator，或把规模增加自动当作新 benchmark。
- `3`：准确、具体、grounded 地评价主要 data/setup/protocol differences 与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material experimental-resource contributions，清楚区分新数据、新问题 formulation、evaluation protocol 与常规实验配置，并严格限定 evidence boundary。

### Material Error

包括：把普通实验超参数变化称为新 setup；把 dataset 规模自动等同于原创性；虚构 collection/protocol；忽略与已有 benchmark 的 material equivalence；无 evidence ID 作外部 novelty judgment。

### Role Leakage

实验设计是否充分属于 QUAL-EXP；资源是否具有广泛价值属于 SIGN。ORIG-EXP 只评价 dataset/benchmark/protocol/setup 本身的原创性。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-EXP"`。

## ORIG-COM — Creative Combination of Existing Methods

### Applicability / N/A

- Applicable：论文的 material contribution依赖两个或以上已有 ideas/methods/components 的组合，并声称组合方式、interaction、rationale 或 resulting capability 具有原创性；简单 pipeline juxtaposition 仍为 applicable，可判 originality 很弱。
- `N/A`：论文不以 existing-component combination 作为 material contribution。

### Allowed Evidence

- `PAPER`：components、integration mechanism、interaction、design rationale、claimed synergy 与 ablations。
- `STRUCTURAL`：architecture/workflow/component/ablation section presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：components 与已有 combinations；external originality judgment 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 combination originality；或 fabricated/misread components、interaction、rationale、capability 或 prior combination，使判断失效。
- `1`：只称组合“创新/拼凑”，未分析 material integration、synergy、necessity 或 evidence。
- `2`：正确评价部分 combination design，但遗漏至少一个会明显改变原创性判断的 material interaction、rationale、existing combination 或 ablation evidence，或把并列使用夸大为 synergy。
- `3`：准确、具体、grounded 地评价主要 integration mechanism、rationale、resulting capability 与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material combination contributions，清楚区分 superficial aggregation、engineering integration 与产生新能力的 creative synergy，并保持严格证据边界。

### Material Error

包括：虚构 synergy/interaction；把组件各自新颖性自动等同于组合新颖；忽略组合已存在的 frozen evidence；把效果提升单独当作组合原创性证明。

### Role Leakage

组合是否技术有效属于 QUAL-MET/EXP，产生多大影响属于 SIGN。ORIG-COM 只评价组合构思与 integration rationale 的原创性。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-COM"`。

## ORIG-NEG — Negative Results or Critical Assessments

### Applicability / N/A

- Applicable：论文把 negative result、failure analysis、critical assessment、limitation study、diagnostic evaluation 或挑战既有 assumption 作为 material contribution。普通 supporting ablation 或 incidental negative result 不自动触发。
- `N/A`：论文没有把 negative/critical assessment 作为 material contribution。

### Allowed Evidence

- `PAPER`：negative findings、critical claims、assumptions challenged、evaluation design、limitations 与 stated contribution。
- `STRUCTURAL`：failure/limitation/diagnostic/critical-evaluation section presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：被挑战的 prior claim/assumption 与已有 critical studies；external originality judgment 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 negative/critical contribution；或 fabricated/misread negative result、target claim、assumption 或 prior assessment，使判断失效。
- `1`：只称批判“有价值/肤浅”，未说明 material finding、rigour、new understanding 或 evidence。
- `2`：正确评价部分 negative contribution，但遗漏至少一个会明显改变原创性判断的 material challenged assumption、scope、rigour 或 frozen comparator，或把普通 failure case 夸大为原创贡献。
- `3`：准确、具体、grounded 地评价主要 negative findings、critical insight、scope 与相对 frozen evidence 的原创性；仅有次要遗漏。
- `4`：充分评价所有 material negative/critical contributions，区分 incidental failure、常规 ablation 与真正改变理解的 rigorous negative result，并保持严格 evidence boundary。

### Material Error

包括：把失败性能本身当作原创贡献；虚构被挑战的 prior claim；忽略使 negative result 不成立的 material design flaw；用无 evidence ID 的外部知识宣称批判已知或首次。

### Role Leakage

negative study 的实验是否充分属于 QUAL-EXP/STA，潜在影响属于 SIGN。ORIG-NEG 只评价 negative/critical understanding 的原创性。

### Admissible Event Codes

使用本文件十个 ORIG codes，`subdimension="ORIG-NEG"`。

## Frozen Round-4 Decisions

- 六项 applicability 按论文声称的 material contribution type 触发。贡献即使最终被判断为不新、minor variation 或 derivative，只要被论文作为 material contribution，仍保持 applicable；不得用 `N/A` 规避负面的 originality 判断。
- Frozen prior-work pool 为空时，仍可依据 `PAPER` 评价论文自身的 novelty claim、文内 related-work positioning，以及 Candidate 对这些内容的判断。证据不足只排除论文外 originality 判断，不自动令整个 subdimension 为 `N/A`。
- `ORIG-PROB` 专门评价 problem/task/research-question formulation；dataset、benchmark、measurement、experimental setup 与 evaluation protocol 的资源/实验设计创新归 `ORIG-EXP`。同一论文若同时有新任务和新资源，两项可分别 applicable，但必须绑定不同 contribution objects，不得重复计分同一事实。
- `ORIG-COM` 只在 existing-method combination 本身是 material contribution 时 applicable；普通 pipeline、模块堆叠或组件并列不自动触发。`ORIG-NEG` 只在 negative result、critical assessment、systematic failure analysis 或揭示既有方法局限本身是 material contribution 时 applicable；普通 ablation、偶发失败或常规 limitations 不自动触发。
- 使用十个统一 event codes + `subdimension`，不新增 novelty-count、novelty bonus 或类似维度。Parametric knowledge 可影响内部推理，但不是 admissible external evidence；任何影响正式 score 的 external “novel / first / already known / derivative” 判断必须绑定当前 target paper 的合法 frozen evidence ID。
