# CLAR Subdimension Scoring Anchors

状态：frozen（2026-08-28），第 2/5 轮 Q51–Q55 已接受。本文的 “scoring anchors” 是 evaluator rubric，不是论文 Source Anchors。

权威 rubric：`AgentReview/REVIEW_Request.md` 第 22–34 行，SHA256 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`。通用协议继承自 `role_specific_reference_free_evaluator_protocol_20260828.md`。

Evaluator 评分对象是 **Candidate Review 对论文清晰度的审查表现**，不是直接给论文写作质量打分。高分既可以准确肯定 material clarity strengths，也可以准确指出 material clarity weaknesses；当 weakness 存在时，grounded、可执行的改进建议是高分条件之一，但清晰论文不因“没有可批评内容”而失分。

## CLAR Event-Code Vocabulary

CLAR 使用统一 `{code, subdimension}` schema：

- `MATERIAL_OMISSION`
- `MATERIAL_MISJUDGMENT`
- `FABRICATED_MATERIAL_CLAIM`
- `INVALID_PAPER_EVIDENCE`
- `DUPLICATE_PADDING`
- `ROLE_LEAKAGE_LOCAL`
- `ROLE_LEAKAGE_SEVERE`
- `SYSTEMATIC_HALLUCINATION`

Authority 修正：LLM Evaluator 只能输出 atomic judgment events；`DUPLICATE_PADDING` 由 deterministic duplicate normalizer 派生，`SYSTEMATIC_HALLUCINATION` 由 parser/aggregator 按 Q30 阈值派生。二者保留在统一 registry 中用于描述最终处理结果，但不得由 Evaluator 直接输出或决定。

CLAR 正式评分通常不需要论文外事实；若 Candidate 或 Evaluator 引入影响 score 的 external factual judgment，仍受 master protocol 的 `UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID` 约束，但该外部事实原则上不应提升 CLAR 分数。

## CLAR-WRT — Writing, Terminology & Algorithm Presentation

### Applicability / N/A

- Applicable：所有具有正文叙述、章节组织、专业术语、概念定义或算法/流程表达的完整研究 submission。方法类型不同不影响 applicability。
- `N/A`：仅在输入并非可评价的完整论文文本时出现；这种情况通常应作为 data/protocol failure，而不是正常论文的 `N/A`。

### Allowed Evidence

- `PAPER`：正文结构、段落、术语定义、assumptions、pseudocode、algorithm、workflow、caption 与 cross-reference。
- `STRUCTURAL`：section/offset、heading、algorithm/figure/table presence 与引用位置。
- `FROZEN_EXTERNAL`：不用于判断论文自身写作是否清楚；外部风格偏好不得影响 score。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 material writing/terminology/algorithm-presentation clarity；或 fabricated/misread 论文结构、定义、算法步骤，使清晰度判断失效。
- `1`：只给出“写得清楚/不清楚”等泛化意见，缺少具体 PAPER evidence；遗漏会实质影响理解的组织、术语或流程问题/强项。
- `2`：正确评价部分 material 表达内容并给出一些具体证据，但遗漏至少一个会明显影响理解或复核的 material clarity point；存在 weakness 时建议过于泛化或不可执行。
- `3`：准确、具体、grounded 地评价主要组织、术语和算法/流程表达，识别主要 material strengths/weaknesses；对主要 weakness 给出可执行建议，仅有次要遗漏。
- `4`：充分覆盖所有 material clarity 内容，准确区分写作问题与技术问题；对真实 weakness 给出针对性、可执行且不改变作者技术意图的建议，对清晰部分也保持校准；不要求制造额外 criticism。

### Material Error

包括：声称术语未定义但论文已明确定义；虚构章节/算法步骤；把技术不正确误报为纯写作问题；忽略导致核心 claim、assumption 或流程无法理解的表达缺陷；用个人风格偏好代替 material clarity 判断。

### Role Leakage

方法是否正确属于 QUAL-MET，是否可复现属于 QUAL-REP，是否新颖属于 ORIG。CLAR-WRT 只评价这些内容是否被清楚、连贯、一致地表达；只有表达本身阻碍理解时才能计入。

### Admissible Event Codes

使用本文件八个 CLAR codes，`subdimension="CLAR-WRT"`。

## CLAR-NOT — Notation & Mathematical Explanation Clarity

### Applicability / N/A

- Applicable：论文的 material claims 或方法使用数学符号、公式、定理、证明、derivation 或形式化定义。缺少应有定义/解释仍为 applicable。
- `N/A`：论文没有 material mathematical notation 或形式化推导；零散常用 metric 名称、简单百分比和非技术编号不使该项 applicable。

### Allowed Evidence

- `PAPER`：symbols、equations、definitions、theorems、proofs、derivations 与 surrounding explanation。
- `STRUCTURAL`：equation/definition/theorem positions、symbol occurrence 与 cross-reference。
- `FROZEN_EXTERNAL`：不用于按外部教材风格重写 notation；外部数学事实若影响 correctness 应归 QUAL-MET 并绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 material notation/math explanation；或 fabricated/misread symbol、equation、definition 或 derivation，导致判断失效。
- `1`：只泛称符号清楚/混乱，未指出 material symbol、定义或推导位置；遗漏阻碍理解的核心 notation issue/strength。
- `2`：正确评价部分 notation consistency/definition/explanation，但遗漏至少一个明显影响理解 material derivation/claim 的问题，或建议不够具体。
- `3`：准确、具体、grounded 地评价主要 symbols、definitions、consistency 与 derivation explanation；识别主要 strengths/weaknesses并给出可执行修正，仅有次要遗漏。
- `4`：充分覆盖所有 material mathematical exposition，准确区分 notation clarity 与 mathematical correctness，对真实问题提出最小且有效的定义、符号或推导说明改进；不要求增加不必要推导。

### Material Error

包括：把已定义符号说成未定义；混淆两个不同 symbols；虚构 equation inconsistency；把 proof correctness 问题仅当成 notation；忽略导致核心 derivation 无法跟随的缺失定义或跳步。

### Role Leakage

公式是否正确、证明是否成立属于 QUAL-MET；CLAR-NOT 评价其符号、定义和解释是否使读者能够理解。两者同时存在时，只计表达层面的判断。

### Admissible Event Codes

使用本文件八个 CLAR codes，`subdimension="CLAR-NOT"`。

## CLAR-FIG — Figures & Visual Aids Clarity

### Applicability / N/A

- Applicable：text-only Markdown 中存在足够的机器可读 visual information，例如完整 table cells、caption、figure/table reference、文本化 visual description 或正文对 visual content 的明确解释，可据此评价 textual clarity 与 reference consistency。
- `N/A`：只有 image link/placeholder 而没有实际 image pixels 或机器可读内容；或者当前 paper 没有足够机器可读 visual information。缺少 pixels 不得被当作论文 weakness。

### Allowed Evidence

- `PAPER`：机器可读 table cells、caption、figure/table reference、文本化 visual description、正文解释及其一致性。
- `STRUCTURAL`：figure/table placeholder、caption/reference presence、section/offset 与 extraction metadata；presence 不证明 pixels 中的任何视觉属性。
- `FROZEN_EXTERNAL`：不用于按外部审美或模板偏好评分。

当前输入不包含 PDF page/image pixels，因此禁止评价字体大小、颜色、布局、曲线重叠、像素级可读性、图像质量、视觉美观或仅能从 pixels 判断的 misleading presentation。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价可观察的 material visual-text representation；或对未观察到的 image pixels/visual 属性作 material judgment，或 fabricated/misread table cells、caption、reference 或文本化 visual claim，使判断失效。
- `1`：只泛称图表清楚/模糊，未绑定机器可读内容；遗漏会实质影响理解的 table textual clarity、caption/reference consistency 或正文解释问题/强项。
- `2`：正确评价部分机器可读 visual information，但遗漏至少一个明显影响解释的 table text、caption、reference 或正文衔接问题；存在 weakness 时建议不够具体。
- `3`：准确、具体、grounded 地评价主要机器可读 tables/captions/references/descriptions 是否一致并支持理解；对主要 weakness 给出可执行修正，仅有次要遗漏。
- `4`：充分覆盖所有 material、可观察的 visual-text information，准确识别其信息价值、解释边界和文本一致性，对真实 weakness 提出针对性修正；明确不对不可见 pixels 作判断，也不奖励更多图表数量。

### Material Error

包括：对不可见字体、颜色、布局、曲线重叠、像素可读性或视觉误导作实质判断；把 image link 当作已观察 pixels；误读/虚构 table cells、caption、reference 或文本化 visual result；把结果无效性当作纯文本呈现问题。对未观察 visual 属性的 material 判断按 unsupported/fabricated judgment 和 master hallucination rules 处理。

### Role Leakage

实验是否有效属于 QUAL-EXP，统计解释是否成立属于 QUAL-STA；CLAR-FIG 只评价当前 text-only input 可观察的 table/caption/reference/description 是否清楚、一致并支持理解。

### Admissible Event Codes

使用本文件八个 CLAR codes，`subdimension="CLAR-FIG"`。

## Frozen Round-2 Decisions

- `CLAR-WRT` 对所有完整、可评价 research submissions 始终 applicable；输入不完整是 data/protocol failure，不是正常 `N/A`。
- `CLAR-NOT` 只在 material mathematical/formal content 存在时 applicable；存在 material notation 但定义/推导不清是 weakness。
- 当前 formal input 是 text-only Markdown。`CLAR-FIG` 只评价机器可读 table/caption/reference/文本化 visual description；没有足够可读 visual information 时为 `N/A`。
- 存在 material clarity weakness 时，`3–4` 要求 grounded、可执行且不改变技术意图的建议；清晰论文不强制制造 suggestion。
- Event schema 使用八个统一 code + `subdimension`。对不可观察 visual 属性作 material 判断适用 unsupported/fabricated judgment 与 master hallucination rules。
