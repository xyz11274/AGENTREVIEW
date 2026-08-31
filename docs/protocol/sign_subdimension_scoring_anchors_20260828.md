# SIGN Subdimension Scoring Anchors

> 本文保存语义合同；当前代码、G2 物化与延期项以本目录 [README](README.md) 为准。

状态：frozen（2026-08-28），第 3/5 轮 Q56–Q60 已接受。本文的 “scoring anchors” 是 evaluator rubric，不是论文 Source Anchors。

Reviewer question taxonomy 以 `AgentReview/REVIEW_Request.md` 第 35–51 行为冻结基础，SHA256 `609eb9308b21b327716e0ae4ed60697be4922f3c829a7def2ea8d2ff0e7cbe42`；本文件是详细 evaluator scoring anchors 的权威。通用协议继承自 `role_specific_reference_free_evaluator_protocol_20260828.md`。

Evaluator 评分对象是 **Candidate Review 对论文 significance 的审查表现**，不是直接给论文影响力打分。高分要求准确校准 material impact strengths、limitations 与 evidence boundary；不奖励 hype、影响范围夸大、更多 criticism 或仅凭模型常识作出的外部影响判断。

## SIGN Event-Code Vocabulary

SIGN 使用统一 `{code, subdimension}` schema：

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

## SIGN-BRD — Broad Research Impact

### Applicability / N/A

- Applicable：所有完整 research submissions。即使工作只面向狭窄问题或 broad impact 很有限，Candidate 是否准确识别这种有限性仍是可评分内容。
- `N/A`：仅在输入不是可评价的完整研究 submission 时出现，此时通常属于 data/protocol failure。

### Allowed Evidence

- `PAPER`：problem scope、claimed contribution、stated beneficiaries/users、generalization claims、limitations 与 discussion。
- `STRUCTURAL`：scope/impact/limitation section、cross-domain experiment presence。
- `FROZEN_EXTERNAL`：跨领域采用、领域规模或 prior influence 等论文外事实；影响 score 时必须绑定 evidence ID。Pool 未覆盖不能被解释为没有 broad impact。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 broad research impact；或 materially exaggerate/fabricate 受众、领域覆盖、采用情况或影响路径，使判断失效。
- `1`：只称“重要/不重要/影响广泛”，没有联系论文 problem、contribution、scope 或 evidence；遗漏核心 broad-impact strength/limitation。
- `2`：正确评价部分潜在研究影响，但遗漏至少一个会明显改变影响范围判断的 material scope、transfer condition 或 limitation，或把可能性表述成已证实影响。
- `3`：准确、具体、grounded 地评价主要研究受众、可迁移贡献、影响条件与局限，识别 material strengths/weaknesses；仅有次要遗漏。
- `4`：充分评价所有 material broad-impact pathways，清楚区分已证明贡献、合理潜力与纯 speculation，对广泛或有限影响均保持校准；不要求扩大受众或制造 criticism。

### Material Error

包括：把单一数据集增益自动等同于跨领域影响；虚构研究群体采用；忽略限制 broad applicability 的关键 assumption；把 paper 未声称的应用范围当作事实；以无 evidence ID 的外部 field knowledge 决定分数。

### Role Leakage

工作是否新颖属于 ORIG，方法是否正确属于 QUAL。SIGN-BRD 评价贡献若成立会影响多少研究方向、以何种路径产生影响，以及证据支持到什么程度。

### Admissible Event Codes

使用本文件十个 SIGN codes，`subdimension="SIGN-BRD"`。

## SIGN-DOM — Domain / Applied Impact

### Applicability / N/A

- Applicable：论文针对明确 application domain、用户群、专业任务或 deployment context，或者提出 material domain-benefit claim。
- `N/A`：纯基础/抽象工作没有具体 domain/application claim，也没有足够 frozen evidence 支持可核验的 domain-impact 判断。

### Allowed Evidence

- `PAPER`：domain problem、users、deployment setting、domain metrics、constraints、case studies 与 claimed benefits。
- `STRUCTURAL`：application/case-study/deployment section 与 domain dataset presence。
- `FROZEN_EXTERNAL`：domain standard、practice、constraint 或 adoption fact；影响 score 必须绑定 evidence ID。Parametric knowledge 不是 admissible evidence。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 domain impact；或 materially misidentify/fabricate domain、users、constraints、deployment evidence 或 benefit。
- `1`：只称“有应用价值/缺少应用”，未联系具体 domain need、evidence 或 constraints；遗漏核心 domain-impact strength/limitation。
- `2`：正确评价部分 domain value，但遗漏至少一个会明显改变实际意义的 material user need、constraint、validation gap 或 benefit limitation；存在明显 overclaim。
- `3`：准确、具体、grounded 地评价主要 domain problem、beneficiaries、evidence、constraints 与实际适用范围；仅有次要遗漏。
- `4`：充分评价所有 material domain-impact pathways，区分实验代理与实际 deployment evidence，对 benefit、constraint 和未验证环节保持良好校准；不要求工作必须已部署。

### Material Error

包括：虚构用户或 deployment；把 benchmark performance 自动当作真实 domain benefit；忽略会阻断应用的 material constraint；以无 evidence ID 的行业/领域事实奖励或处罚 Candidate。

### Role Leakage

系统技术正确性属于 QUAL，社会合规义务属于 POL。SIGN-DOM 只评价对特定 domain/user/task 的潜在或已验证重要性。

### Admissible Event Codes

使用本文件十个 SIGN codes，`subdimension="SIGN-DOM"`。

## SIGN-SOT — Improvement Over State of the Art

### Applicability / N/A

- Applicable：论文提出 SOTA、superiority、relative improvement 或 stronger-than-baseline claim，或者 PAPER 中存在 material baseline comparison。缺少足以支持所提 comparative claim 的验证仍为 applicable weakness。
- `N/A`：论文没有实质 comparative-improvement claim 或 baseline comparison，且 Frozen External Evidence Pool 不足以形成可核验 SOTA 判断。

### Allowed Evidence

- `PAPER`：baseline tables、metrics、datasets、relative claims、variance/statistics 与 limitation statements。
- `STRUCTURAL`：comparison table、baseline/related-work section 与 result reporting presence。
- `FROZEN_EXTERNAL/PRIOR_WORK`：唯一可用于核验论文外 SOTA/baseline facts 的来源；所有影响 score 的 external facts 必须绑定 evidence ID。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 comparative improvement；或 fabricated/misread baseline、metric、result、SOTA status 或 external fact，使判断失效。
- `1`：把任何正向结果直接称为 SOTA/重大提升，或泛称提升有限但没有 PAPER/frozen evidence；遗漏核心 relative-impact issue。
- `2`：正确评价部分 improvement evidence，但遗漏至少一个会明显改变意义的 material baseline strength、metric comparability、consistency、variance 或 dataset limitation，或对 pool coverage 过度外推。
- `3`：准确、具体、grounded 地评价主要 relative claims、提升幅度、baseline strength、consistency 与统计/范围限制，明确 frozen evidence 边界；仅有次要遗漏。
- `4`：充分评价所有 material comparative claims，正确区分数值增益、统计可靠性、实际意义与 dataset scope，不宣称 pool 外 SOTA facts；不要求必须取得最大数值提升。

### Material Error

包括：无 evidence ID 宣称某方法是/不是 SOTA；误读 metric direction 或 table；把不公平 baseline comparison 当强证据；把 marginal gain 自动称为重大影响；pool 为空时依赖参数知识补充 comparator。

### Role Leakage

实验设计公平性本身属于 QUAL-EXP，统计可靠性属于 QUAL-STA；SIGN-SOT 使用这些已经观察到的 evidence 评价 improvement 的 magnitude/importance，不重复给实验完整性或统计规范打分。

### Admissible Event Codes

使用本文件十个 SIGN codes，`subdimension="SIGN-SOT"`；`UNBOUND_EXTERNAL_FACT` 与 `INVALID_EXTERNAL_EVIDENCE_ID` 为强制可用 code。

## SIGN-IMP — Real-World & Societal Impact

### Applicability / N/A

- Applicable：论文提出或存在由其方法、数据、deployment 或使用情境直接形成的 material real-world/societal pathway，包括 benefits、harms、fairness、environmental impact 或 accessibility。
- `N/A`：纯抽象工作没有可识别的 material real-world/societal pathway，论文也未提出相关 claim；不得为凑评分虚构远期影响。

### Allowed Evidence

- `PAPER`：intended use、beneficiaries、affected groups、deployment assumptions、impact statement、limitations、risk/mitigation 与 resource claims。
- `STRUCTURAL`：impact/ethics/limitation/resource section 与 checklist presence；presence 不证明内容充分。
- `FROZEN_EXTERNAL`：人口、环境、行业采用或社会事实；影响 score 必须绑定 evidence ID。无 frozen evidence 时只能评价 paper 自身的 impact reasoning，不能补充外部事实。

### 0–4 Subdimension Scoring Anchors

- `0`：没有有效评价 material real-world/societal impact；或 fabricated affected groups、benefits、harms、deployment、fairness/environment facts，使判断失效。
- `1`：只泛称“有社会价值/存在风险”，未建立从论文贡献到 impact 的具体 pathway；遗漏主要 material benefit/risk/limitation。
- `2`：正确评价部分 impact pathways，但遗漏至少一个会明显改变重要性判断的 material affected group、assumption、trade-off 或 uncertainty，或把 speculative possibility 写成确定后果。
- `3`：准确、具体、grounded 地评价主要 benefits、harms、affected groups、conditions 与 uncertainty，识别 material strengths/limitations；仅有次要遗漏。
- `4`：充分评价所有 material impact pathways，清楚区分直接/间接、已验证/潜在、benefit/risk 与 evidence boundary，对正负影响均保持校准；不以列出更多风险或 benefits 计分。

### Material Error

包括：虚构受影响群体或 deployment；把研究价值自动等同于社会价值；无证据量化环境/公平影响；忽略明显 material trade-off；把 policy noncompliance 当作 significance 本身。

### Role Leakage

SIGN-IMP 评价潜在后果的范围、重要性与证据；POL-ETH/POL-IMP 评价作者是否满足伦理、披露、审批、licence、checklist 或 mitigation 等义务。同一事实可以被两者读取，但不得以同一 compliance 缺失重复构造 significance 分。

### Admissible Event Codes

使用本文件十个 SIGN codes，`subdimension="SIGN-IMP"`。

## Frozen Round-3 Decisions

- `SIGN-BRD` 对所有完整 research submissions 始终 applicable；有限影响范围也是合法判断。
- `SIGN-DOM` 只在存在明确 domain/application/user/professional-task/deployment claim 时 applicable，不推测基础研究的应用价值。
- Frozen prior-work pool 为空但 PAPER 有 comparative/SOTA-style claims 时，`SIGN-SOT` 继续评价 reported improvements；global SOTA identity 不可判定。
- `SIGN-IMP` 评价后果范围/重要性/pathway/uncertainty，POL 评价义务与合规；同一 compliance omission 不重复降低两者。无 material impact pathway 时 `SIGN-IMP=N/A`。
- Event schema 使用十个统一 code + `subdimension`；hype/speculation 属于 `MATERIAL_MISJUDGMENT`，无合法 external evidence ID 使用对应 evidence violation code，不新增 hype penalty dimension。
